"""LG ThinQ1 fake-cloud server (M1, read path).

HTTPS server that terminates TLS and answers the ThinQ1 bootstrap endpoints. Two modes:

- **bridge** (server default): forward each request to real LG, return its response, and
  observe (ingest diagmon) — proves parity before trusting standalone. Mirrors `rethink`'s
  bridge mode.
- **standalone**: answer with our own `0000/OK` XML (PROTOCOL §5 keep-alive hypothesis).

⚠️ Appliance-acceptance is only proven by the supervised sever test (TASK-010/050): point an
appliance at this server (nft DNAT to it), firewall real LG in standalone, and confirm it
operates + reconnects. Run that test with the user present.

Config via env: LGM_HOST, LGM_PORT, LGM_CERT, LGM_KEY, LGM_STATE_DIR,
LGM_MODE (bridge|standalone), LGM_UPSTREAM_HOST, LGM_UPSTREAM_PORT.
Generate a cert with `gen-cert.sh`; run with `python3 -m server.app`.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
from typing import Any, Optional
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import responses
from .state import DeviceStateStore

HOST = os.environ.get("LGM_HOST", "0.0.0.0")
PORT = int(os.environ.get("LGM_PORT", "46030"))
CERT = os.environ.get("LGM_CERT", "data/cert.pem")
KEY = os.environ.get("LGM_KEY", "data/key.pem")
STATE_DIR = os.environ.get("LGM_STATE_DIR", "data")
MODE = os.environ.get("LGM_MODE", "bridge")  # bridge (forward+observe) | standalone
UPSTREAM_HOST = os.environ.get("LGM_UPSTREAM_HOST", "eic.lgthinq.com")
UPSTREAM_PORT = int(os.environ.get("LGM_UPSTREAM_PORT", "46030"))

# Endpoints whose upstream result MUST reach LG (TASK-077): a synthetic 200 here makes
# the appliance believe it is registered while LG never saw the registration (observed
# 2026-09-25 12:16 during an LG outage: stale cloud record, the app could not attach
# the appliance anymore). On upstream failure these are retried briefly, then the 5xx
# is propagated so the appliance retries later on its own.
REGISTRATION_ENDPOINTS = (
    "/api/Device/TotalDeviceInfoSvc",
    "/api/Rtos/ContentsVerSvc",
    "/api/Rtos/FWInfoSettingSvc",
    "/api/Grid/PowerSavingInfoSvc",
)
REGISTRATION_RETRY_DELAYS = (0.5, 1.5)  # seconds; absorbs transient 502 blips

# LG's upstream cert chain isn't always verifiable from our CA bundle (cf. mitm ssl_insecure).
_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# /debug/query's bounded wait for the async snapshot to land (30 x 50 ms = 1.5 s max;
# tests zero this out)
QUERY_WAIT_ROUNDS = 30
QUERY_WAIT_GAP = 0.05


# Verified-healthy LG edges (PROTOCOL.md §2: the pool is heterogeneous and edges rot
# per-endpoint). When DNS resolution or the connection to UPSTREAM_HOST fails, the
# bridge retries against these pinned IPs so the appliances keep working.
PINNED_UPSTREAM_IPS: list[str] = ["52.158.121.103", "52.158.31.24"]


def forward(path: str, headers: dict, body: bytes,
            host: str = UPSTREAM_HOST, port: int = UPSTREAM_PORT,
            method: str = "POST") -> tuple[int, bytes]:
    """Bridge mode: forward a request to real LG; return (status, body).

    Retries against PINNED_UPSTREAM_IPS when DNS or the connection to the primary
    upstream fails (edge rot: PAIRING_RUNBOOK.md §2)."""
    targets = [host] + [ip for ip in PINNED_UPSTREAM_IPS if ip != host]
    last_err: Optional[Exception] = None
    for target in targets:
        try:
            conn = http.client.HTTPSConnection(target, port, context=_CTX, timeout=15)
            try:
                h = {k: v for k, v in headers.items()
                     if k.lower() not in ("host", "content-length", "connection")}
                conn.request(method, path, body=body, headers=h)
                resp = conn.getresponse()
                return resp.status, resp.read()
            finally:
                conn.close()
        except (socket.gaierror, OSError) as e:
            last_err = e
            sys.stderr.write(f"[bridge] forward to {target} failed ({e!r}); trying next\n")
    if last_err is not None:
        raise last_err
    raise ConnectionError("no upstream targets available")


def _parse_item(body: bytes) -> str | None:
    m = re.search(rb"<item>([^<]*)</item>", body)
    return m.group(1).decode("utf-8", "replace") if m else None


def dispatch(path: str, body: bytes, store: DeviceStateStore, *,
             mode: str = "standalone", forwarder=None,
             headers: dict | None = None,
             sleep_fn=None, method: str = "POST",
             query_fn=None) -> tuple[int, str, bytes]:
    """Route one request → (status, content_type, body). Pure function (unit-testable)."""
    if sleep_fn is None:
        sleep_fn = time.sleep
    xml_ct = "text/xml;charset=utf-8"
    # Read-only debug surface (TASK-012): the latest decoded state per devId, as JSON.
    # GET only; everything else falls through to the ThinQ1 POST handling.
    if urlsplit(path).path.endswith("/debug/state"):
        return 200, "application/json", json.dumps(store.latest, default=str).encode()
    # On-demand state refresh (TASK-070): /debug/query?dev=<id> fires a Mon Start on
    # the :47878 channel and waits briefly for the snapshot to land in the store.
    if urlsplit(path).path.endswith("/debug/query"):
        if query_fn is None:
            return 503, "text/plain", b"query channel not wired"
        dev = parse_qs(urlsplit(path).query).get("dev", [None])[0]
        if not dev:
            return 400, "text/plain", b"missing ?dev=<devId>"
        before = (store.latest.get(dev) or {}).get("ts")
        queried = query_fn(dev)
        # the snapshot arrives asynchronously milliseconds later (the appliance
        # replies in ms, cf. the fridge capture): wait a short bounded window for
        # the store's ts for this device to move, then report what we have
        fresh = False
        for _ in range(QUERY_WAIT_ROUNDS):
            if (store.latest.get(dev) or {}).get("ts") not in (None, before):
                fresh = True
                break
            sleep_fn(QUERY_WAIT_GAP)
        payload = {"queried": queried, "fresh": fresh, "latest": store.latest.get(dev)}
        return 200, "application/json", json.dumps(payload, default=str).encode()
    # Observe diagmon in BOTH modes (state ingestion is the point).
    if path.endswith("/report/diagmon"):
        try:
            store.ingest_report(body)
        except Exception as e:  # don't let a bad payload kill the request
            sys.stderr.write(f"[state] ingest failed: {e}\n")
    if mode == "bridge" and forwarder is not None:
        registration = any(path.endswith(ep) for ep in REGISTRATION_ENDPOINTS)
        delays = REGISTRATION_RETRY_DELAYS if registration else ()
        reason = "upstream unavailable"
        upstream_status = 502
        for attempt in range(len(delays) + 1):
            # default reset per attempt: a transport failure must not inherit the
            # status of an earlier attempt when we propagate (TASK-077 review)
            upstream_status = 502
            try:
                status, resp_body = forwarder(path, headers or {}, body, method)
                if status < 500:
                    return status, xml_ct, resp_body
                upstream_status = status
                reason = f"upstream {status}"
            except Exception as e:
                reason = f"forward failed ({e})"
            if not registration:
                # Telemetry (diagmon & co.): the standalone ACK keeps the appliance
                # happy while LG catches up (retry-storm avoidance, 2026-09-24).
                sys.stderr.write(f"[bridge] {reason}; answering standalone\n")
                break
            if attempt < len(delays):
                delay = delays[attempt]
                sys.stderr.write(f"[bridge] {reason}; registration retry "
                                 f"{attempt + 2}/{len(delays) + 1} in {delay}s\n")
                sleep_fn(delay)
        if registration:
            # TASK-077: never fake-ACK a registration. LG must SEE it; the
            # propagated 5xx makes the appliance retry later on its own.
            sys.stderr.write(f"[bridge] registration NOT delivered after "
                             f"{len(delays) + 1} attempts; propagating {upstream_status} "
                             f"to the appliance (TASK-077)\n")
            return upstream_status, "text/plain", b"registration not delivered upstream"
    # standalone responses (PROTOCOL §5):
    if path.endswith("/report/diagmon"):
        return 200, "application/vnd.diagmonlge.dm+xml", responses.diagmon()
    if path.endswith("/api/Device/TotalDeviceInfoSvc"):
        return 200, xml_ct, responses.total_device_info(_parse_item(body))
    if path.endswith("/api/Rtos/ContentsVerSvc"):
        return 200, xml_ct, responses.contents_ver()
    if path.endswith("/api/product/sendPushMessage"):
        return 200, xml_ct, responses.ok()
    if path.endswith("/api/Grid/PowerSavingInfoSvc"):
        return 200, xml_ct, responses.power_saving_info()
    return 200, xml_ct, responses.ok()  # permissive default (avoid retry storms)


class _TLSHTTPServer(ThreadingHTTPServer):
    """TLS is wrapped per connection, in the WORKER thread.

    Wrapping the LISTENING socket makes the handshake run inside the accept
    loop: one client that opens TCP and never completes the handshake blocks
    accept() forever, the backlog fills, and the kernel silently drops every
    new SYN (the washer knocked on a dead door for ~40 min on 2026-09-25
    while the appliance diversion made confused clients routine).
    """

    def __init__(self, address, handler, ssl_ctx):
        super().__init__(address, handler)
        self.ssl_ctx = ssl_ctx

    def finish_request(self, request: socket.socket, client_address: tuple[str, int]) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]  # typeshed's BaseServer generic binds _RequestType loosely; at runtime TCPServer hands us the accepted socket
        try:
            request.settimeout(15)  # handshake budget
            request = self.ssl_ctx.wrap_socket(request, server_side=True)
            request.settimeout(60)  # request/response cycle guard
        except (ssl.SSLError, OSError) as e:
            sys.stderr.write(f"[tls] handshake failed with {client_address}: {e}\n")
            try:
                request.close()
            except OSError:
                pass
            return
        self.RequestHandlerClass(request, client_address, self)


class _Handler(BaseHTTPRequestHandler):
    server_version = "lg-fake-cloud/0.1"

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n) if n else b""
        srv = self.server
        status, ct, resp = dispatch(
            self.path, body, srv.state,  # type: ignore[attr-defined]
            mode=srv.mode, forwarder=srv.forwarder,  # type: ignore[attr-defined]
            headers=dict(self.headers),
            query_fn=getattr(srv, "query", None))  # POST /debug/query is valid (TASK-070)
        self._send(status, ct, resp)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        srv = self.server
        # bridge mode forwards GETs too: a synthetic ACK must never mask a real
        # registration regardless of method (TASK-077)
        status, ct, resp = dispatch(
            self.path, b"", srv.state,  # type: ignore[attr-defined]
            mode=srv.mode, forwarder=srv.forwarder,  # type: ignore[attr-defined]
            headers=dict(self.headers), method="GET",
            query_fn=getattr(srv, "query", None))
        self._send(status, ct, resp)


def main() -> None:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        sys.exit(f"cert/key not found ({CERT}, {KEY}) — run ./gen-cert.sh first.")
    from . import mqtt_bridge
    from . import control_channel
    store = DeviceStateStore(
        STATE_DIR,
        on_state=mqtt_bridge.build_sink(
            control=control_channel.channel(), allow_control=control_channel.ALLOW_CONTROL))
    # wire the pump ingest BEFORE the listener starts, so no WM snapshot can arrive
    # into a storeless channel (TASK-078 review)
    control_channel.set_state_store(store)
    control_channel.start_control_server()  # :47878 control channel (M3, both families)
    control_channel.start_polling(control_channel.channel())  # TASK-070 (off unless LGM_POLL_INTERVAL)
    sys.stderr.write(
        f"[app] MQTT command handling: {'on' if control_channel.ALLOW_CONTROL else 'off'}\n")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd = _TLSHTTPServer((HOST, PORT), _Handler, ctx)
    httpd.state = store  # type: ignore[attr-defined]
    httpd.mode = MODE  # type: ignore[attr-defined]
    httpd.forwarder = forward if MODE == "bridge" else None  # type: ignore[attr-defined]
    httpd.query = control_channel.channel().query_state  # type: ignore[attr-defined]  # /debug/query
    sys.stderr.write(f"LG fake-cloud ({MODE}) on https://{HOST}:{PORT}")
    if MODE == "bridge":
        sys.stderr.write(f" → upstream {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    sys.stderr.write("\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()

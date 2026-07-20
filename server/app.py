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
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

# LG's upstream cert chain isn't always verifiable from our CA bundle (cf. mitm ssl_insecure).
_CTX = ssl._create_unverified_context()


def forward(path: str, headers: dict, body: bytes,
            host: str = UPSTREAM_HOST, port: int = UPSTREAM_PORT) -> tuple[int, bytes]:
    """Bridge mode: forward a request to real LG; return (status, body)."""
    conn = http.client.HTTPSConnection(host, port, context=_CTX, timeout=15)
    try:
        h = {k: v for k, v in headers.items()
             if k.lower() not in ("host", "content-length", "connection")}
        conn.request("POST", path, body=body, headers=h)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


def _parse_item(body: bytes) -> str | None:
    m = re.search(rb"<item>([^<]*)</item>", body)
    return m.group(1).decode("utf-8", "replace") if m else None


def dispatch(path: str, body: bytes, store: DeviceStateStore, *,
             mode: str = "standalone", forwarder=None,
             headers: dict | None = None) -> tuple[int, str, bytes]:
    """Route one request → (status, content_type, body). Pure function (unit-testable)."""
    xml_ct = "text/xml;charset=utf-8"
    # Read-only debug surface (TASK-012): the latest decoded state per devId, as JSON.
    # GET only; everything else falls through to the ThinQ1 POST handling.
    if path.endswith("/debug/state"):
        return 200, "application/json", json.dumps(store.latest, default=str).encode()
    # Observe diagmon in BOTH modes (state ingestion is the point).
    if path.endswith("/report/diagmon"):
        try:
            store.ingest_report(body)
        except Exception as e:  # don't let a bad payload kill the request
            sys.stderr.write(f"[state] ingest failed: {e}\n")
    if mode == "bridge" and forwarder is not None:
        try:
            status, resp_body = forwarder(path, headers or {}, body)
            return status, xml_ct, resp_body
        except Exception as e:
            sys.stderr.write(f"[bridge] forward failed ({e}); falling back to standalone\n")
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
            headers=dict(self.headers))
        self._send(status, ct, resp)

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        srv = self.server
        status, ct, resp = dispatch(self.path, b"", srv.state)  # type: ignore[attr-defined]
        self._send(status, ct, resp)


def main() -> None:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        sys.exit(f"cert/key not found ({CERT}, {KEY}) — run ./gen-cert.sh first.")
    from . import mqtt_bridge
    store = DeviceStateStore(STATE_DIR, on_state=mqtt_bridge.build_sink())
    httpd = ThreadingHTTPServer((HOST, PORT), _Handler)
    httpd.state = store  # type: ignore[attr-defined]
    httpd.mode = MODE  # type: ignore[attr-defined]
    httpd.forwarder = forward if MODE == "bridge" else None  # type: ignore[attr-defined]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
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

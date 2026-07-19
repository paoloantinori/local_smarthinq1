"""LG ThinQ1 fake-cloud server (M1, read path).

A minimal HTTPS server that terminates TLS, answers the bootstrap endpoints with `0000/OK`
XML (PROTOCOL §5), and ingests `diagmon` state. **Standalone only** for now — bridge mode
(forward to real LG + compare) is TASK-013.

⚠️ The responses are the M1 keep-alive *hypothesis* — the central claim ("the appliance
stays happy with our server instead of LG") is only proven by the supervised sever test
(TASK-010/050): point an appliance at this server, firewall real LG, and confirm it
operates and reconnects across reboots. Run that test with the user present.

Config via env: LGM_HOST, LGM_PORT, LGM_CERT, LGM_KEY, LGM_STATE_DIR.
Generate a cert with `gen-cert.sh` (the appliance accepts it — no pinning, PROTOCOL §2).
"""
from __future__ import annotations

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


def _parse_item(body: bytes) -> str | None:
    m = re.search(rb"<item>([^<]*)</item>", body)
    return m.group(1).decode("utf-8", "replace") if m else None


def dispatch(path: str, body: bytes, store: DeviceStateStore) -> tuple[int, str, bytes]:
    """Route one request → (status, content_type, body). Pure function (unit-testable)."""
    xml_ct = "text/xml;charset=utf-8"
    if path.endswith("/report/diagmon"):
        store.ingest_report(body)
        return 200, "application/vnd.diagmonlge.dm+xml", responses.diagmon()
    if path.endswith("/api/Device/TotalDeviceInfoSvc"):
        return 200, xml_ct, responses.total_device_info(_parse_item(body))
    if path.endswith("/api/Rtos/ContentsVerSvc"):
        return 200, xml_ct, responses.contents_ver()
    if path.endswith("/api/product/sendPushMessage"):
        return 200, xml_ct, responses.ok()
    # Permissive default: unknown endpoints get 0000/OK (avoid triggering a retry storm).
    return 200, xml_ct, responses.ok()


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
        store: DeviceStateStore = self.server.state  # type: ignore[attr-defined]
        status, ct, resp = dispatch(self.path, body, store)
        self._send(status, ct, resp)


def main() -> None:
    if not (os.path.exists(CERT) and os.path.exists(KEY)):
        sys.exit(f"cert/key not found ({CERT}, {KEY}) — run ./gen-cert.sh first.")
    store = DeviceStateStore(STATE_DIR)
    httpd = ThreadingHTTPServer((HOST, PORT), _Handler)
    httpd.state = store  # type: ignore[attr-defined]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write(f"LG fake-cloud (standalone) on https://{HOST}:{PORT}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()

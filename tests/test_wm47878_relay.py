"""Offline end-to-end test of the WM :47878 TLS relay (tools/wm47878_tls_relay.py).

Simulates both peers locally: a fake LG upstream (TLS server that sends an opening
record and echoes) and a fake appliance (TLS client pushing records). The relay runs in
explicit-upstream mode (no REDIRECT / SO_ORIGINAL_DST needed). Asserts that every
application-data byte is logged in cleartext in both directions.

The SO_ORIGINAL_DST path is not unit-testable without root REDIRECT rules; it is the
same getsockopt mechanism mitmproxy transparent mode uses (proven on the fridge rig).
"""
from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tools import wm47878_tls_relay as relay  # noqa: E402


def _make_cert(tmpdir: str) -> tuple[str, str]:
    """A throwaway self-signed pair for the test relay's server side."""
    cert = os.path.join(tmpdir, "t.pem")
    key = os.path.join(tmpdir, "t.key")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-subj", "/CN=test", "-keyout", key, "-out", cert, "-days", "1"],
        check=True, capture_output=True)
    return cert, key


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_relay_logs_cleartext_both_directions() -> None:
    """Appliance push and LG opening record both cross the relay and land in the log."""
    LG_OPENING = b"\x01LG-OPENING-192B-PLACEHOLDER"
    APPLIANCE_PUSH = b"\x02APPLIANCE-PUSH-PAYLOAD" * 3

    with tempfile.TemporaryDirectory() as tmp:
        cert, key = _make_cert(tmp)
        logs: list[str] = []

        # --- fake LG upstream: TLS-accept, send the opening, then echo ---
        up_port = _free_port()
        up_srv = socket.socket()
        up_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        up_srv.bind(("127.0.0.1", up_port))
        up_srv.listen(1)
        up_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        up_ctx.load_cert_chain(cert, key)

        def fake_lg() -> None:
            conn, _ = up_srv.accept()
            tls = up_ctx.wrap_socket(conn, server_side=True)
            tls.sendall(LG_OPENING)
            data = tls.recv(4096)
            tls.sendall(b"ECHO" + data)
            tls.close()

        threading.Thread(target=fake_lg, daemon=True).start()

        # --- the relay under test, explicit upstream (test mode) ---
        relay_port = _free_port()
        threading.Thread(
            target=relay.run,
            args=(("127.0.0.1", relay_port), ("127.0.0.1", up_port), cert, key,
                  logs.append),
            daemon=True).start()
        # wait for the relay's own "listening on" log instead of a fixed sleep:
        # no startup race on a loaded box, no probe connection side effects
        deadline = time.time() + 5
        while not any("listening on" in m for m in logs):
            assert time.time() < deadline, f"relay never started; logs={logs}"
            time.sleep(0.02)

        # --- fake appliance: TLS to the relay, push, read opening + echo ---
        cli_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cli_ctx.check_hostname = False
        cli_ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection(("127.0.0.1", relay_port), timeout=5)
        tls = cli_ctx.wrap_socket(raw, server_hostname="whatever")
        tls.sendall(APPLIANCE_PUSH)
        # records arrive as separate TLS flights: read until the whole reply is in
        expected = LG_OPENING + b"ECHO" + APPLIANCE_PUSH
        got = b""
        deadline = time.time() + 5
        while len(got) < len(expected) and time.time() < deadline:
            chunk = tls.recv(65536)
            if not chunk:
                break
            got += chunk
        tls.close()

        assert got == expected
        joined = "\n".join(logs)
        assert "C->U" in joined and APPLIANCE_PUSH.hex() in joined
        assert "U->C" in joined and LG_OPENING.hex() in joined
        assert b"ECHO".hex() in joined


if __name__ == "__main__":
    test_relay_logs_cleartext_both_directions()
    print("PASS relay end-to-end")

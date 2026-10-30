"""Health endpoint for the Supervisor's watchdog URL (TASK-128).

The Supervisor polls /health every 120 s: two consecutive failures restart the app.
200 only when every critical listener completes a TLS HANDSHAKE (bind alone would
read healthy with a wedged accept loop, and a bare connect would spam the server
log with a bogus handshake-failed line per poll); a dead control port is excused
while its re-arm thread retries (TASK-127's designed recovery)."""
from __future__ import annotations

import importlib
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import app as lg_app  # noqa: E402
from server import control_channel as cc  # noqa: E402


def _make_cert(tmpdir: str) -> tuple[str, str]:
    cert, key = os.path.join(tmpdir, "t.pem"), os.path.join(tmpdir, "t.key")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-subj", "/CN=localhost", "-keyout", key, "-out", cert,
                    "-days", "1"], check=True, capture_output=True)
    return cert, key


def _tls_listener(cert: str, key: str) -> tuple[int, threading.Thread, dict]:
    """A real TLS listener (accept + wrap + close): the probe must HANDSHAKE, not
    just connect, so a plain socket is not a valid stand-in."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    raw = socket.socket()
    raw.bind(("127.0.0.1", 0))
    raw.listen(4)
    box: dict = {"stop": False}

    def _serve() -> None:
        raw.settimeout(0.3)
        while not box["stop"]:
            try:
                conn, _ = raw.accept()
            except (socket.timeout, OSError):
                continue
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass  # handshake then clean close
            except OSError:
                pass
        raw.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return raw.getsockname()[1], t, box


def _stop(t: threading.Thread, box: dict) -> None:
    box["stop"] = True
    t.join(timeout=2)


def test_probe_ok_when_target_completes_handshake() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = _make_cert(tmp)
        port, t, box = _tls_listener(cert, key)
        try:
            ok, body = lg_app._health_probe((port,), rearm_check=lambda: False)
            assert ok and body == "ok", (ok, body)
        finally:
            _stop(t, box)


def test_probe_fails_when_target_is_dead() -> None:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    ok, body = lg_app._health_probe((dead,), rearm_check=lambda: False)
    assert not ok and str(dead) in body, (ok, body)


def test_probe_fails_when_target_is_plain_tcp_not_tls() -> None:
    """Bind-only health is not enough: a listener that cannot handshake must fail
    (the wedge class the docstring promises to catch)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        ok, body = lg_app._health_probe((port,), rearm_check=lambda: False)
        assert not ok and str(port) in body, (ok, body)
    finally:
        s.close()


def test_probe_excuses_dead_control_port_while_rearm_active() -> None:
    """TASK-127 contract: re-arm retrying = healthy, or the watchdog would restart
    the app into the same TIME_WAIT and churn."""
    nothing = socket.socket()
    nothing.bind(("127.0.0.1", 0))
    port = nothing.getsockname()[1]
    nothing.close()
    saved = cc.CONTROL_PORT
    cc.CONTROL_PORT = port
    try:
        ok, body = lg_app._health_probe((port,), rearm_check=lambda: True)
        assert ok and "re-arm" in body, (ok, body)
    finally:
        cc.CONTROL_PORT = saved


def test_health_endpoint_200_then_503_over_http() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cert, key = _make_cert(tmp)
        good, t, box = _tls_listener(cert, key)
        probe_sock = socket.socket()
        probe_sock.bind(("127.0.0.1", 0))
        http_port = probe_sock.getsockname()[1]
        probe_sock.close()
        server = lg_app.start_health_server(port=http_port, targets=(good,))
        assert server is not None
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/health", timeout=3) as r:
                assert r.status == 200 and r.read() == b"ok"
            box["stop"] = True  # target dies: the endpoint must turn 503
            t.join(timeout=2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{http_port}/health", timeout=3) as r2:
                    code, body = r2.status, r2.read()
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read()
            assert code == 503 and b"down:" in body, (code, body)
        finally:
            server.shutdown()
            server.server_close()


def test_health_port_env_guard() -> None:
    """LGM_HEALTH_PORT='' (run.sh _opt preserves empty strings) must fall back to
    46032, not crash the module import."""
    os.environ["LGM_HEALTH_PORT"] = ""
    try:
        reload = importlib.reload(lg_app)
        assert reload.HEALTH_PORT == 46032, reload.HEALTH_PORT
    finally:
        os.environ.pop("LGM_HEALTH_PORT", None)
        importlib.reload(lg_app)


if __name__ == "__main__":
    test_probe_ok_when_target_completes_handshake()
    test_probe_fails_when_target_is_dead()
    test_probe_fails_when_target_is_plain_tcp_not_tls()
    test_probe_excuses_dead_control_port_while_rearm_active()
    test_health_endpoint_200_then_503_over_http()
    test_health_port_env_guard()
    print("PASS health server tests")

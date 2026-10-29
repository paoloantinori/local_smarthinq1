"""Bind retry + background re-arm of the :47878 control server (TASK-127).

On an addon restart with host_network the dying previous process can hold the port
for longer than any sane inline budget (Errno 98, production 2026-10-28/29: the app
ran WITHOUT the control channel for hours). Contract under test: a SHORT inline
retry (main() must not block behind the bind), then a daemon re-arm thread that
binds and serves the moment the port frees; a permanently held port returns None
without raising."""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import control_channel  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _hold(port: int) -> socket.socket:
    holder = socket.socket()
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("0.0.0.0", port))
    holder.listen(1)
    return holder


def test_bind_retries_until_holder_releases_inline() -> None:
    """A holder that releases within the inline budget: immediate server."""
    port = _free_port()
    holder = _hold(port)
    threading.Timer(0.15, holder.close).start()
    server = control_channel.start_control_server(port, retries=5, delay=0.1)
    assert server is not None, "bind must succeed once the holder releases inline"
    server.shutdown()
    server.server_close()


def test_bind_returns_fast_then_rearm_recovers() -> None:
    """A holder that outlasts the inline budget (the production race): None
    returned QUICKLY (main() is not blocked), and the re-arm thread raises the
    listener as soon as the port frees."""
    port = _free_port()
    holder = _hold(port)
    t0 = time.monotonic()
    server = control_channel.start_control_server(
        port, retries=3, delay=0.05, rearm_delay=0.3)
    inline_elapsed = time.monotonic() - t0
    assert server is None, "inline phase must give up while the holder lives"
    assert inline_elapsed < 1.0, f"main() blocked {inline_elapsed:.2f}s behind the bind"
    # release AFTER the inline budget: only the re-arm thread can win now
    time.sleep(0.2)
    holder.close()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            probe = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            probe.close()
            return  # re-arm recovered: the port accepts connections again
        except OSError:
            time.sleep(0.1)
    raise AssertionError("re-arm thread did not recover the listener within 5s")


def test_bind_gives_up_without_raising() -> None:
    """A port held forever: None (not an exception), re-arm keeps trying quietly."""
    port = _free_port()
    holder = _hold(port)
    try:
        server = control_channel.start_control_server(
            port, retries=2, delay=0.05, rearm_delay=30.0)
        assert server is None, "must give up (None), not return a server"
    finally:
        holder.close()


if __name__ == "__main__":
    test_bind_retries_until_holder_releases_inline()
    test_bind_returns_fast_then_rearm_recovers()
    test_bind_gives_up_without_raising()
    print("PASS bind retry + re-arm tests")

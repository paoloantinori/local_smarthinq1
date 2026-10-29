"""Lazy MQTT sink (pre-mortem 2026-10-29: broker-down at boot must not leave MQTT
off for the app's whole life).

build_sink with an unreachable broker returns a CALLABLE sink (not None); calling it
drops the event without raising and logs once; the next call retries the connect."""
from __future__ import annotations

import os
import socket
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import mqtt_bridge  # noqa: E402


def _dead_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # closed again: nothing listens, connect must fail
    return port


def test_broker_down_at_boot_returns_retrying_sink() -> None:
    os.environ["LGM_MQTT_HOST"] = "127.0.0.1"
    os.environ["LGM_MQTT_PORT"] = str(_dead_port())
    try:
        sink = mqtt_bridge.build_sink()
        assert sink is not None, (
            "boot race: broker down must still return the lazy sink (None = MQTT "
            "off for the app's whole life, the pre-mortem failure mode)")
        assert callable(sink)
        # dropping an event while the broker is down must not raise (twice: the
        # log-once path and the already-logged path)
        sink("dev-x", {"State": "WM_STATE_POWER_OFF"})
        sink("dev-x", {"State": "WM_STATE_RUNNING"})
    finally:
        os.environ.pop("LGM_MQTT_HOST", None)
        os.environ.pop("LGM_MQTT_PORT", None)


if __name__ == "__main__":
    test_broker_down_at_boot_returns_retrying_sink()
    print("PASS lazy mqtt sink test")

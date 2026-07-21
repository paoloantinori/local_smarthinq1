"""Tests for the MQTT bridge wiring (TASK-064): sink dedup + graceful-degradation guards."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import mqtt_bridge  # noqa: E402


class _FakeHA:
    """Captures publish_discovery/publish_state calls without touching MQTT."""
    def __init__(self) -> None:
        self.discovery: list[str] = []
        self.states: list[dict] = []

    def publish_discovery(self, _client, model_name: str, dev_id: str, fields: list[str]) -> None:
        self.discovery.append(dev_id)

    def publish_state(self, _client, decoded: dict, dev_id: str) -> None:
        self.states.append(decoded)


def test_sink_publishes_discovery_once_per_device() -> None:
    ha = _FakeHA()
    sink = mqtt_bridge._Sink(client=None, ha=ha)
    for _ in range(3):
        sink("dev1", {"modelName": "WTWN3", "monData_decoded": {"State": "RUNNING"}})
    assert ha.discovery == ["dev1"], ha.discovery  # announced once
    assert len(ha.states) == 1, ha.states           # first state published


def test_sink_dedupes_unchanged_state() -> None:
    """A device re-pushing identical state (idle keepalive) must not re-publish."""
    ha = _FakeHA()
    sink = mqtt_bridge._Sink(client=None, ha=ha)
    decoded = {"State": "RUNNING", "Remain_Time_M": "21"}
    for _ in range(5):
        sink("dev1", {"modelName": "WTWN3", "monData_decoded": decoded})
    # 1 discovery + 1 state (the 4 identical re-pushes are deduped)
    assert len(ha.states) == 1, ha.states


def test_sink_publishes_when_state_changes() -> None:
    ha = _FakeHA()
    sink = mqtt_bridge._Sink(client=None, ha=ha)
    sink("dev1", {"modelName": "WTWN3", "monData_decoded": {"State": "RUNNING", "Remain_Time_M": "21"}})
    sink("dev1", {"modelName": "WTWN3", "monData_decoded": {"State": "RUNNING", "Remain_Time_M": "20"}})
    assert len(ha.states) == 2, ha.states  # changed → re-published


def test_build_sink_none_without_host(monkeypatch) -> None:
    monkeypatch.delenv("LGM_MQTT_HOST", raising=False)
    assert mqtt_bridge.build_sink() is None


def test_build_sink_handles_missing_paho(monkeypatch) -> None:
    monkeypatch.setenv("LGM_MQTT_HOST", "127.0.0.1")
    # simulate paho not installed: make the import fail
    import builtins
    real_import = builtins.__import__

    def _no_paho(name, *a, **k):
        if name.startswith("paho"):
            raise ImportError("no paho")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", _no_paho)
    assert mqtt_bridge.build_sink() is None  # graceful: MQTT off, not a crash


if __name__ == "__main__":
    import pytest  # noqa
    for fn in (test_sink_publishes_discovery_once_per_device, test_sink_dedupes_unchanged_state,
               test_sink_publishes_when_state_changes):
        fn(); print(f"PASS {fn.__name__}")
    print("\n(missing-paho + no-host tests use pytest monkeypatch; run via pytest)")

"""Observability: incremental relay-tail watcher (TASK-129 bis) + per-model ages.

Contracts under test: every relay line processed EXACTLY once (offset-based: a
same-time-of-day replay of an old EventMonitoring END is structurally impossible);
the first poll starts at EOF (history feeds seed_memory, never events); ages are
per model and lowered by fresh cloud events; dead identities prune at MAX_MEMORY_AGE."""
from __future__ import annotations

import base64
import datetime
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import observability as obs  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402


WASHER = "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d"
FRIDGE_MODEL = "1REB1GLPX1___"
END_MONDATA = bytes.fromhex("0a00000106070000000000000000000000000108002d006400000300")


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, dev_id: str, payload: dict) -> None:
        self.calls.append((dev_id, dict(payload)))


def _json_line(dev: str, ts: str) -> str:
    body = '{"Header":{"x-lgedm-deviceId":"' + dev + '"},"Body":{"Cmd":"Alive"}}'
    return f"{ts}.123 C->U len=100 hex={body.encode().hex()}\n"


def _event_line(dev: str, ts: str, state_hex: str, model: str = "WTWN3") -> str:
    """A :46030 EventMonitoring report in the REAL two-layer shape: an XML POST
    whose base64 diagMonData carries the inner event XML (monData + eventType)."""
    inner = (b"<lgedmRoot><eventMonitoring><monData>" +
             base64.b64encode(bytes.fromhex(state_hex)) +
             b"</monData><diagData></diagData><eventType>WM_STATE</eventType>"
             b"</eventMonitoring></lgedmRoot>")
    post = (f"<Report><devId>{dev}</devId><modelName>{model}</modelName>"
            f"<id>null</id><devType>201</devType><trigger>Device</trigger>"
            f"<diagMonType>EventMonitoring</diagMonType>"
            f"<diagMonData>{base64.b64encode(inner).decode()}</diagMonData></Report>")
    return f"{ts}.123 C->U len=400 hex={post.encode().hex()}\n"


def test_sighting_epoch_midnight_rollover() -> None:
    now = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=5, second=0, microsecond=0).timestamp()
    seen = obs._sighting_epoch("23:59:00", now)
    assert 300 <= now - seen <= 400, (now - seen)


def test_relay_log_paths_glob() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("relay-47878-1025.log", "relay-NEW_SUFFIX.log"):
            open(os.path.join(tmp, name), "w").close()
        assert len(obs.relay_log_paths(tmp)) == 2, "glob must find renamed logs"


def test_watcher_first_poll_starts_at_eof() -> None:
    """History is NOT replayed as events: the first poll starts at EOF."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-46030-1025.log")
        with open(p, "w") as fh:
            fh.write(_event_line(WASHER, "10:18:53", "0a00000106070000"))
        w = obs.RelayWatcher(tmp, {WASHER: "WTWN3"})
        sightings, events = w.poll()
        assert events == {}, "an END line older than the watcher must not fire"
        assert sightings == {}, "no sightings either (all history is pre-EOF)"


def test_watcher_sees_new_event_lines_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-46030-1025.log")
        open(p, "w").close()  # the file must EXIST before arming
        w = obs.RelayWatcher(tmp, {WASHER: "WTWN3"})
        w.poll()  # arm at EOF
        with open(p, "a") as fh:
            fh.write(_event_line(WASHER, "10:19:24",
                          "000000010607000000000000000000000000000a002d006400000300"))
        sightings, events = w.poll()
        assert WASHER in sightings, sightings
        assert events.get("WTWN3", (0, ""))[1] == "WM_STATE_POWER_OFF", events
        # third pass: the same line is NOT reprocessed (exactly-once)
        _, events2 = w.poll()
        assert events2 == {}, "the same line must fire exactly once"


def test_watcher_event_state_is_registry_decoded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        open(p, "w").close()
        w = obs.RelayWatcher(tmp, {WASHER: "WTWN3"})
        w.poll()
        with open(p, "a") as fh:
            fh.write(_json_line(WASHER, datetime.datetime.now(
                datetime.timezone.utc).strftime("%H:%M:%S")))
        _, events = w.poll()
        assert events == {}, "an Alive frame has no cycle-end state: no event"


def test_publish_ages_model_keyed_and_pruned() -> None:
    store = DeviceStateStore(tempfile.mkdtemp())
    sink = _Sink()
    now = time.time()
    memory = {WASHER: now - 3600,  # 1 h: kept, lowered by sightings
              "deadbeef-0000-0000-0000-000000000000": now - obs.MAX_MEMORY_AGE - 3600}
    sightings = {WASHER: now - 30}
    ages = obs.publish_ages(store, sink, memory, sightings, {}, now_epoch=now)
    assert ages.get("WTWN3") == 30, ages
    assert "deadbeef" not in str(memory), "orphan identity must prune"
    assert sink.calls and sink.calls[0][1]["modelName"] == "WTWN3"


def test_event_flows_into_sink_payload_as_state() -> None:
    store = DeviceStateStore(tempfile.mkdtemp())
    sink = _Sink()
    now = time.time()
    events = {"WTWN3": (now - 5, "WM_STATE_END")}
    ages = obs.publish_ages(store, sink, {}, {WASHER: now - 5}, events, now_epoch=now)
    assert ages["WTWN3"] <= 10, ages
    payloads = [p for _dev, p in sink.calls]
    assert any(p.get("State") == "WM_STATE_END" for p in payloads), payloads


def test_fridge_survives_on_store_path_only() -> None:
    store = DeviceStateStore(tempfile.mkdtemp())
    store.latest["hash-fridge"] = {
        "modelName": FRIDGE_MODEL,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    sink = _Sink()
    ages = obs.publish_ages(store, sink, {}, {}, {})
    assert FRIDGE_MODEL in ages and ages[FRIDGE_MODEL] <= 10, ages


def test_seed_memory_whole_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        with open(p, "w") as fh:
            fh.write(_json_line(WASHER, "09:44:22"))
        store = DeviceStateStore(tempfile.mkdtemp())
        memory = obs.seed_memory(tmp, store)
        assert WASHER in memory, memory


if __name__ == "__main__":
    test_sighting_epoch_midnight_rollover()
    test_relay_log_paths_glob()
    test_watcher_first_poll_starts_at_eof()
    test_watcher_sees_new_event_lines_once()
    test_watcher_event_state_is_registry_decoded()
    test_publish_ages_model_keyed_and_pruned()
    test_event_flows_into_sink_payload_as_state()
    test_fridge_survives_on_store_path_only()
    test_seed_memory_whole_file()
    print("PASS observability tests")

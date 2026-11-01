"""Per-appliance last-frame observability (TASK-129), model-keyed semantics
(code-review 2026-10-31: ages are per MODEL, cloud events contribute via the
view's modelName with an EMPTY devId map, the :46030 XML devId form is scanned,
log names glob, and stale memory identities are pruned)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import observability as obs  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402


WASHER = "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d"
DRYER = "93b55500-4b3a-11f1-ad68-7440be73f9ad"
FRIDGE_MODEL = "1REB1GLPX1___"


def _json_line(dev: str, ts: str) -> str:
    body = ('{"Header":{"x-lgedm-deviceId":"' + dev + '"},"Body":{"Cmd":"Alive"}}')
    return f"{ts}.123 C->U len=100 hex={body.encode().hex()}\n"


def _xml_line(dev: str, ts: str) -> str:
    # the :46030 diagmon shape: XML devId, NO JSON quotes (code-review finding 3)
    body = (f"<Report><devId>{dev}</devId><modelName>WTWN3</modelName>"
            f"<diagMonType>ScomoCourse</diagMonType></Report>")
    return f"{ts}.123 C->U len=200 hex={body.encode().hex()}\n"


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, dev_id: str, payload: dict) -> None:
        self.calls.append((dev_id, dict(payload)))


def _bare_store() -> DeviceStateStore:
    return DeviceStateStore(tempfile.mkdtemp())  # model_by_devid EMPTY (relay posture)


def test_sighting_epoch_midnight_rollover() -> None:
    now = datetime.datetime.now(datetime.timezone.utc).replace(
        hour=0, minute=5, second=0, microsecond=0).timestamp()
    seen = obs._sighting_epoch("23:59:00", now)
    assert 300 <= now - seen <= 400, (now - seen)


def test_scan_tail_json_and_xml_forms() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        recent = (now_dt - datetime.timedelta(seconds=30)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_json_line(DRYER, recent))
        seen = obs.scan_tail(p, time.time())
        assert DRYER in seen, seen

        x = os.path.join(tmp, "relay-46030-1025.log")
        with open(x, "w") as fh:
            fh.write(_xml_line(WASHER, recent))
        seen2 = obs.scan_tail(x, time.time())
        assert WASHER in seen2, seen2


def test_log_names_are_globbed_not_fixed() -> None:
    # a future reprovision renames the log: the sensor must not go blind silently
    with tempfile.TemporaryDirectory() as tmp:
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        fresh = (now_dt - datetime.timedelta(seconds=5)).strftime("%H:%M:%S")
        with open(os.path.join(tmp, "relay-47878-NEW_SUFFIX.log"), "w") as fh:
            fh.write(_json_line(DRYER, fresh))
        assert obs.relay_log_paths(tmp), "glob must find renamed relay logs"


def test_publishes_per_model_with_empty_store_map() -> None:
    """Production relay posture: store.model_by_devid EMPTY (no :46030 reports);
    the static map carries the relay sightings (regression of the first deploy)."""
    store = _bare_store()
    sink = _Sink()
    with tempfile.TemporaryDirectory() as tmp:
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        fresh = (now_dt - datetime.timedelta(seconds=10)).strftime("%H:%M:%S")
        with open(os.path.join(tmp, "relay-47878-1025.log"), "w") as fh:
            fh.write(_json_line(DRYER, fresh))
        ages = obs.publish_ages(tmp, store, sink)
        assert "RC90U2_WW" in ages and ages["RC90U2_WW"] <= 20, ages
        assert sink.calls and sink.calls[0][1]["modelName"] == "RC90U2_WW"


def test_cloud_event_freshness_lowers_age_below_stale_relay() -> None:
    """A fresh cloud event (store view keyed by hash, modelName inside) must
    override a stale relay sighting: after a re-registration the ghost's stale
    age cannot false-alarm a live appliance (review finding 1+2)."""
    store = _bare_store()
    sink = _Sink()
    with tempfile.TemporaryDirectory() as tmp:
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        old = (now_dt - datetime.timedelta(hours=3)).strftime("%H:%M:%S")
        with open(os.path.join(tmp, "relay-47878-1025.log"), "w") as fh:
            fh.write(_json_line(WASHER, old))  # ghost devId, old sighting
        store.latest["ceb2ea2c-hash"] = {
            "modelName": "WTWN3",
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        ages = obs.publish_ages(tmp, store, sink)
        assert ages["WTWN3"] <= 10, ages  # cloud freshness won over 3h-old sighting


def test_fridge_gets_sensor_from_store_only() -> None:
    """The fridge never appears in the WM relay logs: its cloud events alone
    must produce the sensor (review finding 2)."""
    store = _bare_store()
    sink = _Sink()
    store.latest["ffff-hash"] = {
        "modelName": FRIDGE_MODEL,
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with tempfile.TemporaryDirectory() as tmp:
        ages = obs.publish_ages(tmp, store, sink)
        assert FRIDGE_MODEL in ages and ages[FRIDGE_MODEL] <= 10, ages


def test_memory_prunes_dead_identities() -> None:
    """A memory entry older than MAX_MEMORY_AGE (or unmapped) must be dropped:
    a ghost devId cannot publish a growing age forever (review finding 1)."""
    store = _bare_store()
    sink = _Sink()
    memory = {WASHER: time.time() - obs.MAX_MEMORY_AGE - 3600,
              "deadbeef-0000-0000-0000-000000000000": time.time() - 60}
    with tempfile.TemporaryDirectory() as tmp:
        ages = obs.publish_ages(tmp, store, sink, memory=memory)
        assert WASHER not in memory and "deadbeef" not in str(memory), memory
        assert "WTWN3" not in ages  # nothing fresh carried it


def test_memory_keeps_age_growing_after_sighting_leaves_tail() -> None:
    store = _bare_store()
    sink = _Sink()
    memory: dict = {}
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        fresh = (now_dt - datetime.timedelta(seconds=10)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_json_line(WASHER, fresh))
        t0 = time.time()
        first = obs.publish_ages(tmp, store, sink, now_epoch=t0, memory=memory)
        assert first["WTWN3"] <= 15, first
        with open(p, "w") as fh:  # sighting gone from the tail
            fh.write(_json_line(DRYER, datetime.datetime.now().strftime("%H:%M:%S")))
        second = obs.publish_ages(tmp, store, sink, now_epoch=t0 + 120, memory=memory)
        assert second["WTWN3"] >= 125, second


def test_boot_seed_finds_old_sighting_below_tail_window() -> None:
    store = _bare_store()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        old = (now_dt - datetime.timedelta(hours=2)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_json_line(WASHER, old))
            fh.write(_json_line(DRYER, now_dt.strftime("%H:%M:%S")) * 2000)
        seed = obs._seed_memory(tmp, store)
        assert WASHER in seed and DRYER in seed, seed


if __name__ == "__main__":
    test_sighting_epoch_midnight_rollover()
    test_scan_tail_json_and_xml_forms()
    test_log_names_are_globbed_not_fixed()
    test_publishes_per_model_with_empty_store_map()
    test_cloud_event_freshness_lowers_age_below_stale_relay()
    test_fridge_gets_sensor_from_store_only()
    test_memory_prunes_dead_identities()
    test_memory_keeps_age_growing_after_sighting_leaves_tail()
    test_boot_seed_finds_old_sighting_below_tail_window()
    print("PASS observability tests")


def test_scan_events_finds_cycle_end_on_46030() -> None:
    """The washer announces cycle end on :46030 EventMonitoring reports (measured
    2026-11-01: the :47878 pump stops at SPINNING 1m). The scanner must decode the
    base64 diagMonData XML and surface the State per model."""
    import base64
    xml = (b"<lgedmRoot><eventMonitoring><monData>" +
           base64.b64encode(bytes.fromhex("0a0000010607000000000000000000000000" +
                                          "0108002d006400000300")) +
           b"</monData><diagData></diagData><eventType>WM_STATE</eventType>"
           b"</eventMonitoring></lgedmRoot>")
    b64 = base64.b64encode(xml).decode()
    post = (f'<Report><devId>{WASHER}</devId><modelName>WTWN3</modelName>'
            f'<diagMonType>EventMonitoring</diagMonType>'
            f'<diagMonData>{b64}</diagMonData></Report>')
    now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    stamp = (now_dt - datetime.timedelta(seconds=20)).strftime("%H:%M:%S")
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-46030-1025.log")
        with open(p, "w") as fh:
            fh.write(f"{stamp}.123 C->U len=400 hex={post.encode().hex()}\n")
        store = DeviceStateStore(tempfile.mkdtemp())
        out = obs.scan_events(p, time.time(), {"WASHER": "WTWN3",
                                               WASHER: "WTWN3"})
        assert "WTWN3" in out and out["WTWN3"][1] == "WM_STATE_END", out


def test_publish_ages_carries_fresh_event_state() -> None:
    """A fresh EventMonitoring END flows into the sink payload as State (the HA
    side sees the completion even though the :47878 pump never emitted it)."""
    import base64
    store = DeviceStateStore(tempfile.mkdtemp())
    sink = _Sink()
    xml = (b"<lgedmRoot><eventMonitoring><monData>" +
           base64.b64encode(bytes.fromhex("0a0000010607000000000000000000000000" +
                                          "0108002d006400000300")) +
           b"</monData><diagData></diagData><eventType>WM_STATE</eventType>"
           b"</eventMonitoring></lgedmRoot>")
    b64 = base64.b64encode(xml).decode()
    now_dt = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    stamp = (now_dt - datetime.timedelta(seconds=8)).strftime("%H:%M:%S")
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "relay-46030-1025.log"), "w") as fh:
            fh.write(f"{stamp}.123 C->U len=400 hex={post_encode(post_xml(WASHER, b64))}\n")
        ages = obs.publish_ages(tmp, store, sink)
    assert "WTWN3" in ages, ages
    payloads = [p for _dev, p in sink.calls if p.get("modelName") == "WTWN3"]
    assert any(p.get("State") == "WM_STATE_END" for p in payloads), payloads
    assert sink.calls[-1][1].get("State") == "WM_STATE_END"


def post_xml(dev: str, b64: str) -> str:
    return (f'<Report><devId>{dev}</devId><modelName>WTWN3</modelName>'
            f'<diagMonType>EventMonitoring</diagMonType>'
            f'<diagMonData>{b64}</diagMonData></Report>')


def post_encode(xml: str) -> str:
    return xml.encode().hex()

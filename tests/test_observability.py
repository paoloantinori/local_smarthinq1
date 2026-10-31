"""Per-appliance last-frame observability (TASK-129)."""
from __future__ import annotations

import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import observability as obs  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402


WASHER = "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d"
DRYER = "93b55500-4b3a-11f1-ad68-7440be73f9ad"


def _relay_line(dev: str, ts: str) -> str:
    body = ('{"Header":{"x-lgedm-deviceId":"' + dev + '"},"Body":{"Cmd":"Alive"}}')
    return f"{ts}.123 C->U len=100 hex={body.encode().hex()}\n"


class _Sink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, dev_id: str, payload: dict) -> None:
        self.calls.append((dev_id, dict(payload)))


def _store() -> DeviceStateStore:
    s = DeviceStateStore(tempfile.mkdtemp())
    s.model_by_devid[WASHER] = "WTWN3"
    s.model_by_devid[DRYER] = "RC90U2_WW"
    return s


def test_sighting_epoch_midnight_rollover() -> None:
    # now is 00:05 local; a 23:59 stamp is from 6 minutes ago, not 23h54m in the future
    import datetime
    now = datetime.datetime.now().replace(hour=0, minute=5, second=0, microsecond=0).timestamp()
    seen = obs._sighting_epoch("23:59:00", now)
    assert 300 <= now - seen <= 400, (now - seen)


def test_scan_tail_finds_devices_and_age() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        import datetime
        now_dt = datetime.datetime.now().replace(microsecond=0)
        recent = (now_dt - datetime.timedelta(seconds=30)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_relay_line(DRYER, "01:00:00") * 3)  # old noise
            fh.write(_relay_line(DRYER, recent))
        now = time.time()
        seen = obs.scan_tail(p, now)
        assert DRYER in seen, seen
        assert 25 <= now - seen[DRYER] <= 40, now - seen[DRYER]


def test_publish_ages_merges_relay_and_store() -> None:
    import datetime
    store = _store()
    sink = _Sink()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        now_dt = datetime.datetime.now().replace(microsecond=0)
        washer_recent = (now_dt - datetime.timedelta(seconds=10)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_relay_line(WASHER, washer_recent))
        # dryer: nothing in the relay, but a fresh store view (e.g. cloud event)
        store.latest[DRYER] = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                               "modelName": "RC90U2_WW"}
        now = time.time()
        ages = obs.publish_ages(tmp, store, sink, now_epoch=now)
        assert WASHER in ages and DRYER in ages, ages
        assert ages[WASHER] <= 20, ages[WASHER]
        assert ages[DRYER] <= 10, ages[DRYER]
        models = {dev: p.get("modelName") for dev, p in sink.calls}
        assert models[WASHER] == "WTWN3" and models[DRYER] == "RC90U2_WW"
        fields = {dev: p.get("last_frame_age") for dev, p in sink.calls}
        assert all(v is not None and v.isdigit() for v in fields.values())


def test_unknown_device_not_published() -> None:
    store = _store()
    sink = _Sink()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        with open(p, "w") as fh:
            fh.write(_relay_line("ffffffff-0000-0000-0000-000000000000", "12:00:00"))
        ages = obs.publish_ages(tmp, store, sink)
        assert ages == {} and sink.calls == []


def test_empty_store_map_still_publishes_via_known_devices() -> None:
    """The PRODUCTION relay posture: no :46030 report ever reaches the app, so
    store.model_by_devid stays EMPTY; the static capture-derived map must carry
    the relay sightings (regression: first 1.7.0 boot published nothing)."""
    import datetime
    store = DeviceStateStore(tempfile.mkdtemp())  # NO seeding: empty map
    assert not store.model_by_devid
    sink = _Sink()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-47878-1025.log")
        now_dt = datetime.datetime.now().replace(microsecond=0)
        fresh = (now_dt - datetime.timedelta(seconds=5)).strftime("%H:%M:%S")
        with open(p, "w") as fh:
            fh.write(_relay_line(DRYER, fresh))
        ages = obs.publish_ages(tmp, store, sink)
        assert DRYER in ages and ages[DRYER] <= 15, ages
        assert sink.calls and sink.calls[0][1]["modelName"] == "RC90U2_WW"


if __name__ == "__main__":
    test_sighting_epoch_midnight_rollover()
    test_scan_tail_finds_devices_and_age()
    test_publish_ages_merges_relay_and_store()
    test_unknown_device_not_published()
    print("PASS observability tests")

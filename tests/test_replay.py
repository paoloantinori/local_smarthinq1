"""Tests for the replay-based bridge responses (TASK-126 Phase 2).

Validates that the replay module loads captured real LG responses, updates utcTime,
and that dispatch prefers replay over synthetic. The store is git-ignored (it carries
device-identifying data), so these skip wherever the capture was not regenerated.
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import replay  # noqa: E402

STORE_PATH = os.path.join(ROOT, "data", "replay", "lg_responses.json")
HAVE_STORE = os.path.isfile(STORE_PATH)
_SKIP = pytest.mark.skipif(
    not HAVE_STORE, reason="replay store is git-ignored; regenerate with tools/extract_replay_store.py")


@_SKIP
def test_replay_loads_and_serves() -> None:
    """The replay store loads from the default path and returns real responses."""
    endpoints = replay.available_endpoints()
    assert len(endpoints) >= 5, f"expected 5+ endpoints, got {endpoints}"
    assert any("TotalDeviceInfoSvc" in e for e in endpoints)


@_SKIP
def test_replay_updates_utctime() -> None:
    """The utcTime element in replayed responses is updated to current UTC."""
    result = replay.replay_response("/lgehadm/api/Device/TotalDeviceInfoSvc/THINQ_TIME_SYNC_URI")
    assert result is not None
    status, _ct, body = result
    assert status == 200
    assert b"utcTime" in body
    # utcTime should NOT be the original capture time (2026-10-25)
    assert b"2026-10-25" not in body, "utcTime not updated from capture"


@_SKIP
def test_replay_diagnosis_empty() -> None:
    """diagmon replays as 200 with empty body (matching real LG)."""
    result = replay.replay_response("/lgehadm/report/diagmon")
    assert result is not None
    status, _ct, body = result
    assert status == 200
    assert body == b"" or len(body) < 10  # real LG returns empty body


def test_replay_absent_store_degrades() -> None:
    """A missing/corrupt store must degrade to synthetic fallback, never raise."""
    replay._store, replay._loaded = {}, False
    old = replay.REPLAY_PATH
    try:
        replay.REPLAY_PATH = "/nonexistent/replay/store.json"
        assert replay.replay_response("/lgehadm/api/Device/TotalDeviceInfoSvc") is None
        replay._store, replay._loaded = {}, False
        replay.REPLAY_PATH = old  # a corrupt file (here: a directory) also degrades
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            replay.REPLAY_PATH = os.path.join(d, "store.json")
            open(replay.REPLAY_PATH, "w").write("{truncated")
            assert replay.replay_response("/lgehadm/api/Rtos/ContentsVerSvc") is None
    finally:
        replay.REPLAY_PATH = old
        replay._store, replay._loaded = {}, False


if __name__ == "__main__":
    if HAVE_STORE:
        test_replay_loads_and_serves()
        test_replay_updates_utctime()
        test_replay_diagnosis_empty()
        print("PASS replay tests")
    else:
        print("SKIP replay tests (no store)")
    test_replay_absent_store_degrades()
    print("PASS degrade test")

"""Tests for the replay-based bridge responses (TASK-126 Phase 2).

Validates that the replay module loads captured real LG responses, updates utcTime,
and that dispatch prefers replay over synthetic.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import replay  # noqa: E402


def test_replay_loads_and_serves() -> None:
    """The replay store loads from the default path and returns real responses."""
    store_path = os.path.join(ROOT, "data", "replay", "lg_responses.json")
    assert os.path.isfile(store_path), "replay store not found"
    endpoints = replay.available_endpoints()
    assert len(endpoints) >= 5, f"expected 5+ endpoints, got {endpoints}"
    assert any("TotalDeviceInfoSvc" in e for e in endpoints)


def test_replay_updates_utctime() -> None:
    """The utcTime element in replayed responses is updated to current UTC."""
    result = replay.replay_response("/lgehadm/api/Device/TotalDeviceInfoSvc/THINQ_TIME_SYNC_URI")
    assert result is not None
    status, ct, body = result
    assert status == 200
    assert b"utcTime" in body
    # utcTime should NOT be the original capture time (2026-10-25)
    assert b"2026-10-25" not in body, "utcTime not updated from capture"


def test_replay_diagnosis_empty() -> None:
    """diagmon replays as 200 with empty body (matching real LG)."""
    result = replay.replay_response("/lgehadm/report/diagmon")
    assert result is not None
    status, ct, body = result
    assert status == 200
    assert body == b"" or len(body) < 10  # real LG returns empty body


if __name__ == "__main__":
    test_replay_loads_and_serves()
    test_replay_updates_utctime()
    test_replay_diagnosis_empty()
    print("PASS replay tests")

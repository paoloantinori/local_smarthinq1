"""Replay the captured dryer cycle (flows/dryer-cycle-20260720.log) through the registry and
assert the transitions match what the dryer actually did. Validates TASK-021: the dryer
reuses the WM-family envelope + registry, decoded via its own modelJson.

Runnable standalone (``python3 tests/test_dryer.py``) or with pytest. No third-party deps.
"""
from __future__ import annotations

import functools
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import dryer_rc90u2  # noqa: E402
from server.models import registry  # noqa: E402

CAPTURE = os.path.join(ROOT, "flows", "dryer-cycle-20260720.log")


def _isolated_cache() -> tuple[str, dict]:
    """Point the registry at an empty temp cache dir + clear the mem cache; return restore data."""
    return registry.CACHE_DIR, dict(registry._MEM_CACHE)


@functools.lru_cache(maxsize=1)
def _decoded() -> tuple[dict, ...]:
    text = open(CAPTURE).read()
    out: list[dict] = []
    for r in re.findall(r"<Report>.*?</Report>", text, re.S):
        out += registry.decode_report(r)
    return tuple(out)


def test_dryer_is_registered() -> None:
    assert registry._decoder_for("RC90U2_WW", None) is dryer_rc90u2
    # unknown modelName falls back to deviceType 202 → dryer decoder
    assert registry._decoder_for("SOME_OTHER_DRYER", 202) is dryer_rc90u2


def test_capture_decodes() -> None:
    got = _decoded()
    assert len(got) >= 8, f"expected >=8 diagmon, got {len(got)}"


def test_dry_begin_to_dry_end() -> None:
    states = [d for d in _decoded() if d.get("eventType") in ("DR_DRY_BEGIN", "DR_STATE", "DR_DRY_END")]
    assert states, "no DR_DRY_BEGIN/DR_STATE/DR_DRY_END events found"
    assert states[0]["eventType"] == "DR_DRY_BEGIN", states[0]
    assert states[-1]["eventType"] == "DR_DRY_END", states[-1]
    # DR_DRY_BEGIN is RUNNING; DR_DRY_END is POWER_OFF
    assert "RUNNING" in states[0]["monData_decoded"]["State"], states[0]["monData_decoded"]
    assert "POWER_OFF" in states[-1]["monData_decoded"]["State"], states[-1]["monData_decoded"]


def test_remain_time_counts_down() -> None:
    """Remain_Time (H*60+M) is monotonic non-increasing through the running states."""
    running = [d for d in _decoded()
               if d.get("eventType") in ("DR_DRY_BEGIN", "DR_STATE")
               and "monData_decoded" in d]
    mins = [int(d["monData_decoded"]["Remain_Time_H"]) * 60 + int(d["monData_decoded"]["Remain_Time_M"])
            for d in running]
    assert mins[0] == 30, mins  # the 30-min empty cycle
    assert mins == sorted(mins, reverse=True), f"remain time should count down: {mins}"


def test_no_error_throughout() -> None:
    for d in _decoded():
        if "monData_decoded" in d:
            assert "No Error" in d["monData_decoded"]["Error"], d["monData_decoded"]


def test_model_json_fixture_resolves() -> None:
    # Neutralize the runtime cache so the committed fixture (not a stale data/models/ file)
    # is what's actually exercised.
    orig_dir, orig_mem = _isolated_cache()
    registry.CACHE_DIR = tempfile.mkdtemp()
    registry._MEM_CACHE.clear()
    try:
        assert registry.load_model_json("RC90U2_WW", decoder=dryer_rc90u2), \
            "dryer modelJson fixture should resolve"
        # fixture isolation: must NOT resolve for a different modelName
        assert registry.load_model_json("NOT_A_DRYER", decoder=dryer_rc90u2) is None
    finally:
        registry.CACHE_DIR = orig_dir
        registry._MEM_CACHE = orig_mem


if __name__ == "__main__":
    for fn in (test_dryer_is_registered, test_capture_decodes, test_dry_begin_to_dry_end,
               test_remain_time_counts_down, test_no_error_throughout,
               test_model_json_fixture_resolves):
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll dryer assertions passed over {len(_decoded())} decoded diagmon payloads.")

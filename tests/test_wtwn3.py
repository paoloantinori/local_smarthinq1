"""Replay the captured washer cycle (flows/washer-cycle-20260719.log) through the WTWN3
decoder and assert the transitions match what the machine actually did.

Runnable standalone (`python3 tests/test_wtwn3.py`) or with pytest. No third-party deps.
"""
from __future__ import annotations

import functools
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import washer_wtwn3 as w  # noqa: E402  # type: ignore[import-not-found]

CAPTURE = os.path.join(ROOT, "flows", "washer-cycle-20260719.log")


@functools.lru_cache(maxsize=1)
def _decoded() -> tuple[dict, ...]:
    text = open(CAPTURE).read()
    reports = re.findall(r"<Report>.*?</Report>", text, re.S)
    out: list[dict] = []
    for r in reports:
        out += w.decode_report(r)
    return tuple(out)


def test_capture_decodes() -> None:
    assert len(_decoded()) >= 6, f"expected >=6 diagmon, got {len(_decoded())}"


def test_all_from_washer_course_7() -> None:
    for d in _decoded():
        if "monData" in d:
            assert d["monData"]["course"] == 7, d
        if "course" in d:  # WasherMonitoring energyMonInfo plain field
            assert d["course"] == "7", d


def test_run_state_progresses_to_complete() -> None:
    states = [d for d in _decoded()
              if "monData" in d and d.get("eventType") in ("WM_STATE", "WM_WASH_END")]
    assert states, "no WM_STATE/WM_WASH_END events found"
    actives = [d["monData"]["cycle_active"] for d in states]
    assert actives[0] is True, f"cycle should start active, got {actives[0]}"
    assert actives[-1] is False, f"final state should be idle, got {actives[-1]}"


def test_wash_end_present() -> None:
    assert any(d.get("eventType") == "WM_WASH_END" for d in _decoded()), "no WM_WASH_END event"


def test_phase_progresses() -> None:
    steps = [d["monData"]["phase_step"] for d in _decoded()
             if "monData" in d and d.get("eventType") in ("WM_STATE", "WM_WASH_END")]
    assert steps, "no phase_step values"
    assert steps[-1] >= steps[0], f"phase should progress, got {steps}"


if __name__ == "__main__":
    for fn in (test_capture_decodes, test_all_from_washer_course_7,
               test_run_state_progresses_to_complete, test_wash_end_present,
               test_phase_progresses):
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll assertions passed over {len(_decoded())} decoded diagmon payloads.")

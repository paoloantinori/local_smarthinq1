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

from server.models import washer_wtwn3 as w  # noqa: E402

CAPTURE = os.path.join(ROOT, "flows", "washer-cycle-20260719.log")


@functools.lru_cache(maxsize=2)
def _decoded_from(path: str) -> tuple[dict, ...]:
    text = open(path).read()
    reports = re.findall(r"<Report>.*?</Report>", text, re.S)
    out: list[dict] = []
    for r in reports:
        out += w.decode_report(r)
    return tuple(out)


def _decoded() -> tuple[dict, ...]:
    return _decoded_from(CAPTURE)


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


OVERNIGHT_CAPTURE = os.path.join(ROOT, "flows", "washer-overnight-20260723.log")


def _overnight_decoded() -> tuple[dict, ...]:
    """All decoded payloads from the overnight capture (second cycle, TASK-068's
    cross-verification sample)."""
    return _decoded_from(OVERNIGHT_CAPTURE)


def test_wash_end_diagdata_course_crossverified() -> None:
    """TASK-068: WM_WASH_END's 69-byte diagData decodes the course at byte 51,
    cross-verified against the same cycle's energyMonInfo <course> on BOTH captured
    cycles (19/07 course 7 = Mix; 23/07 overnight course 1 = Cotton)."""
    from server.models import registry
    for capture, want_id, want_label in ((CAPTURE, 7, "Mix"),
                                         (OVERNIGHT_CAPTURE, 1, "Cotton")):
        text = open(capture).read()
        ends = [p for r in re.findall(r"<Report>.*?</Report>", text, re.S)
                for p in registry.decode_report(r, model_name="WTWN3", device_type=201)
                if p.get("eventType") == "WM_WASH_END" and "diagData" in p]
        assert ends, "no WM_WASH_END with diagData in the capture"
        d = ends[0]
        assert d["diagData"]["len"] == 69
        assert d["diagData"]["course"] == want_id
        assert d["diagData"]["course_label"] == want_label


def test_energy_summary_fields_reach_the_payload() -> None:
    """TASK-068: the WasherMonitoring energy summary (event/course/power/energyWater/
    useDate) is decoded into the payload (it feeds the HA sensors via the sink)."""
    for decoded, course, power, date in (
            (_decoded(), "7", "1", "20260719"),
            (_overnight_decoded(), "1", "5", "20260723")):
        summaries = [d for d in decoded if "energyWater" in d]
        assert summaries, "no energyMonInfo summary decoded from the capture"
        s = summaries[0]
        assert s["event"] == "2" and s["course"] == course
        assert s["power"] == power and s["energyWater"] == "4"
        assert s["useDate"].startswith(date)


if __name__ == "__main__":
    for fn in (test_capture_decodes, test_all_from_washer_course_7,
               test_run_state_progresses_to_complete, test_wash_end_present,
               test_phase_progresses, test_wash_end_diagdata_course_crossverified,
               test_energy_summary_fields_reach_the_payload):
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll assertions passed over {len(_decoded())} decoded diagmon payloads.")

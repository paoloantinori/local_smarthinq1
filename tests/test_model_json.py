"""Validate the modelJson-driven decoder against the captured cycle.

Uses a SAMPLE modelJson built from our CONFIRMED monData offsets (b5=course, b18=cycle-active,
b19=phase) — this proves the decode mechanism (Monitoring.protocol → bytes → friendly names).
When the real WTWN3 modelJson is fetched (tools/fetch_model_json.py), drop it in and the same
code yields the full state (State/Remain_Time/Course/…).
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import model_json  # noqa: E402
from server.models import washer_wtwn3 as w  # noqa: E402

CAPTURE = os.path.join(ROOT, "flows", "washer-cycle-20260719.log")

# Sample modelJson derived from the CONFIRMED offsets (flows/...state.md).
SAMPLE_MODEL_JSON = {
    "Monitoring": {
        "type": "BINARY(BYTE)",
        "protocol": [
            {"value": "Course", "startByte": 5, "length": 1},
            {"value": "CycleActive", "startByte": 18, "length": 1},
            {"value": "PhaseStep", "startByte": 19, "length": 1},
        ],
    },
    "Value": {
        "CycleActive": {"type": "Enum", "option": {"1": "ACTIVE", "2": "IDLE"}},
    },
}


def _state_bytes() -> list[bytes]:
    text = open(CAPTURE).read()
    out = []
    for r in re.findall(r"<Report>.*?</Report>", text, re.S):
        for d in w.decode_report(r):
            if "monData" in d and d.get("eventType") in ("WM_STATE", "WM_WASH_END"):
                out.append(bytes.fromhex(d["monData"]["raw"]))
    return out


def test_model_json_decodes_cycle() -> None:
    states = _state_bytes()
    assert states, "no monData in capture"
    decoded = [model_json.decode_with_model_json(b, SAMPLE_MODEL_JSON) for b in states]
    # Course (no Value entry → raw) is 7 throughout, matching energyMonInfo <course>7</course>
    assert all(d["Course"] == "7" for d in decoded), decoded
    # CycleActive (Enum) maps 1→ACTIVE during the cycle, 2→IDLE at the end
    assert decoded[0]["CycleActive"] == "ACTIVE", decoded[0]
    assert decoded[-1]["CycleActive"] == "IDLE", decoded[-1]
    # PhaseStep is monotonic
    steps = [int(d["PhaseStep"]) for d in decoded]
    assert steps[-1] >= steps[0], steps


REAL_MODEL = os.path.join(ROOT, "server", "models", "washer_wtwn3.model.json")


def test_real_model_full_decode() -> None:
    """With the REAL WTWN3 modelJson (fetched), the captured cycle decodes to full state."""
    if not os.path.exists(REAL_MODEL):
        print("(skipped: real modelJson not present)")
        return
    import json
    model = json.load(open(REAL_MODEL))
    decoded = [model_json.decode_with_model_json(b, model) for b in _state_bytes()]
    assert all(d["Course"] == "Mix" for d in decoded), [d["Course"] for d in decoded]
    assert "RUNNING" in decoded[0]["State"], decoded[0]["State"]
    assert "POWER_OFF" in decoded[-1]["State"], decoded[-1]["State"]
    assert all("No Error" in d["Error"] for d in decoded)
    assert decoded[-1]["Remain_Time_H"] == "0" and decoded[-1]["Remain_Time_M"] == "0"


if __name__ == "__main__":
    test_model_json_decodes_cycle()
    print("PASS test_model_json_decodes_cycle")
    test_real_model_full_decode()
    print("PASS test_real_model_full_decode")

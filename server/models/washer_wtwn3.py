"""WTWN3 — LG ThinQ1 washer (deviceType 201).

Identity + washer-specific byte interpretations for the WM-family diagmon envelope
(``server.models.wm_envelope``). CONFIRMED offsets are derived from a captured wash cycle
(``flows/washer-cycle-20260719.log``; notes in ``flows/washer-cycle-20260719.state.md``).
The full per-model decode is applied by the registry via the modelJson
(``monData_decoded``); the offsets here are the hand-derived subset surfaced in the raw
``monData`` map.
"""
from __future__ import annotations

from typing import Any

from . import wm_envelope as env
from .wm_envelope import Field

DEVICE_TYPE = 201
MODEL_NAME = "WTWN3"
# Committed modelJson for this model; applied only when modelName == MODEL_NAME (registry).
MODEL_JSON_FIXTURE = "washer_wtwn3.model.json"
# Payload keys whose bytes the registry decodes via the modelJson (the state struct).
STATE_FIELDS = ("monData", "option")

# monData/option is a 28-byte struct. CONFIRMED offsets (derived from the captured cycle;
# see .state.md):
#   b5  = course        (0x07 observed; matches energyMonInfo <course>7</course>)   — HIGH
#   b18 = cycle_active  (0x01 while a cycle is active, 0x02 once idle/complete)    — HIGH
#   b19 = phase_step    (climbs through the cycle 3→6→7→8→10; per-value via modelJson) — MEDIUM
#   b0  = state_byte    (progresses 06→7→8→0a→00(idle); meaning via modelJson)     — MEDIUM
_CYCLE_ACTIVE = {0x01: True, 0x02: False}  # observed values

CONFIRMED_MONDATA_FIELDS = (
    Field(0, "state_byte", "medium", "progresses during cycle (6->7->8->10), 0x00 idle"),
    Field(5, "course", "high", "wash program id (== energyMonInfo <course>)"),
    Field(18, "cycle_active", "high", "0x01 while a cycle is active, 0x02 once idle"),
    Field(19, "phase_step", "medium", "monotonic through the cycle (3->6->7->8->10)"),
)


def _apply_washer_reads(payload: dict[str, Any]) -> dict[str, Any]:
    # cycle_active is observed as 0x01/0x02; map it to a bool so callers can test it directly.
    for key in ("monData", "option"):
        node = payload.get(key)
        if isinstance(node, dict) and "cycle_active" in node:
            node["cycle_active"] = _CYCLE_ACTIVE.get(node["cycle_active"], node["cycle_active"])
    return payload


def decode_diagmon_payload(diag_type: str, data_b64: str) -> dict[str, Any]:
    """Washer-flavoured envelope decode: threads the washer's byte offsets + cycle_active→bool."""
    return _apply_washer_reads(
        env.decode_diagmon_payload(diag_type, data_b64, CONFIRMED_MONDATA_FIELDS))


def decode_report(report_xml: str) -> list[dict[str, Any]]:
    """Decode a ``<Report>`` with washer byte reads applied."""
    return [_apply_washer_reads(p)
            for p in env.decode_report(report_xml, CONFIRMED_MONDATA_FIELDS)]

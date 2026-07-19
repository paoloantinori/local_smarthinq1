"""WTWN3 — LG ThinQ1 washer (deviceType 201) state decoder.

Decodes the washer's ``diagmon`` payloads (``diagMonData`` base64 → XML, whose inner
``monData``/``diagData``/``option`` are base64 → **binary**) into structured state.

CONFIRMED byte offsets are derived from a captured wash cycle
(``flows/washer-cycle-20260719.log``; notes in ``flows/washer-cycle-20260719.state.md``).
Only mappings backed by the observed cycle progression are interpreted here; the remaining
bytes are exposed raw, pending the per-model ``modelJson`` value map. The canonical
ThinQ1 decoder is ``sampsyo/wideq``, which fetches ``modelJson`` from LG's API (needs LG
account auth) and applies each ``Value`` definition (offset/encoding) to the binary blob.
This module is structured to accept such a map later — see ``decode_mondata(model_info=)``.
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

DEVICE_TYPE = 201
MODEL_NAME = "WTWN3"

# Inner-XML fields that are themselves base64 → binary.
_BINARY_FIELDS = ("monData", "diagData", "option")
# Inner-XML text fields worth surfacing verbatim.
_TEXT_FIELDS = (
    "eventType", "event", "course", "power", "energyWater",
    "useDate", "dlCourse", "count", "maxCount",
)

# monData/option is a 28-byte struct. CONFIRMED offsets (derived from the captured cycle;
# see .state.md):
#   b5  = course        (0x07 observed; matches energyMonInfo <course>7</course>)   — HIGH
#   b18 = cycle_active  (0x01 while a cycle is active, 0x02 once idle/complete)    — HIGH
#   b19 = phase_step    (climbs through the cycle 3→6→7→8→10; per-value via modelJson) — MEDIUM
#   b0  = state_byte    (progresses 06→7→8→0a→00(idle); meaning via modelJson)     — MEDIUM
_CYCLE_ACTIVE = {0x01: True, 0x02: False}  # observed values


@dataclass
class Field:
    offset: int
    name: str
    confidence: str
    note: str


CONFIRMED_MONDATA_FIELDS = (
    Field(0, "state_byte", "medium", "progresses during cycle (6->7->8->10), 0x00 idle"),
    Field(5, "course", "high", "wash program id (== energyMonInfo <course>)"),
    Field(18, "cycle_active", "high", "0x01 while a cycle is active, 0x02 once idle"),
    Field(19, "phase_step", "medium", "monotonic through the cycle (3->6->7->8->10)"),
)


def _b64decode(s: str) -> bytes:
    return base64.b64decode(s.strip())


def decode_mondata(b: bytes, model_info: Optional[dict] = None) -> dict[str, Any]:
    """Decode a 28-byte ``monData``/``option`` blob.

    ``model_info`` is an extension point: a future modelJson-derived ``{name: (offset,
    decode_fn)}`` map to interpret the remaining bytes. When ``None``, only the
    CONFIRMED offsets above are interpreted; everything else is in ``raw``.
    """
    out: dict[str, Any] = {"raw": b.hex(), "len": len(b)}
    fields = CONFIRMED_MONDATA_FIELDS
    if model_info:
        # names from a modelJson map take precedence (full decode path).
        for name, (offset, fn) in model_info.items():
            if offset < len(b):
                out[name] = fn(b)
        return out
    for f in fields:
        if f.offset >= len(b):
            continue
        val = b[f.offset]
        if f.name == "course":
            out[f.name] = val
        elif f.name == "cycle_active":
            out[f.name] = _CYCLE_ACTIVE.get(val, f"unknown(0x{val:02x})")
        else:
            out[f.name] = val
    return out


def decode_diagmon_payload(diag_type: str, data_b64: str) -> dict[str, Any]:
    """Decode one diagmon payload: ``diagMonData`` (base64) → inner XML → fields."""
    raw = _b64decode(data_b64)
    try:
        inner_xml = raw.decode("utf-8", "replace")
        root = ET.fromstring(inner_xml)
    except ET.ParseError:
        # diagMonData is not always XML (e.g. ScomoCourse → "100"); return it raw.
        return {"diagMonType": diag_type, "raw_text": raw.decode("utf-8", "replace")}
    result: dict[str, Any] = {"diagMonType": diag_type}
    # plain text fields
    for tag in _TEXT_FIELDS:
        el = root.find(f".//{tag}")
        if el is not None and el.text and el.text.strip():
            result[tag] = el.text.strip()
    # binary sub-fields
    for bname in _BINARY_FIELDS:
        el = root.find(f".//{bname}")
        if el is not None and el.text and el.text.strip():
            raw = _b64decode(el.text)
            result[bname] = decode_mondata(raw) if bname in ("monData", "option") else {
                "raw": raw.hex(), "len": len(raw),
            }
    return result


def decode_report(report_xml: str) -> list[dict[str, Any]]:
    """Decode a ``<Report>`` diagmon POST body → list of decoded payloads (usually 1)."""
    root = ET.fromstring(report_xml)
    diag_type = (root.findtext("diagMonType") or "").strip()
    data = (root.findtext("diagMonData") or "").strip()
    if not (diag_type and data):
        return []
    return [decode_diagmon_payload(diag_type, data)]

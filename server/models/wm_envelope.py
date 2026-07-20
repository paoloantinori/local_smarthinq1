"""ThinQ1 WM-family diagmon envelope decoder (shared across washer/dryer/…).

The WM-family appliances (washer type 201, dryer type 202, …) share one diagmon envelope:
``diagMonData`` is base64 → an XML ``<lgedmRoot>``, whose inner ``monData``/``diagData``/
``option`` are base64 → **binary** (a double decode). This module owns that shared
envelope — it is appliance-agnostic. Per-model byte *interpretation* (which offset means
what) lives in each model's module and is applied via the per-model ``modelJson`` by the
registry; this envelope only decodes structure + surfaces raw bytes.
"""
from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


@dataclass
class Field:
    offset: int
    name: str
    confidence: str
    note: str


# Inner-XML fields that are themselves base64 → binary.
BINARY_FIELDS = ("monData", "diagData", "option")
# Inner-XML text fields worth surfacing verbatim.
TEXT_FIELDS = (
    "eventType", "event", "course", "power", "energyWater",
    "useDate", "dlCourse", "count", "maxCount",
)


def _b64decode(s: str) -> bytes:
    return base64.b64decode(s.strip())


def decode_mondata(b: bytes, mondata_fields: tuple[Field, ...] = ()) -> dict[str, Any]:
    """Decode a ``monData``/``option`` binary blob → ``{raw, len, <interpreted fields>}``.

    Appliance-agnostic: only ``raw``/``len`` unless the caller passes per-model
    ``mondata_fields`` (offset interpretations). The full per-model decode is applied
    separately by the registry via the modelJson (``monData_decoded``), so most callers
    pass no fields.
    """
    out: dict[str, Any] = {"raw": b.hex(), "len": len(b)}
    for f in mondata_fields:
        if f.offset < len(b):
            out[f.name] = b[f.offset]
    return out


def decode_diagmon_payload(diag_type: str, data_b64: str,
                           mondata_fields: tuple[Field, ...] = ()) -> dict[str, Any]:
    """Decode one diagmon payload: ``diagMonData`` (base64) → inner XML → fields."""
    raw = _b64decode(data_b64)
    try:
        inner_xml = raw.decode("utf-8", "replace")
        root = ET.fromstring(inner_xml)
    except ET.ParseError:
        # diagMonData is not always XML (e.g. ScomoCourse → "100"); return it raw.
        return {"diagMonType": diag_type, "raw_text": raw.decode("utf-8", "replace")}
    result: dict[str, Any] = {"diagMonType": diag_type}
    for tag in TEXT_FIELDS:
        el = root.find(f".//{tag}")
        if el is not None and el.text and el.text.strip():
            result[tag] = el.text.strip()
    for bname in BINARY_FIELDS:
        el = root.find(f".//{bname}")
        if el is not None and el.text and el.text.strip():
            raw = _b64decode(el.text)
            result[bname] = decode_mondata(raw, mondata_fields) if bname in ("monData", "option") else {
                "raw": raw.hex(), "len": len(raw),
            }
    return result


def decode_report(report_xml: str,
                  mondata_fields: tuple[Field, ...] = ()) -> list[dict[str, Any]]:
    """Decode a ``<Report>`` diagmon POST body → list of decoded payloads (usually 1)."""
    root = ET.fromstring(report_xml)
    diag_type = (root.findtext("diagMonType") or "").strip()
    data = (root.findtext("diagMonData") or "").strip()
    if not (diag_type and data):
        return []
    return [decode_diagmon_payload(diag_type, data, mondata_fields)]

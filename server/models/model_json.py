"""ThinQ1 modelJson-driven binary state decoder (mirrors ``sampsyo/wideq``'s ``ModelInfo``).

The per-model ``modelJson`` (fetched from LG; see ``tools/fetch_model_json.py``) defines:
- ``Monitoring.type`` == ``"BINARY(BYTE)"`` for byte-encoded state.
- ``Monitoring.protocol``: a list of ``{value: <key>, startByte: <offset>, length: <bytes>}``
  describing the byte layout.
- ``Value``: per-field ``type``/``option`` (Enum/Bit/Range/Reference/String) for friendly names.

This module applies a modelJson to a binary state blob (e.g. a diagmon ``monData``) →
``{key: friendly_value}``. If the diagmon ``monData`` uses the same layout as the poll
monitor (expected: both are the device's state struct, delivered via push vs poll), this
yields the full decoded state (State, Remain_Time, Course, …). The captured cycle lets us
spot-check: e.g. the decoded "Course" should equal the energyMonInfo ``<course>7</course>``.
"""
from __future__ import annotations

import json
from typing import Any, Optional

_UNKNOWN = "Unknown"


class ModelInfo:
    """A thin reimplementation of wideq.client.ModelInfo over a modelJson dict."""

    def __init__(self, data: dict):
        self.data = data

    @property
    def binary_monitor(self) -> bool:
        return self.data.get("Monitoring", {}).get("type") == "BINARY(BYTE)"

    def decode_monitor_binary(self, data: bytes) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for item in self.data["Monitoring"]["protocol"]:
            start, length = item["startByte"], item["length"]
            value = 0
            for v in data[start:start + length]:
                value = (value << 8) + v
            decoded[item["value"]] = str(value)
        return decoded

    def decode(self, data: bytes) -> dict[str, str]:
        if self.binary_monitor:
            return self.decode_monitor_binary(data)
        return json.loads(data.decode("utf8"))  # JSON-encoded monitor

    def value(self, name: str) -> tuple[str, Any]:
        d = self.data["Value"][name]
        t = d["type"]
        if t in ("Enum", "enum"):
            return "enum", d["option"]
        if t == "Range":
            return "range", d["option"]
        if t.lower() == "bit":
            return "bit", {opt["startbit"]: opt["value"] for opt in d["option"]}
        if t.lower() == "reference":
            return "reference", self.data[d["option"][0]]
        if t.lower() == "string":
            return "string", d.get("_comment", "")
        raise ValueError(f"unsupported value type {t!r} for {name!r}")

    def enum_name(self, key: str, value: str) -> str:
        kind, options = self.value(key)
        return options.get(value, _UNKNOWN) if kind == "enum" else value

    def reference_name(self, key: str, value: str) -> Optional[str]:
        kind, ref = self.value(key)
        if kind == "reference" and str(value) in ref:
            return ref[str(value)].get("_comment")
        return None

    def decode_friendly(self, data: bytes) -> dict[str, str]:
        """Decode, then map Enum/Reference fields to friendly names where possible."""
        out: dict[str, str] = {}
        for key, val in self.decode(data).items():
            if key in self.data.get("Value", {}):
                kind, _ = self.value(key)
                if kind == "enum":
                    out[key] = self.enum_name(key, val)
                elif kind == "reference":
                    out[key] = self.reference_name(key, val) or val
                else:
                    out[key] = val
            else:
                out[key] = val
        return out


def decode_with_model_json(data: bytes, model_json: dict) -> dict[str, str]:
    """Decode a binary state blob using a modelJson → {key: friendly_value}."""
    return ModelInfo(model_json).decode_friendly(data)

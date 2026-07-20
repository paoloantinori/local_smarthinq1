"""Multi-model ThinQ1 decoder registry (TASK-060).

Dispatches diagmon decoding by device identity read from each ``<Report>`` — ``modelName``
(the precise key) then ``deviceType`` (fallback) — and applies the per-model ``modelJson``
when one is cached. Non-obvious invariant: a committed fixture is applied *only* to the
decoder's own ``MODEL_NAME`` — a byte layout never crosses modelNames, so a different model
that falls back to this decoder by deviceType gets the envelope decode, never this modelJson.
"""
from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any, Optional

from . import model_json
from . import washer_wtwn3

_BY_MODEL: dict[str, Any] = {washer_wtwn3.MODEL_NAME: washer_wtwn3}
_BY_TYPE: dict[int, Any] = {washer_wtwn3.DEVICE_TYPE: washer_wtwn3}

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(_REPO_ROOT, "data", "models")   # runtime cache (git-ignored)
_FIXTURE_DIR = os.path.dirname(__file__)                 # server/models/ (committed fixtures)

# modelJson files are immutable at runtime — cache them in memory after first resolution.
_MEM_CACHE: dict[str, Optional[dict]] = {}
# modelName becomes a cache filename; reject anything outside a safe charset to prevent path
# traversal via a crafted <modelName> in a device report.
_SAFE_NAME = re.compile(r"[A-Za-z0-9._-]+\Z")


def cache_path(model_name: str) -> str:
    """Where a model's modelJson is cached: ``data/models/<modelName>.model.json``.

    Single owner of the path convention — ``tools/fetch_model_json.py`` imports this so the
    write side and the read side can never drift apart.
    """
    return os.path.join(CACHE_DIR, f"{model_name}.model.json")


def _read_json(path: str) -> Optional[dict]:
    # Missing or corrupt file → treat as absent and fall through to the next source, rather
    # than letting a bad cache crash report ingestion.
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def load_model_json(model_name: str, decoder: Optional[Any] = None) -> Optional[dict]:
    name = (model_name or "").strip()
    if name in _MEM_CACHE:
        return _MEM_CACHE[name]
    model_j: Optional[dict] = None
    if name and _SAFE_NAME.match(name):
        model_j = _read_json(cache_path(name))
    if model_j is None and decoder is not None and name == getattr(decoder, "MODEL_NAME", None):
        fixture = getattr(decoder, "MODEL_JSON_FIXTURE", None)
        if fixture:
            model_j = _read_json(os.path.join(_FIXTURE_DIR, fixture))
    _MEM_CACHE[name] = model_j
    return model_j


def _identity_from_report(report_xml: str) -> tuple[str, Optional[int]]:
    # Let ET.ParseError propagate: malformed XML is a real error, not "unknown device".
    root = ET.fromstring(report_xml)
    model_name = (root.findtext("modelName") or "").strip()
    raw_type = (root.findtext("devType") or "").strip()
    try:
        dev_type = int(raw_type) if raw_type else None
    except ValueError:
        dev_type = None
    return model_name, dev_type


def _decoder_for(model_name: str, device_type: Optional[int]) -> Optional[Any]:
    mod = _BY_MODEL.get((model_name or "").strip())
    if mod is None and device_type is not None:
        mod = _BY_TYPE.get(device_type)
    return mod


def _apply_model_json(payload: dict, model_j: dict, decoder: Any) -> None:
    # Additive richness on top of an already-successful envelope decode: any failure (malformed
    # modelJson, unexpected type) is logged and skipped — it must never abort that decode.
    for field in getattr(decoder, "STATE_FIELDS", ()) or ():
        node = payload.get(field)
        if isinstance(node, dict) and node.get("raw"):
            try:
                raw = bytes.fromhex(node["raw"])
                payload[f"{field}_decoded"] = model_json.decode_with_model_json(raw, model_j)
            except Exception as e:  # noqa: BLE001 - intentional: additive decode, never fatal
                sys.stderr.write(f"[registry] modelJson decode of {field} failed: {e}\n")


def decode_report(report_xml: str, *, model_name: Optional[str] = None,
                  device_type: Optional[int] = None) -> list[dict]:
    """Decode a ``<Report>`` via the registered decoder; apply modelJson if cached.

    An unknown device yields a single diagnostic payload (``diagMonType`` ``UNKNOWN``) rather
    than raising, so a new appliance reporting before its decoder is added does not crash
    ingestion. Callers that already have the identity should pass it to skip re-parsing.
    """
    if model_name is None or device_type is None:
        rn, rt = _identity_from_report(report_xml)
        if model_name is None:
            model_name = rn
        if device_type is None:
            device_type = rt
    decoder = _decoder_for(model_name or "", device_type)
    if decoder is None:
        return [{"diagMonType": "UNKNOWN", "modelName": model_name, "devType": device_type,
                 "note": "no decoder registered; capture the device and add a module to decode"}]
    payloads = decoder.decode_report(report_xml)
    model_j = load_model_json(model_name, decoder)
    if model_j is not None:
        for p in payloads:
            _apply_model_json(p, model_j, decoder)
    return payloads

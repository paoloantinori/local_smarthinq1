"""Replay the captured fridge reports (flows/fridge-20260721.log) through the registry and
assert the decode. Validates TASK-061: the fridge reuses the WM-family envelope + registry,
decoded via its own modelJson.

Runnable standalone (``python3 tests/test_fridge.py``) or with pytest. No third-party deps.
"""
from __future__ import annotations

import functools
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import fridge_1reb1glpx1 as f  # noqa: E402
from server.models import registry  # noqa: E402

CAPTURE = os.path.join(ROOT, "flows", "fridge-20260721.log")


def _isolated_cache() -> tuple[str, dict]:
    return registry.CACHE_DIR, dict(registry._MEM_CACHE)


@functools.lru_cache(maxsize=1)
def _decoded() -> tuple[dict, ...]:
    text = open(CAPTURE).read()
    out: list[dict] = []
    for r in re.findall(r"<Report>.*?</Report>", text, re.S):
        out += registry.decode_report(r)
    return tuple(out)


def test_fridge_is_registered() -> None:
    assert registry._decoder_for("1REB1GLPX1___", None) is f
    # unknown modelName falls back to deviceType 101 → fridge decoder
    assert registry._decoder_for("SOME_OTHER_FRIDGE", 101) is f


def test_capture_decodes() -> None:
    assert len(_decoded()) >= 2, f"expected >=2 diagmon, got {len(_decoded())}"


def test_event_types() -> None:
    """The captured reports include COMMON_WIFI_ON + COMMON_PERIODIC."""
    ets = {p.get("eventType") for p in _decoded()}
    assert "COMMON_WIFI_ON" in ets, ets
    assert "COMMON_PERIODIC" in ets, ets


def test_periodic_carries_mondata() -> None:
    """The COMMON_PERIODIC report carries a non-trivial monData blob."""
    periodic = [p for p in _decoded() if p.get("eventType") == "COMMON_PERIODIC"]
    assert periodic, "no COMMON_PERIODIC event"
    md = periodic[0].get("monData", {})
    assert isinstance(md, dict) and md.get("len", 0) > 0, md


def test_periodic_decodes_via_modeljson() -> None:
    """The COMMON_PERIODIC monData decodes via the fridge's modelJson → monData_decoded."""
    periodic = [p for p in _decoded() if p.get("eventType") == "COMMON_PERIODIC"]
    assert "monData_decoded" in periodic[0], periodic[0].keys()
    d = periodic[0]["monData_decoded"]
    # the modelJson's core fields are present (values may be Unknown for the periodic variant)
    assert {"TempRefrigerator", "TempFreezer", "DoorOpenState"} <= set(d.keys()), set(d.keys())


def test_model_json_fixture_resolves() -> None:
    orig_dir, orig_mem = _isolated_cache()
    registry.CACHE_DIR = tempfile.mkdtemp()
    registry._MEM_CACHE.clear()
    try:
        assert registry.load_model_json("1REB1GLPX1___", decoder=f), \
            "fridge modelJson fixture should resolve"
        assert registry.load_model_json("NOT_A_FRIDGE", decoder=f) is None
    finally:
        registry.CACHE_DIR = orig_dir
        registry._MEM_CACHE = orig_mem


if __name__ == "__main__":
    for fn in (test_fridge_is_registered, test_capture_decodes, test_event_types,
               test_periodic_carries_mondata, test_periodic_decodes_via_modeljson,
               test_model_json_fixture_resolves):
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\nAll fridge assertions passed over {len(_decoded())} decoded payloads.")

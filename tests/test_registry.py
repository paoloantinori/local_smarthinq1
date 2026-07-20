"""Multi-model registry (TASK-060): dispatch by modelName/devType, modelJson cache,
graceful unknown devices, and the safety property that a committed fixture is never applied
to a different modelName."""
from __future__ import annotations

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import registry  # noqa: E402
from server.models import washer_wtwn3 as w  # noqa: E402

CAPTURE = os.path.join(ROOT, "flows", "washer-cycle-20260719.log")


def _reports() -> list[str]:
    text = open(CAPTURE).read()
    reports = re.findall(r"<Report>.*?</Report>", text, re.S)
    assert reports, "no <Report> in capture"
    return reports


def _isolated_cache() -> tuple[str, dict, str]:
    """Point the registry at an empty temp cache dir + clear the mem cache; return restore data."""
    return registry.CACHE_DIR, dict(registry._MEM_CACHE), tempfile.mkdtemp()


def test_identity_read_from_report() -> None:
    name, dtype = registry._identity_from_report(_reports()[0])
    assert name == "WTWN3" and dtype == 201, (name, dtype)


def test_dispatches_washer_by_model_name() -> None:
    payloads = registry.decode_report(_reports()[0])
    assert payloads
    assert payloads[0].get("diagMonType") in ("EventMonitoring", "WasherMonitoring"), payloads[0]


def test_dispatch_matches_direct_module_decode() -> None:
    """Registry envelope decode == calling the washer module directly (it only adds *_decoded)."""
    via_registry = registry.decode_report(_reports()[0])
    via_module = w.decode_report(_reports()[0])
    assert len(via_registry) == len(via_module)
    assert via_registry[0].get("diagMonType") == via_module[0].get("diagMonType")


def test_applies_committed_model_json_across_cycle() -> None:
    """The committed WTWN3 fixture is applied → monData_decoded with Course=Mix, RUNNING seen."""
    registry._MEM_CACHE.clear()  # exercise the resolution path, not a stale mem hit
    decoded_states = [
        p["monData_decoded"]
        for r in _reports() for p in registry.decode_report(r)
        if "monData_decoded" in p
    ]
    assert decoded_states, "no monData_decoded (washer fixture modelJson not applied?)"
    assert all(d["Course"] == "Mix" for d in decoded_states), \
        [d.get("Course") for d in decoded_states]
    assert any("RUNNING" in d["State"] for d in decoded_states), \
        "expected a RUNNING state in the cycle"


def test_load_model_json_finds_fixture_only_for_own_model() -> None:
    """With no cache file present, the fixture resolves for MODEL_NAME and not for another model."""
    orig_dir, orig_mem, tmp = _isolated_cache()
    registry.CACHE_DIR = tmp
    registry._MEM_CACHE.clear()
    try:
        assert registry.load_model_json("WTWN3", decoder=w), "fixture should resolve for WTWN3"
        assert registry.load_model_json("SOME_OTHER_WASHER", decoder=w) is None, \
            "fixture must not leak to a different modelName"
    finally:
        registry.CACHE_DIR = orig_dir
        registry._MEM_CACHE = orig_mem


def test_corrupted_cache_does_not_crash() -> None:
    """A corrupt cache file falls back to the committed fixture (own model) without raising."""
    orig_dir, orig_mem, tmp = _isolated_cache()
    registry.CACHE_DIR = tmp
    registry._MEM_CACHE.clear()
    with open(os.path.join(tmp, "WTWN3.model.json"), "w") as f:
        f.write("{ not valid json")
    try:
        mj = registry.load_model_json("WTWN3", decoder=w)
        assert mj and "Monitoring" in mj, "should fall back to the fixture, not crash"
    finally:
        registry.CACHE_DIR = orig_dir
        registry._MEM_CACHE = orig_mem


def test_path_traversal_model_name_is_rejected() -> None:
    """A crafted modelName cannot traverse out of the cache dir.

    Cache lives in ``tmp/sub``; ``../secret`` would resolve to ``tmp/secret.model.json``
    (the planted file) without sanitization. The guard must block it."""
    orig_dir, orig_mem = registry.CACHE_DIR, dict(registry._MEM_CACHE)
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "secret.model.json"), "w") as f:
        f.write('{"pwned": true}')
    registry.CACHE_DIR = os.path.join(tmp, "sub")
    os.makedirs(registry.CACHE_DIR, exist_ok=True)  # must exist so ../ resolves back to tmp
    registry._MEM_CACHE.clear()
    try:
        assert registry.load_model_json("../secret", decoder=None) is None, \
            "modelName traversal must not read a file outside the cache dir"
        assert registry._SAFE_NAME.match("../secret") is None
        assert registry._SAFE_NAME.match("WTWN3") is not None
    finally:
        registry.CACHE_DIR = orig_dir
        registry._MEM_CACHE = orig_mem


def test_explicit_identity_overrides_report_parsing() -> None:
    payloads = registry.decode_report(_reports()[0], model_name="WTWN3", device_type=201)
    assert payloads  # explicit identity dispatches without relying on report parsing


def test_devtype_fallback_does_not_apply_foreign_layout() -> None:
    """Unknown modelName + known devType 201 → washer envelope decoder runs, but the WTWN3
    modelJson is NOT applied (different byte layout). The decode stays envelope-only."""
    report = _reports()[0].replace("<modelName>WTWN3</modelName>",
                                   "<modelName>NEWWASHER99</modelName>")
    payloads = registry.decode_report(report)
    assert payloads, "devType fallback should still produce payloads"
    assert any("monData" in p for p in payloads), payloads
    assert all("monData_decoded" not in p for p in payloads), \
        "must not apply the WTWN3 layout to an unknown modelName"


def test_unknown_device_is_graceful() -> None:
    unknown = (
        "<Report><modelName>NOPE999</modelName><devType>999</devType>"
        "<diagMonType>X</diagMonType><diagMonData>e30=</diagMonData></Report>"
    )
    payloads = registry.decode_report(unknown)
    assert len(payloads) == 1
    assert payloads[0].get("note") and "no decoder" in payloads[0]["note"], payloads


def test_cache_path_is_under_data() -> None:
    assert os.path.basename(registry.CACHE_DIR) == "models" and "data" in registry.CACHE_DIR


if __name__ == "__main__":
    for fn in (test_identity_read_from_report, test_dispatches_washer_by_model_name,
               test_dispatch_matches_direct_module_decode,
               test_applies_committed_model_json_across_cycle,
               test_load_model_json_finds_fixture_only_for_own_model,
               test_corrupted_cache_does_not_crash,
               test_path_traversal_model_name_is_rejected,
               test_explicit_identity_overrides_report_parsing,
               test_devtype_fallback_does_not_apply_foreign_layout,
               test_unknown_device_is_graceful, test_cache_path_is_under_data):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll registry assertions passed.")

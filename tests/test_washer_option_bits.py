"""WTWN3 option-bit enrichment (TASK-135).

Named on/off flags from the raw Option1/Option2 ints (rethink's WTWN3/WTDN3 spec
tables; door_lock CONFIRMED on our own corpus: 0x40 in every cycle frame, clear in
END/POWER_OFF). Verified against real corpus payloads."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import registry  # noqa: E402
from server.models import washer_wtwn3  # noqa: E402


RUNNING = "0600360107070003070401000000004000000104002d006400000300"
POWER_OFF = "000000010607000000000000000000000000000a002d006400000300"


def test_door_lock_confirmed_on_real_frames() -> None:
    for blob, want in ((RUNNING, "on"), (POWER_OFF, "off")):
        d = registry.decode_mondata(bytes.fromhex(blob), "WTWN3")["monData_decoded"]
        assert d["opt_door_lock"] == want, (blob[:8], d["opt_door_lock"])


def test_all_flags_present_and_off_when_options_zero() -> None:
    d = registry.decode_mondata(bytes.fromhex(POWER_OFF), "WTWN3")["monData_decoded"]
    for name in ("opt_turbowash", "opt_crease_care", "opt_medic_rinse", "opt_pre_wash",
                 "opt_steam", "opt_remote_start", "opt_door_lock", "opt_child_lock"):
        assert d.get(name) == "off", name


def test_multiple_option1_bits_map_to_named_flags() -> None:
    # decimal strings, as the modelJson decode emits them ("64" not "0x40")
    d = washer_wtwn3.enrich_decoded({"Option1": "195", "Option2": "130"})
    # 0xC3 = 0x80 steam + 0x40 pre_wash + 0x02 crease_care + 0x01 turbowash
    assert d["opt_steam"] == "on" and d["opt_pre_wash"] == "on"
    assert d["opt_turbowash"] == "on" and d["opt_crease_care"] == "on"
    assert d["opt_medic_rinse"] == "off"
    # 0x82 = child_lock + remote_start
    assert d["opt_child_lock"] == "on" and d["opt_remote_start"] == "on"
    assert d["opt_door_lock"] == "off"


def test_missing_or_garbage_options_do_not_raise() -> None:
    for bad in ({}, {"Option1": "n/a"}, {"Option2": None}):
        out = washer_wtwn3.enrich_decoded(dict(bad))
        assert not any(k.startswith("opt_") for k in out), (bad, out)


def test_dryer_decode_has_no_washer_flags() -> None:
    d = registry.decode_mondata(bytes.fromhex(
        "00000000000000000000000000000004000112020000006b"), "RC90U2_WW")
    dd = d.get("monData_decoded") or {}
    assert "opt_door_lock" not in dd


if __name__ == "__main__":
    test_door_lock_confirmed_on_real_frames()
    test_all_flags_present_and_off_when_options_zero()
    test_multiple_option1_bits_map_to_named_flags()
    test_missing_or_garbage_options_do_not_raise()
    test_dryer_decode_has_no_washer_flags()
    print("PASS washer option bits tests")

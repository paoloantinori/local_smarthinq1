"""Tests for the modelJson command vocabulary parser (TASK-066)."""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.models import control_vocab as cv  # noqa: E402

WASHER = os.path.join(ROOT, "server", "models", "washer_wtwn3.model.json")
FRIDGE = os.path.join(ROOT, "server", "models", "fridge_1REB1GLPX1.model.json")
DRYER = os.path.join(ROOT, "server", "models", "dryer_rc90u2.model.json")


def test_fridge_has_set_control_with_fields() -> None:
    m = json.load(open(FRIDGE))
    cmds = cv.parse_commands(m)
    set_ctrl = next(c for c in cmds if c.name == "SetControl")
    assert set_ctrl.cmd == "Control" and set_ctrl.cmd_opt == "Set"
    assert not set_ctrl.is_simple_action
    field_keys = {f.key for f in set_ctrl.fields}
    assert {"TempRefrigerator", "TempFreezer", "IcePlus", "EcoFriendly"} <= field_keys


def test_fridge_temp_fields_are_enums_with_labels() -> None:
    m = json.load(open(FRIDGE))
    cmds = cv.parse_commands(m)
    set_ctrl = next(c for c in cmds if c.name == "SetControl")
    temp_fridge = next(f for f in set_ctrl.fields if f.key == "TempRefrigerator")
    assert temp_fridge.field_type == "Enum"
    assert temp_fridge.options["4"] == "4"  # 4 = 4 degrees C


def test_washer_has_operation_start_and_stop() -> None:
    m = json.load(open(WASHER))
    cmds = cv.parse_commands(m)
    names = {c.name for c in cmds}
    assert "OperationStart" in names
    assert "OperationStop" in names
    start = next(c for c in cmds if c.name == "OperationStart")
    assert start.is_simple_action
    assert start.cmd == "Control" and start.cmd_opt == "Operation"


def test_washer_power_off() -> None:
    m = json.load(open(WASHER))
    cmds = cv.parse_commands(m)
    power = next(c for c in cmds if c.name == "PowerOff")
    assert power.cmd_opt == "Power" and power.value_template == "Off"


def test_to_ha_entity_simple_action() -> None:
    cmd = cv.Command(name="OperationStart", cmd="Control", cmd_opt="Operation", value_template="Start")
    entity = cmd.to_ha_entity()
    assert entity["component"] == "button"
    assert entity["payload"] == "Start"


def test_to_ha_entity_set_control() -> None:
    m = json.load(open(FRIDGE))
    cmds = cv.parse_commands(m)
    set_ctrl = next(c for c in cmds if c.name == "SetControl")
    entity = set_ctrl.to_ha_entity()
    assert entity["component"] == "select"
    assert len(entity["fields"]) == 4


def test_to_value_dict_for_set_control() -> None:
    cmd = cv.Command(name="SetControl", cmd="Control", cmd_opt="Set", value_template="",
                     fields=[cv.CommandField("RETM", "Enum", {"4": "4"})])
    val = cmd.to_value_dict(RETM="4")
    assert val == {"RETM": "4"}


if __name__ == "__main__":
    for fn in (test_fridge_has_set_control_with_fields, test_fridge_temp_fields_are_enums_with_labels,
               test_washer_has_operation_start_and_stop, test_washer_power_off,
               test_to_ha_entity_simple_action, test_to_ha_entity_set_control,
               test_to_value_dict_for_set_control):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll command vocabulary tests passed.")

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


def test_field_wire_key_extracted_from_template() -> None:
    """The fridge template pairs each {{Field}} with a wire key (RETM, REFT, …)."""
    m = json.load(open(FRIDGE))
    cmds = cv.parse_commands(m)
    set_ctrl = next(c for c in cmds if c.name == "SetControl")
    by_key = {f.key: f.wire_key for f in set_ctrl.fields}
    assert by_key == {"TempRefrigerator": "RETM", "TempFreezer": "REFT",
                      "IcePlus": "REIP", "EcoFriendly": "REEF"}


def test_simple_action_expands_to_one_button() -> None:
    cmd = cv.Command(name="OperationStart", cmd="Control", cmd_opt="Operation",
                     value_template="Start")
    entities = cmd.expand_entities()
    assert len(entities) == 1
    e = entities[0]
    assert e.component == "button"
    assert e.cmd_opt == "Operation"
    assert e.payload == "Start"
    # Button wire format is not captured yet → to_wire rejects until approved (no guessing).
    assert e.to_wire("PRESS") is None


def test_to_wire_returns_wire_command_with_cmd_and_cmd_opt() -> None:
    """to_wire yields a WireCommand carrying cmd/cmd_opt so send_command isn't hardcoded."""
    m = json.load(open(FRIDGE))
    entities = {e.slug: e for e in cv.all_entities(m)}
    wire = entities["temprefrigerator"].to_wire("4")
    assert wire is not None
    assert wire.cmd == "Control" and wire.cmd_opt == "Set"
    assert wire.value == {"RETM": "4"}


def test_to_wire_rejects_unknown_select_payload() -> None:
    """A malformed select payload must not become a physical-device write."""
    m = json.load(open(FRIDGE))
    entities = {e.slug: e for e in cv.all_entities(m)}
    assert entities["tempfreezer"].to_wire("garbage") is None
    assert entities["tempfreezer"].to_wire("") is None


def test_command_labels_have_no_enum_markers() -> None:
    """select option labels are cleaned (no @..._W), consistent with TASK-065 state labels."""
    m = json.load(open(FRIDGE))
    entities = {e.slug: e for e in cv.all_entities(m)}
    labels = entities["iceplus"].ha_options()
    assert labels == ["CP_OFF_EN", "CP_ON_EN"], labels
    # cleaned labels round-trip back to the wire value
    wire = entities["iceplus"].to_wire("CP_ON_EN")
    assert wire is not None and wire.value == {"REIP": "2"}


def test_set_control_expands_to_one_entity_per_field() -> None:
    m = json.load(open(FRIDGE))
    cmds = cv.parse_commands(m)
    set_ctrl = next(c for c in cmds if c.name == "SetControl")
    entities = set_ctrl.expand_entities()
    assert len(entities) == 4
    assert {e.slug for e in entities} == {"temprefrigerator", "tempfreezer", "iceplus", "ecofriendly"}
    assert all(e.component == "select" for e in entities)


def test_field_entity_to_wire_translates_label_to_ordinal() -> None:
    """A select payload is the display label; to_wire returns the wire value (enum ordinal)."""
    m = json.load(open(FRIDGE))
    entities = {e.slug: e for e in cv.all_entities(m)}
    w = entities["temprefrigerator"].to_wire("4")
    assert w is not None and w.value == {"RETM": "4"}
    # freezer label -19 is wire ordinal 5; the label and wire value must not be conflated
    w = entities["tempfreezer"].to_wire("-19")
    assert w is not None and w.value == {"REFT": "5"}
    w = entities["tempfreezer"].to_wire("-15")
    assert w is not None and w.value == {"REFT": "1"}


def test_all_entities_publishes_only_set_commands() -> None:
    """Only Set commands (fridge selects) are published. Buttons (physical actuation) and
    non-sendable actions (CourseDownload) stay hidden until approved/captured (CLAUDE.md #5)."""
    m = json.load(open(WASHER))
    slugs = {e.slug for e in cv.all_entities(m)}
    assert slugs == set(), f"washer has no Set commands; nothing should be published: {slugs}"
    fr = json.load(open(FRIDGE))
    fridge_slugs = {e.slug for e in cv.all_entities(fr)}
    assert fridge_slugs == {"temprefrigerator", "tempfreezer", "iceplus", "ecofriendly"}


if __name__ == "__main__":
    for fn in (test_fridge_has_set_control_with_fields, test_fridge_temp_fields_are_enums_with_labels,
               test_washer_has_operation_start_and_stop, test_washer_power_off,
               test_field_wire_key_extracted_from_template,
               test_simple_action_expands_to_one_button,
               test_to_wire_returns_wire_command_with_cmd_and_cmd_opt,
               test_to_wire_rejects_unknown_select_payload,
               test_set_control_expands_to_one_entity_per_field,
               test_field_entity_to_wire_translates_label_to_ordinal,
               test_command_labels_have_no_enum_markers,
               test_all_entities_publishes_only_set_commands):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll command vocabulary tests passed.")

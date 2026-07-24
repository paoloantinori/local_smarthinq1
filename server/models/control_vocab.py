"""Per-model command vocabulary extracted from the modelJson ``ControlWifi`` section (TASK-066).

Each model's modelJson defines what commands the appliance accepts via the ``:47878`` control
channel. This module parses that definition into a structured command registry, so the MQTT
bridge can publish the right HA command entities (buttons, selects, numbers) and the control
channel can format the right ``Control``/``Set`` message.

Washer/dryer commands: ``OperationStart``, ``OperationStop``, ``OperationWakeUp``, ``PowerOff``
(simple buttons, ``CmdOpt: "Operation"`` / ``"Power"``).

Fridge commands: ``SetControl`` with per-field placeholders (TempRefrigerator, TempFreezer,
IcePlus, EcoFriendly), each an Enum with friendly labels.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CommandField:
    """One controllable field within a command (e.g. TempRefrigerator = Enum 1-7)."""
    key: str
    field_type: str  # "Enum" | "Range"
    options: dict[str, str] = field(default_factory=dict)  # enum value → label
    min_val: Optional[int] = None
    max_val: Optional[int] = None


@dataclass
class Command:
    """A single command the appliance accepts (e.g. SetControl, OperationStart)."""
    name: str  # action name from the modelJson (SetControl, OperationStart, etc.)
    cmd: str  # the "Cmd" value (Control)
    cmd_opt: str  # the "CmdOpt" value (Set, Operation, Power)
    value_template: str  # raw template with {{placeholders}} or a literal like "Start"
    fields: list[CommandField] = field(default_factory=list)

    @property
    def is_simple_action(self) -> bool:
        """True if this is a button-style command (no fields, just cmd+cmdOpt+value)."""
        return not self.fields

    def to_value_dict(self, **kwargs: str) -> dict[str, str]:
        """Build the Value dict for a Control/Set command from user-provided field values."""
        if self.is_simple_action:
            return {}
        return {k: v for k, v in kwargs.items() if v is not None}

    def to_ha_entity(self) -> dict[str, Any]:
        """Describe this command as an HA entity shape for discovery."""
        if self.is_simple_action:
            return {"component": "button", "name": self.name, "cmd": self.cmd,
                    "cmd_opt": self.cmd_opt, "payload": self.value_template}
        return {"component": "select" if any(f.field_type == "Enum" for f in self.fields) else "number",
                "name": self.name, "cmd": self.cmd, "cmd_opt": self.cmd_opt,
                "fields": [{"key": f.key, "type": f.field_type,
                            "options": f.options, "min": f.min_val, "max": f.max_val}
                           for f in self.fields]}


def parse_commands(model_json: dict) -> list[Command]:
    """Parse the ``ControlWifi.action`` section of a modelJson into a list of commands."""
    actions = model_json.get("ControlWifi", {}).get("action", {})
    value_section = model_json.get("Value", {})
    commands: list[Command] = []
    for action_name, spec in actions.items():
        cmd = spec.get("cmd", "")
        cmd_opt = spec.get("cmdOpt", "")
        val_template = spec.get("value", "")
        # extract {{FieldName}} placeholders
        field_keys = re.findall(r"\{\{(\w+)\}\}", val_template)
        fields: list[CommandField] = []
        for fk in field_keys:
            vdef = value_section.get(fk, {})
            ftype = vdef.get("type", "Enum")
            if ftype == "Range":
                opts = vdef.get("option", {})
                fields.append(CommandField(
                    key=fk, field_type="Range",
                    min_val=opts.get("min"), max_val=opts.get("max")))
            else:
                opts = vdef.get("option", {})
                fields.append(CommandField(key=fk, field_type="Enum", options=opts))
        commands.append(Command(
            name=action_name, cmd=cmd, cmd_opt=cmd_opt,
            value_template=val_template, fields=fields))
    return commands

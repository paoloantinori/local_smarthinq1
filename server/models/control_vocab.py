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
from typing import Optional

from .model_json import clean_label


@dataclass
class CommandField:
    """One controllable field within a command (e.g. TempRefrigerator = Enum 1-7).

    For a ``Set`` command the value template maps the human field key (``TempRefrigerator``)
    to the **wire key** the appliance expects (``RETM``). ``wire_key`` carries that so a field
    entity can produce a well-formed ``Value`` dict without re-parsing the template at send time.
    """
    key: str
    field_type: str  # "Enum" | "Range"
    options: dict[str, str] = field(default_factory=dict)  # enum wire value → display label
    min_val: Optional[int] = None
    max_val: Optional[int] = None
    wire_key: Optional[str] = None  # the appliance-side Value key (RETM, REFT, …), if known


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

    @property
    def is_sendable(self) -> bool:
        """A real :47878 command carries a ``Cmd``. Actions like ``CourseDownload`` (cmd=None)
        are not Control/Set commands and must not become command entities."""
        return bool(self.cmd)

    def expand_entities(self) -> list["CommandEntity"]:
        """Expand into one HA command entity per field (multi-field commands), or one button
        entity (simple actions).

        Note: ``all_entities`` (the only path that publishes) filters to ``Set`` commands, so
        simple-action buttons never reach HA through it. The button branch here exists so a
        future caller can expand a button once its wire format is captured and approved
        (CLAUDE.md #5); ``for_simple_action`` is exercised directly by the tests meanwhile."""
        if self.is_simple_action:
            return [CommandEntity.for_simple_action(self)]
        entities: list[CommandEntity] = []
        for f in self.fields:
            entities.append(CommandEntity.for_field(self, f))
        return entities


@dataclass
class CommandEntity:
    """One HA command entity (button / select / number) plus everything the MQTT handler needs
    to turn a received payload into a wire ``Value`` dict. Self-describing on purpose: the
    bridge's on_message stays appliance-agnostic."""
    slug: str            # unique within the device (e.g. temprefrigerator, operationstart)
    component: str       # "button" | "select" | "number"
    name: str            # HA entity name (TempRefrigerator, Operation Start)
    cmd: str             # the Control Cmd
    cmd_opt: str         # the Control CmdOpt (Set for fridge fields, Operation/Power for buttons)
    wire_key: Optional[str]      # Value key this entity sets (RETM); None for buttons
    payload: str = ""            # literal Value buttons send (Start / Off / Stop)
    options: dict[str, str] = field(default_factory=dict)  # wire value → label (selects)
    min_val: Optional[int] = None
    max_val: Optional[int] = None

    @classmethod
    def for_simple_action(cls, c: Command) -> "CommandEntity":
        return cls(slug=_slug(c.name), component="button", name=_pretty(c.name),
                   cmd=c.cmd, cmd_opt=c.cmd_opt, wire_key=None, payload=c.value_template)

    @classmethod
    def for_field(cls, c: Command, f: CommandField) -> "CommandEntity":
        component = "select" if f.field_type == "Enum" else "number"
        return cls(slug=_slug(f.key), component=component, name=_pretty(f.key),
                   cmd=c.cmd, cmd_opt=c.cmd_opt, wire_key=f.wire_key or f.key,
                   options=dict(f.options), min_val=f.min_val, max_val=f.max_val)

    def ha_options(self) -> list[str]:
        """Display labels for a select, in wire-value order (so the fridge shows 7,6,5,…°C)."""
        return [self.options[k] for k in sorted(self.options)]

    def to_wire(self, payload: str) -> Optional["WireCommand"]:
        """Turn a received HA payload into a :class:`WireCommand` for the control channel, or
        None to reject it (an unknown select payload must not reach a physical device).

        Buttons return their literal payload as the Value (``{"Start": "Start"}`` is wrong;
        buttons encode the action in cmd/cmd_opt, so Value is the payload under a key the
        appliance expects, here just the payload string wrapped per the command). Selects receive
        a display label and translate it back to the wire value (freezer ``-19`` → ``REFT 5``,
        the enum ordinal, not the label)."""
        if self.wire_key is None:
            # Button (cmd/cmd_opt encode the action). The exact button Value wire format is not
            # captured yet, so buttons are unpublished (all_entities gates them out) and to_wire
            # rejects until the format is confirmed from a capture (capture-driven: no guessing).
            return None
        if self.options:
            for wire_val, label in self.options.items():
                if payload == label:
                    return WireCommand(self.cmd, self.cmd_opt, {self.wire_key: wire_val})
            # Unknown label for a fixed-option select: reject rather than forward garbage to the
            # appliance (a malformed HA payload must not change a fridge setpoint). Do NOT accept a
            # raw wire key either, since ha_options() only ever offers labels, so a key the user
            # never saw is not a legitimate input.
            return None
        # Range / no option table: payload is the raw value (but reject empty).
        if payload == "":
            return None
        return WireCommand(self.cmd, self.cmd_opt, {self.wire_key: payload})


@dataclass
class WireCommand:
    """A fully-resolved command ready for ``control_channel.send_command``: the wire Cmd/CmdOpt
    plus the Value dict. The bridge passes these straight through, no model logic at send time."""
    cmd: str
    cmd_opt: str
    value: dict[str, str]


def _slug(name: str) -> str:
    return name.lower().replace("_", "")


def _pretty(name: str) -> str:
    return name.replace("_", " ").strip()


def _wire_keys_from_template(val_template: str) -> dict[str, str]:
    """Map each ``{{Field}}`` placeholder to its wire key in the value template.

    The fridge template is a JSON literal: ``{ "RETM":"{{TempRefrigerator}}", ... }``. We pair
    each ``"KEY":"{{field}}"`` so a field entity can emit ``Value={"RETM": ...}`` directly,
    without the handler re-parsing anything at send time.
    """
    pairs: dict[str, str] = {}
    for wire_key, field_key in re.findall(r'"(\w+)"\s*:\s*"\{\{(\w+)\}\}"', val_template):
        pairs[field_key] = wire_key
    return pairs


def parse_commands(model_json: dict) -> list[Command]:
    """Parse the ``ControlWifi.action`` section of a modelJson into a list of commands."""
    actions = model_json.get("ControlWifi", {}).get("action", {})
    value_section = model_json.get("Value", {})
    commands: list[Command] = []
    for action_name, spec in actions.items():
        cmd = spec.get("cmd", "")
        cmd_opt = spec.get("cmdOpt", "")
        val_template = spec.get("value", "")
        wire_keys = _wire_keys_from_template(val_template)
        # extract {{FieldName}} placeholders
        field_keys = re.findall(r"\{\{(\w+)\}\}", val_template)
        fields: list[CommandField] = []
        for fk in field_keys:
            vdef = value_section.get(fk, {})
            ftype = vdef.get("type", "Enum")
            opts = vdef.get("option", {})
            if ftype == "Range":
                fields.append(CommandField(
                    key=fk, field_type="Range", wire_key=wire_keys.get(fk),
                    min_val=opts.get("min"), max_val=opts.get("max")))
            else:
                # Clean the @..._W markers from each label (TASK-065), so HA shows
                # "CP_OFF_EN" not "@CP_OFF_EN_W" and the to_wire reverse lookup is stable.
                cleaned = {k: clean_label(v) for k, v in opts.items()}
                fields.append(CommandField(key=fk, field_type="Enum", options=cleaned,
                                           wire_key=wire_keys.get(fk)))
        commands.append(Command(
            name=action_name, cmd=cmd, cmd_opt=cmd_opt,
            value_template=val_template, fields=fields))
    return commands


def all_entities(model_json: dict) -> list[CommandEntity]:
    """Every published command entity for a model. Currently only ``Set`` commands (the
    fridge's per-field selects) are published, the captured + validated command type.
    Button commands (OperationStart/PowerOff) are physical-actuation and stay hidden until
    their wire format is captured and the user approves them (CLAUDE.md #5). The plumbing
    (CommandEntity carries cmd/cmd_opt; send_command accepts them) is in place for that."""
    entities: list[CommandEntity] = []
    for c in parse_commands(model_json):
        if c.is_sendable and c.cmd_opt == "Set":
            entities.extend(c.expand_entities())
    return entities

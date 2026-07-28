"""Tests for the MQTT command path (TASK-067): HA command → :47878 control channel.

A command published to an entity's command_topic must reach ``control_channel.send_command``
with the correct wire ``Value`` dict. Uses a fake paho client + fake control channel so no broker
or socket is needed. ``allow_control`` gating is asserted (the safety invariant, CLAUDE.md #5).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import mqtt_bridge, ha_mqtt  # noqa: E402
from server.models import control_vocab as cv  # noqa: E402

FRIDGE_MODEL = "1REB1GLPX1___"
FRIDGE_DEV = "fridge-dev"


class _FakeMsg:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


class _FakeClient:
    """Captures subscribe + the sink's discovery/state publishes (publish is a coroutine-free
    stub). on_message is set by the sink once it subscribes to command topics."""
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []
        self.subscribed: list[str] = []
        self.on_message: Any = None

    def publish(self, topic: str, payload, qos: int = 0, retain: bool = False):
        self.published.append((topic, payload if isinstance(payload, str) else payload.decode()))

    def subscribe(self, topic: str):
        self.subscribed.append(topic)


class _FakeControl:
    """Records send_command calls (cmd/cmd_opt/value)."""
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, str, str]] = []

    def send_command(self, dev_id: str, value: dict, *, cmd: str = "Control",
                     cmd_opt: str = "Set") -> bool:
        self.sent.append((dev_id, value, cmd, cmd_opt))
        return True


def _fridge_sink(control: Any, allow_control: bool) -> mqtt_bridge._Sink:
    sink = mqtt_bridge._Sink(_FakeClient(), ha_mqtt, control, allow_control)
    # Seed the command-entity table as if a first fridge ingest had announced it.
    model_j = json.load(open(os.path.join(ROOT, "server", "models",
                                          "fridge_1REB1GLPX1.model.json")))
    sink._cmd_entities[FRIDGE_DEV] = {e.slug: e for e in cv.all_entities(model_j)}
    return sink


def test_command_published_reaches_send_command() -> None:
    """The load-bearing acceptance test: a select command → control_channel.send_command."""
    control = _FakeControl()
    sink = _fridge_sink(control, allow_control=True)
    topic = ha_mqtt.command_topic(FRIDGE_DEV, "select", "temprefrigerator")
    sink._on_message(None, None, _FakeMsg(topic, b"4"))
    assert control.sent == [(FRIDGE_DEV, {"RETM": "4"}, "Control", "Set")], control.sent


def test_select_label_translated_to_wire_ordinal() -> None:
    """Freezer label -19 is wire ordinal 5, not the label value."""
    control = _FakeControl()
    sink = _fridge_sink(control, allow_control=True)
    topic = ha_mqtt.command_topic(FRIDGE_DEV, "select", "tempfreezer")
    sink._on_message(None, None, _FakeMsg(topic, b"-19"))
    assert control.sent == [(FRIDGE_DEV, {"REFT": "5"}, "Control", "Set")], control.sent


def test_invalid_payload_does_not_reach_appliance() -> None:
    """A malformed select payload is rejected by to_wire; send_command is not called."""
    control = _FakeControl()
    sink = _fridge_sink(control, allow_control=True)
    topic = ha_mqtt.command_topic(FRIDGE_DEV, "select", "tempfreezer")
    sink._on_message(None, None, _FakeMsg(topic, b"garbage"))
    assert control.sent == [], control.sent


def test_allow_control_off_does_not_subscribe() -> None:
    """When allow_control is off, the sink must NOT subscribe to command topics — no surface
    to accidentally trigger (the safety invariant)."""
    control = _FakeControl()
    sink = mqtt_bridge._Sink(_FakeClient(), ha_mqtt, control, allow_control=False)
    assert sink._client.subscribed == [], sink._client.subscribed
    assert sink._client.on_message is None


def test_allow_control_on_subscribes_to_command_wildcard() -> None:
    control = _FakeControl()
    sink = mqtt_bridge._Sink(_FakeClient(), ha_mqtt, control, allow_control=True)
    assert any("+/cmd" in t for t in sink._client.subscribed), sink._client.subscribed


def test_command_for_unknown_entity_ignored() -> None:
    """A command to a slug we never announced must not reach the appliance."""
    control = _FakeControl()
    sink = _fridge_sink(control, allow_control=True)
    topic = ha_mqtt.command_topic(FRIDGE_DEV, "select", "nonexistent")
    sink._on_message(None, None, _FakeMsg(topic, b"x"))
    assert control.sent == [], control.sent


def test_command_discovery_published_on_first_ingest() -> None:
    """First state ingest for a fridge (allow_control on) publishes command entities too."""
    control = _FakeControl()
    sink = mqtt_bridge._Sink(_FakeClient(), ha_mqtt, control, allow_control=True)
    # The fridge modelJson is a committed fixture the registry resolves by modelName.
    sink(FRIDGE_DEV, {"modelName": FRIDGE_MODEL,
                       "monData_decoded": {"TempRefrigerator": "4"}})
    cmd_configs = [t for t, _p in sink._client.published if "/cmd" not in t
                   and t.endswith("/config") and t.split("/")[1] in ("button", "select", "number")]
    # fridge SetControl → 4 select entities (temprefrigerator, tempfreezer, iceplus, ecofriendly)
    assert any("temprefrigerator" in t for t in cmd_configs), cmd_configs


def test_command_topic_round_trip() -> None:
    """parse_command_topic inverts command_topic."""
    for component, slug in [("button", "operationstart"), ("select", "temprefrigerator")]:
        topic = ha_mqtt.command_topic("devX", component, slug)
        parsed = ha_mqtt.parse_command_topic(topic)
        assert parsed == ("devX", component, slug), (topic, parsed)


def test_parse_command_topic_rejects_non_command() -> None:
    assert ha_mqtt.parse_command_topic("homeassistant/sensor/lgthinq_dev/state") is None
    assert ha_mqtt.parse_command_topic("homeassistant/select/lgthinq_dev/temp/config") is None


if __name__ == "__main__":
    for fn in (test_command_published_reaches_send_command,
               test_select_label_translated_to_wire_ordinal,
               test_allow_control_off_does_not_subscribe,
               test_allow_control_on_subscribes_to_command_wildcard,
               test_command_for_unknown_entity_ignored,
               test_command_discovery_published_on_first_ingest,
               test_command_topic_round_trip,
               test_parse_command_topic_rejects_non_command):
        fn(); print(f"PASS {fn.__name__}")
    print("\nAll MQTT command tests passed.")

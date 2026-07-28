"""Home Assistant MQTT-discovery bridge for the fake-cloud server (M4 / TASK-040 spike).

Publishes the decoded appliance state to MQTT using HA's "MQTT discovery" convention, so HA
auto-creates sensor entities with no custom integration. The server (or a small sidecar)
calls :func:`publish_state` whenever a diagmon report lands; HA sees live sensor values.

This is the spike for D-2 (HA bridge = MQTT discovery, like ``anszom/rethink``). Validated
end-to-end against a local mosquitto broker; pointing it at HA's real broker is a
host/credentials change only.

Contract (HA MQTT discovery): all of a device's sensors read from ONE JSON state topic,
and each sensor's ``value_template`` extracts its field from that JSON. So
:func:`publish_state` writes the whole decoded dict as one retained JSON message; discovery
derives one sensor **per decoded field** (appliance-agnostic — the fields come from the
device's modelJson, not a hardcoded washer list), with device_class/unit overrides for known
fields. So the fridge gets TempRefrigerator/DoorOpenState/etc., not the washer's State/Course.

See https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

# Per-field HA overrides for known field names (device_class / unit_of_measurement). Every
# decoded field becomes a sensor; this table only adds HA metadata where it's known.
_FIELD_OVERRIDES: dict[str, dict[str, str]] = {
    "TempRefrigerator": {"device_class": "temperature", "unit_of_measurement": "°C"},
    "TempFreezer": {"device_class": "temperature", "unit_of_measurement": "°C"},
    "Remain_Time_H": {"device_class": "duration", "unit_of_measurement": "h"},
    "Remain_Time_M": {"device_class": "duration", "unit_of_measurement": "min"},
    "WaterFilterUsedMonth": {"unit_of_measurement": "months"},
}


def _slug(device_id: str) -> str:
    return f"lgthinq_{device_id}"


def _state_topic(device_id: str, *, discovery_prefix: str = "homeassistant") -> str:
    return f"{discovery_prefix}/sensor/{_slug(device_id)}/state"


def _device_payload(model_name: str, device_id: str) -> dict[str, Any]:
    return {
        "identifiers": [_slug(device_id)],
        "manufacturer": "LG",
        "model": model_name,
        "name": model_name,
    }


def publish_discovery(client: Any, model_name: str, device_id: str, fields: list[str], *,
                      discovery_prefix: str = "homeassistant") -> None:
    """Announce one sensor per decoded field (appliance-agnostic)."""
    slug = _slug(device_id)
    state_topic = _state_topic(device_id, discovery_prefix=discovery_prefix)
    device = _device_payload(model_name, device_id)
    for field in fields:
        object_id = field.lower()
        cfg: dict[str, Any] = {
            "name": field.replace("_", " "),
            "state_topic": state_topic,
            "value_template": "{{ value_json.%s }}" % field,
            "unique_id": f"{slug}_{object_id}",
            "device": device,
        }
        cfg.update(_FIELD_OVERRIDES.get(field, {}))
        client.publish(f"{discovery_prefix}/sensor/{slug}/{object_id}/config",
                       json.dumps(cfg), qos=1, retain=True)


def publish_state(client: Any, decoded: dict[str, Any], device_id: str, *,
                  discovery_prefix: str = "homeassistant") -> None:
    """Publish the decoded state as one retained JSON message on the shared state topic."""
    client.publish(_state_topic(device_id, discovery_prefix=discovery_prefix),
                   json.dumps({k: str(v) for k, v in decoded.items()}), qos=0, retain=True)


# ── error alert (binary_sensor) ─────────────────────────────────────────────────────────────
# A plain text "Error" sensor (one per decoded field) is easy to miss. This adds a
# binary_sensor that is `on` whenever the appliance reports a real error, so an HA automation
# can trigger on it (e.g. the washer's DE2 door fault at a scheduled start). Derived purely
# from the shared JSON state topic's `Error` field, no extra ingest plumbing.

# Templates that map the Error field to on/off. `'No Error'` is the decoded idle value; any
# other non-empty value is a real fault. (Verified: the WM friendly decoder emits exactly
# `'No Error'` for idle across washer + dryer. A future appliance with a different idle
# sentinel would stick `on` at idle, and must be added here.)
# The `is defined` guard is required: HA renders MQTT value_templates with strict-undefined
# semantics, so `value_json.Error` RAISES on a device whose state JSON has no Error key (the
# fridge). `is defined` short-circuits to off for those devices instead of erroring.
_ERROR_ON_TEMPLATE = ("{{ 'on' if value_json.Error is defined and value_json.Error "
                      "and value_json.Error != 'No Error' else 'off' }}")


def publish_error_alert_discovery(client: Any, model_name: str, device_id: str, *,
                                  discovery_prefix: str = "homeassistant") -> None:
    """Publish one binary_sensor that is on while the appliance reports an error.

    Idempotent + cheap: one retained config per device. The appliance must carry an `Error`
    field in its decoded state (the washer/dryer do; the fridge does not, in which case the
    sensor stays off permanently, which is correct)."""
    slug = _slug(device_id)
    cfg: dict[str, Any] = {
        "name": "Error",
        "state_topic": _state_topic(device_id, discovery_prefix=discovery_prefix),
        "value_template": _ERROR_ON_TEMPLATE,
        "unique_id": f"{slug}_error_alert",
        "device": _device_payload(model_name, device_id),
    }
    client.publish(f"{discovery_prefix}/binary_sensor/{slug}/error_alert/config",
                   json.dumps(cfg), qos=1, retain=True)


# ── command discovery (TASK-067, the bidirectional half) ───────────────────────────────────
# Each CommandEntity becomes an HA button/select/number with its own command_topic. The bridge
# subscribes to a wildcard over these topics; on a message it parses the topic to recover
# (device_id, slug) and dispatches. Identity lives in the TOPIC, not the payload: buttons send
# free-form payloads ("Start", "PRESS") that don't carry identity.


def command_topic(device_id: str, component: str, slug: str, *,
                  discovery_prefix: str = "homeassistant") -> str:
    """Where a command entity receives payloads from HA."""
    return f"{discovery_prefix}/{component}/{_slug(device_id)}/{slug}/cmd"


def parse_command_topic(topic: str, *, discovery_prefix: str = "homeassistant") \
        -> Optional[tuple[str, str, str]]:
    """Inverse of :func:`command_topic`: (device_id, component, slug) or None if not a command
    topic. The device_id is recovered from the lgthinq_<id> node."""
    prefix = discovery_prefix.rstrip("/")
    m = re.match(rf"^{re.escape(prefix)}/(\w+)/lgthinq_(\S+)/(\w+)/cmd$", topic)
    if not m:
        return None
    component, dev_id, slug = m.group(1), m.group(2), m.group(3)
    return dev_id, component, slug


def publish_command_discovery(client: Any, model_name: str, device_id: str,
                              entities: list, *, discovery_prefix: str = "homeassistant") -> None:
    """Announce one HA command entity per :class:`control_vocab.CommandEntity`.

    ``entities`` is a list of ``CommandEntity`` (we import lazily via duck typing to avoid a
    hard dependency from ha_mqtt → models). Each carries its slug/component/cmd/cmd_opt plus,
    for selects, the option labels and the slug the bridge routes on.
    """
    device = _device_payload(model_name, device_id)
    for e in entities:
        object_id = e.slug
        cmd_topic = command_topic(device_id, e.component, object_id,
                                  discovery_prefix=discovery_prefix)
        cfg: dict[str, Any] = {
            "name": e.name,
            "command_topic": cmd_topic,
            "unique_id": f"{_slug(device_id)}_cmd_{object_id}",
            "device": device,
        }
        if e.component == "button":
            pass  # buttons send "PRESS" by default; the action is encoded in cmd/cmd_opt
        elif e.component == "select":
            cfg["options"] = e.ha_options()
        elif e.component == "number":
            if e.min_val is not None:
                cfg["min"] = e.min_val
            if e.max_val is not None:
                cfg["max"] = e.max_val
        client.publish(f"{discovery_prefix}/{e.component}/{_slug(device_id)}/{object_id}/config",
                       json.dumps(cfg), qos=1, retain=True)



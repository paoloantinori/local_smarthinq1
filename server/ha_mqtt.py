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
from typing import Any

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



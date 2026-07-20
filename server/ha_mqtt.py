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
derives one sensor per decoded key (appliance-agnostic — no per-model field list here), with
device_class/unit overrides from a small table.

See https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""
from __future__ import annotations

import json
from typing import Any

# The decoded fields this bridge surfaces as HA sensors, with optional HA overrides.
# A presentation choice (which fields are worth entities), not a redeclaration of the
# decoder's schema. Friendly-name / enum cleanup belongs in the decoder (model_json), not
# here — values are published verbatim.
_SENSORS: list[dict[str, str]] = [
    {"field": "State", "object_id": "run_state", "name": "Run state"},
    {"field": "Course", "object_id": "course", "name": "Course"},
    {"field": "Remain_Time_H", "object_id": "remain_hours", "name": "Remaining (hours)",
     "device_class": "duration", "unit_of_measurement": "h"},
    {"field": "Remain_Time_M", "object_id": "remain_minutes", "name": "Remaining (min)",
     "device_class": "duration", "unit_of_measurement": "min"},
    {"field": "Error", "object_id": "error", "name": "Error"},
]


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


def publish_discovery(client: Any, model_name: str, device_id: str, *,
                      discovery_prefix: str = "homeassistant") -> None:
    """Announce the appliance's sensors to HA (one config message per sensor)."""
    slug = _slug(device_id)
    state_topic = _state_topic(device_id, discovery_prefix=discovery_prefix)
    device = _device_payload(model_name, device_id)
    for s in _SENSORS:
        cfg: dict[str, Any] = {
            "name": s["name"],
            "state_topic": state_topic,
            "value_template": "{{ value_json.%s }}" % s["field"],
            "unique_id": f"{slug}_{s['object_id']}",
            "device": device,
        }
        cfg.update({k: s[k] for k in ("device_class", "unit_of_measurement") if k in s})
        client.publish(f"{discovery_prefix}/sensor/{slug}/{s['object_id']}/config",
                       json.dumps(cfg), qos=1, retain=True)


def publish_state(client: Any, decoded: dict[str, Any], device_id: str, *,
                  discovery_prefix: str = "homeassistant") -> None:
    """Publish the decoded state as one retained JSON message on the shared state topic."""
    client.publish(_state_topic(device_id, discovery_prefix=discovery_prefix),
                   json.dumps({k: str(v) for k, v in decoded.items()}), qos=0, retain=True)


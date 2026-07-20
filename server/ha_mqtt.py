"""Home Assistant MQTT-discovery bridge for the fake-cloud server (M4 / TASK-040 spike).

Publishes the decoded appliance state to MQTT using HA's "MQTT discovery" convention, so HA
auto-creates sensor entities with no custom integration. The server (or a small sidecar)
calls :func:`publish_state` whenever a diagmon report lands; HA sees live sensor values.

This is the spike for D-2 (HA bridge = MQTT discovery, like ``anszom/rethink``). Validated
end-to-end against a local mosquitto broker; pointing it at HA's real broker is a
host/credentials change only.

Contract (HA MQTT discovery): all of a device's sensors read from ONE JSON state topic
(``state_topic``), and each sensor's ``value_template`` extracts its field from that JSON.
So :func:`publish_state` writes the whole decoded dict as one retained JSON message; the
per-sensor config messages point at it via ``value_template``.

See https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""
from __future__ import annotations

import json
from typing import Any

# Maps a decoded monData_decoded field → an HA sensor definition.
# (field, object_id, friendly name, device_class, unit). Minimal for the spike.
_SENSORS = (
    # field            object_id           friendly name        device_class  unit
    ("State",          "run_state",        "Run state",         None,         None),
    ("Course",         "course",           "Course",            None,         None),
    ("Remain_Time_H",  "remain_hours",     "Remaining (hours)", "duration",   "h"),
    ("Remain_Time_M",  "remain_minutes",   "Remaining (min)",   "duration",   "min"),
    ("Error",          "error",            "Error",             None,         None),
)

# Decoded enum values that aren't in the modelJson Value map come through as
# "@WM_STATE_RUNNING_W" / "@WM_TITAN2_OPTION_SPIN_1000_W". Deterministically strip the
# leading "@" and trailing "_W" only (no guessing which middle segments are "meaningful" —
# that's the modelJson Value map's job). Plain labels (Mix, No Error, digit strings) pass through.
def _short(value: str) -> str:
    if value.startswith("@") and value.endswith("_W"):
        return value[1:-2]
    return value


def _device_payload(model_name: str, device_id: str) -> dict[str, Any]:
    return {
        "identifiers": [f"lgthinq_{device_id}"],
        "manufacturer": "LG",
        "model": model_name,
        "name": model_name,
    }


def _state_topic(device_id: str, *, discovery_prefix: str = "homeassistant") -> str:
    return f"{discovery_prefix}/sensor/lgthinq_{device_id}/state"


def publish_discovery(client: Any, model_name: str, device_id: str, *,
                      discovery_prefix: str = "homeassistant") -> None:
    """Announce the appliance's sensors to HA (one config message per sensor)."""
    state_topic = _state_topic(device_id, discovery_prefix=discovery_prefix)
    base = f"{discovery_prefix}/sensor/lgthinq_{device_id}"
    device = _device_payload(model_name, device_id)
    for field, object_id, name, device_class, unit in _SENSORS:
        topic = f"{base}/{object_id}/config"
        cfg: dict[str, Any] = {
            "name": name,
            "state_topic": state_topic,                       # shared JSON topic
            "value_template": "{{ value_json.%s }}" % field,  # extract this field
            "unique_id": f"lgthinq_{device_id}_{object_id}",
            "device": device,
        }
        if device_class:
            cfg["device_class"] = device_class
        if unit:
            cfg["unit_of_measurement"] = unit
        client.publish(topic, json.dumps(cfg), qos=1, retain=True)


def publish_state(client: Any, decoded: dict[str, Any], device_id: str, *,
                  discovery_prefix: str = "homeassistant") -> None:
    """Publish the current decoded state as ONE retained JSON message on the shared state
    topic. Per-sensor ``value_template``s extract individual fields from this JSON."""
    state_topic = _state_topic(device_id, discovery_prefix=discovery_prefix)
    # shorten enum values for readability; leave plain labels (Mix, No Error, digit strings).
    payload = {field: _short(str(v)) for field, v in decoded.items()}
    client.publish(state_topic, json.dumps(payload), qos=0, retain=True)

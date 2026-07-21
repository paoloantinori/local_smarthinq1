"""MQTT bridge: publish decoded appliance state to Home Assistant (M4 / TASK-064).

Isolates the paho-mqtt dependency + HA-discovery wiring from the HTTP server. The server
builds a sink (:func:`build_sink`) and passes it to ``DeviceStateStore(on_state=...)``; the
sink publishes HA discovery (once per device) + the shared JSON state on each ingest.

Contract: the sink is called from the ingest request thread, so it must be effectively
non-blocking — paho's ``publish`` is async (fire-and-forget), so this holds as long as the
broker is reachable. Any setup failure (paho missing, broker down, version mismatch) degrades
gracefully: ``build_sink`` returns ``None`` and the server runs without the bridge — the
bridge must never take the fake-cloud down with it.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional


def build_sink() -> Optional[Any]:
    """Connect to the configured MQTT broker and return an on_state sink, or None.

    Reads ``LGM_MQTT_HOST`` (required to enable), ``LGM_MQTT_PORT`` (default 1883), and
    optional ``LGM_MQTT_USER`` / ``LGM_MQTT_PASS``. Returns None (and logs) if paho-mqtt is
    missing or the broker is unreachable — so the server keeps serving appliances."""
    host = os.environ.get("LGM_MQTT_HOST")
    if not host:
        return None
    try:
        import paho.mqtt.client as mqtt  # type: ignore[import-not-found]
        from . import ha_mqtt
    except ImportError:
        sys.stderr.write("[mqtt] LGM_MQTT_HOST set but paho-mqtt not installed; MQTT off\n")
        return None
    # CallbackAPIVersion.VERSION2 needs paho >= 2.1; fall back for older paho.
    if hasattr(mqtt, "CallbackAPIVersion"):
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # type: ignore[attr-defined]
    else:
        client = mqtt.Client()  # type: ignore[call-arg]
    user, password = os.environ.get("LGM_MQTT_USER"), os.environ.get("LGM_MQTT_PASS")
    if user:
        client.username_pw_set(user, password or "")
    try:
        client.connect(host, int(os.environ.get("LGM_MQTT_PORT", "1883")))
        client.loop_start()
    except Exception as e:  # noqa: BLE001 — broker-down degrades gracefully, not fatal
        sys.stderr.write(f"[mqtt] disabled (broker connect failed: {e}); server continues\n")
        return None
    sys.stderr.write(f"[mqtt] publishing HA discovery to {host}\n")
    return _Sink(client, ha_mqtt)


class _Sink:
    """on_state sink: publish HA discovery (once per device) then the shared JSON state.
    Dedupes: only re-publishes state when it changed since the last publish for that device."""

    def __init__(self, client, ha):
        self._client = client
        self._ha = ha
        self._announced: set[str] = set()
        self._last: dict[str, dict] = {}

    def __call__(self, dev_id: str, payload: dict) -> None:
        decoded = payload.get("monData_decoded")
        if not decoded:
            return
        if dev_id not in self._announced:
            model_name = payload.get("modelName") or dev_id
            self._ha.publish_discovery(self._client, str(model_name), dev_id,
                                       list(decoded.keys()))
            self._announced.add(dev_id)
        # dedupe: skip the MQTT publish if the decoded state is unchanged since last time.
        if self._last.get(dev_id) == decoded:
            return
        self._last[dev_id] = decoded
        self._ha.publish_state(self._client, decoded, dev_id)

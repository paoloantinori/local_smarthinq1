"""MQTT bridge: publish decoded appliance state to Home Assistant (M4 / TASK-064) and
subscribe to HA command topics to drive the :47878 control channel (TASK-067).

Isolates the paho-mqtt dependency + HA-discovery wiring from the HTTP server. The server
builds a sink (:func:`build_sink`) and passes it to ``DeviceStateStore(on_state=...)``. On
each ingest the sink publishes HA state discovery (re-announced when a payload shape
introduces new fields, e.g. the TASK-068 energy summary) + the shared merged JSON state;
when ``allow_control`` is on it also publishes command discovery and subscribes to command
topics, routing received commands to the control channel.

Contract: the sink is called from the ingest request thread, so it must be effectively
non-blocking. paho's ``publish`` and ``loop_start`` are async (fire-and-forget), and the
``on_message`` callback runs on paho's network thread; it only does a dict lookup + a socket
send, both fast. The one synchronous step is the per-device first-ingest command-vocab
resolution (``registry.model_json_for`` reads the committed fixture from disk once, then is
in-memory cached); the fixtures are small, so this is a bounded one-time cost per device. Any
setup failure (paho missing, broker down, version mismatch) degrades gracefully: ``build_sink``
returns ``None`` and the server runs without the bridge, which must never take the fake-cloud
down with it.
"""
from __future__ import annotations

import os
import sys
from typing import Any, Optional

from .models import control_vocab
from .models import registry
from .models.wm_envelope import BINARY_FIELDS


def build_sink(control: Any = None, allow_control: bool = False) -> Optional[Any]:
    """Connect to the configured MQTT broker and return an on_state sink, or None.

    Reads ``LGM_MQTT_HOST`` (required to enable), ``LGM_MQTT_PORT`` (default 1883), and
    optional ``LGM_MQTT_USER`` / ``LGM_MQTT_PASS``. Returns None (and logs) if paho-mqtt is
    missing or the broker is unreachable, so the server keeps serving appliances.

    ``control`` is the :47878 ControlChannel; when ``allow_control`` is True the sink also
    publishes command entities and routes received commands to ``control.send_command``."""
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
    return _Sink(client, ha_mqtt, control, allow_control)


class _Sink:
    """on_state sink: publish the per-device MERGED state + HA discovery (re-announced
    when new field keys appear from a different payload shape). When allow_control is
    on, also publish command discovery + route received commands.

    Dedupes: only re-publishes state when it changed since the last publish for that device."""

    def __init__(self, client, ha, control: Any = None, allow_control: bool = False) -> None:
        self._client = client
        self._ha = ha
        self._control = control
        self._allow_control = allow_control
        self._last: dict[str, dict] = {}
        # per-device merged state view + the discovery field set already announced
        self._merged: dict[str, dict] = {}
        self._fields: dict[str, list[str]] = {}
        # dev_id -> {slug -> CommandEntity}, built when command discovery is published.
        self._cmd_entities: dict[str, dict[str, Any]] = {}
        if allow_control and control is not None and client is not None:
            client.on_message = self._on_message
            client.subscribe("homeassistant/+/lgthinq_+/+/cmd")
            sys.stderr.write("[mqtt] control enabled: subscribed to command topics\n")

    # keys that never belong in the published state view: raw binary envelopes
    # (single-sourced from the envelope's BINARY_FIELDS) and churn-only metadata
    _EXCLUDED_KEYS = frozenset(BINARY_FIELDS) | {
        "ts", "devId", "diagMonType", "modelName", "raw_text", "note"}

    def __call__(self, dev_id: str, payload: dict) -> None:
        # Merge per-key (last-known-wins): WM appliances push heterogeneous payload
        # shapes on the same channel (WM_STATE carries monData_decoded; WasherMonitoring
        # carries the cycle energy summary; WM_WASH_END adds the diagData summary;
        # the :47878 pump carries monData_decoded again). One merged state per device
        # keeps every HA sensor populated across shapes (TASK-068). The friendly
        # `*_decoded` dicts are SPREAD one level so each decoded field stays its own
        # sensor (State, Course, Error, ...), exactly as the pre-merge sink published;
        # a payload whose view is empty (e.g. ScomoCourse's raw_text) publishes nothing.
        view: dict = {}
        for k, v in payload.items():
            if k in self._EXCLUDED_KEYS:
                continue
            if k.endswith("_decoded") and isinstance(v, dict):
                view.update(v)
            elif k == "diagData" and isinstance(v, dict):
                # the cycle summary's INTERPRETED fields only (course, course_label);
                # the raw hex stays out. Prefixed: top-level `course` is the
                # energyMonInfo id and must not collide with the summary's.
                view.update({f"diagData_{ik}": iv for ik, iv in v.items()
                             if ik not in ("raw", "len")})
            else:
                view[k] = v
        if not view:
            return
        merged = {**self._merged.get(dev_id, {}), **view}
        self._merged[dev_id] = merged

        fields = self._fields.get(dev_id) or []
        if set(merged) - set(fields):
            fields = sorted(set(fields) | set(merged))
            self._fields[dev_id] = fields
            model_name = payload.get("modelName") or dev_id
            self._ha.publish_discovery(self._client, str(model_name), dev_id, fields)
            if "Error" in merged:
                self._ha.publish_error_alert_discovery(self._client, str(model_name), dev_id)
            self._publish_command_discovery(dev_id, str(model_name))
        # dedupe: skip the MQTT publish if the merged state is unchanged since last time.
        if self._last.get(dev_id) == merged:
            return
        self._last[dev_id] = merged
        self._ha.publish_state(self._client, merged, dev_id)

    def _publish_command_discovery(self, dev_id: str, model_name: str) -> None:
        if not (self._allow_control and self._control is not None):
            return
        model_j = registry.model_json_for(model_name)
        if model_j is None:
            return  # no modelJson → command vocab unknown; state-only for this device
        entities = control_vocab.all_entities(model_j)
        if not entities:
            return
        self._ha.publish_command_discovery(self._client, model_name, dev_id, entities)
        self._cmd_entities[dev_id] = {e.slug: e for e in entities}

    def _on_message(self, _client, _userdata, message) -> None:
        """Route a HA command payload to the control channel. Runs on paho's network thread."""
        try:
            parsed = self._ha.parse_command_topic(str(message.topic))
            if parsed is None:
                return
            dev_id, _component, slug = parsed
            entity = self._cmd_entities.get(dev_id, {}).get(slug)
            if entity is None:
                sys.stderr.write(f"[mqtt] command for unknown entity {dev_id[:8]}/{slug}; ignored\n")
                return
            payload = message.payload.decode("utf-8", "replace") if message.payload else ""
            wire = entity.to_wire(payload)
            if wire is None:
                sys.stderr.write(f"[mqtt] rejected {dev_id[:8]}/{slug} payload={payload!r} (invalid)\n")
                return
            sent = self._control.send_command(dev_id, wire.value, cmd=wire.cmd, cmd_opt=wire.cmd_opt)
            sys.stderr.write(
                f"[mqtt] command {dev_id[:8]}/{slug} payload={payload!r} → "
                f"{wire.cmd}/{wire.cmd_opt} {wire.value} sent={sent}\n")
        except Exception as e:  # noqa: BLE001: runs on the broker thread; must never break ingestion
            sys.stderr.write(f"[mqtt] on_message error (ignored): {e}\n")

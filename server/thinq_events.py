"""ThinQ Connect API event subscriber (TASK-081).

Subscribes to LG's official push-event MQTT (AWS IoT Core) and routes device state
changes into our MQTT bridge for Home Assistant. Complements the :46030 bridge decode
with cloud-side push notifications: HA sees state changes as they happen, without
intercepting the appliance's :47878 connection (the LG app stays functional).

Requires a Personal Access Token (PAT) from connect-pat.lgthinq.com, passed via
LGM_THINQ_PAT or a file path in LGM_THINQ_PAT_FILE. Runs in a background thread
with its own asyncio event loop (the SDK is asyncio-native, the HTTP server is not).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import uuid
from typing import Any, Optional

# The SDK is optional: if thinqconnect is missing or no PAT is configured, this
# module is a no-op and the server runs without cloud events.
try:
    from thinqconnect import ThinQApi, ThinQMQTTClient  # type: ignore[import-not-found] # noqa: F401
    _SDK_AVAILABLE = True
except ImportError:
    ThinQApi = None  # type: ignore[assignment,misc]
    ThinQMQTTClient = None  # type: ignore[assignment,misc]
    _SDK_AVAILABLE = False


def _load_pat() -> str:
    pat = os.environ.get("LGM_THINQ_PAT", "").strip()
    if pat:
        return pat
    path = os.environ.get("LGM_THINQ_PAT_FILE", "").strip()
    if path and os.path.isfile(path):
        return open(path).read().strip()
    return ""


def start_event_subscriber(on_event: Any, country_code: str = "IT") -> Optional[threading.Thread]:
    """Start the ThinQ Connect MQTT subscriber in a background thread.

    ``on_event(alias, model_name, device_id, payload_dict)`` is called for each
    state-change event from the cloud. Returns the thread, or None if the SDK
    is missing or no PAT is configured (the server runs without cloud events).
    """
    pat = _load_pat()
    if not _SDK_AVAILABLE:
        sys.stderr.write("[thinq] thinqconnect SDK not installed; cloud events off\n")
        return None
    if not pat:
        sys.stderr.write("[thinq] no PAT configured (LGM_THINQ_PAT or LGM_THINQ_PAT_FILE); "
                         "cloud events off\n")
        return None

    client_id = str(uuid.uuid4())

    def run() -> None:
        asyncio.run(_loop(pat, client_id, country_code, on_event))

    t = threading.Thread(target=run, daemon=True, name="thinq-events")
    t.start()
    sys.stderr.write("[thinq] cloud event subscriber started\n")
    return t


async def _loop(pat: str, client_id: str, country_code: str, on_event: Any) -> None:
    """The asyncio loop: connect to LG's MQTT, subscribe devices, dispatch events."""
    from aiohttp import ClientSession  # type: ignore[import-not-found]
    from thinqconnect import ThinQApi as _Api  # type: ignore[import-not-found]
    from thinqconnect import ThinQMQTTClient as _Mqtt  # type: ignore[import-not-found]

    async with ClientSession() as session:
        api = _Api(session, access_token=pat, country_code=country_code,
                   client_id=client_id)
        devices = await api.async_get_device_list() or []
        for d in devices:
            await api.async_post_event_subscribe(d["deviceId"])
        names = ", ".join(d["deviceInfo"]["alias"] for d in devices)
        sys.stderr.write(f"[thinq] subscribed {len(devices)} devices: {names}\n")

        def on_message(topic: str, payload: Any) -> None:
            _dispatch(devices, on_event, topic, payload)

        mqtt = _Mqtt(
            thinq_api=api,
            client_id=client_id,
            on_message_received=on_message,
        )
        await mqtt.async_init()
        if not await mqtt.async_prepare_mqtt():
            sys.stderr.write("[thinq] MQTT prepare failed; cloud events off\n")
            return
        await mqtt.async_connect_mqtt()
        sys.stderr.write("[thinq] MQTT connected; listening for events\n")
        # keep the loop alive: the MQTT client handles reconnection internally
        while True:
            await asyncio.sleep(60)


def _dispatch(devices: list[dict], on_event: Any, topic: str, payload: Any) -> None:
    """Translate a raw MQTT message into our on_event callback."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(payload.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return
    if not isinstance(payload, dict):
        return
    # match the topic's deviceId back to the device info
    for d in devices:
        dev_id = d["deviceId"]
        if dev_id in topic:
            info = d.get("deviceInfo", {})
            try:
                on_event(info.get("alias", "unknown"), info.get("modelName", ""),
                         dev_id, payload)
            except Exception as e:  # noqa: BLE001: a bad event must never kill the listener
                sys.stderr.write(f"[thinq] event handler error: {e!r}\n")
            return
    sys.stderr.write(f"[thinq] event for unknown topic: {topic[:80]}\n")

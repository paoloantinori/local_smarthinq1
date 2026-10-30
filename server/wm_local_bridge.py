#!/usr/bin/env python3
"""Tail the 47878 relay log, decode the washer's monData, publish to MQTT (retained).

Runs inside the lg_thinq_fake_cloud addon (where the relay log lives). The relay
terminates the appliance's TLS, so the log holds the DECRYPTED application records:
[4-byte BE length][JSON] with Header.x-lgedm-deviceId + Body.{Cmd,Format,Data}.
The washer's Mon snapshots (Format B64) decode via the real modelJson
(server/models/washer_wtwn3.model.json, decode validated by tests/test_model_json.py).

Output: retained JSON on lgthinq/local/washer, published only on change:
  {"State": "RUNNING", "Course": "Mix", "Remain_Time": "1h33m", ...}

This is the LG-cloud-independent completion channel (fix_2026-07-06's goal): the
monData rides the appliance's OWN 47878 channel through our transparent relay.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# repo layout keeps the decoder in server/models/; the addon deploy flattens to /data
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "models"))
from model_json import decode_with_model_json  # noqa: E402

LINE_RE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d+ (C->U|U->C) len=\d+ hex=([0-9a-f]+)\s*$")
WASHER_DEV = "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d"
TOPIC = "lgthinq/local/washer"
BLANK = {"", "unknown", "unavailable", "none", "None", "-"}
# Terminal states only (denylist): every other model-JSON state (INITIAL, PAUSE,
# RESERVE, DETECTING, DRYING, COOLDOWN, RINSEHOLD, ...) is mid-cycle for the
# dark-channel warning; an allowlist would miss them (code-review 2026-10-30).
TERMINAL_STATES = {"WM_STATE_POWER_OFF", "WM_STATE_END"}
WASHER_DARK_AFTER_S = 1800  # 30 min without a usable snapshot while mid-cycle
RELAY_ALIVE_WITHIN_S = 60


def _ts() -> str:
    """Dated UTC timestamp (Z-marked): machine-comparable with thinq_events'
    ISO stamps and docker's own timestamps for cross-reference (incident
    2026-10-30: cross-day silence was undatable from prints alone, and the
    old time-only prints skewed +1h under the pre-DST process)."""
    return time.strftime("%F %T", time.gmtime()) + "Z"


def should_warn_washer_dark(last_state, snap_age_s, line_age_s, warned):
    """The 2026-10-30 incident class as a pure predicate: relay alive (lines
    flowing), washer believed mid-cycle, but no usable Mon snapshot in ages."""
    return (not warned
            and snap_age_s > WASHER_DARK_AFTER_S
            and line_age_s < RELAY_ALIVE_WITHIN_S
            and str(last_state) not in TERMINAL_STATES)


def usable_snapshot(snap):
    """A decoded snapshot counts as channel evidence only if it carries at
    least one real value: all-blank dropout frames decode to non-None dicts
    and must NOT refresh the dark-channel clock (code-review 2026-10-30)."""
    return snap is not None and any(v not in BLANK for v in snap.values())


def records_from_hex(hexdata: str) -> list[bytes]:
    """Split one relay line into its [4-byte BE length][JSON] records."""
    data = bytes.fromhex(hexdata)
    out, i = [], 0
    while i + 4 <= len(data):
        n = int.from_bytes(data[i : i + 4], "big")
        if n <= 0 or i + 4 + n > len(data):
            break
        out.append(data[i + 4 : i + 4 + n])
        i += 4 + n
    return out


def washer_snapshot(line: str, mj: dict, device: str) -> dict | None:
    """Return the decoded washer state dict from one relay log line, or None."""
    m = LINE_RE.match(line)
    if not m or m.group(1) != "C->U":  # snapshots ride appliance->LG
        return None
    snap = None
    for rec in records_from_hex(m.group(2)):
        try:
            msg = json.loads(rec)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        hdr = msg.get("Header", {})
        body = msg.get("Body", {})
        if hdr.get("x-lgedm-deviceId") != device:
            continue
        if body.get("Format") != "B64" or not body.get("Data"):
            continue
        try:
            snap = decode_with_model_json(base64.b64decode(body["Data"]), mj)
        except Exception as exc:  # noqa: BLE001 - malformed snapshot: skip, keep last
            print(f"{_ts()} decode error: {exc}", flush=True)
    return snap


def publish(client, payload: dict) -> None:
    client.publish(TOPIC, json.dumps(payload, ensure_ascii=False), retain=True, qos=0)


# NOTA: nessuna MQTT discovery (homeassistant/...): collideva con l'entita' REST
# (code-review 25/10, finding 4) e la retained e' stata pulita dal broker.
# TOPIC (fuori da homeassistant/) resta come canale di debug retained.


def push_ha(payload: dict) -> None:
    """Push the state straight to the HA REST API.

    REST is the ONLY entity writer: the MQTT discovery config was dropped
    (code-review 25/10) because MQTT-discovered + REST-written entities on the
    same object_id collide into divergent _2 duplicates. The retained state on
    TOPIC stays as a debug channel (not under homeassistant/, no discovery).
    """
    auth = "/data/ha_bridge_auth.json"
    if not os.path.exists(auth):
        return
    try:
        import aiohttp

        async def _post():
            with open(auth, encoding="utf-8") as fh:
                cfg = json.load(fh)
            state = str(payload.get("State", "unknown")).replace("WM_STATE_", "")
            async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {cfg['token']}"}) as s:
                async with s.post(
                    f"{cfg['url']}/api/states/sensor.lavatrice_stato_locale",
                    json={"state": state,
                          "attributes": {**payload, "friendly_name": "Lavatrice Stato Locale"}},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status >= 400:
                        print(f"ha push {resp.status}", flush=True)

        asyncio.run(_post())
    except Exception as exc:  # noqa: BLE001 - HA unreachable: MQTT retained still carries it
        print(f"ha push error: {exc!r}", flush=True)


def publish_discovery(client) -> None:
    """HA MQTT discovery: the sensor appears without touching configuration.yaml."""
    cfg = {
        "name": "Lavatrice Stato Locale",
        "unique_id": "lavatrice_stato_locale",
        "state_topic": TOPIC,
        "value_template": "{{ value_json.State | replace('WM_STATE_', '') }}",
        "json_attributes_topic": TOPIC,
        "device": {
            "identifiers": [WASHER_DEV],
            "name": "Lavatrice (locale)",
            "manufacturer": "LG",
            "model": "WTWN3",
        },
    }
    client.publish(
        "homeassistant/sensor/lavatrice_stato_locale/config",
        json.dumps(cfg, ensure_ascii=False),
        retain=True,
        qos=0,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--log", default="/data/relay-47878-1025.log")
    p.add_argument("--model", default="/data/washer_wtwn3.model.json")
    p.add_argument("--device", default="82c2ebc0-0e9f-11f5-82b4-0051eda91d3d")
    p.add_argument("--mqtt-host", default="127.0.0.1")
    p.add_argument("--mqtt-port", type=int, default=1883)
    p.add_argument("--mqtt-user", default="mqtt")
    p.add_argument("--mqtt-pass", default="")
    args = p.parse_args()

    # Default MQTT credentials from the addon's own options file (no secrets on argv).
    opts_path = "/data/options.json"
    if not args.mqtt_pass and os.path.exists(opts_path):
        with open(opts_path, encoding="utf-8") as fh:
            opts = json.load(fh)
        args.mqtt_user = opts.get("mqtt_user", args.mqtt_user)
        args.mqtt_pass = opts.get("mqtt_password", "")

    with open(args.model, encoding="utf-8") as fh:
        mj = json.load(fh)

    import paho.mqtt.client as mqttc

    cli = mqttc.Client(mqttc.CallbackAPIVersion.VERSION1)
    if args.mqtt_user:
        cli.username_pw_set(args.mqtt_user, args.mqtt_pass)
    cli.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    cli.loop_start()

    # Replay the tail first so a bridge restart restores the last known state.
    # Deep tail: the dryer's keepalive chatter can push the washer's sparse
    # records out of a shallow window.
    with open(args.log, errors="replace") as fh:
        tail = fh.readlines()[-3000:]
    last: dict = {}
    for line in tail:
        snap = washer_snapshot(line, mj, args.device)
        if snap:
            last = snap
    if last and any(v not in BLANK for v in last.values()):
        publish(cli, last)
        push_ha(last)
        print(f"initial publish: {last}", flush=True)

    last_beat = time.time()
    last_line = time.time()
    last_snap = time.time()
    quiet_warned = False
    washer_quiet_warned = False
    with open(args.log, errors="replace") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:  # rotation or quiet
                time.sleep(1)
                now = time.time()
                # channel-silence observability (incident 2026-10-30): a relay
                # gone quiet is indistinguishable from a dead bridge unless the
                # bridge says so itself; one throttled line per quiet period
                if not quiet_warned and now - last_line > 900:  # 15 min
                    quiet_warned = True
                    print(f"{_ts()} quiet: no relay lines for "
                          f"{int(now - last_line)}s "
                          f"(last publish {int(now - last_beat)}s ago; "
                          f"appliance offline or relay down)", flush=True)
                # washer went dark MID-CYCLE: the 2026-10-30 class. Terminal
                # states are excluded because a powered-off washer is
                # legitimately silent (PROTOCOL.md: no idle stream, while the
                # dryer keeps the relay lines flowing for days).
                if should_warn_washer_dark(last.get("State"), now - last_snap,
                                           now - last_line, washer_quiet_warned):
                    washer_quiet_warned = True
                    print(f"{_ts()} washer silent mid-cycle: no usable washer "
                          f"Mon snapshot for {int(now - last_snap)}s while relay "
                          f"lines flow and last state is {last.get('State')} "
                          f"(last publish {int(now - last_beat)}s ago)", flush=True)
                # heartbeat: republish the last known state every 5 min so HA
                # never goes stale across broker or HA restarts
                if last and time.time() - last_beat > 300:
                    publish(cli, last)
                    push_ha(last)
                    last_beat = time.time()
                try:
                    if os.path.getsize(args.log) < fh.tell():
                        fh.seek(0)
                except OSError:
                    time.sleep(3)
                continue
            snap = washer_snapshot(line, mj, args.device)
            last_line = time.time()
            quiet_warned = False
            if usable_snapshot(snap):
                last_snap = time.time()
                washer_quiet_warned = False
            # blank-snapshot guard: an all-blank dropout frame must not
            # overwrite the last known good state (kept for the heartbeat)
            if snap and snap != last and any(v not in BLANK for v in snap.values()):
                last = snap
                last_beat = time.time()
                publish(cli, last)
                push_ha(last)
                print(f"{_ts()} publish: {last}", flush=True)


if __name__ == "__main__":
    main()

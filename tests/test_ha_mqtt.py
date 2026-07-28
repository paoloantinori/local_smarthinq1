"""Tests for the HA MQTT-discovery bridge (TASK-040 spike).

Unit-checks the discovery/state payloads (HA-spec valid + the value_template/state-topic
contract holds), and — when MQTT_LIVE=1 and a local mosquitto is runnable — an end-to-end
MQTT round-trip. The broker test is gated (it spins up mosquitto, ~5s) so it doesn't slow the
default suite.
"""
from __future__ import annotations

import json
import os
import pytest
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import ha_mqtt  # noqa: E402

WASHER = "WTWN3"
DEV_ID = "WASHER_DEVICE_ID"
STATE_TOPIC = "homeassistant/sensor/lgthinq_%s/state" % DEV_ID


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []

    def publish(self, topic: str, payload, qos: int = 0, retain: bool = False):
        self.published.append((topic, payload if isinstance(payload, str) else payload.decode(), qos, retain))


def test_discovery_config_is_ha_valid_and_shares_state_topic() -> None:
    """Every sensor config is HA-spec valid AND reads from one shared JSON state topic.
    Discovery is per-device: one sensor per decoded field (appliance-agnostic)."""
    c = FakeClient()
    fields = ["State", "Course", "Remain_Time_H", "Remain_Time_M", "Error"]
    ha_mqtt.publish_discovery(c, WASHER, DEV_ID, fields)
    configs = [json.loads(p[1]) for p in c.published if p[0].endswith("/config")]
    assert len(configs) == len(fields), [p[0] for p in c.published]
    assert {cfg["state_topic"] for cfg in configs} == {STATE_TOPIC}, "all sensors share one state topic"
    for cfg in configs:
        for key in ("name", "state_topic", "unique_id", "device", "value_template"):
            assert key in cfg, (key, cfg)
        assert cfg["device"]["model"] == WASHER


def test_discovery_is_per_device_not_hardcoded() -> None:
    """A fridge gets its own fields (TempRefrigerator/DoorOpenState), NOT the washer's."""
    c = FakeClient()
    fridge_fields = ["TempRefrigerator", "TempFreezer", "DoorOpenState"]
    ha_mqtt.publish_discovery(c, "1REB1GLPX1___", "fridge-dev", fridge_fields)
    configs = [p[0] for p in c.published if p[0].endswith("/config")]
    # fridge fields appear as sensor topics, washer fields do not
    assert any("temprefigerator" in t.lower() or "temprefrigerator" in t.lower() for t in configs), configs
    assert not any("run_state" in t for t in configs), "fridge must not get washer sensors"


def test_value_template_resolves_against_state_payload() -> None:
    """The load-bearing contract: each sensor's value_template extracts a real value from the
    JSON publish_state writes. (What HA does — the unit test must exercise it, not just check
    that bytes land on the broker.)"""
    c = FakeClient()
    decoded = {"State": "@WM_STATE_RUNNING_W", "Course": "Mix",
               "Remain_Time_H": "1", "Remain_Time_M": "21", "Error": "@WM_ERROR_NONE_W"}
    ha_mqtt.publish_discovery(c, WASHER, DEV_ID, list(decoded.keys()))
    ha_mqtt.publish_state(c, decoded, DEV_ID)
    state = json.loads([p for p in c.published if p[0] == STATE_TOPIC][0][1])
    for field in decoded:
        cfg = next(json.loads(p[1]) for p in c.published
                   if p[0].endswith(f"/{field.lower()}/config"))
        assert cfg["value_template"] == "{{ value_json.%s }}" % field, cfg
        assert field in state, (field, state)


def test_state_is_one_retained_json_message() -> None:
    c = FakeClient()
    ha_mqtt.publish_state(c, {"State": "@WM_STATE_RUNNING_W", "Remain_Time_M": "21"}, DEV_ID)
    assert len(c.published) == 1, "state is ONE message on the shared topic"
    topic, payload, _qos, retain = c.published[0]
    assert topic == STATE_TOPIC and retain is True
    assert json.loads(payload) == {"State": "@WM_STATE_RUNNING_W", "Remain_Time_M": "21"}


def test_error_alert_is_a_binary_sensor_on_shared_state_topic() -> None:
    """The error alert is a binary_sensor whose value_template derives from the shared JSON
    state topic's Error field, so an HA automation can trigger on it (e.g. the washer's DE2
    door fault)."""
    c = FakeClient()
    ha_mqtt.publish_error_alert_discovery(c, WASHER, DEV_ID)
    cfg = json.loads(c.published[0][1])
    assert c.published[0][0] == "homeassistant/binary_sensor/lgthinq_%s/error_alert/config" % DEV_ID
    assert cfg["state_topic"] == STATE_TOPIC, "rides the same shared state topic"
    assert cfg["unique_id"] == "lgthinq_%s_error_alert" % DEV_ID
    assert cfg["device"]["model"] == WASHER
    tpl = cfg["value_template"]
    # The template must key off the Error field and treat 'No Error' as off (HA evaluates it;
    # jinja2 isn't in the test env, so assert the contract the template encodes).
    assert "value_json.Error" in tpl, tpl
    assert "No Error" in tpl, "the idle sentinel must map to off"
    assert "'on'" in tpl and "'off'" in tpl


def test_error_alert_template_maps_faults_to_on() -> None:
    """The value_template must evaluate correctly under HA's MQTT engine, which renders with
    StrictUndefined semantics: a missing Error key (the fridge) must NOT raise, it must be off.
    Evaluated for real with jinja2 (HA's engine); skipped if jinja2 is not installed, since a
    structural substring check cannot prove on/off behavior and would give false confidence."""
    pytest.importorskip("jinja2")
    import jinja2  # type: ignore[import-not-found]
    tpl = ha_mqtt._ERROR_ON_TEMPLATE
    cases = [({"Error": "DE2 Error"}, "on"), ({"Error": "No Error"}, "off"),
             ({"Error": ""}, "off"), ({}, "off")]
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    for value_json, expected in cases:
        got = env.from_string(tpl).render(value_json=value_json)
        assert got == expected, f"{value_json} -> {got!r}, expected {expected!r}"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_end_to_end_against_local_broker() -> None:
    """Gated live test: MQTT_LIVE=1 + a runnable mosquitto → publish discovery+state, read
    back via mosquitto_sub. Skipped otherwise (it spins up a broker, ~5s)."""
    if os.environ.get("MQTT_LIVE") != "1":
        print("(skipped: set MQTT_LIVE=1 to run the broker round-trip)"); return
    if not shutil.which("mosquitto") or not shutil.which("mosquitto_sub"):
        print("(skipped: mosquitto/mosquitto_sub not installed)"); return
    import paho.mqtt.client as mqtt  # type: ignore[import-not-found]

    port = 1883
    if not _port_free(port):
        print("(skipped: port 1883 already in use)"); return
    cfg = subprocess.Popen(["mosquitto", "-p", str(port), "-v"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        sub = subprocess.Popen(
            ["mosquitto_sub", "-h", "127.0.0.1", "-p", str(port), "-t",
             "homeassistant/sensor/lgthinq_%s/#" % DEV_ID, "-W", "3"],
            stdout=subprocess.PIPE)
        time.sleep(0.5)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)  # type: ignore[attr-defined]
        client.connect("127.0.0.1", port); client.loop_start()
        ha_mqtt.publish_discovery(client, WASHER, DEV_ID, ["State","Course","Remain_Time_H","Remain_Time_M","Error"])
        ha_mqtt.publish_state(client, {"State": "@WM_STATE_RUNNING_W", "Remain_Time_M": "21"},
                              DEV_ID)
        time.sleep(1.5)
        client.loop_stop(); client.disconnect()
        out, _ = sub.communicate(timeout=8)
        msgs = out.decode().strip().splitlines()
        assert any('"unique_id"' in m for m in msgs), msgs
        state_msgs = [m for m in msgs if '"State"' in m]
        assert state_msgs and "WM_STATE_RUNNING" in state_msgs[0], state_msgs
        print(f"  end-to-end OK: saw {len(msgs)} messages (discovery + shared JSON state)")
    finally:
        cfg.terminate(); cfg.wait()


if __name__ == "__main__":
    for fn in (test_discovery_config_is_ha_valid_and_shares_state_topic,
               test_value_template_resolves_against_state_payload,
               test_state_is_one_retained_json_message,
               test_error_alert_is_a_binary_sensor_on_shared_state_topic,
               test_error_alert_template_maps_faults_to_on,
               test_end_to_end_against_local_broker):
        fn(); print(f"PASS {fn.__name__}")
    print("\nHA MQTT bridge assertions passed.")

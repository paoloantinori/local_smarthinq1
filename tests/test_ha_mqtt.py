"""Tests for the HA MQTT-discovery bridge (TASK-040 spike).

Unit-checks the discovery/state payloads (HA-spec valid + the value_template/state-topic
contract holds), and — when a local mosquitto broker is runnable — an end-to-end MQTT
round-trip via mosquitto_sub.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import ha_mqtt  # noqa: E402

WASHER = "WTWN3"
DEV_ID = "d9bf16c0-c7c0-11ea-bec4-0051eda91d3d"
STATE_TOPIC = "homeassistant/sensor/lgthinq_%s/state" % DEV_ID


class FakeClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int, bool]] = []

    def publish(self, topic: str, payload, qos: int = 0, retain: bool = False):
        self.published.append((topic, payload if isinstance(payload, str) else payload.decode(), qos, retain))


def test_short_reduces_enums_only() -> None:
    # deterministic: strip leading @ and trailing _W; no guessing middle segments
    assert ha_mqtt._short("@WM_STATE_RUNNING_W") == "WM_STATE_RUNNING"
    assert ha_mqtt._short("@WM_TITAN2_OPTION_SPIN_1000_W") == "WM_TITAN2_OPTION_SPIN_1000"
    assert ha_mqtt._short("@WM_ERROR_NONE_W") == "WM_ERROR_NONE"
    assert ha_mqtt._short("No Error") == "No Error"      # plain label passes through
    assert ha_mqtt._short("Quick Dry") == "Quick Dry"
    assert ha_mqtt._short("21") == "21"                  # digit string untouched


def test_discovery_emits_one_config_per_sensor() -> None:
    c = FakeClient()
    ha_mqtt.publish_discovery(c, WASHER, DEV_ID)
    configs = [p for p in c.published if p[0].endswith("/config")]
    assert len(configs) == len(ha_mqtt._SENSORS), [p[0] for p in configs]


def test_discovery_payload_and_contract() -> None:
    """Config is HA-spec valid AND every sensor reads from the same shared JSON state topic."""
    c = FakeClient()
    ha_mqtt.publish_discovery(c, WASHER, DEV_ID)
    configs = [json.loads(p[1]) for p in c.published if p[0].endswith("/config")]
    state_topics = {cfg["state_topic"] for cfg in configs}
    assert len(state_topics) == 1, "all sensors must share one state topic"
    assert state_topics.pop() == STATE_TOPIC
    for cfg in configs:
        for key in ("name", "state_topic", "unique_id", "device", "value_template"):
            assert key in cfg, (key, cfg)
        assert cfg["device"]["model"] == WASHER


def test_value_template_resolves_against_state_payload() -> None:
    """The load-bearing contract: each sensor's value_template must extract a real value
    from the JSON publish_state writes. (This is what HA does; the unit tests must exercise
    it so the bridge is proven for real HA, not just that bytes land on the broker.)"""
    import re as _re
    c = FakeClient()
    decoded = {"State": "@WM_STATE_RUNNING_W", "Course": "Mix",
               "Remain_Time_H": "1", "Remain_Time_M": "21", "Error": "@WM_ERROR_NONE_W"}
    ha_mqtt.publish_discovery(c, WASHER, DEV_ID)
    ha_mqtt.publish_state(c, decoded, DEV_ID)
    state_payload = json.loads([p for p in c.published if p[0] == STATE_TOPIC][0][1])
    # resolve each sensor's {{ value_json.<Field> }} against the state JSON
    for p in c.published:
        if not p[0].endswith("/config"):
            continue
        cfg = json.loads(p[1])
        m = _re.search(r"value_json\.(\w+)", cfg["value_template"])
        assert m, cfg["value_template"]
        field = m.group(1)
        assert field in state_payload, (field, state_payload)
        assert state_payload[field] != "", (field, state_payload)


def test_state_payload_is_shared_json_with_short_enums() -> None:
    c = FakeClient()
    ha_mqtt.publish_state(c, {"State": "@WM_STATE_RUNNING_W", "Remain_Time_M": "21",
                             "Error": "@WM_ERROR_NONE_W"}, DEV_ID)
    assert len(c.published) == 1, "state is ONE message on the shared topic"
    topic, payload, _qos, retain = c.published[0]
    assert topic == STATE_TOPIC and retain is True
    state = json.loads(payload)
    assert state["State"] == "WM_STATE_RUNNING"
    assert state["Remain_Time_M"] == "21"
    assert state["Error"] == "WM_ERROR_NONE"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def test_end_to_end_against_local_broker() -> None:
    """If a local mosquitto is runnable, publish discovery+state and read both back via
    mosquitto_sub. Skipped (not failed) if no broker is available — this is a spike."""
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
        ha_mqtt.publish_discovery(client, WASHER, DEV_ID)
        ha_mqtt.publish_state(client, {"State": "@WM_STATE_RUNNING_W", "Remain_Time_M": "21"},
                              DEV_ID)
        time.sleep(1.5)
        client.loop_stop(); client.disconnect()
        out, _ = sub.communicate(timeout=8)
        msgs = out.decode().strip().splitlines()
        assert any('"unique_id"' in m for m in msgs), msgs       # discovery configs landed
        # the shared state JSON landed and carries the stripped enum
        state_msgs = [m for m in msgs if '"State"' in m]
        assert state_msgs and "WM_STATE_RUNNING" in state_msgs[0], state_msgs
        print(f"  end-to-end OK: saw {len(msgs)} messages (discovery + shared JSON state)")
    finally:
        cfg.terminate(); cfg.wait()


if __name__ == "__main__":
    for fn in (test_short_reduces_enums_only, test_discovery_emits_one_config_per_sensor,
               test_discovery_payload_and_contract, test_value_template_resolves_against_state_payload,
               test_state_payload_is_shared_json_with_short_enums,
               test_end_to_end_against_local_broker):
        fn(); print(f"PASS {fn.__name__}")
    print("\nHA MQTT bridge assertions passed.")

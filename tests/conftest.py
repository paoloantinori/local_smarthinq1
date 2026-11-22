"""Shared stubs for the smarthinq_mitm test suite (hoisted 2026-11-22: the
FakeMsg stand-in was duplicated between test_mqtt_bridge and test_mqtt_command)."""


class FakeMsg:
    """Minimal paho MQTTMessage stand-in (topic, payload, retain)."""

    def __init__(self, topic: str, payload: bytes, retain: bool = False) -> None:
        self.topic = topic
        self.payload = payload
        self.retain = retain

"""TASK-89: client-scoped pushes carry the deviceId in the payload, not the topic."""
from server.thinq_events import _dispatch, _find_device_id

DEVICES = [
    {"deviceId": "dev-hashed-1",
     "deviceInfo": {"alias": "Lavatrice", "modelName": "F0L9CWPW1"}},
    {"deviceId": "dev-hashed-2",
     "deviceInfo": {"alias": "Asciugatrice", "modelName": "RC90U2WW"}},
]


def test_find_device_id_top_level() -> None:
    assert _find_device_id({"deviceId": "x", "other": 1}) == "x"


def test_find_device_id_nested_envelope() -> None:
    envelope = {"event": {"type": "PUSH", "deviceId": "nested-id"}, "ts": 1}
    assert _find_device_id(envelope) == "nested-id"


def test_find_device_id_absent() -> None:
    assert _find_device_id({"foo": {"bar": [1, 2]}}) is None
    assert _find_device_id("string") is None


def test_dispatch_device_topic_still_works() -> None:
    seen = []
    _dispatch(DEVICES, lambda a, m, d, p: seen.append((a, d)), "dev-hashed-1/anything",
              {"state": "ON"})
    assert seen == [("Lavatrice", "dev-hashed-1")]


def test_dispatch_client_topic_resolved_from_payload() -> None:
    """The 2026-10-03 regression: app/clients/<uuid>/push events were dropped."""
    seen = []
    _dispatch(DEVICES, lambda a, m, d, p: seen.append((a, d)),
              "app/clients/a61e1b97-4119-4cbd-a24b-43017ca47a86/push",
              {"eventType": "PUSH", "deviceId": "dev-hashed-2", "data": {"state": "OFF"}})
    assert seen == [("Asciugatrice", "dev-hashed-2")]


def test_dispatch_client_topic_nested_device_id() -> None:
    seen = []
    _dispatch(DEVICES, lambda a, m, d, p: seen.append((a, d)),
              "app/clients/some-uuid/push",
              {"event": {"deviceId": "dev-hashed-1"}})
    assert seen == [("Lavatrice", "dev-hashed-1")]


def test_dispatch_unknown_payload_not_dispatched(capsys) -> None:
    seen = []
    _dispatch(DEVICES, lambda a, m, d, p: seen.append(a),
              "app/clients/some-uuid/push", {"nothing": "here"})
    assert seen == []
    assert "payload=" in capsys.readouterr().err


def test_dispatch_bytes_payload() -> None:
    seen = []
    _dispatch(DEVICES, lambda a, m, d, p: seen.append(a), "app/clients/x/push",
              b'{"deviceId": "dev-hashed-1"}')
    assert seen == ["Lavatrice"]

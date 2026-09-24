"""Tests for the :47878 control channel server (TASK-031).

Validates the msgpack framing (encode/decode round-trip), the message vocabulary
(DevInfo/Alive/Mon/Control acks), and the command-sending API (safety-gated).
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import control_channel as cc  # noqa: E402


# ── msgpack encoding / decoding round-trip ──────────────────────────────────────────────────


def test_msgpack_roundtrip_simple_ack() -> None:
    """A ReturnCode ack encodes + decodes correctly."""
    msg = cc.make_message("FRIDGE_DEV", "cmd-1", ReturnCode="0000")
    encoded = cc.encode_message(msg)
    # Wire format since 2026-09-24: JSON inside ONE msgpack string (the format
    # the appliances actually speak, cf. flows/fridge-47878): first byte is a
    # str prefix (fixstr/str8/str16), and the payload parses as JSON.
    assert encoded[0] in range(0xA0, 0xC0) or encoded[0] in (0xD9, 0xDA), \
        f"expected msgpack str prefix, got 0x{encoded[0]:02x}"
    assert b'{"Header"' in encoded[:16]
    msgs, remainder = cc.decode_messages(encoded)
    assert len(msgs) == 1 and not remainder
    decoded = msgs[0]
    assert decoded["Body"]["ReturnCode"] == "0000"
    assert decoded["Body"]["CmdWId"] == "cmd-1"


def test_msgpack_roundtrip_control_set() -> None:
    """A Control/Set command (with nested Value map) encodes + decodes correctly."""
    msg = cc.make_message(
        "FRIDGE_DEV", "n-cmd-42",
        Cmd="Control", CmdOpt="Set",
        Value={"RETM": "4", "REFT": "1", "REIP": "1", "REEF": "0"},
        Data="",
    )
    encoded = cc.encode_message(msg)
    msgs, _ = cc.decode_messages(encoded)
    assert len(msgs) == 1
    d = msgs[0]
    assert d["Body"]["Cmd"] == "Control"
    assert d["Body"]["Value"]["RETM"] == "4"


def test_decode_multiple_messages() -> None:
    """Two concatenated messages decode into a list."""
    m1 = cc.encode_message(cc.make_message("D1", "c1", ReturnCode="0000"))
    m2 = cc.encode_message(cc.make_message("D2", "c2", Cmd="Alive"))
    msgs, remainder = cc.decode_messages(m1 + m2)
    assert len(msgs) == 2 and not remainder
    assert msgs[0]["Body"]["CmdWId"] == "c1"
    assert msgs[1]["Body"]["CmdWId"] == "c2"


def test_decode_partial_leaves_remainder() -> None:
    """A truncated message leaves the partial bytes as remainder."""
    msg = cc.encode_message(cc.make_message("D", "c", ReturnCode="0000"))
    msgs, remainder = cc.decode_messages(msg[:5])
    assert msgs == []  # not enough data yet
    assert len(remainder) == 5


# ── message handling ────────────────────────────────────────────────────────────────────────


class _FakeSock:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        return b""

    def close(self) -> None:
        pass


def test_devinfo_handler_responds_with_ack() -> None:
    sock = _FakeSock()
    msg = cc.make_message("FRIDGE_DEV", "cmd-1", Cmd="DevInfo",
                          Format="B64", Data="RndWZXI9dGVzdA==")
    resp = cc.handle_incoming("FRIDGE_DEV", msg, sock)
    assert resp is not None
    msgs, _ = cc.decode_messages(resp)
    assert msgs[0]["Body"]["ReturnCode"] == "0000"


def test_alive_handler_responds_with_ack() -> None:
    msg = cc.make_message("FRIDGE_DEV", "cmd-2", Cmd="Alive")
    resp = cc.handle_incoming("FRIDGE_DEV", msg, _FakeSock())
    assert resp is not None
    msgs, _ = cc.decode_messages(resp)
    assert msgs[0]["Body"]["ReturnCode"] == "0000"


def test_mon_start_responds_with_ack() -> None:
    msg = cc.make_message("FRIDGE_DEV", "n-1", Cmd="Mon", CmdOpt="Start")
    resp = cc.handle_incoming("FRIDGE_DEV", msg, _FakeSock())
    assert resp is not None
    msgs, _ = cc.decode_messages(resp)
    assert msgs[0]["Body"]["ReturnCode"] == "0000"


def test_appliance_ack_returns_none() -> None:
    """When the appliance acks our Control/Set, we don't respond."""
    msg = cc.make_message("FRIDGE_DEV", "n-cmd-1", ReturnCode="0000")
    resp = cc.handle_incoming("FRIDGE_DEV", msg, _FakeSock())
    assert resp is None


def test_control_command_safety_gate() -> None:
    """send_command is rejected when allow_control is off (the default)."""
    # ensure it's off
    cc.ALLOW_CONTROL = False
    ch = cc.ControlChannel()
    sock = _FakeSock()
    ch.register("FRIDGE_DEV", sock)
    result = ch.send_command("FRIDGE_DEV", {"RETM": "4"})
    assert result is False
    assert sock.sent == []  # nothing was sent


def test_control_command_sent_when_enabled() -> None:
    """send_command works when allow_control is on."""
    cc.ALLOW_CONTROL = True
    try:
        ch = cc.ControlChannel()
        sock = _FakeSock()
        ch.register("FRIDGE_DEV", sock)
        result = ch.send_command("FRIDGE_DEV", {"RETM": "4"})
        assert result is True
        assert len(sock.sent) == 1
        msgs, _ = cc.decode_messages(sock.sent[0])
        assert msgs[0]["Body"]["Cmd"] == "Control"
        assert msgs[0]["Body"]["Value"]["RETM"] == "4"
    finally:
        cc.ALLOW_CONTROL = False  # always reset


if __name__ == "__main__":
    for fn in (
        test_msgpack_roundtrip_simple_ack,
        test_msgpack_roundtrip_control_set,
        test_decode_multiple_messages,
        test_decode_partial_leaves_remainder,
        test_devinfo_handler_responds_with_ack,
        test_alive_handler_responds_with_ack,
        test_mon_start_responds_with_ack,
        test_appliance_ack_returns_none,
        test_control_command_safety_gate,
        test_control_command_sent_when_enabled,
    ):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll control channel assertions passed.")

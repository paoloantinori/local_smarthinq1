"""WM-family :47878 server tests (TASK-078): [4B len][JSON] framing, first-byte
family dispatch, the WM pump ingest into the state store, and the never-park
guarantee on garbage frames.

The fridge's raw-msgpack path is covered by tests/test_control_channel.py.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import control_channel as cc  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402


# ── framing ────────────────────────────────────────────────────────────────────────────────

def test_len4_roundtrip() -> None:
    msg = cc.make_message("WM_DEV", "n-1", Cmd="Alive")
    enc = cc.encode_message_len4(msg)
    assert enc[:4] == struct.pack("!I", len(enc) - 4)
    msgs, remainder = cc.decode_messages_len4(enc)
    assert len(msgs) == 1 and not remainder
    assert msgs[0]["Body"]["Cmd"] == "Alive"


def test_len4_multiple_and_truncated_tail() -> None:
    m1 = cc.encode_message_len4(cc.make_message("D", "c1", ReturnCode="0000"))
    m2 = cc.encode_message_len4(cc.make_message("D", "c2", Cmd="Alive"))
    msgs, remainder = cc.decode_messages_len4(m1 + m2 + m1[:3])
    assert [m["Body"]["CmdWId"] for m in msgs] == ["c1", "c2"]
    assert remainder == m1[:3]  # the incomplete tail waits for more data


def test_len4_garbage_is_dropped_never_parked() -> None:
    """TASK-078 acceptance: unparsable frames are logged, never silently parked
    (the 2026-09-25 outage was a reader wedging forever on unparseable bytes).

    Two guarantees, per garbage kind: a complete-but-not-JSON frame is skipped
    cleanly (framing intact, following good frames parse); an insane declared
    length desyncs the stream, so the reader resync-scans forward, parses what it
    can and RETAINS the rest in the remainder: it never hangs and never drops
    bytes silently."""
    good1 = cc.encode_message_len4(cc.make_message("D", "c1", Cmd="Alive"))
    good2 = cc.encode_message_len4(cc.make_message("D", "c2", Cmd="Alive"))
    not_json = struct.pack("!I", 5) + b"not{}"

    msgs, remainder = cc.decode_messages_len4(good1 + not_json + good2)
    assert [m["Body"]["CmdWId"] for m in msgs] == ["c1", "c2"]
    assert not remainder

    insane = struct.pack("!I", 99 << 20) + b"{}"
    msgs, remainder = cc.decode_messages_len4(good1 + insane + good2)
    assert [m["Body"]["CmdWId"] for m in msgs] == ["c1"], "pre-garbage frames must parse"
    assert remainder, "post-desync bytes are retained, not silently dropped"


# ── protocol layer with the WM codec ──────────────────────────────────────────────────────

def test_wm_ack_uses_len4_encoder() -> None:
    msg = cc.make_message("WM_DEV", "w-1", Cmd="DevInfo", Format="B64", Data="eA==")
    resp = cc.handle_incoming("WM_DEV", msg, encode=cc.encode_message_len4)
    assert resp is not None
    msgs, _ = cc.decode_messages_len4(resp)
    assert msgs[0]["Body"]["ReturnCode"] == "0000"


def test_wm_pump_ingests_into_state_store() -> None:
    """A Format=B64 Data record feeds DeviceStateStore.ingest_mondata (monData)."""
    with tempfile.TemporaryDirectory() as tmp:
        store = DeviceStateStore(tmp)
        store.model_by_devid["WM_DEV"] = "WTWN3"  # normally learned from 46030 reports
        cc.set_state_store(store)
        try:
            blob = bytes.fromhex("01032b032b0100030a04010000000000000003000031006400000400")
            data = base64.b64encode(blob).decode()
            msg = cc.make_message("WM_DEV", "n-p1", ReturnCode="0000", Format="B64", Data=data)
            resp = cc.handle_incoming("WM_DEV", msg, encode=cc.encode_message_len4)
            assert resp is None  # snapshots are ingested, not answered
            latest = store.latest.get("WM_DEV")
            assert latest is not None, "the pump snapshot must land in the store"
            assert latest["diagMonType"] == "PUMP_47878"
            assert "monData_decoded" in latest
        finally:
            cc.set_state_store(None)


def test_wm_pump_with_unmapped_device_is_skipped_not_fatal() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = DeviceStateStore(tmp)  # no model_by_devid entry
        cc.set_state_store(store)
        try:
            msg = cc.make_message("UNKNOWN_DEV", "n-p2", ReturnCode="0000",
                                  Format="B64", Data=base64.b64encode(b"\x01\x02").decode())
            resp = cc.handle_incoming("UNKNOWN_DEV", msg, encode=cc.encode_message_len4)
            assert resp is None and "UNKNOWN_DEV" not in store.latest
        finally:
            cc.set_state_store(None)


# ── end-to-end: one TLS WM client against the live handler ────────────────────────────────

def _make_cert(tmpdir: str) -> tuple[str, str]:
    cert, key = os.path.join(tmpdir, "t.pem"), os.path.join(tmpdir, "t.key")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t",
         "-keyout", key, "-out", cert, "-days", "1"],
        check=True, capture_output=True)
    return cert, key


def test_tls_wm_client_full_exchange() -> None:
    """A fake WM appliance (TLS + len4 framing) connects to the control server:
    DevInfo gets a len4 ack + Mon Start, and a pump snapshot reaches the store."""
    with tempfile.TemporaryDirectory() as tmp:
        # snapshot the module globals this test must mutate; restore in finally so
        # no other test inherits a TLS context pointing at a deleted tempdir cert
        saved_cert, saved_key, saved_ctx = cc.CERT, cc.KEY, cc._TLS_CTX
        cert, key = _make_cert(tmp)
        cc.CERT, cc.KEY = cert, key
        cc._TLS_CTX = None  # rebuild the lazy context with the test cert
        store = DeviceStateStore(tmp)
        store.model_by_devid["WM_E2E"] = "WTWN3"
        cc.set_state_store(store)

        server = cc.start_control_server(0)
        assert server is not None
        port = server.server_address[1]
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            raw = socket.create_connection(("127.0.0.1", port), timeout=5)
            tls = ctx.wrap_socket(raw, server_hostname="wm")

            tls.sendall(cc.encode_message_len4(
                cc.make_message("WM_E2E", "e-1", Cmd="DevInfo", Format="B64", Data="eA==")))
            time.sleep(0.3)
            # the reply (ack) and the auto Mon Start both arrive as len4 frames
            buf = b""
            tls.settimeout(2)
            try:
                while len(cc.decode_messages_len4(buf)[0]) < 2:
                    buf += tls.recv(4096)
            except (socket.timeout, TimeoutError):
                pass
            msgs, _ = cc.decode_messages_len4(buf)
            kinds = {(m["Body"].get("Cmd"), m["Body"].get("CmdOpt")) for m in msgs}
            assert ("Mon", "Start") in kinds, f"expected auto Mon Start, got {kinds}"
            assert any("ReturnCode" in m["Body"] for m in msgs), "DevInfo must be acked"

            blob = bytes.fromhex("01032b032b0100030a04010000000000000003000031006400000400")
            tls.sendall(cc.encode_message_len4(
                cc.make_message("WM_E2E", "n-e2e", ReturnCode="0000", Format="B64",
                                Data=base64.b64encode(blob).decode())))
            deadline = time.time() + 3
            while time.time() < deadline and "WM_E2E" not in store.latest:
                time.sleep(0.05)
            assert "WM_E2E" in store.latest, "pump snapshot must reach the store over TLS"
            assert store.latest["WM_E2E"]["diagMonType"] == "PUMP_47878"
            tls.close()
        finally:
            cc.set_state_store(None)
            cc.CERT, cc.KEY, cc._TLS_CTX = saved_cert, saved_key, saved_ctx
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    for fn in (test_len4_roundtrip, test_len4_multiple_and_truncated_tail,
               test_len4_garbage_is_dropped_never_parked, test_wm_ack_uses_len4_encoder,
               test_wm_pump_ingests_into_state_store,
               test_wm_pump_with_unmapped_device_is_skipped_not_fatal,
               test_tls_wm_client_full_exchange):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll WM control-channel assertions passed.")

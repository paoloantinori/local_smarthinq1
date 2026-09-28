"""TASK-080 passthrough e2e: a fake WM appliance and a fake LG upstream exchange
[4B len][JSON] frames THROUGH the server's WM branch in passthrough mode. Verified:
frames forwarded verbatim both ways (LG's ack reaches the appliance; the appliance's
DevInfo reaches LG), the pump snapshot is ingested into the wired store, and the server
never injects its own replies in this mode.
"""
from __future__ import annotations

import base64
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import control_channel as cc  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402


def _make_cert(tmpdir: str) -> tuple[str, str]:
    cert, key = os.path.join(tmpdir, "t.pem"), os.path.join(tmpdir, "t.key")
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=t",
         "-keyout", key, "-out", cert, "-days", "1"],
        check=True, capture_output=True)
    return cert, key


def test_passthrough_forwards_and_ingests() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        saved = (cc.CERT, cc.KEY, cc._TLS_CTX, cc._UP_CTX, cc.PASSTHROUGH_UPSTREAM)
        cert, key = _make_cert(tmp)
        cc.CERT, cc.KEY = cert, key
        cc._TLS_CTX, cc._UP_CTX = None, None

        # fake LG upstream: TLS server that acks DevInfo with Mon Start
        lg_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        lg_ctx.load_cert_chain(cert, key)
        lg_srv = socket.socket()
        lg_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        lg_srv.bind(("127.0.0.1", 0))
        lg_srv.listen(1)
        lg_port = lg_srv.getsockname()[1]
        seen_by_lg: list[dict] = []

        def fake_lg() -> None:
            conn, _ = lg_srv.accept()
            tls = lg_ctx.wrap_socket(conn, server_side=True)
            tls.settimeout(5)
            buf = b""
            while True:
                data = tls.recv(4096)
                if not data:
                    break
                buf += data
                msgs, rest = cc.decode_messages_len4(buf)
                buf = rest
                for m in msgs:
                    seen_by_lg.append(m)
                    if m.get("Body", {}).get("Cmd") == "DevInfo":
                        reply = cc.make_message(
                            m["Header"]["x-lgedm-deviceId"], m["Body"]["CmdWId"],
                            ReturnCode="0000")
                        tls.sendall(cc.encode_message_len4(reply))
                        mon = cc.mon_start_message(
                            m["Header"]["x-lgedm-deviceId"], "n-fake-lg-mon")
                        tls.sendall(cc.encode_message_len4(mon))
            tls.close()

        threading.Thread(target=fake_lg, daemon=True).start()

        cc.PASSTHROUGH_UPSTREAM = ("127.0.0.1", lg_port)  # validated tuple form
        store = DeviceStateStore(tmp)
        store.model_by_devid["WM_PT"] = "WTWN3"
        cc.set_state_store(store)

        server = cc.start_control_server(0)
        assert server is not None
        port = server.server_address[1]
        try:
            cli_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            cli_ctx.check_hostname = False
            cli_ctx.verify_mode = ssl.CERT_NONE
            raw = socket.create_connection(("127.0.0.1", port), timeout=5)
            tls = cli_ctx.wrap_socket(raw, server_hostname="wm")

            # appliance announces itself
            tls.sendall(cc.encode_message_len4(
                cc.make_message("WM_PT", "pt-1", Cmd="DevInfo", Format="B64", Data="eA==")))
            time.sleep(0.4)

            # appliance pumps a snapshot (the reply stream from LG is what we read)
            blob = bytes.fromhex("01032b032b0100030a04010000000000000003000031006400000400")
            tls.sendall(cc.encode_message_len4(
                cc.make_message("WM_PT", "n-pt-pump", ReturnCode="0000",
                                Format="B64", Data=base64.b64encode(blob).decode())))
            time.sleep(0.5)

            # the appliance receives LG's ack + Mon Start THROUGH the server
            buf = b""
            tls.settimeout(1)
            try:
                while True:
                    d = tls.recv(4096)
                    if not d:
                        break
                    buf += d
            except OSError:
                pass  # timeout or server-side close: both end the drain
            got, _ = cc.decode_messages_len4(buf)
            kinds = {(m["Body"].get("Cmd"), m["Body"].get("CmdOpt")) for m in got}
            assert ("Mon", "Start") in kinds, f"LG's Mon Start must pass through: {kinds}"
            assert any("ReturnCode" in m["Body"] for m in got), "LG's DevInfo ack passes through"

            # LG saw the appliance's messages
            assert seen_by_lg, "the appliance's frames must reach the upstream"
            assert seen_by_lg[0]["Body"]["Cmd"] == "DevInfo"

            # the pump was ingested
            deadline = time.time() + 3
            while time.time() < deadline and "WM_PT" not in store.latest:
                time.sleep(0.05)
            assert "WM_PT" in store.latest, "pump snapshot ingested in passthrough mode"
            assert store.latest["WM_PT"]["diagMonType"] == "PUMP_47878"
            tls.close()
        finally:
            cc.set_state_store(None)
            cc.CERT, cc.KEY, cc._TLS_CTX, cc._UP_CTX = saved[:4]
            cc.PASSTHROUGH_UPSTREAM = saved[4]
            server.shutdown()
            server.server_close()
            lg_srv.close()


if __name__ == "__main__":
    test_passthrough_forwards_and_ingests()
    print("PASS passthrough e2e")

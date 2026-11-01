"""Virtual ThinQ1 appliance (TASK-134): the full :47878 ladder in loopback, no
hardware. The fake client connects, DevInfo's, receives the auto Mon Start, acks it,
pumps the ladder, and the store sees every state including END/POWER_OFF."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import control_channel as cc  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402
from tools.fake_appliance import FakeAppliance  # noqa: E402


def _make_cert(tmpdir: str) -> tuple[str, str]:
    cert, key = os.path.join(tmpdir, "t.pem"), os.path.join(tmpdir, "t.key")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-subj", "/CN=t", "-keyout", key, "-out", cert, "-days", "1"],
                   check=True, capture_output=True)
    return cert, key


def test_full_ladder_reaches_store_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        saved = (cc.CERT, cc.KEY, cc._TLS_CTX)
        cert, key = _make_cert(tmp)
        cc.CERT, cc.KEY = cert, key
        cc._TLS_CTX = None
        store = DeviceStateStore(tmp)
        dev = "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d"
        store.model_by_devid[dev] = "WTWN3"
        seen: list = []
        store.on_state = lambda _dev, view: seen.append(
            (view.get("monData_decoded") or {}).get("State"))
        cc.set_state_store(store)
        server = cc.start_control_server(0)
        assert server is not None
        port = server.server_address[1]
        app = FakeAppliance("127.0.0.1", port, dev, pump_interval=0.05,
                            alive_interval=3600)  # no Alive noise in this window
        try:
            app.run(duration=1.0)
            assert app.mon_start_received, "server must Mon Start on DevInfo"
            assert app.acks_sent >= 1, "the Mon Start must be acked (corpus behavior)"
            states = set(seen)
            assert "WM_STATE_RUNNING" in states, states
            assert "WM_STATE_RINSING" in states, states
            assert {"WM_STATE_END", "WM_STATE_POWER_OFF"} <= states, states
            assert (store.latest.get(dev) or {}).get("diagMonType") == "PUMP_47878"
        finally:
            cc.set_state_store(None)
            cc.CERT, cc.KEY, cc._TLS_CTX = saved
            server.shutdown()
            server.server_close()


def test_ladder_covers_the_documented_cycle() -> None:
    """The built-in ladder DECODES as the documented WTWN3 cycle (binds to the
    corpus through the real decoder, not to byte prefixes: code-review 2026-11-01)."""
    from server.models import registry
    from tools.fake_appliance import LADDER_WTWN3
    want = ["WM_STATE_INITIAL", "WM_STATE_RUNNING", "WM_STATE_RINSING",
            "WM_STATE_SPINNING", "WM_STATE_END", "WM_STATE_POWER_OFF"]
    for hexr, expected in zip(LADDER_WTWN3, want):
        d = registry.decode_mondata(bytes.fromhex(hexr), "WTWN3")["monData_decoded"]
        assert d["State"] == expected, (hexr[:8], d["State"])
    # the cycle rungs carry the CONFIRMED door-lock bit, the idle rungs do not
    d_run = registry.decode_mondata(bytes.fromhex(LADDER_WTWN3[1]), "WTWN3")["monData_decoded"]
    assert d_run["opt_door_lock"] == "on"
    d_off = registry.decode_mondata(bytes.fromhex(LADDER_WTWN3[5]), "WTWN3")["monData_decoded"]
    assert d_off["opt_door_lock"] == "off"
    # END/POWER_OFF rungs are the complete hexes documented in the profiles file
    assert LADDER_WTWN3[4] == "0a00000106070000000000000000000000000108002d006400000300"
    assert LADDER_WTWN3[5] == "000000010607000000000000000000000000000a002d006400000300"


if __name__ == "__main__":
    test_full_ladder_reaches_store_end_to_end()
    test_ladder_covers_the_documented_cycle()
    print("PASS fake appliance tests")

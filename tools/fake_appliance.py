"""Virtual ThinQ1 WM appliance (TASK-134): a scripted CLIENT speaking the full
:47878 ladder against our server, for loopback tests and CI without real hardware.

What it does (the appliance side of PROTOCOL.md section 4.4, cross-checked against
anszom/rethink's client implementation, their issue #100):

1. optional :46030 registration POST (TotalDeviceInfoSvc with the fixed ThinQ1 client
   headers) to exercise the replay path end to end;
2. TLS connect to the control channel ([4B len][JSON] framing);
3. DevInfo on connect; Alive every `alive_interval`; both acked-numbered;
4. on the server's `Mon Start`: pump the snapshot ladder at `pump_interval`,
   reusing the Mon CmdWId exactly like the real modules (corpus-verified);
5. ack any numbered server command (echo CmdWId, ReturnCode 0000).

CLI (defaults point at a local dev server):
  python3 tools/fake_appliance.py --device 82c2ebc0-0e9f-11f5-82b4-0051eda91d3d \
      [--host 127.0.0.1] [--control-port 47878] [--http-port 46030] [--skip-http]
"""
from __future__ import annotations

import argparse
import base64
import os
import socket
import ssl
import sys
import threading
import time
import uuid
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import control_channel as cc  # noqa: E402  (single source for framing)

# Real washer WTWN3 ladder: complete frames as captured in the 2026-10-29/31 relay
# corpus (cycle B of the WTWN3; the profiles file flows/thinq1-wm-profiles-20261101.md
# documents the same states from a DIFFERENT cycle: option/course bytes differ per
# cycle, both variants are real captures; these are the complete ones).
LADDER_WTWN3 = [
    "01032b032b0100030a0401000000004000000104002d006400000300",  # INITIAL
    "0600360107070003070401000000004000000104002d006400000300",  # RUNNING
    "0700230107070003070001000000004000000104002d006400000300",  # RINSING
    "0800080107070000070001000000004000000104002d006400000300",  # SPINNING
    "0a00000106070000000000000000000000000108002d006400000300",  # END
    "000000010607000000000000000000000000000a002d006400000300",  # POWER_OFF
]

# Fixed ThinQ1 client credential observed in rethink's bridge (their issue #100
# cross-check) and matching our captures' header shape.
LGE_USER = "lgehadmUser"
LGE_PASSWORD = "bxLoLAZ+rp3oJDbEzRuIfAG4YumeqwWM9l6uUH6TupQ="


class FakeAppliance:
    """Threaded virtual appliance: drives the full :47878 ladder.

    Attributes after run: `mon_start_received`, `acks_sent`, `registration_ok`."""

    def __init__(self, host: str, control_port: int, device_id: str,
                 ladder: Optional[list[str]] = None,
                 pump_interval: float = 0.1, alive_interval: float = 60.0,
                 model_name: str = "WTWN3", device_type: int = 201) -> None:
        self.host, self.control_port, self.device_id = host, control_port, device_id
        self.model_name, self.device_type = model_name, device_type
        self.ladder = [bytes.fromhex(h) for h in (ladder or LADDER_WTWN3)]
        self.pump_interval, self.alive_interval = pump_interval, alive_interval
        self.mon_start_received = False
        self.acks_sent = 0
        self.registration_ok: Optional[bool] = None
        self._stop = threading.Event()
        self._sock: Optional[ssl.SSLSocket] = None

    # -- :46030 registration (replay path) ------------------------------------
    def register_http(self, http_port: int, timeout: float = 5.0) -> bool:
        """POST TotalDeviceInfoSvc the way the module does; any 2xx = ok."""
        import http.client
        body = (f"<lgedmRoot><countryCode>WW</countryCode>"
                f"<modelName>{self.model_name}</modelName>"
                f"<itemList><item>THINQ_TIME_SYNC_URI</item></itemList></lgedmRoot>")
        headers = {
            "Accept": "text/xml", "Content-Type": "text/xml;charset=utf-8",
            "x-lgedm-userid": LGE_USER, "x-lgedm-password": LGE_PASSWORD,
            "x-lgedm-deviceid": self.device_id,
            "x-lgedm-devicetype": str(self.device_type),
        }
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(self.host, http_port, context=ctx,
                                           timeout=timeout)
        try:
            conn.request("POST", "/lgehadm/api/Device/TotalDeviceInfoSvc",
                         body=body, headers=headers)
            self.registration_ok = 200 <= conn.getresponse().status < 300
        except (OSError, http.client.HTTPException) as e:  # BadStatusLine is not an OSError
            sys.stderr.write(f"[fake-appliance] registration failed: {e}\n")
            self.registration_ok = False
        finally:
            conn.close()
        return self.registration_ok

    # -- :47878 ladder ---------------------------------------------------------
    def run(self, duration: Optional[float] = None) -> None:
        """Connect and drive the ladder until `duration` elapses (None = until stop())."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        raw = socket.create_connection((self.host, self.control_port), timeout=5)
        self._sock = ctx.wrap_socket(raw, server_hostname="appliance")
        self._sock.settimeout(0.2)
        self._send(Cmd="DevInfo", Format="B64",
                   Data=base64.b64encode(
                       b"RuleVer=1.33,FwVer=QC_Modem_1.2.80,regFail=N").decode())
        last_alive = 0.0
        last_pump = 0.0
        pump_i = 0
        mon_cmd_w_id: Optional[str] = None
        started = time.monotonic()
        buf = b""
        while not self._stop.is_set():
            now = time.monotonic()
            if duration is not None and now - started > duration:
                break
            if now - last_alive >= self.alive_interval:
                self._send(Cmd="Alive")
                last_alive = now
            if self.mon_start_received and now - last_pump >= self.pump_interval:
                blob = self.ladder[pump_i % len(self.ladder)]
                pump_i += 1
                self._send(ReturnCode="0000", Format="B64",
                           Data=base64.b64encode(blob).decode(),
                           cmd_w_id=mon_cmd_w_id)
                last_pump = now
            # block only until the next pump tick once pumping (a fixed recv
            # timeout would gate the cadence to the timeout itself, found by the
            # loopback test); pre-Mon-Start the 0.2 s cap is what lets the
            # duration exit fire
            recv_timeout = min(0.2, self.pump_interval) if self.mon_start_received else 0.2
            self._sock.settimeout(max(0.01, recv_timeout))
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
            except (socket.timeout, TimeoutError):
                continue
            msgs, buf = cc.decode_messages_len4(buf)
            for m in msgs:
                body = m.get("Body", {})
                if body.get("Cmd") == "Mon" and body.get("CmdOpt") == "Start":
                    self.mon_start_received = True
                    mon_cmd_w_id = body.get("CmdWId")
                    self._send(ReturnCode="0000", cmd_w_id=mon_cmd_w_id)  # corpus: ack Mon
                    self.acks_sent += 1
                elif "CmdWId" in body and "ReturnCode" not in body:
                    self._send(ReturnCode="0000", cmd_w_id=body["CmdWId"])
                    self.acks_sent += 1
        try:
            self._sock.close()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()

    def _send(self, cmd_w_id: Optional[str] = None, **body_fields) -> None:
        assert self._sock is not None
        # sends get their own generous budget: the recv timeout (as low as the pump
        # interval) must not turn a stalled-server moment into a dead client thread
        self._sock.settimeout(2.0)
        try:
            self._sock.sendall(cc.encode_message_len4(
                cc.make_message(self.device_id, cmd_w_id or str(uuid.uuid4()),
                                **body_fields)))
        finally:
            self._sock.settimeout(0.2)


def main() -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--control-port", type=int, default=47878)
    p.add_argument("--http-port", type=int, default=46030)
    p.add_argument("--skip-http", action="store_true",
                   help="skip the :46030 registration step")
    p.add_argument("--device", default="82c2ebc0-0e9f-11f5-82b4-0051eda91d3d")
    p.add_argument("--seconds", type=float, default=30.0)
    args = p.parse_args()
    app = FakeAppliance(args.host, args.control_port, args.device)
    if not args.skip_http:
        print(f"registration: {app.register_http(args.http_port)}")
    t = threading.Thread(target=app.run, kwargs={"duration": args.seconds}, daemon=True)
    t.start()
    t.join()
    print(f"mon_start_received={app.mon_start_received} acks_sent={app.acks_sent}")
    return 0 if app.mon_start_received else 1


if __name__ == "__main__":
    raise SystemExit(main())

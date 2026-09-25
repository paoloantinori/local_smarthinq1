"""The :47878 control channel server (M3 / TASK-031).

ThinQ1 appliances maintain a persistent outbound TCP connection to the cloud on :47878.
The protocol is **raw TCP** (NOT TLS, NOT HTTP). Each message is ONE msgpack *string* whose
content is the JSON text ``{"Header": {...}, "Body": {...}}`` (never a msgpack map:
map-form messages were never understood by the appliances, 2026-09-24).

This module implements the server side: it accepts the appliance's persistent connection,
handles the command vocabulary (DevInfo, Alive, Mon), and can push Control/Set commands.

**Safety:** pushing commands (Control/Set) is behind ``allow_control`` (default off), per
CLAUDE.md rule #5. Read-only responses (DevInfo, Alive, Mon acks) are always enabled.

See ``flows/fridge-47878-control-20260721.log`` for the captured protocol, and
``PROTOCOL.md §4`` for the documentation.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
from socketserver import BaseRequestHandler, ThreadingTCPServer
from typing import Any, Optional


# ── msgpack encoding (manual — no dependency) ──────────────────────────────────────────────
# The :47878 protocol wraps each message as ONE msgpack string containing JSON (see
# encode_message). The subset needed: string framing (fixstr/str8/str16) for sending,
# plus maps/ints/bools for decoding whatever form a peer sends.


def _mp_str(s: str) -> bytes:
    """Encode a string as msgpack (fixstr / str8 / str16)."""
    b = s.encode("utf-8")
    n = len(b)
    if n < 32:
        return bytes([0xa0 | n]) + b
    if n < 256:
        return bytes([0xd9, n]) + b
    return bytes([0xda, n >> 8, n & 0xFF]) + b


def _mp_encode(obj: Any) -> bytes:
    """Encode a Python object as msgpack (maps + strings only — covers our protocol)."""
    if isinstance(obj, str):
        return _mp_str(obj)
    if isinstance(obj, dict):
        n = len(obj)
        if n < 16:
            out = bytes([0x80 | n])
        else:
            out = bytes([0xDE, n >> 8, n & 0xFF])
        for k, v in obj.items():
            out += _mp_str(str(k)) + _mp_encode(v)
        return out
    if isinstance(obj, bool):
        return bytes([0xC3 if obj else 0xC2])
    if isinstance(obj, int):
        if 0 <= obj < 128:
            return bytes([obj])
        if 0 <= obj < 256:
            return bytes([0xCC, obj])
        return bytes([0xCD, obj >> 8, obj & 0xFF]) + b""[:0]
    raise TypeError(f"unsupported type for msgpack: {type(obj)}")


def encode_message(msg: dict) -> bytes:
    """Encode a {Header, Body} message for the wire: JSON inside ONE msgpack string.

    The real protocol (flows/fridge-47878-control-20260721.log) carries each message
    as a msgpack STR whose content is JSON, not as a msgpack map. The old map
    encoding was never understood by the appliances (2026-09-24).
    """
    return _mp_str(json.dumps(msg, separators=(",", ":")))


def make_message(device_id: str, cmd_w_id: str, **body_fields: Any) -> dict:
    """Build a protocol message dict."""
    return {
        "Header": {"x-lgedm-deviceId": device_id},
        "Body": {"CmdWId": cmd_w_id, **body_fields},
    }


# ── msgpack decoding (receive side — minimal, reads what we encode + what the appliance sends) ─


class MsgpackReader:
    """Read msgpack values from a byte buffer (streaming). Supports the subset used by :47878."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _byte(self) -> int:
        b = self.data[self.pos]
        self.pos += 1
        return b

    def read(self) -> Any:
        """Read one msgpack value. Returns None if not enough data."""
        if self.pos >= len(self.data):
            return None
        prefix = self._byte()
        # fixmap (0x80-0x8f)
        if 0x80 <= prefix <= 0x8F:
            return self._read_map(prefix & 0x0F)
        # fixstr (0xa0-0xbf)
        if 0xA0 <= prefix <= 0xBF:
            return self._read_str(prefix & 0x1F)
        # str8
        if prefix == 0xD9:
            return self._read_str(self._byte())
        # str16
        if prefix == 0xDA:
            n = (self._byte() << 8) | self._byte()
            return self._read_str(n)
        # map16
        if prefix == 0xDE:
            n = (self._byte() << 8) | self._byte()
            return self._read_map(n)
        # positive fixint
        if prefix < 0x80:
            return prefix
        # uint8
        if prefix == 0xCC:
            return self._byte()
        # false / true
        if prefix == 0xC2:
            return False
        if prefix == 0xC3:
            return True
        raise ValueError(f"unsupported msgpack prefix 0x{prefix:02x} at pos {self.pos - 1}")

    def _read_str(self, n: int) -> str:
        if self.pos + n > len(self.data):
            raise ValueError("truncated string")
        s = self.data[self.pos:self.pos + n].decode("utf-8", "replace")
        self.pos += n
        return s

    def _read_map(self, n: int) -> dict:
        d: dict[str, Any] = {}
        for _ in range(n):
            key = self.read()
            val = self.read()
            d[str(key)] = val
        return d


def decode_messages(buf: bytes) -> tuple[list[dict], bytes]:
    """Decode all complete msgpack messages from a buffer. Returns (messages, remainder)."""
    reader = MsgpackReader(buf)
    msgs: list[dict] = []
    while reader.pos < len(buf):
        start = reader.pos
        try:
            val = reader.read()
        except (ValueError, IndexError):
            reader.pos = start  # incomplete message — rewind to its start, wait for more data
            break
        if isinstance(val, str):
            # Real appliances send JSON inside a msgpack string: parse it into
            # a message dict (flows capture format, 2026-09-24).
            try:
                val = json.loads(val)
            except ValueError:
                reader.pos = start
                break
        if isinstance(val, dict):
            msgs.append(val)
        else:
            reader.pos = start  # not a map — stop
            break
    consumed = reader.pos
    return msgs, buf[consumed:]


# ── the control channel server ──────────────────────────────────────────────────────────────

CONTROL_PORT = int(os.environ.get("LGM_CONTROL_PORT", "47878"))
ALLOW_CONTROL = os.environ.get("LGM_ALLOW_CONTROL", "") != ""  # off by default


class ControlChannel:
    """Manages the :47878 persistent connection from an appliance.

    The appliance connects outbound (it initiates). Our server accepts, then exchanges
    msgpack messages: DevInfo on connect, periodic Alive/Mon, and (if allow_control)
    Control/Set commands.
    """

    def __init__(self) -> None:
        self._connections: dict[str, socket] = {}  # devId -> socket
        self._lock = threading.Lock()
        self._cmd_counter = 0

    def register(self, dev_id: str, sock: socket) -> None:
        with self._lock:
            self._connections[dev_id] = sock

    def unregister(self, dev_id: str) -> None:
        with self._lock:
            self._connections.pop(dev_id, None)

    def send_command(self, dev_id: str, value: dict[str, str], *,
                     cmd: str = "Control", cmd_opt: str = "Set") -> bool:
        """Push a Control command to a connected appliance. Returns False if not connected
        or allow_control is off.

        ``cmd``/``cmd_opt`` default to the fridge's ``Control``/``Set`` (the captured+validated
        command). Washer/dryer buttons need ``cmd_opt="Operation"``/``"Power"``; those are
        physical-actuation commands kept unpublished until approved (CLAUDE.md #5)."""
        if not ALLOW_CONTROL:
            sys.stderr.write("[control] allow_control is off; command rejected\n")
            return False
        with self._lock:
            sock = self._connections.get(dev_id)
            if sock is None:
                return False
            self._cmd_counter += 1
            cmd_w_id = f"n-{dev_id[:8]}-{self._cmd_counter}"
            msg = make_message(dev_id, cmd_w_id, Cmd=cmd, CmdOpt=cmd_opt, Value=value, Data="")
            try:
                sock.sendall(encode_message(msg))
                sys.stderr.write(f"[control] sent {cmd}/{cmd_opt} to {dev_id[:8]}: {value}\n")
                return True
            except OSError as e:
                sys.stderr.write(f"[control] send failed: {e}\n")
                self._connections.pop(dev_id, None)
                return False


# Global instance (the server + command API share it).
_channel = ControlChannel()


def channel() -> ControlChannel:
    """The shared :47878 ControlChannel. The MQTT bridge routes commands here (TASK-067)."""
    return _channel


def handle_incoming(dev_id: str, msg: dict, _sock: socket) -> Optional[bytes]:
    """Process an incoming message from the appliance. Returns bytes to send back, or None."""
    body = msg.get("Body", {})
    cmd = body.get("Cmd", "")
    cmd_w_id = body.get("CmdWId", "")
    cmd_opt = body.get("CmdOpt", "")

    if cmd == "DevInfo":
        # Appliance announces itself on connect. Ack with ReturnCode 0000.
        return encode_message(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Alive":
        # Keepalive ping. Ack.
        return encode_message(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Mon" and cmd_opt == "Start":
        # Cloud asks the appliance to start monitoring (push periodic state).
        # Ack, then the appliance will push B64 state snapshots.
        return encode_message(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Mon" and cmd_opt == "Stop":
        return encode_message(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if "ReturnCode" in body:
        # This is an ack FROM the appliance (response to a Control/Set we sent).
        # Nothing to send back — the command was acknowledged.
        sys.stderr.write(
            f"[control] ack from {dev_id[:8]}: CmdWId={cmd_w_id} ReturnCode={body['ReturnCode']}\n")
        return None

    if "Format" in body and body.get("Format") == "B64":
        # A state snapshot from the appliance (in response to Mon).
        # Nothing to send back; log the FULL payload (decoded hex) so the byte
        # layout can be analyzed for fields the 46030 telemetry lacks (the
        # door-open hunt, 2026-09-24).
        data = body.get("Data", "")
        try:
            hexdump = base64.b64decode(data).hex()
        except Exception:
            hexdump = "<b64 invalid>"
        sys.stderr.write(
            f"[control] SNAP {dev_id[:8]} b64len={len(data)} hex={hexdump[:400]}\n")
        return None

    # Unknown message type — log it.
    sys.stderr.write(f"[control] unhandled from {dev_id[:8]}: Cmd={cmd} CmdOpt={cmd_opt}\n")
    return None


# socket type alias (avoid importing the full socket module for the type hint)
socket = Any


class _ControlHandler(BaseRequestHandler):
    """Handle one appliance's persistent :47878 connection."""

    def handle(self) -> None:  # noqa: N802
        sock = self.request
        try:
            sock.settimeout(300)  # a dead peer must not hold the thread forever
        except OSError:
            pass
        buf = b""
        dev_id = "unknown"

        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
                msgs, buf = decode_messages(buf)
                for msg in msgs:
                    dev_id = msg.get("Header", {}).get("x-lgedm-deviceId", dev_id)
                    _channel.register(dev_id, sock)
                    response = handle_incoming(dev_id, msg, sock)
                    if response:
                        sock.sendall(response)
                    # Read-only monitoring: when the appliance announces itself,
                    # ask it to push periodic state snapshots (what the real
                    # cloud does, cf. the fridge capture). No actuation here;
                    # Control/Set stay behind allow_control.
                    if msg.get("Body", {}).get("Cmd") == "DevInfo":
                        mon = make_message(dev_id, f"n-{dev_id[:8]}-mon",
                                           Cmd="Mon", CmdOpt="Start", Format="B64")
                        sock.sendall(encode_message(mon))
                        sys.stderr.write(f"[control] sent Mon Start to {dev_id[:8]}\n")
        except OSError as e:
            sys.stderr.write(f"[control] connection error: {e}\n")
        finally:
            if dev_id != "unknown":
                _channel.unregister(dev_id)
                sys.stderr.write(f"[control] {dev_id[:8]} disconnected\n")


def start_control_server(port: int = CONTROL_PORT) -> Optional[ThreadingTCPServer]:
    """Start the :47878 control channel server. Returns the server, or None on failure."""
    try:
        server = ThreadingTCPServer(("0.0.0.0", port), _ControlHandler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        sys.stderr.write(f"[control] listening on :{port} (allow_control={'on' if ALLOW_CONTROL else 'off'})\n")
        return server
    except OSError as e:
        sys.stderr.write(f"[control] failed to start on :{port}: {e}\n")
        return None

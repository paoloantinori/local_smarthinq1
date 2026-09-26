"""The :47878 control channel server (M3 / TASK-031, bilingual since TASK-078).

ThinQ1 appliances maintain a persistent outbound TCP connection to the cloud on :47878.
Two families share the port, dispatched by the connection's FIRST BYTE (PROTOCOL.md §4/§4.4):

- **fridge (REF)**: raw TCP (NOT TLS); each message is ONE msgpack *string* whose
  content is the JSON text ``{"Header": {...}, "Body": {...}}`` (never a msgpack map).
- **washer/dryer (WM)**: TLS (first byte ``0x16`` = ClientHello; our cert is accepted,
  no pinning) carrying ``[4-byte big-endian length][JSON]`` frames.

Both speak the same Header/Body vocabulary (DevInfo, Alive, Mon, ReturnCode acks, B64
pump snapshots), so the protocol layer is shared; only framing and transport differ.
The WM pump's B64 ``Data`` is the same monData the diagmon path decodes: it is ingested
into the state store (TASK-078) when one is wired via :func:`set_state_store`.

Invalid frames are LOGGED and dropped, never silently parked (the 2026-09-25 outage was
exactly a reader parking forever on bytes it could not parse).

**Safety:** pushing commands (Control/Set) is behind ``allow_control`` (default off), per
CLAUDE.md rule #5. Read-only responses (DevInfo, Alive, Mon acks) are always enabled.

See ``flows/fridge-47878-control-20260721.log`` and ``PROTOCOL.md §4`` for the fridge,
``flows/wm47878-cleartext-redacted-20260925.log`` and ``PROTOCOL.md §4.4`` for the WM.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
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


def encode_message_len4(msg: dict) -> bytes:
    """Encode a {Header, Body} message for the WM family: [4-byte BE length][JSON]
    (PROTOCOL.md §4.4, decoded from the cleartext relay corpus)."""
    payload = json.dumps(msg, separators=(",", ":")).encode()
    return struct.pack("!I", len(payload)) + payload


# a sane upper bound: real frames are ~150-300 B; anything declaring megabytes is
# garbage, and garbage must be dropped, not buffered forever (TASK-078 acceptance)
_LEN4_MAX_FRAME = 1 << 20


def decode_messages_len4(buf: bytes) -> tuple[list[dict], bytes]:
    """Decode all complete [4B BE length][JSON] frames from a buffer.

    Unlike the msgpack reader (which rewinds and waits on a truncated tail), this
    reader distinguishes truncated frames (kept in the remainder, legit mid-stream)
    from garbage (a complete frame whose JSON does not parse, or an insane declared
    length): garbage is LOGGED and dropped so the stream can never wedge silently.
    Returns (messages, remainder)."""
    msgs: list[dict] = []
    pos = 0
    desync_logged = False
    while pos + 4 <= len(buf):
        declared = int.from_bytes(buf[pos:pos + 4], "big")
        if declared > _LEN4_MAX_FRAME:
            if not desync_logged:  # one line per desync episode, not per scanned byte
                sys.stderr.write(f"[control] len4 desync at {pos}: declared {declared}B; "
                                 f"resync-scanning (head={buf[pos:pos + 24].hex()})\n")
                desync_logged = True
            pos += 1  # stream is desynced: scan forward instead of dropping everything
            continue
        if desync_logged:
            sys.stderr.write(f"[control] len4 resynced at offset {pos}\n")
            desync_logged = False
        if pos + 4 + declared > len(buf):
            break  # truncated tail: wait for more data
        try:
            val = json.loads(buf[pos + 4:pos + 4 + declared])
        except ValueError:
            sys.stderr.write(f"[control] len4 frame not JSON (declared {declared}B); "
                             f"dropping frame head={buf[pos + 4:pos + 28].hex()}\n")
            pos += 4 + declared
            continue
        if isinstance(val, dict):
            msgs.append(val)
        else:
            sys.stderr.write(f"[control] len4 frame is {type(val).__name__}, not a "
                             f"message object; dropping it\n")
        pos += 4 + declared
    return msgs, buf[pos:]


def make_message(device_id: str, cmd_w_id: str, **body_fields: Any) -> dict:
    """Build a protocol message dict."""
    return {
        "Header": {"x-lgedm-deviceId": device_id},
        "Body": {"CmdWId": cmd_w_id, **body_fields},
    }


def mon_start_message(device_id: str, cmd_w_id: str) -> dict:
    """The cloud's on-demand state query in the CAPTURED shape: the fridge capture's
    Mon Start carries Cmd/CmdOpt only (no Format field); no invented fields
    (CLAUDE.md rule 1)."""
    return make_message(device_id, cmd_w_id, Cmd="Mon", CmdOpt="Start")


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
    remainder = buf[consumed:]
    if not msgs and len(remainder) > _MSGBUF_SANITY_LIMIT:
        # never-park guard (the 2026-09-25 outage mechanism): a stream that never
        # yields a message and keeps growing would rewind forever, mute and
        # unbounded; real messages are ~150-300 B, so a mute remainder this large
        # is garbage. LOG and drop it.
        sys.stderr.write(f"[control] msgpack mute buffer at {len(remainder)}B with zero "
                         f"messages; dropping garbage head={remainder[:24].hex()}\n")
        return [], b""
    return msgs, remainder


# a mute remainder larger than this is garbage: real messages are a few hundred bytes
_MSGBUF_SANITY_LIMIT = 1 << 16


# ── the control channel server ──────────────────────────────────────────────────────────────

CONTROL_PORT = int(os.environ.get("LGM_CONTROL_PORT", "47878"))
ALLOW_CONTROL = os.environ.get("LGM_ALLOW_CONTROL", "") != ""  # off by default
# same env contract as the 46030 server: import its constants so the two listeners
# can never drift onto different certs (app imports control_channel only lazily in
# main(), so this module-level import cannot cycle)
from .app import CERT, KEY  # noqa: E402

# Lazy TLS context for the WM family (first byte 0x16 = ClientHello). Same cert/key
# as the 46030 server; the appliances accept it (no pinning, verified 2026-09-25).
_TLS_CTX: Optional[ssl.SSLContext] = None


def _tls_ctx() -> ssl.SSLContext:
    global _TLS_CTX
    if _TLS_CTX is None:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT, KEY)
        _TLS_CTX = ctx
    return _TLS_CTX


# Optional state store for the WM pump ingest (TASK-078); wired by app.main().
_STATE_STORE: Optional[Any] = None


def set_state_store(store: Any) -> None:
    """Wire the state store so the WM pump's B64 monData snapshots are decoded and
    stored/published exactly like diagmon reports (TASK-078)."""
    global _STATE_STORE
    _STATE_STORE = store


class ControlChannel:
    """Manages the :47878 persistent connection from an appliance (either family).

    The appliance connects outbound (it initiates). Our server accepts, then exchanges
    messages: DevInfo on connect, periodic Alive/Mon, and (if allow_control)
    Control/Set commands. Each connection remembers its family's encoder (msgpack
    for the fridge, [4B len][JSON] for the WM family) so pushed commands speak the
    right framing.
    """

    def __init__(self) -> None:
        # devId -> (sock, encode, send_lock)
        self._connections: dict[str, tuple[Any, Any, threading.Lock]] = {}
        self._lock = threading.Lock()
        self._cmd_counter = 0

    def register(self, dev_id: str, sock: Any, encode: Any = encode_message,
                 send_lock: Optional[threading.Lock] = None) -> None:
        with self._lock:
            self._connections[dev_id] = (sock, encode, send_lock or threading.Lock())

    def unregister(self, dev_id: str, sock: Any = None) -> None:
        """Remove dev_id's registration. When ``sock`` is given, remove it ONLY if it
        is still the registered socket: a superseded handler (half-dead peer whose
        recv times out late) must never unregister the appliance's NEWER live
        connection."""
        with self._lock:
            entry = self._connections.get(dev_id)
            if entry is None:
                return
            if sock is None or entry[0] is sock:
                self._connections.pop(dev_id, None)

    def devices(self) -> list[str]:
        """The currently connected devIds (for the polling loop)."""
        with self._lock:
            return list(self._connections)

    def _next_wid(self, dev_id: str) -> str:
        """Atomically mint the next message id (three concurrent callers exist:
        polling thread, /debug/query, MQTT commands)."""
        with self._lock:
            self._cmd_counter += 1
            return f"n-{dev_id[:8]}-{self._cmd_counter}"

    def _push(self, dev_id: str, msg: dict) -> bool:
        """Send one message on dev_id's registered connection, using its family
        codec and send lock (TLS sockets are NOT safe for concurrent SSL_write).
        A failed send drops the registration, but ONLY if it is still THIS entry:
        a stale socket dying after the appliance reconnected must never wipe the
        newer live registration (same identity check as unregister)."""
        with self._lock:
            entry = self._connections.get(dev_id)
            if entry is None:
                return False
            sock, encode, send_lock = entry
        with send_lock:
            try:
                sock.sendall(encode(msg))
                return True
            except OSError as e:
                sys.stderr.write(f"[control] send failed: {e}\n")
                with self._lock:
                    current = self._connections.get(dev_id)
                    if current is not None and current[0] is sock:
                        self._connections.pop(dev_id, None)
                return False

    def query_state(self, dev_id: str) -> bool:
        """Send an on-demand `Mon Start` and let the snapshot flow through the normal
        ingest path (TASK-070). READ-ONLY: unlike send_command this is NOT gated by
        allow_control (a query, not an actuation; CLAUDE.md #5). The reply arrives as
        a regular Format=B64 message and is ingested + published like the periodic
        push, so there is nothing to await here."""
        return self._push(dev_id, mon_start_message(dev_id, self._next_wid(dev_id)))

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
        msg = make_message(dev_id, self._next_wid(dev_id),
                           Cmd=cmd, CmdOpt=cmd_opt, Value=value, Data="")
        ok = self._push(dev_id, msg)
        if ok:
            sys.stderr.write(f"[control] sent {cmd}/{cmd_opt} to {dev_id[:8]}: {value}\n")
        return ok


# Global instance (the server + command API share it).
_channel = ControlChannel()


def channel() -> ControlChannel:
    """The shared :47878 ControlChannel. The MQTT bridge routes commands here (TASK-067)."""
    return _channel


def handle_incoming(dev_id: str, msg: dict,
                    encode: Any = encode_message) -> Optional[bytes]:
    """Process an incoming message from the appliance. Returns bytes to send back
    (encoded with the connection's family codec), or None."""
    body = msg.get("Body", {})
    cmd = body.get("Cmd", "")
    cmd_w_id = body.get("CmdWId", "")
    cmd_opt = body.get("CmdOpt", "")

    if cmd == "DevInfo":
        # Appliance announces itself on connect. Ack with ReturnCode 0000.
        return encode(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Alive":
        # Keepalive ping. Ack.
        return encode(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Mon" and cmd_opt == "Start":
        # Cloud asks the appliance to start monitoring (push periodic state).
        # Ack, then the appliance will push B64 state snapshots.
        return encode(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if cmd == "Mon" and cmd_opt == "Stop":
        return encode(make_message(dev_id, cmd_w_id, ReturnCode="0000"))

    if "Format" in body and body.get("Format") == "B64":
        # A state snapshot from the appliance (in response to Mon). MUST be checked
        # before the bare-ack branch: real pump frames carry ReturnCode AND
        # Format+Data together (the 2026-09-25 corpus shape). The WM pump's Data is
        # the same monData the diagmon path decodes (PROTOCOL.md §4.4): ingest it
        # when a store is wired (TASK-078), always log the hex tail.
        data = body.get("Data", "")
        try:
            blob = base64.b64decode(data)
        except Exception:
            blob = b""
            sys.stderr.write(f"[control] SNAP {dev_id[:8]}: invalid b64\n")
        if blob:
            # full hex is opt-in (LGM_SNAP_HEX): at ~1.5 Hz it is a per-frame stderr
            # firehose, and the decoded fields are already in the state store
            if os.environ.get("LGM_SNAP_HEX"):
                sys.stderr.write(
                    f"[control] SNAP {dev_id[:8]} b64len={len(data)} "
                    f"hex={blob.hex()[:400]}\n")
            else:
                sys.stderr.write(f"[control] SNAP {dev_id[:8]} b64len={len(data)}\n")
            if _STATE_STORE is not None:
                _STATE_STORE.ingest_mondata(dev_id, blob)
        return None

    if "ReturnCode" in body:
        # This is an ack FROM the appliance (response to a Control/Set we sent).
        # Nothing to send back: the command was acknowledged.
        sys.stderr.write(
            f"[control] ack from {dev_id[:8]}: CmdWId={cmd_w_id} ReturnCode={body['ReturnCode']}\n")
        return None

    # Unknown message type — log it.
    sys.stderr.write(f"[control] unhandled from {dev_id[:8]}: Cmd={cmd} CmdOpt={cmd_opt}\n")
    return None


class _ControlHandler(BaseRequestHandler):
    """Handle one appliance's persistent :47878 connection (either family)."""

    def handle(self) -> None:  # noqa: N802
        sock: Any = self.request
        buf = b""
        dev_id = "unknown"

        try:
            try:
                # hello budget BEFORE the peek: a connected-but-silent peer must not
                # hold the handler thread forever on the first byte either
                sock.settimeout(60)
            except OSError:
                pass
            # Family dispatch on the first byte (PROTOCOL.md §4 vs §4.4):
            # 0x16 = TLS ClientHello (WM family) -> TLS + [4B len][JSON];
            # anything else = the fridge's raw msgpack channel.
            # MSG_PEEK looks WITHOUT consuming: the ClientHello must stay in the
            # socket for wrap_socket's handshake to see it.
            first = sock.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first[0] == 0x16:
                sock.settimeout(15)  # handshake budget
                sock = _tls_ctx().wrap_socket(sock, server_side=True)
                decode, encode = decode_messages_len4, encode_message_len4
            else:
                decode, encode = decode_messages, encode_message
            try:
                sock.settimeout(300)  # a dead peer must not hold the thread forever
            except OSError:
                pass

            # one send-lock per connection: TLS sockets are not safe for concurrent
            # SSL_write, and send_command (MQTT thread) can race this thread's acks
            send_lock = threading.Lock()
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data
                msgs, buf = decode(buf)
                for msg in msgs:
                    dev_id = msg.get("Header", {}).get("x-lgedm-deviceId", dev_id)
                    _channel.register(dev_id, sock, encode, send_lock)
                    response = handle_incoming(dev_id, msg, encode=encode)
                    if response:
                        with send_lock:
                            sock.sendall(response)
                    # Read-only monitoring: when the appliance announces itself,
                    # ask it to push periodic state snapshots (what the real
                    # cloud does, cf. the fridge capture and the WM corpus).
                    # No actuation here; Control/Set stay behind allow_control.
                    if msg.get("Body", {}).get("Cmd") == "DevInfo":
                        mon = mon_start_message(dev_id, f"n-{dev_id[:8]}-mon")
                        with send_lock:
                            sock.sendall(encode(mon))
                        sys.stderr.write(f"[control] sent Mon Start to {dev_id[:8]}\n")
        except ssl.SSLError as e:  # BEFORE OSError: SSLError is an OSError subclass
            sys.stderr.write(f"[control] TLS error: {e}\n")
        except OSError as e:
            sys.stderr.write(f"[control] connection error: {e}\n")
        finally:
            if dev_id != "unknown":
                # sock (not just dev_id): a superseded handler must not unregister
                # the appliance's newer live connection after a late timeout
                _channel.unregister(dev_id, sock)
                sys.stderr.write(f"[control] {dev_id[:8]} disconnected\n")


POLL_INTERVAL = float(os.environ.get("LGM_POLL_INTERVAL", "0"))  # 0 = push-only (TASK-070)


def start_polling(channel: "ControlChannel", interval: float = POLL_INTERVAL) -> Optional[threading.Thread]:
    """TASK-070: query every connected device every `interval` seconds. Off when the
    interval is 0 (the default: appliances push on their own; the LG app keeps working
    in bridge mode). Read-only, never gated by allow_control."""
    if interval <= 0:
        return None

    def loop() -> None:
        sys.stderr.write(f"[control] polling every {interval}s (query Mon Start)\n")
        while True:
            try:
                for dev_id in channel.devices():
                    channel.query_state(dev_id)
            except Exception as e:  # noqa: BLE001: the daemon loop must survive anything
                sys.stderr.write(f"[control] polling round failed: {e}\n")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


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

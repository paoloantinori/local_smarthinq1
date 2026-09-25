"""TLS-terminating transparent relay for the WM-family :47878 channel (PROTOCOL.md §4.4).

Why this exists: the washer/dryer `:47878` is TLS (no SNI, TLS 1.2, no pinning verified
only for `:46030`). Passive capture sees ciphertext; the fridge-style msgpack server
cannot speak it (2026-09-25 outage). The only way to read the channel is to terminate
TLS with our `*.lgthinq.com` cert and relay to the real LG upstream, so:

  appliance ⇄ [TLS relay: our cert / log cleartext] ⇄ real LG (TLS, CERT_NONE)

The appliance sees the real cloud: LG's opening records pass through untouched, so the
server-gated 1 Hz push can start, and every application-data byte both ways is logged.
Pure relay: nothing is modified, nothing is originated (CLAUDE.md #5: no actuation; any
LG-app command sent during a window passes through unchanged).

Engine: ONE thread per connection driving both TLS endpoints through ssl.MemoryBIO
(SSLObject) over a selectors loop. Two threads doing simultaneous recv/sendall on the
same SSLSocket (the classic pump pair) silently lose records ~25% of the time in local
testing; single-threaded non-blocking SSL objects make concurrent OpenSSL access
impossible by construction.

Topology (capture-fridge.sh model, port 47878): the router policy-routes the appliance's
:47878 to this box as next-hop (dst preserved); a local nft REDIRECT sends it to this
listener; SO_ORIGINAL_DST recovers the real LG IP for the upstream leg. With --upstream
the relay runs explicit (no REDIRECT needed); that is the mode the tests exercise.

Usage:
  python3 tools/wm47878_tls_relay.py --cert data/cert.pem --key data/key.pem \
      [--upstream IP:47878] [--listen 0.0.0.0:47878] [--log-file FILE]
"""
from __future__ import annotations

import argparse
import selectors
import socket
import ssl
import struct
import sys
import threading
import time
from typing import Callable, Optional, TextIO

# SO_ORIGINAL_DST on Linux (same mechanism mitmproxy transparent mode relies on).
SO_ORIGINAL_DST = 80
LogFn = Callable[[str], None]
IDLE_TIMEOUT = 300.0  # the channel keepalives every 60 s; 300 s silent = dead


def original_dst(sock: socket.socket) -> tuple[str, int]:
    """The pre-REDIRECT destination of a transparently-redirected socket."""
    raw = sock.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    _, port, host = struct.unpack("!HH4s", raw[:8])
    return socket.inet_ntoa(host), port


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"


def _server_ctx(certfile: str, keyfile: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    return ctx


def _client_ctx() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _End:
    """One TLS endpoint: a non-blocking socket plus an SSLObject over MemoryBIOs."""

    def __init__(self, sock: socket.socket, ctx: ssl.SSLContext,
                 server_side: bool, hostname: Optional[str] = None,
                 name: str = "?") -> None:
        self.sock = sock
        sock.setblocking(False)
        self.inb = ssl.MemoryBIO()
        self.outb = ssl.MemoryBIO()
        self.tls = ctx.wrap_bio(self.inb, self.outb,
                                server_side=server_side, server_hostname=hostname)
        self.name = name                 # "C" (appliance) / "U" (LG) for log lines
        self.pending = bytearray()   # ciphertext not yet accepted by the socket
        self.handshaken = False
        self.dead = False


def _flush(end: _End) -> bool:
    """Push pending ciphertext to the socket. True = alive (sent or would block)."""
    while end.pending:
        try:
            n = end.sock.send(end.pending)
        except BlockingIOError:
            return True                      # socket buffer full: retry on EPOLLOUT
        except OSError:
            return False
        del end.pending[:n]
    return True


def _drive_handshake(end: _End, log: LogFn) -> bool:
    """Advance the handshake. True when it completed this call. A TLS alert (e.g. the
    appliance rejecting our cert: THE failure this rig must report cleanly) marks the
    end dead instead of escaping as a bare thread traceback."""
    if end.handshaken or end.dead:
        return False
    try:
        end.tls.do_handshake()
        end.handshaken = True
        return True
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
        return False
    except (ssl.SSLError, OSError) as e:
        log(f"{_ts()} {end.name} handshake failed ({e!r})")
        end.dead = True
        return False


def _drain(end: _End) -> bool:
    """Move ciphertext the TLS layer produced (handshake flights, records) to the
    socket queue. With MemoryBIO this is OUR job; SSLSocket did it internally."""
    data = end.outb.read()
    if data:
        end.pending += data
        return True
    return False


def _read_plain(end: _End) -> Optional[bytes]:
    """One decrypt step: None = nothing ready / want-read; b'' = clean TLS EOF."""
    try:
        return end.tls.read(65536)
    except ssl.SSLWantReadError:
        return None
    except ssl.SSLZeroReturnError:
        return b""
    except (ssl.SSLError, OSError):
        end.dead = True
        return b""


def _feed_socket(end: _End) -> bool:
    """Move socket-received ciphertext into the TLS input BIO. False on hard error."""
    try:
        data = end.sock.recv(65536)
    except BlockingIOError:   # EAGAIN: nothing to read yet (OSError subclass, checked first)
        return True
    except OSError:
        return False
    if data == b"":
        end.inb.write_eof()
        return True
    end.inb.write(data)
    return True


def _bridge(client_end: _End, up_end: _End, log: LogFn, peer: str) -> None:
    """Single-threaded full-duplex pump between the two TLS endpoints.

    Progress-driven: handshakes, decrypt/encrypt and socket writes advance in a tight
    loop while there is work; the selector blocks ONLY when every step is parked on
    want-read. This keeps latency at zero (an early version re-selected after each
    handshake step and paid a 5 s stall per flight).
    """
    sel = selectors.DefaultSelector()
    sel.register(client_end.sock, selectors.EVENT_READ, client_end)
    sel.register(up_end.sock, selectors.EVENT_READ, up_end)

    def sock_wants_write(end: _End) -> int:
        return selectors.EVENT_WRITE if end.pending else 0

    last_alive = time.monotonic()
    try:
        while not (client_end.dead or up_end.dead):
            progressed = False
            for end in (client_end, up_end):
                if _drive_handshake(end, log):
                    progressed = True

            if client_end.handshaken and up_end.handshaken:
                for src, dst, label in ((client_end, up_end, "C->U"),
                                        (up_end, client_end, "U->C")):
                    while not (src.dead or dst.dead):
                        plain = _read_plain(src)
                        if plain is None:
                            break
                        if plain == b"":
                            src.dead = True
                            break
                        log(f"{_ts()} {label} len={len(plain)} hex={plain.hex()}")
                        try:
                            dst.tls.write(plain)
                        except (ssl.SSLError, OSError):
                            dst.dead = True
                            break
                        progressed = True
                        last_alive = time.monotonic()

            for end in (client_end, up_end):
                if _drain(end):
                    progressed = True
                had_pending = len(end.pending)
                if not _flush(end):
                    end.dead = True
                elif len(end.pending) < had_pending:
                    progressed = True

            if client_end.dead or up_end.dead:
                break
            if progressed:
                continue

            for end in (client_end, up_end):
                sel.modify(end.sock, selectors.EVENT_READ | sock_wants_write(end), end)
            events = sel.select(timeout=5.0)
            if not events:
                if time.monotonic() - last_alive > IDLE_TIMEOUT:
                    break
                continue
            for key, mask in events:
                end: _End = key.data
                if mask & selectors.EVENT_WRITE and not _flush(end):
                    end.dead = True
                if mask & selectors.EVENT_READ and not _feed_socket(end):
                    end.dead = True
    finally:
        # Hard close (SSLObject has no close_notify API worth the portability cost;
        # the appliances re-handshake from scratch anyway, cf. empty session id).
        for end in (client_end, up_end):
            try:
                end.sock.close()
            except OSError:
                pass
        sel.close()
        log(f"{_ts()} {peer} relay closed")


def handle_connection(client: socket.socket, server_ctx: ssl.SSLContext,
                      client_ctx: ssl.SSLContext, upstream: tuple[str, int],
                      log: LogFn) -> None:
    """TLS-terminate `client`, TLS-connect to `upstream`, relay both ways (one thread)."""
    peer = client.getpeername()[0]
    try:
        up_sock = socket.create_connection(upstream, timeout=10)
    except OSError as e:
        log(f"{_ts()} {peer} upstream connect failed ({e!r}); closing")
        try:
            client.close()
        except OSError:
            pass
        return
    client_end = _End(client, server_ctx, server_side=True, name="C")
    up_end = _End(up_sock, client_ctx, server_side=False, hostname=upstream[0], name="U")
    log(f"{_ts()} {peer} relay open → {upstream[0]}:{upstream[1]}")
    _bridge(client_end, up_end, log, peer)


def run(listen: tuple[str, int], upstream: Optional[tuple[str, int]],
        certfile: str, keyfile: str, log: LogFn) -> None:
    server_ctx = _server_ctx(certfile, keyfile)
    client_ctx = _client_ctx()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(listen)
    srv.listen(8)
    log(f"{_ts()} listening on {listen[0]}:{listen[1]} (upstream="
        f"{f'{upstream[0]}:{upstream[1]}' if upstream else 'SO_ORIGINAL_DST'})")
    while True:
        client, _ = srv.accept()
        try:
            dst = upstream or original_dst(client)
        except OSError as e:
            # A connection that did not come through the nft REDIRECT (LAN probe,
            # rig torn down out of order): must not kill the whole capture process.
            peer = "unknown"
            try:
                peer = client.getpeername()[0]
            except OSError:
                pass
            log(f"{_ts()} {peer} not redirect-derived, no original dst ({e!r}); ignoring")
            try:
                client.close()
            except OSError:
                pass
            continue
        threading.Thread(target=handle_connection,
                         args=(client, server_ctx, client_ctx, dst, log), daemon=True).start()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "WM :47878 TLS relay")
    p.add_argument("--listen", default="0.0.0.0:47878")
    p.add_argument("--upstream", default=None,
                   help="explicit upstream host:port (skip SO_ORIGINAL_DST; test mode)")
    p.add_argument("--cert", default="data/cert.pem")
    p.add_argument("--key", default="data/key.pem")
    p.add_argument("--log-file", default=None, help="also append the log here")
    args = p.parse_args(argv)

    lh, lp = args.listen.rsplit(":", 1)
    upstream = None
    if args.upstream:
        uh, up = args.upstream.rsplit(":", 1)
        upstream = (uh, int(up))

    sink: Optional[TextIO] = open(args.log_file, "a") if args.log_file else None

    def log(msg: str) -> None:
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()
        if sink:
            sink.write(msg + "\n")
            sink.flush()

    try:
        run((lh, int(lp)), upstream, args.cert, args.key, log)
    except KeyboardInterrupt:
        return 0
    finally:
        if sink:
            sink.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

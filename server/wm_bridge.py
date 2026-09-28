"""The WM-family :47878 MITM bridge engine (TASK-080; extracted from
tools/wm47878_tls_relay.py so the server and the tool share ONE implementation).

One thread drives both TLS endpoints through ssl.MemoryBIO (SSLObject) over a selectors
loop: full-duplex forwarding between the appliance (terminated with our cert) and real
LG (CERT_NONE upstream), with an optional observer for the decrypted appliance→LG
application data. Two threads doing simultaneous recv/sendall on the same SSLSocket
silently lose records (~25% in local testing); the single-threaded non-blocking engine
makes concurrent OpenSSL access impossible by construction.
"""
from __future__ import annotations

import selectors
import socket
import ssl
import time
from typing import Callable, Optional

class End:
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


def flush(end: End) -> bool:
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


def drive_handshake(end: End, log: Callable[[str], None]) -> bool:
    """Advance the handshake. True when it completed this call."""
    if end.handshaken or end.dead:
        return False
    try:
        end.tls.do_handshake()
        end.handshaken = True
        return True
    except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
        return False
    except (ssl.SSLError, OSError) as e:
        log(f"{end.name} handshake failed ({e!r})")
        end.dead = True
        return False


def drain(end: End) -> bool:
    """Move ciphertext the TLS layer produced (handshake flights, records) to the
    socket queue. With MemoryBIO this is OUR job; SSLSocket did it internally."""
    data = end.outb.read()
    if data:
        end.pending += data
        return True
    return False


def read_plain(end: End) -> Optional[bytes]:
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


def feed_socket(end: End) -> bool:
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


def bridge(client_end: End, up_end: End, log: Callable[[str], None], peer: str,
           on_client_data: Optional[Callable[[bytes], None]] = None,
           on_up_data: Optional[Callable[[bytes], None]] = None,
           idle_timeout: Optional[float] = 300.0) -> None:
    """Single-threaded full-duplex pump between the two TLS endpoints.

    Progress-driven: handshakes, decrypt/encrypt and socket writes advance in a tight
    loop while there is work; the selector blocks ONLY when every step is parked on
    want-read. ``on_client_data`` observes each decrypted appliance→LG application-data
    chunk (the server's pump ingest hook); forwarding is never altered.
    """
    sel = selectors.DefaultSelector()
    sel.register(client_end.sock, selectors.EVENT_READ, client_end)
    sel.register(up_end.sock, selectors.EVENT_READ, up_end)
    last_alive = time.monotonic()
    log(f"{peer} passthrough open")

    def sock_wants_write(end: End) -> int:
        return selectors.EVENT_WRITE if end.pending else 0

    try:
        while not (client_end.dead or up_end.dead):
            progressed = False
            for end in (client_end, up_end):
                if drive_handshake(end, log):
                    progressed = True

            if client_end.handshaken and up_end.handshaken:
                while not (client_end.dead or up_end.dead):
                    plain = read_plain(client_end)
                    if plain is None:
                        break
                    if plain == b"":
                        client_end.dead = True
                        break
                    if on_client_data is not None:
                        try:
                            on_client_data(plain)
                        except Exception as e:  # noqa: BLE001: observer must never break the pipe
                            log(f"observer error ({e!r})")
                    last_alive = time.monotonic()
                    try:
                        up_end.tls.write(plain)
                    except (ssl.SSLError, OSError):
                        up_end.dead = True
                        break
                    progressed = True
                while not (client_end.dead or up_end.dead):
                    plain = read_plain(up_end)
                    if plain is None:
                        break
                    if plain == b"":
                        up_end.dead = True
                        break
                    if on_up_data is not None:
                        try:
                            on_up_data(plain)
                        except Exception as e:  # noqa: BLE001: observer must never break the pipe
                            log(f"observer error ({e!r})")
                    last_alive = time.monotonic()
                    try:
                        client_end.tls.write(plain)
                    except (ssl.SSLError, OSError):
                        client_end.dead = True
                        break
                    progressed = True

            for end in (client_end, up_end):
                if drain(end):
                    progressed = True
                had_pending = len(end.pending)
                if not flush(end):
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
                if idle_timeout is not None and \
                        time.monotonic() - last_alive > idle_timeout:
                    log(f"{peer} passthrough idle {idle_timeout}s: closing")
                    break
                continue
            for key, mask in events:
                end: End = key.data
                if mask & selectors.EVENT_WRITE and not flush(end):
                    end.dead = True
                if mask & selectors.EVENT_READ and not feed_socket(end):
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
        log(f"{peer} passthrough closed")

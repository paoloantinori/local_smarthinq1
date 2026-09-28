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

The engine (End/bridge) is single-sourced in server/wm_bridge.py (TASK-080): this tool
is the CLI/logging front-end over it. The server's passthrough mode uses the same engine.

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
import socket
import ssl
import struct
import sys
import threading
import time
from typing import Callable, Optional, TextIO

sys.path.insert(0, ".")
from server.wm_bridge import End, bridge  # noqa: E402

# SO_ORIGINAL_DST on Linux (same mechanism mitmproxy transparent mode relies on).
SO_ORIGINAL_DST = 80
LogFn = Callable[[str], None]


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


def handle_connection(client: socket.socket, server_ctx: ssl.SSLContext,
                      client_ctx: ssl.SSLContext, upstream: tuple[str, int],
                      log: LogFn) -> None:
    """TLS-terminate `client`, TLS-connect to `upstream`, relay both ways, logging
    every decrypted application-data chunk in both directions."""
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
    client_end = End(client, server_ctx, server_side=True, name="C")
    up_end = End(up_sock, client_ctx, server_side=False, hostname=upstream[0], name="U")
    log(f"{_ts()} {peer} relay open → {upstream[0]}:{upstream[1]}")

    def on_client(chunk: bytes) -> None:
        log(f"{_ts()} C->U len={len(chunk)} hex={chunk.hex()}")

    def on_up(chunk: bytes) -> None:
        log(f"{_ts()} U->C len={len(chunk)} hex={chunk.hex()}")

    bridge(client_end, up_end, lambda m: log(f"{_ts()} {m}"), f"{peer}",
           on_client_data=on_client, on_up_data=on_up)


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
            log(f"{_ts()} {client.getpeername()[0]} not redirect-derived, "
                f"no original dst ({e!r}); ignoring")
            try:
                client.close()
            except OSError:
                pass
            continue
        threading.Thread(target=handle_connection,
                         args=(client, server_ctx, client_ctx, dst, log), daemon=True).start()


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "WM :47878 TLS relay").splitlines()[0])
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

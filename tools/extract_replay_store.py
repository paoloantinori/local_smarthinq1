"""Extract real LG response templates from a transparent-relay 46030 log.

Builds the replay store (data/replay/lg_responses.json) that server/replay.py
serves in place of synthetic XML. Each entry maps an endpoint to the REAL
response LG sent (headers + body), captured by the relay on a day the cloud
was healthy.

Usage: python3 tools/extract_replay_store.py <relay-46030-log> [--out <path>]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys


def _parse_response(buf: bytes) -> tuple[str, str] | None:
    """A complete HTTP response from buf, or None while headers/body are still short.

    The relay logs one line per chunk; a response spanning several chunks must be
    reassembled here or the store would hold a truncated body, later replayed
    mid-XML to the appliance (the same 'content looks wrong, appliance rejects'
    class of failure this tool exists to avoid)."""
    if not buf.startswith(b"HTTP"):
        return None
    head, sep, rest = buf.partition(b"\r\n\r\n")
    if not sep:
        return None
    m = re.search(rb"Content-Length:\s*(\d+)", head, re.IGNORECASE)
    if not m:
        return None
    if len(rest) < int(m.group(1)):
        return None
    return (head.decode("utf-8", "replace"),
            rest[:int(m.group(1))].decode("utf-8", "replace"))


def _keep(conn: dict, pairs: dict) -> None:
    """First complete response per endpoint wins (later ones are retries/dupes)."""
    if "response" in conn and "endpoint" in conn:
        ep = conn["endpoint"]
        if ep not in pairs:
            pairs[ep] = {
                "response_headers": conn["response"][0],
                "response_body": conn["response"][1],
            }


def extract(log_path: str) -> dict:
    with open(log_path, errors="replace") as f:
        lines = f.readlines()
    pairs: dict = {}
    conn: dict | None = None
    for line in lines:
        if "relay open" in line:
            conn = {}
            continue
        if "relay closed" in line and conn is not None:
            # a response without Content-Length (close-delimited) can only be
            # finalized now: headers + whatever body arrived before the close
            buf = conn.get("up_buf", b"")
            if "response" not in conn and buf.startswith(b"HTTP"):
                head, sep, rest = buf.partition(b"\r\n\r\n")
                if sep:
                    conn["response"] = (head.decode("utf-8", "replace"),
                                        rest.decode("utf-8", "replace"))
            _keep(conn, pairs)
            conn = None
            continue
        if conn is None:
            continue
        rm = re.search(r'C->U len=\d+ hex=([0-9a-f]+)', line)
        if rm and "endpoint" not in conn:
            text = bytes.fromhex(rm.group(1)).decode("utf-8", "replace")
            pm = re.search(r'POST (\S+)', text)
            if pm:
                conn["endpoint"] = pm.group(1)
        sm = re.search(r'U->C len=\d+ hex=([0-9a-f]+)', line)
        if sm and "response" not in conn:
            conn["up_buf"] = conn.get("up_buf", b"") + bytes.fromhex(sm.group(1))
            parsed = _parse_response(conn["up_buf"])
            if parsed is not None:
                conn["response"] = parsed
                _keep(conn, pairs)
    return pairs


def main() -> int:
    p = argparse.ArgumentParser(description=( __doc__ or "Extract replay store").splitlines()[0])
    p.add_argument("log_path", help="relay-46030 log file")
    p.add_argument("--out", default="data/replay/lg_responses.json")
    args = p.parse_args()

    pairs = extract(args.log_path)
    if not pairs:
        print("ERROR: no request/response pairs found", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    json.dump(pairs, open(args.out, "w"), indent=1)
    print(f"Replay store: {args.out} ({len(pairs)} endpoints)")
    for ep in pairs:
        print(f"  {ep}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

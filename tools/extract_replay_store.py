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


def extract(log_path: str) -> dict:
    lines = open(log_path, errors="replace").readlines()
    pairs = {}
    conn = None
    for line in lines:
        if "relay open" in line:
            conn = {}
            continue
        if "relay closed" in line and conn is not None:
            conn = None
            continue
        if conn is None:
            continue
        rm = re.search(r'C->U len=\d+ hex=([0-9a-f]+)', line)
        if rm and "endpoint" not in conn:
            raw = bytes.fromhex(rm.group(1))
            text = raw.decode("utf-8", "replace")
            pm = re.search(r'POST (\S+)', text)
            if pm:
                conn["endpoint"] = pm.group(1)
        sm = re.search(r'U->C len=\d+ hex=([0-9a-f]+)', line)
        if sm and "response_body" not in conn:
            raw = bytes.fromhex(sm.group(1))
            text = raw.decode("utf-8", "replace")
            if text.startswith("HTTP"):
                parts = text.split("\r\n\r\n", 1)
                conn["response_headers"] = parts[0]
                conn["response_body"] = parts[1] if len(parts) > 1 else ""
                if "endpoint" in conn:
                    ep = conn["endpoint"]
                    if ep not in pairs:
                        pairs[ep] = {
                            "response_headers": conn["response_headers"],
                            "response_body": conn["response_body"],
                        }
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

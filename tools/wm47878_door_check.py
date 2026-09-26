"""Door-verdict tool for the WM :47878 cleartext corpus (saved from the live session
2026-09-26, container /tmp/door_check.py; parameterized for the repo).

Reads a wm47878_tls_relay.py log, extracts every pump frame (C->U records whose Body
carries Format=B64 + Data), decodes the 28-byte monData, and groups the DISTINCT values
with their time windows. If door open/close cycles happened during the session, a door
bit would appear as extra distinct values clustered around those times: one distinct
value across the whole session = no door bit in the frame (the 2026-09-25 verdict,
PROTOCOL.md §4.4).

Usage: python3 tools/wm47878_door_check.py [relay-log]
       (default log path: the live corpus on the HAOS addon, for on-Pi use)
"""
from __future__ import annotations

import base64
import json
import re
import sys
from collections import OrderedDict

DEFAULT_LOG = "/data/relay-47878.log"


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else DEFAULT_LOG
    frames: list[tuple[str, str]] = []  # (ts, hex of the 28-byte monData)
    for line in open(path, errors="replace"):
        m = re.match(r"(\d\d:\d\d:\d\d\.\d+).*hex=([0-9a-f]+)", line)
        if not m or "C->U" not in line:
            continue
        try:
            raw = bytes.fromhex(m.group(2))
            txt = raw[4:].decode("utf-8", "replace")  # skip the 4-byte BE length
            body = json.loads(txt).get("Body", {})
            data = body.get("Data")
            # pump frames carry ReturnCode+Format+Data and NO Cmd; DevInfo also has
            # Format+Data but its Data is the RuleVer identity text, not monData
            if data and body.get("Format") == "B64" and "Cmd" not in body:
                frames.append((m.group(1), base64.b64decode(data).hex()))
        except (ValueError, json.JSONDecodeError):
            continue

    if not frames:
        print("no pump frames in the log")
        return 0

    first: "OrderedDict[str, str]" = OrderedDict()
    last: dict[str, str] = {}
    count: dict[str, int] = {}
    for ts, v in frames:
        if v not in first:
            first[v] = ts
            count[v] = 0
        last[v] = ts
        count[v] += 1

    print("total frames:", len(frames), "| distinct 28-byte values:", len(first))
    for i, v in enumerate(first, 1):
        print(f"{i}. {first[v]} -> {last[v]}  (x{count[v]})  {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

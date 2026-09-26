"""Timeline tool for the WM :46030 cleartext corpus (saved from the live session
2026-09-26, container /tmp/timeline.py; parameterized for the repo).

Reads a wm47878_tls_relay.py log captured on :46030 (plain HTTP inside TLS, no length
prefix) and prints the request/response ladder: every POST endpoint (with the appliance's
modelName when present) and LG's real HTTP status for each. This is the view that shows
an onboarding ladder completing green until the failing rung (the 2026-09-26 diagmon rot:
everything 200, then diagmon 502, app pairing stuck at 99%; PROTOCOL.md §2).

Usage: python3 tools/wm46030_timeline.py [relay-log] [from-time HH:MM:SS] [tail N]
       (default log path: the live corpus on the HAOS addon, for on-Pi use; an
       unpadded from-time hour like 9:00:00 is accepted and zero-padded)
"""
from __future__ import annotations

import re
import sys

DEFAULT_LOG = "/data/relay-46030.log"


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else DEFAULT_LOG
    since = (argv[2] if len(argv) > 2 else "00:00:00").zfill(8)
    tail = int(argv[3]) if len(argv) > 3 else 30

    events: list[tuple[str, str, str]] = []
    for line in open(path, errors="replace"):
        m = re.match(r"(\d\d:\d\d:\d\d)\.\d+ .*?(C->U|U->C) len=\d+ hex=([0-9a-f]+)", line)
        if not m:
            continue
        ts, direction, hx = m.group(1), m.group(2), m.group(3)
        if ts < since:
            continue
        try:
            txt = bytes.fromhex(hx).decode("utf-8", "replace")
        except ValueError:
            continue
        if direction == "C->U":
            p = re.search(r"POST (\S+)", txt)
            model = re.search(r"modelName>([A-Z0-9_]+)<", txt)
            if p:
                tag = model.group(1) if model else ""
                events.append((ts, "APPLIANCE->LG",
                               p.group(1).split("/")[-1] + (f" [{tag}]" if tag else "")))
        else:
            s = re.search(r"HTTP/1\.1 (\d+)", txt)
            if s:
                events.append((ts, "LG->APPLIANCE", s.group(1)))

    for ts, d, what in events[-tail:]:
        print(ts, d, what)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Size cap for the relay logs on /data (TASK-130).

The transparent relays append forever (~13-75 MB/day measured, pre-mortem 2026-10-29);
on the HAOS SD card that is a slow but certain disk death. The cap TRUNCATES IN PLACE:
a rename/rotate would orphan the wm_local_bridge follow loop, which tails the fd and
would keep reading the dead inode forever (its shrink check, getsize < tell, only
understands truncation, not replacement). The tail of the file is preserved so the
bridge's restart replay (last 3000 lines) still has context.

Env: LGM_RELAY_LOG_CAP_MB (default 200, 0 disables). Env-only on purpose: no form
option, no schema churn.
"""
from __future__ import annotations

import glob
import os
import sys
import threading
import time
from typing import Optional

CAP_MB = float(os.environ.get("LGM_RELAY_LOG_CAP_MB", "200") or 200)
CHECK_INTERVAL = 300.0  # 5 min: at the measured growth this is far inside any cap


def truncate_oversized(paths: list[str], cap_bytes: int, keep_bytes: int = 0) -> dict[str, int]:
    """Truncate files over cap_bytes in place, keeping the last keep_bytes (default cap//4).

    Returns {path: new_size} for the files actually truncated."""
    truncated: dict[str, int] = {}
    if cap_bytes <= 0:
        return truncated
    if keep_bytes <= 0:
        keep_bytes = cap_bytes // 4
    for path in paths:
        try:
            with open(path, "r+b") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                if size <= cap_bytes:
                    continue
                fh.seek(max(0, size - keep_bytes), os.SEEK_SET)
                tail = fh.read()
                # drop the partial first line: a hex/timestamp frame cut in half
                # would poison the bridge's parser on replay
                nl = tail.find(b"\n")
                tail = tail[nl + 1:] if nl >= 0 else tail
                fh.seek(0)
                fh.write(tail)
                fh.truncate()
                truncated[path] = len(tail)
        except OSError as e:
            sys.stderr.write(f"[logcap] {path}: {e}\n")
    return truncated


def relay_log_paths(state_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(state_dir, "relay-*.log")))


def start_logcap_thread(state_dir: str, cap_mb: float = CAP_MB,
                        interval: float = CHECK_INTERVAL) -> Optional[threading.Thread]:
    """Daemon thread capping the relay logs every `interval` seconds."""
    cap_bytes = int(cap_mb * 1024 * 1024)
    if cap_bytes <= 0:
        sys.stderr.write(f"[logcap] disabled (LGM_RELAY_LOG_CAP_MB={cap_mb})\n")
        return None

    def _loop() -> None:
        while True:
            done = truncate_oversized(relay_log_paths(state_dir), cap_bytes)
            for path, new_size in done.items():
                sys.stderr.write(f"[logcap] truncated {path} to {new_size} bytes "
                                 f"(cap {cap_bytes})\n")
            time.sleep(interval)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    sys.stderr.write(f"[logcap] capping relay logs in {state_dir} at {cap_mb:g} MB "
                     f"every {interval:g}s\n")
    return t

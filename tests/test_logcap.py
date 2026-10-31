"""Relay log size cap (TASK-130): in-place truncation, tail preserved, bridge-safe.

The wm_local_bridge follow loop survives truncation (getsize < tell -> seek(0)) but
NOT replacement: the cap must truncate in place, never rename. The kept tail must
start at a line boundary (a half hex frame would poison the parser's replay)."""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import logcap  # noqa: E402


def test_over_cap_truncated_in_place_tail_kept() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-x.log")
        lines = [f"12:00:{i:02d}.000 C->U len=10 hex=deadbeef{i:04d}\n" for i in range(2000)]
        with open(p, "w") as fh:
            fh.writelines(lines)
        done = logcap.truncate_oversized([p], cap_bytes=10000, keep_bytes=4000)
        assert p in done, "file over cap must be truncated"
        with open(p) as fh:
            kept = fh.read()
        # tail preserved: the LAST line survives whole
        assert kept.endswith("deadbeef1999\n"), kept[-60:]
        # starts at a line boundary (no half frame)
        assert kept.startswith("12:00:"), kept[:40]
        assert len(kept) < len("".join(lines))


def test_under_cap_untouched() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-x.log")
        with open(p, "w") as fh:
            fh.write("small\n")
        done = logcap.truncate_oversized([p], cap_bytes=1024)
        assert done == {}
        assert open(p).read() == "small\n"


def test_cap_zero_disables() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-x.log")
        with open(p, "w") as fh:
            fh.write("x" * 100000)
        assert logcap.truncate_oversized([p], cap_bytes=0) == {}
        assert os.path.getsize(p) == 100000


def test_bridge_follow_survives_truncation() -> None:
    """The exact check wm_local_bridge runs: after truncation its offset exceeds
    the size, and the documented recovery is seek(0). Simulated here to lock the
    contract the cap relies on."""
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "relay-x.log")
        with open(p, "w") as fh:
            fh.write("a" * 50000 + "\nline1\nline2\n")
        fh = open(p, "rb")
        fh.seek(0, os.SEEK_END)
        offset_before = fh.tell()
        logcap.truncate_oversized([p], cap_bytes=10000, keep_bytes=5000)
        size_after = os.path.getsize(p)
        assert offset_before > size_after  # bridge's shrink condition fires
        fh.seek(0)  # its documented recovery
        head = fh.read(20)
        fh.close()
        assert head.startswith(b"line") or head.startswith(b"\nline") or b"\n" in head


if __name__ == "__main__":
    test_over_cap_truncated_in_place_tail_kept()
    test_under_cap_untouched()
    test_cap_zero_disables()
    test_bridge_follow_survives_truncation()
    print("PASS logcap tests")

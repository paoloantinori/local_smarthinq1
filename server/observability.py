"""Per-appliance last-frame observability (TASK-129).

The rig cannot tell "appliance powered off" from "leg broken" (pre-mortem 2026-10-29:
the washer's DNAT counters sat at 0 for days with zero signal anywhere). This module
publishes a `last_frame_age` sensor per appliance: seconds since that appliance was
last seen on ANY local channel:

- the transparent-relay logs (the ONLY source that sees the dryer's idle 1 Hz push,
  which never generates cloud events: without it an idle dryer reads as stale);
- the state store's latest ingest ts (pump :47878, diagmon :46030, cloud events).

The age is published through the MQTT sink as a plain merged-view field: a new field
key auto-creates its HA sensor (mqtt_bridge._Sink). Alerting on a threshold is an HA
automation, deliberately not server logic. Relay timestamps carry NO date: the epoch
is reconstructed and a >5 min "future" reading is attributed to yesterday (writer
crossed midnight; the logcap keeps files short so multi-day ambiguity cannot build).
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional

TAIL_BYTES = 65536  # at ~1 Hz the dryer fills this in well under a minute
CHECK_INTERVAL = 60.0
_DEV_ID = re.compile(rb'"x-lgedm-deviceId":"([0-9a-f-]+)"')
_LINE_TS = re.compile(r"^(\d{2}:\d{2}:\d{2})\.\d+\s")

# Capture-derived local devIds (flows/, wm-bridge service): the store learns
# devId→model only from :46030 reports, which NEVER arrive in the relay posture
# (production since 2026-10-28): without this map the relay sightings would be
# filtered out and nothing would ever publish. Union at runtime with the store map.
KNOWN_DEVICES: dict[str, str] = {
    "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d": "WTWN3",
    "93b55500-4b3a-11f1-ad68-7440be73f9ad": "RC90U2_WW",
}


def _sighting_epoch(hhmmss: str, now_epoch: float) -> float:
    """Reconstruct an epoch from a date-less HH:MM:SS log stamp (container-local time).

    A candidate more than 5 min in the future means the writer crossed midnight just
    before now: the stamp is from yesterday."""
    hh, mm, ss = (int(x) for x in hhmmss.split(":"))
    now = datetime.fromtimestamp(now_epoch)
    cand = now.replace(hour=hh, minute=mm, second=ss, microsecond=0).timestamp()
    if cand > now_epoch + 300:
        cand -= 86400
    return cand


def scan_tail(path: str, now_epoch: float, tail_bytes: int = TAIL_BYTES) -> dict[str, float]:
    """{devId: last sighting epoch} from the tail of one relay log."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - tail_bytes), os.SEEK_SET)
            blob = fh.read()
    except OSError:
        return {}
    out: dict[str, float] = {}
    for raw in blob.split(b"\n"):
        line = raw.decode("utf-8", "replace")
        m = _LINE_TS.match(line)
        if not m:
            continue
        hexm = re.search(r"hex=([0-9a-fA-F]+)", line)
        if not hexm:
            continue
        try:
            text = bytes.fromhex(hexm.group(1))
        except ValueError:
            continue
        for dev in _DEV_ID.findall(text):
            out[dev.decode()] = _sighting_epoch(m.group(1), now_epoch)
    return out


def store_ages(store, now_epoch: float) -> dict[str, float]:
    """{devId: age} from the store's latest ingest timestamps."""
    out: dict[str, float] = {}
    for dev, view in store.latest.items():
        if dev not in store.model_by_devid:
            continue  # cloud-hash keys mirror a local appliance; skip the duplicate
        ts = view.get("ts")
        if not ts:
            continue
        try:
            epoch = datetime.fromisoformat(ts).timestamp()
        except ValueError:
            continue
        out[dev] = max(0.0, now_epoch - epoch)
    return out


def publish_ages(state_dir: str, store, sink, now_epoch: Optional[float] = None) -> dict[str, float]:
    """One pass: scan relay logs + store, publish `last_frame_age` per known device.

    Returns the {devId: age} map (for tests). Devices never seen on any channel are
    not published (a sensor for them would read as a false alarm)."""
    if now_epoch is None:
        now_epoch = time.time()
    models: dict[str, str] = dict(KNOWN_DEVICES)
    models.update(getattr(store, "model_by_devid", {}) or {})
    relay: dict[str, float] = {}
    for name in ("relay-47878-1025.log", "relay-46030-1025.log"):
        relay.update(scan_tail(os.path.join(state_dir, name), now_epoch))
    ages: dict[str, float] = {}
    for dev, seen in relay.items():
        if dev in models:
            ages[dev] = max(0.0, now_epoch - seen)
    for dev, age in store_ages(store, now_epoch).items():
        if dev in models and (dev not in ages or age < ages[dev]):
            ages[dev] = age
    for dev, age in ages.items():
        model = models.get(dev)
        if not model or sink is None:
            continue
        try:
            sink(dev, {"modelName": model, "last_frame_age": str(int(age))})
        except Exception as e:  # observability must never hurt the sink's caller
            sys.stderr.write(f"[observability] sink publish failed for {dev[:8]}: {e}\n")
    return ages


def start_observability(state_dir: str, store, sink,
                        interval: float = CHECK_INTERVAL) -> Optional[threading.Thread]:
    t = threading.Thread(target=_loop, args=(state_dir, store, sink, interval), daemon=True)
    t.start()
    sys.stderr.write(f"[observability] last_frame_age every {interval:g}s "
                     f"(relay tails + store)\n")
    return t


def _loop(state_dir: str, store, sink, interval: float) -> None:
    while True:
        try:
            publish_ages(state_dir, store, sink)
        except Exception as e:
            sys.stderr.write(f"[observability] pass failed: {e}\n")
        time.sleep(interval)

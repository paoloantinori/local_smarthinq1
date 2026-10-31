"""Per-appliance last-frame observability (TASK-129).

The rig cannot tell "appliance powered off" from "leg broken" (pre-mortem 2026-10-29:
the washer's DNAT counters sat at 0 for days with zero signal anywhere). This module
publishes a `last_frame_age` sensor per MODEL: seconds since that appliance was last
seen on ANY local channel:

- the transparent-relay logs: :47878 carries the JSON header form of the deviceId;
  :46030 carries the diagmon XML <devId> form (both matched). The relay tail is the
  ONLY source that sees the dryer's idle 1 Hz push, which never generates cloud
  events (without it an idle dryer reads as stale);
- the state store's latest views: per-MODEL (each view carries its modelName), so
  cloud events reach the sensor even though store.model_by_devid stays empty in the
  relay posture, and the fridge (never in the WM relay logs) still gets a sensor
  from its cloud events (code-review 2026-10-30).

Age is keyed on MODEL and computed as the minimum across sources: a fresh cloud
event overrides a stale relay sighting (review finding: after an appliance
re-registration a ghost devId could otherwise publish a growing age into the same
model key). Relay timestamps carry NO date: the epoch is reconstructed and a >5 min
"future" reading is attributed to yesterday (writer crossed midnight). The boot seed
scans whole files, so a days-old sighting can reconstruct 0-24 h stale, not "exactly
one day": alert thresholds must tolerate that.

Alerting on an age threshold is an HA automation, deliberately not server logic.
"""
from __future__ import annotations

import glob
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Optional

TAIL_BYTES = 65536  # at ~1 Hz the dryer fills this in well under a minute
CHECK_INTERVAL = 60.0
MAX_MEMORY_AGE = 7 * 86400  # a sighting this old tracks a dead identity: drop it
_JSON_DEV = re.compile(rb'"x-lgedm-deviceId":"([0-9a-f-]+)"')
_XML_DEV = re.compile(rb"<devId>([0-9a-f-]+)</devId>")
_LINE_TS = re.compile(r"^(\d{2}:\d{2}:\d{2})\.\d+\s")

# Capture-derived local devIds (flows/, wm-bridge service): the store learns
# devId→model only from :46030 reports, which never arrive in the relay posture
# (production since 2026-10-28): without this map the relay sightings would be
# filtered out and nothing would ever publish. Union at runtime with the store map.
KNOWN_DEVICES: dict[str, str] = {
    "82c2ebc0-0e9f-11f5-82b4-0051eda91d3d": "WTWN3",
    "93b55500-4b3a-11f1-ad68-7440be73f9ad": "RC90U2_WW",
}


def relay_log_paths(state_dir: str) -> list[str]:
    """Glob, not fixed names: the -1025 suffix already changed once (2026-10-25
    reprovision) and a silent rename must not blind the sensor (code-review)."""
    return sorted(glob.glob(os.path.join(state_dir, "relay-*.log")))


def _sighting_epoch(hhmmss: str, now_epoch: float) -> float:
    """Reconstruct an epoch from a date-less HH:MM:SS log stamp (container-local time).

    A candidate more than 5 min in the future means the writer crossed midnight just
    before now: the stamp is from yesterday. DST-transition nights put this off by
    up to an hour for sightings from the changeover window (accepted: bounded)."""
    hh, mm, ss = (int(x) for x in hhmmss.split(":"))
    now = datetime.fromtimestamp(now_epoch)
    cand = now.replace(hour=hh, minute=mm, second=ss, microsecond=0).timestamp()
    if cand > now_epoch + 300:
        cand -= 86400
    return cand


def scan_tail(path: str, now_epoch: float, tail_bytes: Optional[int] = TAIL_BYTES) -> dict[str, float]:
    """{devId: last sighting epoch} from the tail (or whole file) of one relay log."""
    try:
        size = os.path.getsize(path)
        start = 0 if tail_bytes is None else max(0, size - tail_bytes)
        with open(path, "rb") as fh:
            fh.seek(start, os.SEEK_SET)
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
        devs = _JSON_DEV.findall(text) + _XML_DEV.findall(text)
        for dev in devs:
            out[dev.decode()] = _sighting_epoch(m.group(1), now_epoch)
    return out


def store_model_ages(store, now_epoch: float) -> dict[str, float]:
    """{modelName: age} from the store's latest views (each view carries modelName).

    Keyed on MODEL, not devId: cloud-event keys are per-registration hashes with an
    EMPTY devId→model map in the relay posture; the modelName inside the view is
    the only identity they carry, and the fridge survives on this path alone."""
    out: dict[str, float] = {}
    for view in store.latest.values():
        model, ts = view.get("modelName"), view.get("ts")
        if not model or not ts:
            continue
        try:
            epoch = datetime.fromisoformat(ts).timestamp()
        except ValueError:
            continue
        age = max(0.0, now_epoch - epoch)
        if model not in out or age < out[model]:
            out[model] = age
    return out


def publish_ages(state_dir: str, store, sink, now_epoch: Optional[float] = None,
                 memory: Optional[dict] = None) -> dict[str, float]:
    """One pass: scan relay logs + store, publish `last_frame_age` per model.

    `memory` (owned by the thread) carries the last sighting epoch per devId ACROSS
    passes; entries older than MAX_MEMORY_AGE (or whose devId lost its model map)
    are pruned, so a dead identity cannot publish a growing age forever. Lost on
    restart: the boot seed rebuilds it from whole-file scans. A fresh cloud event
    (store, per model) lowers the age below any stale sighting. Returns
    {modelName: age} (for tests)."""
    if now_epoch is None:
        now_epoch = time.time()
    dev_models: dict[str, str] = dict(KNOWN_DEVICES)
    dev_models.update(getattr(store, "model_by_devid", {}) or {})

    fresh: dict[str, float] = {}
    for name in relay_log_paths(state_dir):
        for dev, seen in scan_tail(os.path.join(state_dir, name), now_epoch).items():
            fresh[dev] = max(seen, fresh.get(dev, 0.0))

    latest: dict[str, float] = dict(memory) if memory else {}
    for dev, seen in fresh.items():
        latest[dev] = max(seen, latest.get(dev, 0.0))
    for dev in list(latest):
        if not dev_models.get(dev) or now_epoch - latest[dev] > MAX_MEMORY_AGE:
            del latest[dev]
    if memory is not None:
        memory.clear()
        memory.update(latest)

    model_seen: dict[str, float] = {}
    for dev, seen in latest.items():
        model = dev_models[dev]
        model_seen[model] = max(seen, model_seen.get(model, 0.0))
    for model, age in store_model_ages(store, now_epoch).items():
        model_seen[model] = max(now_epoch - age, model_seen.get(model, 0.0))

    ages = {model: max(0.0, now_epoch - seen) for model, seen in model_seen.items()}
    for model, age in ages.items():
        dev = next((d for d, m in dev_models.items() if m == model), None)
        if dev is None:
            dev = next((k for k, v in store.latest.items()
                        if v.get("modelName") == model), model)
        if sink is None:
            continue
        try:
            sink(dev, {"modelName": model, "last_frame_age": str(int(age))})
        except Exception as e:  # observability must never hurt the sink's caller
            sys.stderr.write(f"[observability] sink publish failed for {model}: {e}\n")
    return ages


def start_observability(state_dir: str, store, sink,
                        interval: float = CHECK_INTERVAL) -> Optional[threading.Thread]:
    t = threading.Thread(target=_loop, args=(state_dir, store, sink, interval), daemon=True)
    t.start()
    sys.stderr.write(f"[observability] last_frame_age every {interval:g}s "
                     f"(relay tails + store)\n")
    return t


def _seed_memory(state_dir: str, store, now_epoch: Optional[float] = None) -> dict[str, float]:
    """One-shot WHOLE-FILE scan at boot: seed the sighting memory so a device idle
    since before the boot (its last frame below the tail window) still gets its
    stale age published instead of waiting to be seen once."""
    if now_epoch is None:
        now_epoch = time.time()
    dev_models: dict[str, str] = dict(KNOWN_DEVICES)
    dev_models.update(getattr(store, "model_by_devid", {}) or {})
    seed: dict[str, float] = {}
    for name in relay_log_paths(state_dir):
        for dev, seen in scan_tail(os.path.join(state_dir, name), now_epoch,
                                   tail_bytes=None).items():
            model = dev_models.get(dev)
            if model:
                seed[dev] = max(seen, seed.get(dev, 0.0))
    return seed


def _loop(state_dir: str, store, sink, interval: float) -> None:
    memory: dict[str, float] = _seed_memory(state_dir, store)
    if memory:
        sys.stderr.write(f"[observability] boot seed: {len(memory)} device(s) "
                         "with prior sightings\n")
    while True:
        try:
            publish_ages(state_dir, store, sink, memory=memory)
        except Exception as e:
            sys.stderr.write(f"[observability] pass failed: {e}\n")
        time.sleep(interval)

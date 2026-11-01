"""Per-appliance last-frame observability (TASK-129) + cycle-end events (TASK-129 bis).

The rig cannot tell "appliance powered off" from "leg broken" (pre-mortem 2026-10-29).
This module publishes a `last_frame_age` sensor per model: seconds since that
appliance was last seen on ANY local channel.

Design notes that encode hard-won facts (code-review 2026-10-31..11-01):

- The relay logs follow the tail INCREMENTALLY by file offset: every line is
  processed exactly once. The stamps are date-less (wm_relay writes UTC HH:MM:SS),
  so a window re-read can replay yesterday's EventMonitoring END as a "fresh" event
  the next day at the same time-of-day (real production log: a quiet day holds
  >24 h of tail). Offsets make that structurally impossible.
- The washer announces cycle end on :46030, NOT on :47878 (measured 2026-11-01: the
  :47878 pump stops at SPINNING rem=1m while :46030 carries EventMonitoring reports
  whose monData decodes to END then POWER_OFF). Event delivery is intermittent on
  every channel (push missed 31/10, :46030 EventMonitoring missed 01/11): a derived
  completion rule (SPINNING low-remaining + silence) is tracked separately.
- Relay timestamps are UTC. A >5 min "future" reading is attributed to yesterday
  (writer crossed midnight); DST-transition nights can be off by an hour (bounded).
- Alerting on an age threshold is an HA automation, deliberately not server logic.
"""
from __future__ import annotations

import base64
import glob
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

CHECK_INTERVAL = 60.0
MAX_MEMORY_AGE = 7 * 86400  # a sighting this old tracks a dead identity: drop it
_JSON_DEV = re.compile(rb'"x-lgedm-deviceId":"([0-9a-f-]+)"')
_XML_DEV = re.compile(rb"<devId>([0-9a-f-]+)</devId>")
_LINE_TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2})")
_EVENT_MONITORING = "<diagMonType>EventMonitoring<"
_DIAG_DATA = re.compile(r"<diagMonData>([A-Za-z0-9+/=]+)</diagMonData>")
_EVENT_TYPE = re.compile(r"<eventType>([^<]+)</eventType>")
_MON_DATA = re.compile(r"<monData>([A-Za-z0-9+/=]+)</monData>")

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
    reprovision) and a silent rename must not blind the sensor."""
    return sorted(glob.glob(os.path.join(state_dir, "relay-*.log")))


def _sighting_epoch(hhmmss: str, now_epoch: float) -> float:
    """Reconstruct an epoch from a date-less UTC HH:MM:SS log stamp.

    A candidate more than 5 min in the future means the writer crossed midnight just
    before now: the stamp is from yesterday. DST-transition nights can be off by an
    hour for sightings from the changeover window (accepted: bounded)."""
    hh, mm, ss = (int(x) for x in hhmmss.split(":"))
    now = datetime.fromtimestamp(now_epoch, tz=timezone.utc)
    cand = now.replace(hour=hh, minute=mm, second=ss, microsecond=0).timestamp()
    if cand > now_epoch + 300:
        cand -= 86400
    return cand


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


class RelayWatcher:
    """Incremental follower of the relay logs: every line processed exactly once.

    `poll()` returns (sightings, events) from lines appended since the previous
    call: sightings {devId: epoch}; events {modelName: (epoch, State)} for
    :46030 EventMonitoring reports (the WM cycle-end announcements). The first
    poll starts at EOF: historical lines feed the age seed, never the events
    (a same-time-of-day replay of an old END is structurally impossible)."""

    def __init__(self, state_dir: str, dev_models: dict[str, str]) -> None:
        self.state_dir = state_dir
        self.dev_models = dict(dev_models)
        self._offsets: dict[str, int] = {}

    def poll(self, now_epoch: Optional[float] = None) -> tuple[dict, dict]:
        if now_epoch is None:
            now_epoch = time.time()
        sightings: dict[str, float] = {}
        events: dict[str, tuple[float, str]] = {}
        for path in relay_log_paths(self.state_dir):
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            off = self._offsets.get(path)
            if off is None:
                # first sight: arm at EOF (no history replay) and REMEMBER it,
                # or the delta after this poll would be invisible forever
                off = size
                self._offsets[path] = size
            if size < off:  # logcap truncated in place: restart from the top
                off = 0
            if size == off:
                continue
            self._offsets[path] = size
            try:
                with open(path, "rb") as fh:
                    fh.seek(off, os.SEEK_SET)
                    blob = fh.read()
            except OSError:
                continue
            for raw in blob.split(b"\n"):
                line = raw.decode("utf-8", "replace")
                m = _LINE_TS.match(line)
                if not m:
                    continue
                hexm = re.search(r"hex=([0-9a-fA-F]+)", line)
                if not hexm:
                    continue
                try:
                    txt = bytes.fromhex(hexm.group(1)).decode("utf-8", "replace")
                except ValueError:
                    continue
                seen = _sighting_epoch(m.group(0), now_epoch)
                for dev in _JSON_DEV.findall(txt.encode()) + _XML_DEV.findall(txt.encode()):
                    sightings[dev.decode()] = max(seen, sightings.get(dev.decode(), 0.0))
                if _EVENT_MONITORING not in txt:
                    continue
                model = self._event_model(self.dev_models, txt)
                state = self._event_state(model, txt)
                if model and state:
                    events[model] = (seen, state)
        return sightings, events

    def _event_model(self, dev_models: dict[str, str], txt: str) -> Optional[str]:
        dev = _XML_DEV.search(txt.encode())
        if dev is not None:
            model = dev_models.get(dev.group(1).decode())
            if model:
                return model
        name = re.search(r"<modelName>([^<]+)</modelName>", txt)
        if name is not None:
            return name.group(1)
        return None

    def _event_state(self, model: Optional[str], txt: str) -> Optional[str]:
        b64 = _DIAG_DATA.search(txt)
        if b64 is None or not model:
            return None
        from server.models import registry  # local import: decode on demand only
        try:
            # eventType e monData stanno DENTRO il base64 di diagMonData
            xml = base64.b64decode(b64.group(1)).decode("utf-8", "replace")
            et = _EVENT_TYPE.search(xml)
            mon = _MON_DATA.search(xml)
            if et is None or mon is None or et.group(1) != "WM_STATE":
                return None
            d = registry.decode_mondata(base64.b64decode(mon.group(1)),
                                        model).get("monData_decoded") or {}
            return d.get("State")
        except Exception:
            return None


def seed_memory(state_dir: str, store, now_epoch: Optional[float] = None) -> dict[str, float]:
    """One-shot WHOLE-FILE scan: seed the age memory so a device idle since before
    the boot still gets its stale age published. Events are deliberately NOT
    seeded (see RelayWatcher): no same-time-of-day END replays at boot."""
    if now_epoch is None:
        now_epoch = time.time()
    dev_models: dict[str, str] = dict(KNOWN_DEVICES)
    dev_models.update(getattr(store, "model_by_devid", {}) or {})
    memory: dict[str, float] = {}
    for path in relay_log_paths(state_dir):
        for dev, seen in scan_whole_file(path, now_epoch).items():
            memory[dev] = max(seen, memory.get(dev, 0.0))
    return memory


def scan_whole_file(path: str, now_epoch: float) -> dict[str, float]:
    """{devId: last sighting epoch} over the WHOLE file (boot seed for ages)."""
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return {}
    out: dict[str, float] = {}
    for raw in blob.split(b"\n"):
        m = _LINE_TS.match(raw.decode("utf-8", "replace"))
        hexm = re.search(r"hex=([0-9a-fA-F]+)", raw.decode("utf-8", "replace"))
        if not m or not hexm:
            continue
        try:
            txt = bytes.fromhex(hexm.group(1)).decode("utf-8", "replace")
        except ValueError:
            continue
        seen = _sighting_epoch(m.group(0), now_epoch)
        for dev in _JSON_DEV.findall(txt.encode()) + _XML_DEV.findall(txt.encode()):
            out[dev.decode()] = max(seen, out.get(dev.decode(), 0.0))
    return out


def publish_ages(store, sink, memory: dict[str, float],
                 sightings: dict[str, float], events: dict[str, tuple[float, str]],
                 now_epoch: Optional[float] = None) -> dict[str, float]:
    """Publish the per-model `last_frame_age` (+ fresh EventMonitoring State).

    Ages: memory (carried across passes, pruned at MAX_MEMORY_AGE) lowered by the
    pass's sightings and by fresh cloud events (store, per model). Events: a fresh
    :46030 EventMonitoring State flows into the payload so HA sees the completion
    even though the :47878 pump never emitted it. Returns {modelName: age}."""
    if now_epoch is None:
        now_epoch = time.time()
    dev_models: dict[str, str] = dict(KNOWN_DEVICES)
    dev_models.update(getattr(store, "model_by_devid", {}) or {})

    for dev, seen in sightings.items():
        memory[dev] = max(seen, memory.get(dev, 0.0))
    for dev in list(memory):
        if not dev_models.get(dev) or now_epoch - memory[dev] > MAX_MEMORY_AGE:
            del memory[dev]

    model_seen: dict[str, float] = {}
    for dev, seen in memory.items():
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
        payload = {"modelName": model, "last_frame_age": str(int(age))}
        if model in events:
            payload["State"] = events[model][1]  # fine ciclo visto su :46030
        try:
            sink(dev, payload)
        except Exception as e:  # observability must never hurt the sink's caller
            sys.stderr.write(f"[observability] sink publish failed for {model}: {e}\n")
    return ages


def model_of_event(dev_models: dict[str, str], store, dev: str) -> Optional[str]:
    model = dev_models.get(dev)
    if model:
        return model
    for view in store.latest.values():
        if view.get("modelName") and dev in str(view):
            return view.get("modelName")
    return None


def start_observability(state_dir: str, store, sink,
                        interval: float = CHECK_INTERVAL) -> Optional[threading.Thread]:
    t = threading.Thread(target=_loop, args=(state_dir, store, sink, interval), daemon=True)
    t.start()
    sys.stderr.write(f"[observability] last_frame_age every {interval:g}s "
                     f"(incremental relay tails + store)\n")
    return t


def _loop(state_dir: str, store, sink, interval: float) -> None:
    memory = seed_memory(state_dir, store)
    watcher = RelayWatcher(state_dir, {**KNOWN_DEVICES,
                                       **(getattr(store, "model_by_devid", {}) or {})})
    while True:
        try:
            sightings, events = watcher.poll()
            publish_ages(store, sink, memory, sightings, events)
        except Exception as e:
            sys.stderr.write(f"[observability] pass failed: {e}\n")
        time.sleep(interval)

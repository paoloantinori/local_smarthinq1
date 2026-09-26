"""Per-device diagmon state store (M1 / TASK-012).

Holds the latest decoded state per `devId` in memory and appends every report to a JSONL
event log under the state dir. The report XML is parsed once here for devId + identity, which
is forwarded to the registry so it does not re-parse. Payloads flagged ``diagMonType=UNKNOWN``
(a device with no registered decoder) are surfaced on stderr, not stored — they are
diagnostics, not state.
"""
from __future__ import annotations

import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Callable, Optional

from .models import registry

# Optional sink notified after each successfully-ingested payload (devId, payload). Called
# synchronously from the ingest thread, so it must be effectively non-blocking. None = off.
StateSink = Callable[[str, dict], None]


class DeviceStateStore:
    def __init__(self, state_dir: str, on_state: Optional[StateSink] = None):
        self.latest: dict[str, dict] = {}
        # devId -> modelName, learned from every 46030 report; the :47878 pump's
        # messages carry only a deviceId (PROTOCOL.md §4.4), so its ingest resolves
        # the model through this map (TASK-078).
        self.model_by_devid: dict[str, str] = {}
        self._warned: set[tuple[str, str]] = set()  # per-device once-only warnings
        self.on_state = on_state
        os.makedirs(state_dir, exist_ok=True)
        self.log_path = os.path.join(state_dir, "diagmon.jsonl")

    def ingest_report(self, report_xml: str | bytes) -> list[dict]:
        """Decode a <Report> diagmon body, store latest per devId, append to JSONL."""
        if isinstance(report_xml, bytes):
            report_xml = report_xml.decode("utf-8", "replace")
        root = ET.fromstring(report_xml)
        dev_id = (root.findtext("devId") or "").strip()
        model_name = (root.findtext("modelName") or "").strip()
        raw_type = (root.findtext("devType") or "").strip()
        try:
            device_type = int(raw_type) if raw_type else None
        except ValueError:
            device_type = None
        ts = datetime.now(timezone.utc).isoformat()
        if dev_id and model_name:
            self.model_by_devid[dev_id] = model_name
        payloads = registry.decode_report(
            report_xml, model_name=model_name, device_type=device_type)
        for p in payloads:
            p["devId"] = dev_id
            p["modelName"] = model_name
            p["ts"] = ts
            if p.get("diagMonType") == "UNKNOWN":
                sys.stderr.write(
                    f"[state] unknown device devId={dev_id} model={model_name} "
                    f"type={device_type}: {p.get('note')}\n")
                continue
            self._commit(dev_id, p)
        return payloads

    def ingest_mondata(self, dev_id: str, blob: bytes) -> Optional[dict]:
        """Ingest a raw monData blob pushed on the :47878 pump (TASK-078).

        The model is resolved via the devId→modelName map learned from 46030 reports.
        Degraded outcomes (unmapped devId, missing modelJson, decode failure) are
        logged ONCE per device+reason and NOT committed: at ~1.5 Hz a per-frame note
        would flood the JSONL and park a field-less payload in latest (mirrors
        ingest_report skipping UNKNOWN diagnostics)."""
        model_name = self.model_by_devid.get(dev_id)
        if not model_name:
            self._warn_once(dev_id, "unmapped",
                            f"[state] pump data for unmapped devId={dev_id[:8]}; waiting "
                            "for its first 46030 report to learn the model")
            return None
        p = registry.decode_mondata(blob, model_name)
        if "monData_decoded" not in p:
            self._warn_once(dev_id, f"note:{p.get('note')}",
                            f"[state] pump decode degraded for {model_name}: "
                            f"{p.get('note')}")
            return None
        p["devId"] = dev_id
        p["modelName"] = model_name
        p["ts"] = datetime.now(timezone.utc).isoformat()
        self._commit(dev_id, p)
        return p

    def _warn_once(self, dev_id: str, reason: str, message: str) -> None:
        key = (dev_id, reason)
        if key in self._warned:
            return
        self._warned.add(key)
        sys.stderr.write(message + "\n")

    def _commit(self, dev_id: str, p: dict) -> None:
        """Store latest per devId, append to the JSONL, fire the sink. Shared by the
        diagmon and pump ingests; a sink failure must never break ingestion."""
        self.latest[dev_id] = p
        with open(self.log_path, "a") as f:
            f.write(json.dumps(p, default=str) + "\n")
        if self.on_state is not None:
            try:
                self.on_state(dev_id, p)
            except Exception as e:
                sys.stderr.write(f"[state] on_state sink failed: {e}\n")

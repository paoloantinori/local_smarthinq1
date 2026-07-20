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

# Optional sink notified after each successfully-ingested payload (devId, payload). Used by
# the MQTT bridge (TASK-064) to publish state on ingest; None = no sink.
StateSink = Callable[[str, dict], None]


class DeviceStateStore:
    def __init__(self, state_dir: str, on_state: Optional[StateSink] = None):
        self.latest: dict[str, dict] = {}
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
            self.latest[dev_id] = p
            with open(self.log_path, "a") as f:
                f.write(json.dumps(p, default=str) + "\n")
            if self.on_state is not None:
                try:
                    self.on_state(dev_id, p)
                except Exception as e:  # a sink failure must never break ingestion
                    sys.stderr.write(f"[state] on_state sink failed: {e}\n")
        return payloads

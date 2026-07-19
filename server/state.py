"""Per-device diagmon state store (M1 / TASK-012).

Holds the latest decoded state per `devId` in memory and appends every report to a JSONL
event log under the state dir. Decoding reuses the per-model decoder
(``server.models.washer_wtwn3``). No field interpretation beyond what the decoder confirms.
"""
from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from .models import washer_wtwn3


class DeviceStateStore:
    def __init__(self, state_dir: str):
        self.latest: dict[str, dict] = {}
        os.makedirs(state_dir, exist_ok=True)
        self.log_path = os.path.join(state_dir, "diagmon.jsonl")

    def ingest_report(self, report_xml: str | bytes) -> list[dict]:
        """Decode a <Report> diagmon body, store latest per devId, append to JSONL."""
        if isinstance(report_xml, bytes):
            report_xml = report_xml.decode("utf-8", "replace")
        root = ET.fromstring(report_xml)
        dev_id = (root.findtext("devId") or "").strip()
        ts = datetime.now(timezone.utc).isoformat()
        payloads = washer_wtwn3.decode_report(report_xml)
        for p in payloads:
            p["devId"] = dev_id
            p["ts"] = ts
            self.latest[dev_id] = p
            with open(self.log_path, "a") as f:
                f.write(json.dumps(p, default=str) + "\n")
        return payloads

"""Replay-based bridge responses (TASK-126 Phase 2).

Serves REAL LG responses captured by the transparent relay (2026-10-25/28), not
synthetic XML. The September bridge proved that synthetic responses are rejected by
the appliance (application-level content check); the October relay proved real
responses pass. This module replays the captured responses with dynamic fields
(utcTime, Date header) updated to current values.

The replay store lives at data/replay/lg_responses.json (git-ignored: contains
device-identifying data). Regenerate with:
  python3 tools/extract_replay_store.py /tmp/lg-protocol-analysis/relay-46030-new.log
The store is loaded once at first use and cached; regenerating it requires a server
restart.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

REPLAY_PATH = os.environ.get("LGM_REPLAY_STORE", "data/replay/lg_responses.json")

_store: Optional[dict] = None


def _load() -> dict:
    global _store
    if _store is None:
        if os.path.isfile(REPLAY_PATH):
            _store = json.load(open(REPLAY_PATH))
        else:
            _store = {}
    return _store or {}


def _update_dynamic(body: str) -> str:
    """Update time-dependent fields in a captured response body."""
    now = datetime.now(timezone.utc)
    # utcTime format: "2026-10-25 16:27:48" (UTC, no timezone suffix)
    utc_str = now.strftime("%Y-%m-%d %H:%M:%S")
    body = re.sub(
        r'(<elementCode>utcTime</elementCode><elementValue>)[^<]+(</elementValue>)',
        r'\g<1>' + utc_str + r'\g<2>', body)
    return body


def replay_response(path: str) -> Optional[tuple[int, str, bytes]]:
    """Get the replayed response for an endpoint, or None if no capture available.

    Returns (status, content_type, body) with utcTime updated to now."""
    store = _load()
    # Match by service name as a URL segment (e.g. TotalDeviceInfoSvc in
    # /lgehadm/api/Device/TotalDeviceInfoSvc/THINQ_TIME_SYNC_URI); bare substring
    # would false-positive (e.g. "Mon" inside "Monitor")
    path_segments = path.rstrip("/").split("/")
    for ep_path, data in store.items():
        service = ep_path.rstrip("/").split("/")[-1]
        if service and service in path_segments:
            body = _update_dynamic(data["response_body"])
            ct_match = re.search(r'Content-Type: ([^\r\n]+)', data["response_headers"])
            ct = ct_match.group(1).strip() if ct_match else "text/xml;charset=utf-8"
            status_match = re.search(r'HTTP/\S+ (\d+)', data["response_headers"])
            status = int(status_match.group(1)) if status_match else 200
            return status, ct, body.encode("utf-8")
    return None


def available_endpoints() -> list[str]:
    """Endpoints that have captured real responses."""
    return list(_load().keys())

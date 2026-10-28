"""Replay-based bridge responses (TASK-126 Phase 2).

Serves REAL LG responses captured by the transparent relay (2026-10-25/28), not
synthetic XML. The September bridge proved that synthetic responses are rejected by
the appliance (application-level content check); the October relay proved real
responses pass. This module replays the captured responses with utcTime (the only
dynamic field observed) updated to current UTC.

The captured ContentsVerSvc body carries LG's downUrl, which the synthetic builder
deliberately omits ("never OTA against us", responses.contents_ver). We replay it
anyway, as a documented decision: the URL targets eic.lgthinq.com:6030 (real LG, a
port we do NOT intercept), verName matches what the appliances already run, and the
TASK-126 analysis names the stripped downUrl as one of the three plausible causes of
the synthetic bridge's rejection.

The replay store lives at data/replay/lg_responses.json (git-ignored: contains
device-identifying data). Regenerate with:
  python3 tools/extract_replay_store.py /tmp/lg-protocol-analysis/relay-46030-new.log
Keys are "<endpoint>|<item>" (the request's <item>: TotalDeviceInfoSvc has two
variants with different bodies). The store is loaded once at first use and cached;
regenerating it requires a server restart.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

REPLAY_PATH = os.environ.get("LGM_REPLAY_STORE", "data/replay/lg_responses.json")

_store: dict = {}
_loaded = False


def _load() -> dict:
    global _store, _loaded
    if not _loaded:
        _loaded = True
        _store = {}
        if os.path.isfile(REPLAY_PATH):
            try:
                with open(REPLAY_PATH) as f:
                    _store = json.load(f) or {}
            except (OSError, ValueError) as e:
                # a truncated regeneration (json.dump is not atomic) must degrade to
                # the synthetic fallback, not raise on every request: cache the
                # failure, say it once, keep serving
                sys.stderr.write(f"[replay] store unreadable ({e!r}); synthetic fallback\n")
        if _store:
            sys.stderr.write(f"[replay] {len(_store)} endpoints from {REPLAY_PATH}\n")
        else:
            sys.stderr.write(f"[replay] no store at {REPLAY_PATH}; synthetic fallback\n")
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


def replay_response(path: str, item: Optional[str] = None) -> Optional[tuple[int, str, bytes]]:
    """Get the replayed response for an endpoint+item, or None if no capture.

    Returns (status, content_type, body) with utcTime updated to now. Matching is
    strict: the service name must be a URL segment of the path (bare substring would
    false-positive, e.g. "Mon" inside "Monitor") AND the stored <item> must equal
    the request's; a capture of the other TotalDeviceInfoSvc variant must never be
    served for the variant it was not (the appliance checks content, TASK-126)."""
    store = _load()
    path_segments = path.rstrip("/").split("/")
    want = item or ""
    for ep_key, data in store.items():
        ep_path, _, ep_item = ep_key.partition("|")
        service = ep_path.rstrip("/").split("/")[-1]
        if service and service in path_segments and ep_item == want:
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

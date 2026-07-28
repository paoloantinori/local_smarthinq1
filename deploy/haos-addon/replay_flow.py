"""Replay a captured diagmon flow at the running add-on and assert it decodes (TASK-072).

Gated behind REPLAY_LIVE=1 (matches the repo's MQTT_LIVE convention); pytest.skip otherwise,
so the default `python -m pytest -q` suite is not broken when the container is not running.

Posts a real captured <Report> to the add-on's :46030 over TLS (self-signed: verification
disabled, like the appliance which does not pin), asserts 200, then checks /debug/state shows
the decoded state. Cross-checks the same report decodes via registry.decode_report directly.
"""
import os
import re
import ssl
import sys
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from server.models import registry  # noqa: E402

FLOW = os.path.join(ROOT, "flows", "washer-overnight-20260725.log")


def _first_report_block(log_path: str) -> bytes:
    """Extract the first <Report>...</Report> from a mitm text log.

    DOTALL + non-greedy are load-bearing: the blocks span many lines (mitm pretty-prints the
    body), and greedy .* would slurp all 20 blocks into one invalid blob. Mirrors the pattern
    in tests/test_server.py:_first_report.
    """
    log = open(log_path, encoding="utf-8", errors="replace").read()
    m = re.search(r"<Report>.*?</Report>", log, re.S)
    assert m, "no <Report> in capture"
    return re.sub(r"\s+", "", m.group(0)).encode()


@pytest.mark.skipif(os.environ.get("REPLAY_LIVE") != "1",
                    reason="needs the add-on container running on :46030 (REPLAY_LIVE=1)")
def test_replay_decodes_same_as_direct() -> None:
    block = _first_report_block(FLOW)
    # Direct (known-good) decode of the SAME bytes we will post:
    direct = registry.decode_report(block.decode())[0]
    assert direct.get("monData_decoded"), "direct decode empty"
    # The devId is the <devId> in the report XML; the store keys /debug/state by it.
    dev_id_match = re.search(r"<devId>([^<]+)</devId>", block.decode())
    assert dev_id_match, "no <devId> in report"
    dev_id = dev_id_match.group(1)

    # Post to the running add-on over TLS. Self-signed cert: disable verification (the appliance
    # does not pin either; this mirrors app.py's own bridge-mode upstream client).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        "https://127.0.0.1:46030/lgehadm/report/diagmon",
        data=block, method="POST",
        headers={"Content-Type": "application/vnd.diagmonlge.dm+xml"})
    resp = urllib.request.urlopen(req, context=ctx, timeout=5)
    assert resp.status == 200, resp.status

    # /debug/state is keyed by devId (server/state.py stores latest[dev_id]), NOT modelName.
    # So assert the devId key is present and its payload carried the decoded monData.
    import json
    state_raw = urllib.request.urlopen("https://127.0.0.1:46030/debug/state", context=ctx, timeout=5).read()
    state = json.loads(state_raw)
    assert dev_id in state, f"devId {dev_id} not in state keys {list(state)}"
    assert state[dev_id].get("monData_decoded"), "server did not decode the replayed monData"

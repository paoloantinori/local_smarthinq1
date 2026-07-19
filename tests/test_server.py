"""Unit tests for the M1 fake-cloud: response builders + dispatch + diagmon ingest.

Standalone (`python3 tests/test_server.py`) or pytest. No third-party deps.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import app, responses  # noqa: E402  # type: ignore[import-not-found]
from server.state import DeviceStateStore  # noqa: E402  # type: ignore[import-not-found]

CAPTURE = os.path.join(ROOT, "flows", "washer-cycle-20260719.log")


def _store() -> DeviceStateStore:
    return DeviceStateStore(tempfile.mkdtemp())


def _first_report() -> bytes:
    text = open(CAPTURE).read()
    m = re.search(r"<Report>.*?</Report>", text, re.S)
    assert m, "no <Report> in capture"
    return m.group(0).encode()


def test_responses_are_success_xml() -> None:
    for b in (responses.ok(),
              responses.contents_ver(),
              responses.total_device_info("THINQ_TIME_SYNC_URI"),
              responses.total_device_info("DM_SETTING_INFO_GET_URI")):
        assert b"returnCd>0000" in b, b
        assert b"returnMsg>OK" in b, b


def test_time_sync_has_utctime() -> None:
    assert b"utcTime" in responses.total_device_info("THINQ_TIME_SYNC_URI")


def test_contents_ver_no_downurl() -> None:
    r = responses.contents_ver()
    assert b"verName" in r
    assert b"downUrl" not in r, "must omit downUrl so the device doesn't OTA against us"


def test_dispatch_totaldeviceinfo() -> None:
    s = _store()
    status, _, body = app.dispatch(
        "/lgehadm/api/Device/TotalDeviceInfoSvc",
        b"<item>THINQ_TIME_SYNC_URI</item>", s)
    assert status == 200 and b"returnCd>0000" in body


def test_dispatch_diagmon_ingests() -> None:
    s = _store()
    status, _, _ = app.dispatch("/lgehadm/report/diagmon", _first_report(), s)
    assert status == 200
    assert any("d9bf16c0" in k for k in s.latest), f"washer not stored: {list(s.latest)}"


def test_state_writes_jsonl() -> None:
    s = _store()
    s.ingest_report(_first_report())
    lines = open(s.log_path).read().strip().splitlines()
    assert lines, "JSONL empty"
    assert "\"devId\"" in lines[0]


def test_bridge_mode_forwards_and_observes() -> None:
    """Bridge mode returns the real (forwarded) response AND still ingests diagmon."""
    s = _store()
    calls = []

    def stub_fwd(path, _headers, _body):
        calls.append(path)
        return 200, b"<real LG response>"

    status, _, body = app.dispatch(
        "/lgehadm/report/diagmon", _first_report(), s,
        mode="bridge", forwarder=stub_fwd)
    assert status == 200 and body == b"<real LG response>", "bridge must return the forwarded body"
    assert calls and calls[0].endswith("/report/diagmon"), "must forward the request"
    assert any("d9bf16c0" in k for k in s.latest), "bridge must still ingest (observe)"


def test_bridge_fallback_on_forward_error() -> None:
    """If the upstream forward fails, bridge falls back to the standalone response."""
    s = _store()

    def bad_fwd(_path, _headers, _body):
        raise ConnectionError("upstream down")

    status, _, body = app.dispatch(
        "/lgehadm/api/Rtos/ContentsVerSvc", b"", s, mode="bridge", forwarder=bad_fwd)
    assert status == 200 and b"returnCd>0000" in body, "fallback should yield our 0000/OK"


if __name__ == "__main__":
    for fn in (test_responses_are_success_xml, test_time_sync_has_utctime,
               test_contents_ver_no_downurl, test_dispatch_totaldeviceinfo,
               test_dispatch_diagmon_ingests, test_state_writes_jsonl):
        fn()
        print(f"PASS {fn.__name__}")
    print("\nAll M1 server assertions passed.")

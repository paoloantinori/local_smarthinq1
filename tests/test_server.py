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

from server import app, responses  # noqa: E402
from server.state import DeviceStateStore  # noqa: E402

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


def test_time_sync_matches_real_lg_shape() -> None:
    """The TIME_SYNC response uses real LG's itemList/elementList envelope + the
    'YYYY-MM-DD HH:MM:SS' utcTime format — guard the shape (load-bearing for the
    standalone sever test, not just the utcTime presence)."""
    import re as _re
    body = responses.total_device_info("THINQ_TIME_SYNC_URI")
    assert b"<itemList>" in body and b"<elementList>" in body, body
    assert b"<elementCode>utcTime</elementCode>" in body, body
    assert _re.search(rb"<elementValue>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}</elementValue>", body), body


def test_contents_ver_no_downurl() -> None:
    r = responses.contents_ver()
    assert b"verName" in r
    assert b"downUrl" not in r, "must omit downUrl so the device doesn't OTA against us"


def test_power_saving_info_matches_real_lg() -> None:
    """PowerSavingInfoSvc returns the real non-0000 code (0108 / 'No Saving Data.'), not OK."""
    s = _store()
    status, _, body = app.dispatch("/lgehadm/api/Grid/PowerSavingInfoSvc", b"", s)
    assert status == 200
    assert b"0108" in body and b"No Saving Data" in body, body
    assert b"<returnCd>0000</returnCd>" not in body, "must not be the generic OK"


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


def test_debug_state_returns_json() -> None:
    """GET /debug/state returns the latest decoded state as JSON (TASK-012 read surface)."""
    import json as _json
    s = _store()
    # empty store → valid JSON, empty object
    status, ct, body = app.dispatch("/debug/state", b"", s)
    assert status == 200 and ct == "application/json", (status, ct)
    assert _json.loads(body) == {}
    # after ingesting a washer report → that devId's decoded state is present
    app.dispatch("/lgehadm/report/diagmon", _first_report(), s)
    status, ct, body = app.dispatch("/debug/state", b"", s)
    state = _json.loads(body)
    assert any("d9bf16c0" in k for k in state), list(state)


def test_state_writes_jsonl() -> None:
    s = _store()
    s.ingest_report(_first_report())
    lines = open(s.log_path).read().strip().splitlines()
    assert lines, "JSONL empty"
    assert "\"devId\"" in lines[0]


def test_on_state_sink_fires_on_ingest() -> None:
    """TASK-064: the on_state sink fires for each ingested payload (with devId + decoded)."""
    calls: list[tuple[str, dict]] = []

    def sink(dev_id: str, payload: dict) -> None:
        calls.append((dev_id, payload))

    s = DeviceStateStore(tempfile.mkdtemp(), on_state=sink)
    s.ingest_report(_first_report())
    assert calls, "sink must fire on ingest"
    assert any("d9bf16c0" in dev_id for dev_id, _ in calls), [d for d, _ in calls]
    assert "monData_decoded" in calls[0][1], "sink payload should carry the decoded state"


def test_on_state_sink_skips_unknown_devices() -> None:
    """An UNKNOWN-device payload is not stored, so the sink must not fire for it."""
    calls: list = []
    s = DeviceStateStore(tempfile.mkdtemp(), on_state=lambda d, p: calls.append((d, p)))
    unknown = (b"<Report><devId>unknown123</devId><modelName>NOPE</modelName>"
               b"<devType>999</devType><diagMonType>X</diagMonType>"
               b"<diagMonData>e30=</diagMonData></Report>")
    s.ingest_report(unknown)
    assert not calls, "sink must not fire for unknown-device diagnostics"


def test_on_state_sink_failure_does_not_break_ingest() -> None:
    """A sink that raises must not break ingestion (the store wraps it in try/except)."""
    s = DeviceStateStore(tempfile.mkdtemp(), on_state=lambda d, p: (_ for _ in ()).throw(RuntimeError("boom")))
    s.ingest_report(_first_report())
    assert any("d9bf16c0" in k for k in s.latest), "ingest must still store state"


def test_unknown_device_not_stored_as_state() -> None:
    """A device with no registered decoder yields an UNKNOWN note that must not pollute the
    state store or the JSONL event log (it is a diagnostic, not state)."""
    unknown = (b"<Report><devId>unknown123</devId><modelName>NOPE</modelName>"
               b"<devType>999</devType><diagMonType>X</diagMonType>"
               b"<diagMonData>e30=</diagMonData></Report>")
    s = _store()
    s.ingest_report(unknown)
    assert "unknown123" not in s.latest, "unknown device must not pollute latest state"
    log = open(s.log_path).read().strip() if os.path.exists(s.log_path) else ""
    assert log == "", "unknown device must not be written to the state JSONL"


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

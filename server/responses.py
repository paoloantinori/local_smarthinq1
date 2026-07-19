"""XML response builders for the LG ThinQ1 fake-cloud (M1).

These implement the keep-alive contract hypothesis (PROTOCOL §5): return well-formed
`0000/OK` XML so the appliance is satisfied without the real cloud. Templates match the
*shape* of captured real-LG responses; the exact field set/order must be validated in the
supervised sever test (TASK-010/050 — cut the real cloud, confirm the appliance operates
and reconnects). Keep responses minimal.
"""
from __future__ import annotations

from datetime import datetime, timezone

_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<lgedmRoot>{body}"
    "<returnCd>0000</returnCd><returnMsg>OK</returnMsg>"
    "</lgedmRoot>"
)


def _wrap(body: str = "") -> bytes:
    return _XML.format(body=body).encode()


def ok() -> bytes:
    """Generic 0000/OK success."""
    return _wrap()


def total_device_info(item: str | None) -> bytes:
    """`/lgehadm/api/Device/TotalDeviceInfoSvc` — dispatch on the `<item>` selector."""
    if item == "THINQ_TIME_SYNC_URI":
        utc = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return _wrap(
            f"<utcTime>{utc}</utcTime><timezone>0</timezone><countryCode>WW</countryCode>"
        )
    if item == "DM_SETTING_INFO_GET_URI":
        # echo sane (empty) settings; refine from a captured settings response.
        return _wrap(
            "<settingInfoList></settingInfoList>"
            "<pushDetailSettingList></pushDetailSettingList>"
        )
    return ok()


def contents_ver() -> bytes:
    """`/lgehadm/api/Rtos/ContentsVerSvc` — report current verName, OMIT downUrl so the
    device never attempts an OTA against us (PROTOCOL §5.2)."""
    return _wrap("<verName>QC_Modem_1.2.80</verName><size>0</size>")


def diagmon() -> bytes:
    """`/lgehadm/report/diagmon` — devices push telemetry here; the real cloud answers
    `200` with an empty body (per captures)."""
    return b""

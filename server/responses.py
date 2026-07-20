"""XML response builders for the LG ThinQ1 fake-cloud (M1).

These implement the keep-alive contract (PROTOCOL §5): return well-formed XML so the
appliance is satisfied without the real cloud. Shapes match captured real-LG responses
(dryer capture, `data/mitm.log`, 2026-07-20); field sets are minimal but structurally
faithful so the appliance accepts them. Validate in the supervised sever test (TASK-050).
"""
from __future__ import annotations

from datetime import datetime, timezone

_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<lgedmRoot>{body}"
    "<returnCd>{return_cd}</returnCd><returnMsg>{return_msg}</returnMsg>"
    "</lgedmRoot>"
)


def _wrap(body: str = "", *, return_cd: str = "0000", return_msg: str = "OK") -> bytes:
    return _XML.format(body=body, return_cd=return_cd, return_msg=return_msg).encode()


def ok() -> bytes:
    """Generic 0000/OK success."""
    return _wrap()


def total_device_info(item: str | None) -> bytes:
    """`/lgehadm/api/Device/TotalDeviceInfoSvc` — dispatch on the `<item>` selector.

    Real LG wraps the values in itemList/elementList; utcTime is space-separated
    (`YYYY-MM-DD HH:MM:SS`), timezone is an int offset (observed `2` = CEST)."""
    if item == "THINQ_TIME_SYNC_URI":
        utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        body = (
            "<itemList>"
            f"<elementList><elementCode>utcTime</elementCode><elementValue>{utc}</elementValue></elementList>"
            "<elementList><elementCode>timezone</elementCode><elementValue>2</elementValue></elementList>"
            "<item>THINQ_TIME_SYNC_URI</item><returnCode>0000</returnCode>"
            "</itemList>"
        )
        return _wrap(body)
    if item == "DM_SETTING_INFO_GET_URI":
        # echo sane (empty) settings; refine from a captured settings response.
        return _wrap(
            "<settingInfoList></settingInfoList>"
            "<pushDetailSettingList></pushDetailSettingList>"
        )
    return ok()


def contents_ver() -> bytes:
    """`/lgehadm/api/Rtos/ContentsVerSvc` — report current verName; OMIT downUrl (and md5)
    so the device never attempts an OTA against us (PROTOCOL §5)."""
    return _wrap("<verName>QC_Modem_1.2.80</verName>")


def power_saving_info() -> bytes:
    """`/lgehadm/api/Grid/PowerSavingInfoSvc` — real LG returns 0108/'No Saving Data.'
    (a non-0000 code the appliance accepts)."""
    return _wrap(return_cd="0108", return_msg="No Saving Data.")


def diagmon() -> bytes:
    """`/lgehadm/report/diagmon` — devices push telemetry here; the real cloud answers
    `200` with an empty body (per captures)."""
    return b""

"""1REB1GLPX1___ — LG ThinQ1 fridge (devType 101) state decoder.

Reuses the shared WM-family diagmon envelope (``server.models.wm_envelope``) — confirmed
against a captured periodic report (``flows/fridge-20260721.log``): the fridge uses the same
base64→XML→binary double-decode and ``monData``/``diagData`` fields. Only its event triggers
differ (``COMMON_WIFI_ON`` / ``COMMON_PERIODIC``) and its modelJson (a 12-field state struct:
``TempRefrigerator`` / ``TempFreezer`` / ``DoorOpenState`` / …).

NB the live ``<Report>`` advertises ``modelName 1REB1GLPX1___`` / ``devType 101``; the fetched
modelJson's own ``Info.modelName`` is ``2REB1GLPX1___`` (same physical device, two LG model
strings). This module + the committed fixture are keyed on the *live* report's identity
(``1REB1GLPX1___``), which is what the registry dispatches on.
"""
from __future__ import annotations

from .wm_envelope import decode_report

DEVICE_TYPE = 101
MODEL_NAME = "1REB1GLPX1___"
MODEL_JSON_FIXTURE = "fridge_1REB1GLPX1.model.json"
STATE_FIELDS = ("monData",)

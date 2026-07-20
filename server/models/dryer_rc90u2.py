"""RC90U2_WW — LG ThinQ1 dryer (deviceType 202) state decoder.

Reuses the shared WM-family diagmon envelope (``server.models.wm_envelope``) — the dryer
shares that envelope (same ``diagMonType`` set and base64→XML→binary double-decode; only
its event triggers ``DR_*`` differ). This module only declares the dryer's identity + its
own modelJson; the registry + ``model_json.py`` do the rest.
"""
from __future__ import annotations

from .wm_envelope import decode_report

DEVICE_TYPE = 202
MODEL_NAME = "RC90U2_WW"
MODEL_JSON_FIXTURE = "dryer_rc90u2.model.json"
STATE_FIELDS = ("monData", "option")

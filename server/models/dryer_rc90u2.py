"""RC90U2_WW — LG ThinQ1 dryer (deviceType 202) state decoder.

Reuses ``washer_wtwn3``'s WM-family envelope decoders — the dryer shares that envelope
(same ``diagMonType`` set and base64→XML→binary double-decode; only its event triggers
``DR_*`` differ). This module only declares the dryer's identity + its own modelJson.
"""
from __future__ import annotations

from .washer_wtwn3 import decode_report

DEVICE_TYPE = 202
MODEL_NAME = "RC90U2_WW"
MODEL_JSON_FIXTURE = "dryer_rc90u2.model.json"
STATE_FIELDS = ("monData", "option")

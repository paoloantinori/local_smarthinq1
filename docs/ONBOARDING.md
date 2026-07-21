# Adding a new ThinQ1 appliance (onboarding runbook)

This guide walks through adding a **new** LG ThinQ1 appliance to this project — from
identifying it on your network to seeing it decode. It assumes you've already set up the
capture rig (see [`NETWORK_SETUP.md`](NETWORK_SETUP.md) + the repo README).

## 0. Is it ThinQ1?

This project works only for **ThinQ1** appliances (legacy XML / `lgehadm` / `IOE Client` /
`User-Agent: IOE Client`). ThinQ2 appliances (JSON/MQTT) are a different protocol — this
does not apply. If your appliance's firmware string starts with `QC_Modem`, it's ThinQ1.

## 1. Identify the appliance

Find the appliance on your network and in your LG account:

**On your router** (DHCP leases / ARP):
```bash
ssh firewall 'cat /tmp/dhcp.leases | grep -i lg'   # or search by hostname
```

**In Home Assistant** (if you run the `smartthinq_sensors` integration):
```bash
ssh ha 'jq -r ".data.devices[] | select(.manufacturer==\"LG\") |
  {name, model: .model, identifiers: .identifiers}" /config/.storage/core.device_registry'
```
This gives you: `modelName`, `deviceId` (UUID), LAN IP, and the firmware version.

Record: **modelName**, **deviceId**, **LAN IP**, **devType** (201=washer, 202=dryer,
101=fridge, 401=AC, …).

## 2. Capture its diagmon traffic

**If the appliance connects to LG by hostname** (SNI present — washer/dryer do):
```bash
# Scope capture-ctl to the new appliance's IP
sed -i 's/^APPLIANCES=.*/APPLIANCES="<new-ip>"/' .capture.env
./capture-ctl on
# provoke an event (start a cycle, open a door, etc.)
tail -f data/mitm.log        # look for POST .../report/diagmon
./capture-ctl off
```

**If the appliance connects to LG by raw IP** (no SNI — the fridge does):
```bash
sudo ./fridge-capture-setup.sh    # sets up transparent-mode routing + mitm
# provoke an event
tail -f data/fridge-mitm.log      # look for POST .../report/diagmon
sudo ./fridge-capture-teardown.sh
```
See [`PROTOCOL.md`](PROTOCOL.md) §2 for how to tell which case you're in (check conntrack:
if the appliance's `:46030` goes to a hostname IP with SNI, use `capture-ctl`; if it goes
to a raw IP with no SNI, use the transparent rig).

Extract the `<Report>` blocks from the capture to `flows/<appliance>-<date>.log` (see
the existing `flows/*.log` files for the header format).

## 3. Fetch its modelJson

The modelJson defines the byte layout (`Monitoring.protocol`) + friendly-name maps (`Value`)
for the device's binary state. Fetch it from LG:

```bash
# Get a refresh token (wideq login or from the HA smartthinq integration's config):
#   ssh ha 'jq -r ".data.entries[] | select(.domain==\"smartthinq_sensors\") |
#     .data.token" /config/.storage/core.config_entries'
# Then (substitute your region/language — check the HA config entry for region):
LG_REFRESH_TOKEN=<token> python tools/fetch_model_json.py <deviceId> <region> <language>
# → writes data/models/<modelName>.model.json
```

Verify: `Monitoring.type` should be `BINARY(BYTE)`.

## 4. Add the decoder module

**If the new appliance shares the WM-family envelope** (same `diagMonType` set:
`EventMonitoring`/`WasherMonitoring`/`ScomoCourse`, same base64→XML→binary double-decode
— washers, dryers, and most laundry appliances do):

Create a thin module under `server/models/`:
```python
"""<modelName> — LG ThinQ1 <appliance-type> (deviceType <N>) state decoder.

Reuses the shared WM-family diagmon envelope (``server.models.wm_envelope``).
"""
from __future__ import annotations
from .wm_envelope import decode_report

DEVICE_TYPE = <N>
MODEL_NAME = "<modelName>"
MODEL_JSON_FIXTURE = "<modelName>.model.json"
STATE_FIELDS = ("monData", "option")   # or ("monData",) if no option field
```

Copy the modelJson to the committed fixtures:
```bash
cp data/models/<modelName>.model.json server/models/<modelName>.model.json
```

Register it in `server/models/registry.py`:
```python
from . import <module_name>
_MODULES = [..., <module_name>]
```

**If it's a new appliance class** (different `diagMonType` set / envelope shape — capture
it first, derive the envelope from the real payload, do not assume the WM shape). Write a
decoder module that parses its envelope, then hands the binary blob to
`server/models/model_json.py` for the modelJson decode. See `wm_envelope.py` as the template.

## 5. Add a replay test

```python
# tests/test_<appliance>.py — replay the capture through the registry
from server.models import registry
CAPTURE = os.path.join(ROOT, "flows/<appliance>-<date>.log")

def test_capture_decodes():
    text = open(CAPTURE).read()
    reports = re.findall(r"<Report>.*?</Report>", text, re.S)
    decoded = [p for r in reports for p in registry.decode_report(r)]
    assert decoded, "no payloads"
    assert "monData_decoded" in decoded[0], "modelJson not applied?"
```

## 6. Verify

```bash
python -m pytest -q                           # all tests pass (including the new one)
python -m pyright server/ tests/              # type check clean
```

Done — the appliance now decodes through the registry and will appear in HA (via the MQTT
bridge) once the server is running with `LGM_MQTT_HOST` set.

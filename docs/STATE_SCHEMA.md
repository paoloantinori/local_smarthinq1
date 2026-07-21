# Normalised State Schema (TASK-022)

The contract between the decoders and the HA bridge. Every appliance's decoded state
follows this shape, regardless of model. Consumers (HA MQTT bridge, `/debug/state`,
future integrations) rely on these invariants.

## Version

**v1** (2026-07-21). Bump on any breaking change to the shape.

## Shape

```python
{
    "devId": str,              # e.g. "WASHER_DEVICE_ID"
    "modelName": str,          # e.g. "WTWN3", "RC90U2_WW", "1REB1GLPX1___" (may be "")
    "diagMonType": str,        # e.g. "EventMonitoring", "WasherMonitoring"
    "eventType": str | None,   # e.g. "WM_STATE", "DR_DRY_BEGIN", "COMMON_PERIODIC" (absent for some types)
    "ts": str,                 # ISO-8601 UTC timestamp
    "monData_decoded": {       # the modelJson-decoded state struct (ABSENT if no modelJson cached)
        "<FieldName>": str,    # one entry per Monitoring.protocol field, value = friendly label
        ...                    # fields are per-model (washer: State/Course/Remain_Time_*, fridge: TempRefrigerator/DoorOpenState)
    },
    "monData": {               # the raw binary envelope decode (ALWAYS present for EventMonitoring)
        "raw": str,            # hex string
        "len": int,            # byte count
    },
    # Optional envelope fields (present when the report carries them):
    "diagData": {"raw": str, "len": int} | None,   # cycle/event summary binary
    "note": str | None,         # only for UNKNOWN devices (no registered decoder)
}
```

## Invariants

1. **`devId`** is always present and non-empty (read from the `<Report>`).
2. **`modelName`** is the *live* value from `<Report>` (may differ from the modelJson's
   own `Info.modelName` — e.g. fridge advertises `1REB1GLPX1___` but modelJson says
   `2REB1GLPX1___`). May be empty if the report omits it.
3. **`monData_decoded`** is present **iff** a modelJson resolved for this `modelName`.
   Absent → the device has no cached modelJson (envelope-only decode). The HA bridge
   skips publishing in that case (no fields to discover).
4. **`monData_decoded` values** are friendly labels: enum/reference values are resolved
   via the modelJson Value map + `_clean_label` (no `@…_W` markers). Unknown/unmapped
   values appear as `"Unknown"` or the raw digit string.
5. **`monData.raw`** is the hex-encoded binary blob, always present for `EventMonitoring`
   payloads (the binary state struct before modelJson interpretation).
6. **Fields in `monData_decoded`** are per-model (defined by the modelJson's
   `Monitoring.protocol`): washer has `State`/`Course`/`Remain_Time_*`/`Wash`/`SpinSpeed`;
   dryer has `State`/`Course`/`ProcessState`/`DryLevel`; fridge has
   `TempRefrigerator`/`TempFreezer`/`DoorOpenState`/etc. Consumers **must not** assume a
   fixed field set — iterate the keys.

## Consumers

- **HA MQTT bridge** (`server/ha_mqtt.py`): publishes `monData_decoded` as a JSON state
  topic; discovery derives one sensor per key (appliance-agnostic).
- **`GET /debug/state`** (`server/app.py`): returns `store.latest` (a dict of devId →
  this schema) as JSON.
- **`server/state.py` JSONL log**: appends one JSON line per ingested payload (this shape).

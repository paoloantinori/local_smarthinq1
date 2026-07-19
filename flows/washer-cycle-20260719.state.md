# Washer (WTWN3) cycle state — byte-decode notes (M2 / TASK-020)

Source: `flows/washer-cycle-20260719.log` — cycle ran 2026-07-19 02:56:26, course 7.
All diagmon are from `devId d9bf16c0` (washer WTWN3). Note: source IP appears as
`192.168.20.1` in the log due to hairpin masquerade — attribute flows by `devId`.

## monData (28 bytes) across the cycle (WM_STATE → WM_WASH_END → idle)
```
state1 WM_STATE : 06 01 21 01 21 07 00 03 07 04 02 00 00 00 00 40 00 00 01 03 00 39 00 64 00 00 04 00
state2 WM_STATE : 07 00 2f 01 21 07 00 03 07 00 02 00 00 00 00 40 00 00 01 06 00 39 00 64 00 00 04 00
state3 WM_STATE : 08 00 0b 01 21 07 00 00 07 00 00 00 00 00 00 40 00 00 01 07 00 39 00 64 00 00 04 00
state4 WM_STATE : 0a 00 00 01 21 07 00 00 00 00 00 00 00 00 00 00 00 01 08 00 39 00 64 00 00 04 00
WM_WASH_END     : 0a 00 00 01 21 07 00 00 00 00 00 00 00 00 00 00 00 01 08 00 39 00 64 00 00 04 00  (+ diagData)
idle WM_STATE   : 00 00 00 01 21 07 00 00 00 00 00 00 00 00 00 00 00 00 02 0a 00 39 00 64 00 00 04 00
```

## Byte map (CONFIRMED against this cycle; validated by `server/models/washer_wtwn3.py` +
`tests/test_wtwn3.py` — 7-diagmon replay passes)
Derived programmatically (byte positions that change across the 6 WM_STATE/WM_WASH_END states):
- **b5 = course** — `0x07` constant; matches `energyMonInfo <course>7</course>`. HIGH.
- **b18 = cycle_active** — `0x01` while a cycle is active, `0x02` once idle/complete. HIGH.
- **b19 = phase_step** — monotonic `3→6→7→8→8→10`. MEDIUM (per-value meaning via modelJson).
- **b0 = state** — progresses `6→7→8→10→10→0(idle)`. MEDIUM.
Also change during the cycle (sub-fields, meaning TBD via modelJson): b1, b2, b7, b8, b9, b10, b15.
Constant: b21=`0x39`, b23=`0x64` (temp/time config? — SPECULATIVE).

> An earlier version of these notes had cycle_active/phase_step off by one (b19/b20 instead of
> b18/b19). The replay test caught it. Trust the decoder + test, not the eyeball.

## WM_WASH_END diagData (69 bytes) — full cycle summary (richest; needs value map)
```
03010100040506073900000100ff013300fd0000041e1e0404261e000004fd646400fd00fd00fdfdfd000013b23a33000f3700073202000464080002123cbb585c099a091a
```

## energyMonInfo (WasherMonitoring) — plain fields (decoded)
`event=2, course=7, power=1, energyWater=4, dlCourse=0, useDate=20260719 02:56:26`.
`option` = 28-byte binary (same shape as monData).

## Decode plan (M2)
ThinQ1 appliances emit BINARY state; the CLIENT decodes via the per-model `modelJson`
(value map: byte offsets → typed fields). `sampsyo/wideq` is the canonical decoder
(it fetches modelJson from LG's API). Next steps:
1. Obtain the WTWN3 `modelJson` — via wideq's LG-API method (needs LG account auth) or a
   cached copy (wideq/rethink repos).
2. Build `server/models/wtwn3.py` mapping `monData`/`diagData` → state (course, phase,
   run-state, temps, remaining time, error) → feed `docs/STATE_SCHEMA.md` (TASK-022).
3. Validate against this captured cycle (e.g. course=7, run-state 1→2).

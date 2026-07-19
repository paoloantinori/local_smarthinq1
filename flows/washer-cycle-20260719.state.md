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

## Hypothesized byte map (derived from cycle progression — CONFIRM via modelJson)
- **b5 = course** — `0x07` (=7); matches `energyMonInfo course=7`. HIGH confidence.
- **b19 = run-state** — `0x01` running → `0x02` complete. MEDIUM.
- **b20 = phase/step** — climbs `03→06→07→08`. MEDIUM.
- **b0 = state** — `06→07→08→0a`, `00` when idle. MEDIUM.
- b1 (`01` first state, `00` after), b2 (`21/2f/0b` then `00`), b15 (`40` early, `00` later): unknown.
- b21-22 (`0x0039`=57), b23-24 (`0x0064`=100): constant — temp/time config? SPECULATIVE.

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

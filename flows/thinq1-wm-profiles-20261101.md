# ThinQ1 WM profiles: real-device capture data (2026-11-01)

Real-world protocol profiles for the two WM appliances behind this repo, extracted
from the transparent-relay corpus (86,232 decoded `:47878` messages, 2026-10-25..31,
plus live cycle observations). Offered to `anszom/rethink` (issue #100) as
cross-validation data and as the documentation of a protocol generation they do not
cover (RC90U2_WW ThinQ1). All hex is the raw 24/28-byte `monData` blob (the B64
`Data` payload), exactly as pushed.

## Washer WTWN3 (ThinQ1, QCA4002, FwVer QC_Modem_1.2.80, deviceType 201)

28-byte frame. Distinct real payloads observed (complete cycle ladders, 2026-10-29..31):

| State | Frame (hex) | Notes |
|---|---|---|
| INITIAL (standby) | `01032b032b0100030a0401000000004000000104002d006400000300` | seen at boot/selection |
| RUNNING | `0600mm0107070003070401000000004000000104002d006400000300` | `mm` = remaining minutes (0x18..0x3b observed), byte-identical layout across the countdown |
| RINSING | `0700mm000e0b000305` + tail as RUNNING | same countdown encoding |
| SPINNING | `08000300140c000005`-class | countdown can pause at 1m for ~10 min (final spin stretch) |
| ERROR (0x12) | `12000801070705000700`-class | mid-cycle, self-recovered after ~13 min (unbalance/drain retry class); pushed like any state |
| END | `0a00000106070000000000000000000000000108002d006400000300` | pushed ~1 Hz for ~30 s |
| POWER_OFF | `000000010607000000000000000000000000000a002d006400000300` | pushed ~1 Hz for ~90 s, then SILENCE (auto power-down) |

- **Option2 bit 0x40 = door LOCK** (cycle engaged): set in every RUNNING/RINSING/
  SPINNING frame above, clear in END/POWER_OFF. It is NOT an open/close sensor:
  cycling the door while idle produces zero frame change (tested, 2026-09-25).
- Push behavior: state frames ONLY while a cycle runs; total silence after the
  post-POWER_OFF burst. No idle heartbeat on this channel.

## Dryer RC90U2_WW, ThinQ1 generation (QCA4002, deviceType 202)

24-byte frame. IMPORTANT: same model name as the ThinQ2/QCA4010 unit documented in
rethink, but a DIFFERENT protocol generation on the wire: ThinQ1 XML `:46030` +
TLS `[4B len][JSON]` `:47878`, no AABB.

| State | Frame (hex) | Notes |
|---|---|---|
| idle POWER_OFF | `00000000000000000000000000000004000112020000006b` | 97.7% of the corpus: pushed ~1 Hz forever, idle included (firehose behavior, opposite of the washer) |
| active/cycle | `0600360107070003070401000000004000000104002d006400000300` | state enum values are MODEL-SPECIFIC: this same 24-byte shape decodes with different labels than the washer's 28-byte frame |
| END (live) | State/ProcessState `WM_STATE_END`, pushed like the washer's | live capture 2026-10-28T17:38:05Z, published to MQTT end to end |
| post-cycle idle | as idle, option bytes 18-19 differ (`...04000107040000006b`) | option-field variance, meaning UNCONFIRMED |

- Corpus split: dryer 85,220 messages (98.8%) vs washer 1,012 (1.2%), same household,
  same relay: the push asymmetry is per-model behavior, not environment.
- The `Mon Start` gate, `Alive` cadence (60 s) and the fire-and-forget pump are as
  documented for the WM family in the PROTOCOL.md of this repo (section 4.4).

## Provenance

- Corpus: `:47878` transparent-relay logs (this repo's TASK-076/080 rig), decoded with
  the committed modelJson decoders; per-state hex re-verified 2026-11-01 against the
  live relay log.
- IDs: washer devId `82c2ebc0-0e9f-11f5-82b4-0051eda91d3d`, dryer devId
  `93b55500-4b3a-11f1-ad68-7440be73f9ad` (module-stable local IDs; the ThinQ Connect
  API uses per-registration hash IDs instead).

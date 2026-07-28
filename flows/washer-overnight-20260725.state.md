# Washer (WTWN3) overnight cycle — decoded state (2026-07-25)

Source: `flows/washer-overnight-20260725.log` — captured after the session-crash rig restore.
mitm came back up at 2026-07-24 23:02 CEST (21:02 UTC); the washer stayed in deep backoff
until ~02:02 UTC, then reconnected on its own. The rig captured the running phase onward;
the very start of the cycle (before 02:02 UTC) fell during the outage/backoff and was missed.

Decoded through `server/models/registry.decode_report` (WTWN3 modelJson). Two cycles visible:
the scheduled cycle completing, and a second short attempt that errored out.

## Cycle 1 — scheduled cycle (Course: Mix), ran to completion
| time GMT | event | State | Remain | Error |
|---|---|---|---|---|
| (pre-02:02) | WM_STATE | RUNNING | 1h19m | No Error |
| 02:02:26 | WM_STATE | RINSING | 0h47m | No Error |
| 02:37:25 | WM_STATE | SPINNING | 0h11m | No Error |
| 03:30:38 | WM_STATE | END | 0h0m | No Error |
| 03:30:39 | WM_WASH_END | END | 0h0m | No Error |
| 03:30:42 | WM_STATE | POWER_OFF | 0h0m | No Error |

Clean run: RUNNING → RINSING → SPINNING → END → POWER_OFF, Remain counting down to 0, no error.
Completed ~03:30 UTC (05:30 CEST).

## Cycle 2 — second attempt (Course: Cotton), errored then restarted
| time GMT | event | State | Remain | Error |
|---|---|---|---|---|
| 04:04:08 | COMMON_WIFI_ON | INITIAL | 3h51m | No Error |
| 04:04:09 | WM_WASH_BEGIN | DETECTING | 3h51m | No Error |
| 04:04:11 | WM_ERROR | ERROR | 3h51m | **DE2 Error** |
| 04:04:12 | WM_STATE | POWER_OFF | 3h51m | No Error |
| 04:04:15 | WM_WASH_BEGIN | DETECTING | 3h51m | No Error |
| 04:04:17 | WM_STATE | RUNNING | 1h33m | No Error |

A second cycle was started (Cotton), hit a **DE2 error** (LG door-lock/door-error code) at
04:04:11, powered off, then was restarted at 04:04:15 and went RUNNING by 04:04:17.

## Notes
- `DE2` is an LG washer door-error (door not locked / door switch fault). It self-cleared on
  the restart, so likely a door that wasn't fully latched on the first attempt.
- Reports shown as `(no monData_decoded)` are `WasherMonitoring` polls / `ScomoCourse` binary
  blobs that carry no per-state binary monData — expected, not a decode failure.
- Capture artifact: mitm ran with `flow_detail=3` (text log), no `-w` flow file. This `.log`
  IS the capture; the `.state.md` is the decoded readout.

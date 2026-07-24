# Backlog

Task-level breakdown of [`ROADMAP.md`](ROADMAP.md). **Written to be executed by a coding
agent (Claude Code + GLM 5.2).** Every task is self-contained: goal, files, acceptance
criteria, verification. Do them roughly top-to-bottom; respect the `Depends on` field.

**Status legend:** ⬜ todo · 🟦 in progress · ✅ done · 🚫 blocked

**Working agreement for the coding agent**
- Capture-driven: never invent a payload — point to a capture file or capture it first.
- Small, verifiable commits. Each task lists a concrete *Verify* command/observation; run it
  and paste the output in the PR/commit before marking ✅.
- If reality contradicts `PROTOCOL.md`, **update `PROTOCOL.md` in the same change** — it is a
  living doc, not a spec to preserve.
- Out-of-scope items are listed per task to prevent gold-plating. Don't do them.
- Ask before anything that can physically actuate an appliance (M3+).

---

## M0 — Capture & characterise

### TASK-001 ✅ Document & scriptify the capture rig
**Done as** `capture-ctl` — nft DNAT scoped per-appliance on `:46030` (not the originally
spec'd `capture/run-bridge.sh` / DNS path: DNS diversion proved unworkable against the
`eic.lgthinq.com` CNAME — see `PROTOCOL.md` §2). Design:
`docs/superpowers/specs/2026-07-18-capture-toggle-design.md`.
**Depends on:** —
**Goal.** A one-command, documented, reproducible capture rig. ✅
**Built.** `capture-ctl` — nft DNAT scoped per-appliance on `:46030` + hairpin masquerade
(OpenWrt fw4); mitmproxy 12.x + `lg_portfix.py` + `--set ssl_insecure=true`. No DNS changes,
no credentials. Design + verified results:
[`docs/superpowers/specs/2026-07-18-capture-toggle-design.md`](2026-07-18-capture-toggle-design.md).
**Why not the original spec** (`capture/run-bridge.sh`, DNS redirect, `upstream_dns`, `-w`):
DNS diversion proved unworkable against the `eic.lgthinq.com` CNAME (see `PROTOCOL.md` §2);
`upstream_dns`/`allow_remote_connections` don't exist in mitmproxy 12.x. Original spec is in
git history. The `PROTOCOL.md §2` transport wiring is resolved (no longer `(UNCONFIRMED)`).
**Verify.** ✅ `capture-ctl on` diverts + decrypts (self-test passes); `capture-ctl off`
restores. Captured a full washer wash cycle 2026-07-19 (`flows/washer-cycle-20260719.log`).

### TASK-002 🟦 Capture the full appliance lifecycle
**Progress.** (c) washer cycle ✅ 2026-07-19 (`flows/washer-cycle-20260719.log`); (d) dryer
cycle + (e) app-issued control still pending.
**Depends on:** TASK-001
**Goal.** Get captures that actually contain the states we need to model — current captures
are boot/idle only.
**Scope.** Capture and save separate labelled flow files for: (a) cold boot / reconnect,
(b) idle, (c) **a complete wash cycle** start→finish on the washer, (d) the same on the
dryer, (e) app actions: open the ThinQ app and view each appliance, then **issue a control
from the app** (e.g. remote stop, or start-course if the model allows). (e) likely needs the
app's TLS unpinned — see `references.md` (Frida) — record whether it was needed.
**Acceptance.** `flows/` contains the labelled captures; a short `capture/INVENTORY.md` maps
each file to what the appliance was doing and which `diagMonType`/endpoints appeared.
**Verify.** Grep each flow for `diagmon` and list distinct `diagMonType` values + endpoints.
**Out of scope.** Decoding payloads (TASK-005) — just capture and catalogue.

### TASK-003 ✅ Complete the endpoint catalogue in PROTOCOL.md
**Done.** 2026-07-21. Diffed all captures (flows/ + data/mitm.log + data/fridge-mitm.log)
against PROTOCOL §3: 7 distinct endpoints found (TotalDeviceInfoSvc, ContentsVerSvc,
FWInfoSettingSvc, PowerSavingInfoSvc, ClosingDoorEventSvc, report/diagmon, sendPushMessage).
Added ClosingDoorEventSvc (fridge door-close event, UNCONFIRMED). No endpoint in any capture
is absent from PROTOCOL §3.
**Depends on:** TASK-002
**Goal.** From the new captures, extend `PROTOCOL.md §3` to every endpoint, `<item>`, and
`diagMonType` seen, with one real request/response example each.
**Acceptance.** No endpoint appears in `flows/` that is absent from `PROTOCOL.md`. Each has an
example and a one-line "what it's for".
**Verify.** Script that lists unique `(method, path, item/diagMonType)` tuples across all
flows and diffs against the table in `PROTOCOL.md` → empty diff.
**Out of scope.** Semantics of the state fields (that's M2).

---

## M1 — Local fake-cloud server (read path)

> Decision D-1 (see CLAUDE.md): server is **Python**. Pick the HTTP stack in TASK-010.

### TASK-010 ✅ Server skeleton + TLS termination
**Done.** `server/app.py` (stdlib `http.server` + `ssl`) terminates TLS with a generated
`*.lgthinq.com` cert (`gen-cert.sh`) and listens on `:46030`. **Appliance-acceptance
validated 2026-07-20 (bridge mode, dryer):** the dryer completed the TLS handshake against
our cert and sent real requests we logged — re-confirms the no-pinning premise
(`PROTOCOL.md §2`) for a live appliance, not just captures.
**Depends on:** TASK-001
**Goal.** A Python HTTPS server that the appliance will actually talk to, presenting a cert
the module accepts, listening where the capture rig points (`:46030` and/or `:443`).
**Scope.**
- Project layout `server/` (e.g. `aiohttp` or `fastapi`+`uvicorn`; justify the pick briefly).
- TLS: reuse/adapt the CA the appliance already accepted in captures. Document how the cert
  is generated and installed. **Confirm empirically** the appliance completes the TLS
  handshake against our cert (this validates `PROTOCOL.md §2`'s no-pinning claim).
- Structured request logging (method, path, headers, decoded XML body).
- Config file for bind addrs/ports and a `mode: bridge|standalone` switch (bridge default).
**Acceptance.** Appliance opens a TLS session to our server and sends a real request that we
log fully decoded. No `502`/handshake errors in the appliance's retry loop.
**Verify.** Point one appliance's DNS at the server; observe a decoded `diagmon` or
`TotalDeviceInfoSvc` request in the log.
**Out of scope.** Correct responses (TASK-011) — logging + 200 stub is enough here.

### TASK-011 🟦 Implement the bootstrap endpoints (keep-alive contract)
**Depends on:** TASK-010, TASK-003
**Goal.** Answer the mandatory endpoints with well-formed XML so the appliance is satisfied
without the real cloud. Implements `PROTOCOL.md §5`.
**Scope.** Handlers returning correct `0000/OK` XML for: `TotalDeviceInfoSvc`
(`THINQ_TIME_SYNC_URI` with live server time + timezone; `DM_SETTING_INFO_GET_URI` echoing
sane settings), `Rtos/ContentsVerSvc` (report current `verName`, **omit `downUrl`** so no OTA
is offered — verify the appliance accepts this), and `report/diagmon` (accept, `200`, empty).
Responses built from templates matching captured byte structure.
**Acceptance.** Diff of our responses vs captured real-cloud responses (same request) differs
only in expected-dynamic fields (timestamp). Appliance issues no error/retry storm.
**Verify.** In bridge mode, log both our synthetic response and the real one for the same
request and diff them. Then in standalone mode, confirm the appliance's request cadence stays
normal for ≥10 min.
**Out of scope.** Decoding/using `diagmon` content (TASK-012), control.

### TASK-012 ✅ Ingest & persist diagmon state
**Done.** `server/state.py::DeviceStateStore` decodes each `report/diagmon` (base64→XML→binary
via the registry) and stores latest per-`devId` + appends JSONL. **Validated live 2026-07-20
(dryer, bridge mode):** a started cycle's `DR_DRY_BEGIN` (State RUNNING, Course Quick Dry,
Remain 30m) was ingested + decoded through the server, matching the direct capture.
**Depends on:** TASK-011
**Goal.** Decode incoming `diagmon` reports (`diagMonData` base64 → XML → dict; note the inner
`monData`/`diagData`/`option` are base64 → **binary** — store raw for M2) and hold latest
per-device state in memory + append to a persistent event log.
**Scope.** A per-`deviceId` state store keyed by `diagMonType`; base64/XML decode; JSONL event
log under `data/`. Expose a read-only `GET /debug/state` for inspection. **No interpretation
of field meaning yet** — store raw decoded key/values.
**Acceptance.** `GET /debug/state` shows live-updating raw state for both appliances as they
report. Event log grows with each `diagmon`.
**Verify.** Run a wash cycle (or replay a captured flow); watch `/debug/state` change and the
JSONL log append.
**Out of scope.** Mapping raw values to human meaning (TASK-020).

### TASK-013 ✅ Bridge mode (forward + observe)
**Done.** `server/app.py` `mode=bridge` forwards each request to real LG (`:46030`) and
returns the real response, observing (ingesting diagmon). **Validated 2026-07-20 (dryer):**
with the dryer's `:46030` DNAT'd to the server, it ran normally (app + cycle unaffected) —
the dryer's full bootstrap (`TotalDeviceInfoSvc`, `ContentsVerSvc`, `PowerSavingInfoSvc`,
`FWInfoSettingSvc`) + `report/diagmon` cycle pushes all flowed through the server, returned
`200`, and were decoded. **Standalone (real LG firewalled) is NOT yet validated — that's the
remaining de-risk** (the supervised sever test, TASK-050).
**Depends on:** TASK-010
**Goal.** Optional passthrough to the real LG cloud so we can run alongside it and compare,
mirroring `rethink`'s bridge mode. De-risks standalone by proving parity.
**Scope.** When `mode: bridge`, forward each request upstream (honouring the 46030 port
quirk), return the real response to the device, and log request + both responses.
**Acceptance.** With bridge on, appliances behave exactly as with no proxy; captured
divergences (if any) are logged for review.
**Verify.** 30 min in bridge mode with the app open: appliance + app fully functional; log
shows matched request/response pairs.
**Out of scope.** Standalone correctness (that's TASK-011's job) — this is the comparison rig.

---

## M2 — State decoding & modelling

### TASK-020 ✅ Decode washer (WTWN3) state model
**Done.** 2026-07-19. The real WTWN3 `modelJson` (fetched from LG via wideq + the
smartthinq integration's refresh_token; `tools/fetch_model_json.py`) is committed at
`server/models/washer_wtwn3.model.json`. `server/models/model_json.py` decodes all 22
`Monitoring.protocol` fields; replaying `flows/washer-cycle-20260719.log` yields a state
timeline matching the cycle (Course=Mix, State RUNNING→POWER_OFF, Remain_Time→0, …),
validated by `tests/test_model_json.py::test_real_model_full_decode`. The hand-derived
byte map stays in `flows/washer-cycle-20260719.state.md` as the derivation record.
**Depends on:** TASK-012, TASK-002(c)
**Goal.** Turn the washer's raw reported values into a documented, typed state model.
**Context.** Cross-reference `wideq` `modelJson` value maps and `rethink` washer pages
(`references.md`) for the WTWN3/type-201 field encodings; validate against a captured cycle.
**Scope.** A `server/models/washer_wtwn3.py` (or data-driven `server/models/*.json`) mapping raw fields →
{running/idle/finished state, current course, remaining time, spin, temperature, door
locked, error code, tub-clean counter…} — only fields actually observed. Document each
mapping's evidence (which capture, which value → which physical state).
**Acceptance.** Replaying the captured full-cycle flow produces a state timeline whose
transitions match what the machine actually did (start → wash → rinse → spin → end).
**Verify.** A decode test over the captured cycle flow asserts the expected ordered
transitions.
**Out of scope.** Dryer (TASK-021), HA entity shapes (M4).

### TASK-021 ✅ Decode dryer (RC90U2) state model
**Done.** 2026-07-20. Captured a full empty dry cycle on `:46030`
(`flows/dryer-cycle-20260720.log` — decrypts cleanly; the dryer talks to the SNI host
`eic.lgthinq.com` so the regular rig works, unlike the fridge). The dryer **reuses the
washer's WM-family envelope** — same `diagMonType` set (`EventMonitoring` /
`WasherMonitoring` / `ScomoCourse`), same base64→XML→binary double-decode — only the event
triggers differ (`DR_DRY_BEGIN` / `DR_STATE` / `DR_DRY_END` vs `WM_*`). `server/models/
dryer_rc90u2.py` reuses `washer_wtwn3`'s envelope decoders and declares the dryer's identity
(`MODEL_NAME RC90U2_WW`, type 202); its modelJson (`server/models/dryer_rc90u2.model.json`,
17 fields, `BINARY(BYTE)`) is applied by the registry. Replaying the capture yields the full
lifecycle: `POWER_OFF` → `DRY_BEGIN` (RUNNING, Quick Dry, 30m) → `DRY` phase counting down
→ `COOLING` at 1m → `DRY_END` (`POWER_OFF`, 0m, No Error). Validated by
`tests/test_dryer.py` (registration, DRY_BEGIN→DRY_END, remain-time counts down, no-error,
fixture isolation). 33 tests, pyright clean.
**Depends on:** TASK-020, TASK-060
**Goal.** Same as TASK-020 for the dryer (type 202); reuse the TASK-020 framework. ✅
**Acceptance / Verify.** As TASK-020, against the dryer cycle capture. ✅
**Out of scope.** Anything washer-specific already covered.

### TASK-063 ✅ Promote the WM-family envelope out of `washer_wtwn3`
**Depends on:** TASK-021 (the dryer makes the smell concrete).
**Why.** Two models (washer + dryer) now share the WM-family diagmon envelope, but the
envelope code (base64→XML→binary double-decode, `_BINARY_FIELDS`, `_TEXT_FIELDS`,
`decode_diagmon_payload`, `decode_report`) lives inside `washer_wtwn3.py`, and
`dryer_rc90u2.py` reaches across to import it from a *peer model module*. That hides the
shared abstraction and makes the washer a fake "base" module. A third WM-family model would
do the same, cementing it. Also: `decode_mondata` applies washer-derived byte offsets
(`CONFIRMED_MONDATA_FIELDS`) to every blob — dead-but-coupled code for the dryer (correct
today only because the modelJson decode path writes `monData_decoded` separately and nothing
reads the washer-interpreted `monData` for the dryer).
**Goal.** Split `server/models/wm_envelope.py` (shared envelope: double-decode + dispatch +
`decode_report`/`decode_diagmon_payload`) from the per-model byte tables. Washer + dryer
import the envelope; each model module keeps only its identity + its own confirmed byte
offsets (passed into a generic `decode_mondata(blob, fields=())`). The registry keeps
dispatching by `modelName`/`deviceType` to a module; it doesn't care that both import the
same envelope.
**Done.** 2026-07-20. Split `server/models/wm_envelope.py` (appliance-agnostic: double-decode,
`Field`, `BINARY_FIELDS`/`TEXT_FIELDS`, generic `decode_mondata(blob, fields=())`,
`decode_diagmon_payload`, `decode_report`) from the washer's byte tables
(`CONFIRMED_MONDATA_FIELDS`, `_CYCLE_ACTIVE`, `cycle_active`→bool). Washer + dryer import the
envelope; `dryer_rc90u2` now imports `decode_report` from `wm_envelope`, not from
`washer_wtwn3`. Verified: dryer `monData` is raw-only (`['len','raw']`) — washer offsets no
longer leak; washer `monData` keeps its reads. 33 tests, pyright clean.
**Acceptance.** Washer + dryer decode unchanged (all existing tests green); the envelope
module has no appliance-specific byte offsets; `dryer_rc90u2` no longer imports from
`washer_wtwn3`. ✅
**Verify.** `python -m pytest -q` stays green (33 tests); eyeball that the washer's
`CONFIRMED_MONDATA_FIELDS` no longer influence the dryer's `monData`. ✅
**Out of scope.** Auto-registration / a data-driven model table (premature at 2 models).

### TASK-022 ✅ Stable public state schema
**Done.** 2026-07-21. Documented in `docs/STATE_SCHEMA.md` (v1). The decoded state has a
stable top-level shape (devId/modelName/diagMonType/eventType/ts/monData_decoded/monData)
with per-model fields in monData_decoded (iterable, not fixed). Consumers (HA MQTT bridge,
/debug/state, JSONL log) documented. Both appliance types (WM + REF) conform.
**Depends on:** TASK-020, TASK-021
**Goal.** Define the normalised, appliance-agnostic state object the HA layer consumes
(so M4 doesn't depend on per-model internals). Version it.
**Acceptance.** Both models emit the same top-level schema; schema documented in
`docs/STATE_SCHEMA.md`. Adding a new appliance means adding a mapping, not changing consumers.
**Verify.** Both appliances' decoders validate against the schema.

### TASK-060 ✅ Multi-model decoder registry & modelJson cache
**Depends on:** TASK-020
**Unblocks:** TASK-021 (dryer), TASK-061 (fridge), TASK-053 (onboarding runbook).
**Goal.** Make "support a new ThinQ1 appliance" a mechanical, no-recompile step: dispatch
diagmon decoding by device identity (`modelName` then `deviceType`, read from each
`<Report>`) instead of hard-wiring the washer, and resolve the per-model `modelJson` from a
git-ignored cache dir so a user's fetched modelJson is never committed. This is the "easy way
to expand to other models" surface (see `PROTOCOL.md §6`).
**Scope.**
- `server/models/registry.py`: `decode_report(report_xml)` picks the decoder module by
  `modelName` (precise — two washers of different vintage share type 201 but differ in
  modelJson) then `deviceType` (fallback), and `load_model_json(model_name)` resolves the
  modelJson from `data/models/<modelName>.model.json` (runtime cache, git-ignored) then the
  decoder's committed fixture. Unknown device → a graceful note payload, never an exception.
- Decoder modules declare `MODEL_JSON_FIXTURE` (committed fixture path) alongside their
  existing `DEVICE_TYPE`/`MODEL_NAME`. Reusing an existing class = one registry entry; a new
  class (fridge, AC, …) = one decoder module + one entry.
- Refactor `server/state.py` to ingest via the registry; when a modelJson is loaded, apply
  the modelJson full decode to each `monData` (additive `monData_decoded` key). Validated for
  the washer; a no-op for models with no cached modelJson (unchanged behaviour).
- `tools/fetch_model_json.py` writes `data/models/<modelName>.model.json` by default
  (`modelName` resolved from the device record) with a `--stdout` escape hatch.
- Update `PROTOCOL.md §6` to describe the registry + cache as the supported expansion path.
**Acceptance.** A washer `<Report>` ingests through the registry and yields its prior decode
plus `monData_decoded` (Course=Mix, State RUNNING…). A report with an unregistered
`modelName`+`devType` decodes to a graceful note, no exception.
**Verify.** `python -m pytest tests/test_registry.py tests/test_server.py tests/test_wtwn3.py
tests/test_model_json.py` — all green; overall `python -m pytest -q` stays green.
**Out of scope.** Dryer/fridge decoders themselves (TASK-021 / TASK-061 — capture-gated);
control (M3); HA mapping (M4).

### TASK-061 ✅ Fridge support — new ThinQ1 appliance class (capture-gated)
**Depends on:** TASK-060 (done), TASK-062 (the capture rig), and a fridge diagmon capture.
**Progress (2026-07-20).** Identity + modelJson already done — the hard half of "add a model":
- **Identity:** `modelName 2REB1GLPX1___`, `deviceId FRIDGE_DEVICE_ID`,
  LAN IP `192.168.20.FRIDGE`, MAC `<FRIDGE_MAC>` (matches the deviceId suffix), firmware
  `QC_Modem_1.2.80` (same ThinQ1 modem as the washer/dryer).
- **ThinQ1 CONFIRMED:** fetched modelJson has `Monitoring.type = BINARY(BYTE)` → the same
  byte protocol as the washer, so `server/models/model_json.py` will decode it.
- **State model known:** 12-byte struct — `TempRefrigerator`, `TempFreezer`, `IcePlus`,
  `FreshAirFilter`, `SmartSavingMode`, `WaterFilterUsedMonth`, `DoorOpenState`, `TempUnit`,
  `SmartSavingModeStatus`, `LockingStatus`, `ActiveSavingStatus`, `EcoFriendly`. Cached at
  `data/models/2REB1GLPX1___.model.json` (fetched via wideq + the smartthinq integration's
  token, read from HA `ssh ha` → `/config/.storage/core.config_entries` entry
  `05a727e2…`, region `IT`, `use_api_v2=true`, oauth `https://gb.lgeapi.com/`).
- **fetch tool fix:** `modelName` is nested under `Info` for non-washer classes (washer has
  it top-level) — `tools/fetch_model_json.py` now reads both.
**Remaining (NOW UNBLOCKED — TASK-062 ✅, capture in hand).** We have the envelope: a fridge
capture (`flows/fridge-20260721.log`) with `diagMonType EventMonitoring`, `eventType`s
`COMMON_WIFI_ON` + `COMMON_PERIODIC`, and binary `monData` (13/170 bytes — variable, not the
fixed 12 the modelJson implied) + `diagData`. The fridge's envelope is the WM family's
**same shape** (base64→XML→binary double-decode, `monData`/`diagData`/`option` fields) — so it
likely reuses `wm_envelope` + a registry entry, exactly like the dryer, decoded via its
modelJson. **Identity note:** the live `<Report>` carries `modelName 1REB1GLPX1___` / `devType
101` (not the modelJson's `2REB1GLPX1___`) — the registry must key on `1REB1GLPX1___`/`101`,
and the cached modelJson (`2REB1GLPX1___.model.json`) must be renamed/mapped to match. Then
write `server/models/fridge_*.py` + a replay test against `flows/fridge-20260721.log`.
**Acceptance / Verify.** As TASK-020, against the fridge capture: a decode matching the
captured state (e.g. a door event's `DoorOpenState` flips, or temps read sanely).

### TASK-062 ✅ Capture rig for no-SNI / IP-based ThinQ1 clients (e.g. the fridge)
**Done.** 2026-07-21. **Proven on the live fridge**: the transparent-mode route-as-next-hop
design decrypts no-SNI/IP-connecting ThinQ1 clients. `capture-fridge.sh` (router policy-route:
fridge `:46030` → `.200` next-hop, dst preserved) + `fridge-capture-setup.sh`/`-teardown.sh`
(`.200`: local nft REDIRECT + `mitmdump --mode transparent` + conntrack flush) are committed.
Captured the first decrypted fridge diagmon (`flows/fridge-20260721.log`): `report/diagmon`
with `eventType COMMON_WIFI_ON` + `COMMON_PERIODIC` (a 170-byte `monData` state snapshot), and
a `FWInfoSettingSvc` POST. Fridge settled + recovered direct-to-LG on teardown.
**Key identity finding for TASK-061:** the live fridge reports `modelName 1REB1GLPX1___` /
`devType 101` — different from the modelJson's `2REB1GLPX1___`. The registry must key on
`1REB1GLPX1___`/`101`; the cached modelJson filename needs reconciling (rename or map).
**Depends on:** — (rig work; enables TASK-061 and likely other appliances).
**Why this exists (discovered 2026-07-20).** The current `capture-ctl` rig intercepts
`*.lgthinq.com:46030` in mitmproxy **regular mode**, which routes by SNI. The washer/dryer
work because they connect to the *hostname* `eic.lgthinq.com` (ClientHello carries SNI). The
fridge connects to LG **by raw IP** (`68.219.0.211`, then `52.158.31.24` — LG **rotates**
these) with **no SNI**, so regular-mode mitm has nothing to route on and **silently drops the
SYN**. Confirmed empirically: the fridge's `:46030` was DNAT'd to mitm but every SYN went
`UNREPLIED`; mitm logged nothing. Two more fridge behaviours compound this:
- The fridge is **quiet on `:46030` while its persistent `:47878` keepalive is up**; it only
  floods `:46030` (re-registration) when `:47878` is disrupted. So a `:46030` capture likely
  needs `:47878` disrupted first.
- A `:47878` reverse-mode mitm (`--mode reverse:https://<ip>:47878`) was tried and **did not
  handshake** either (flow `UNREPLIED`).
**Which channel carries the telemetry (hint, observed 2026-07-20).** Watching the fridge's
conntrack (transport-level, no decryption) during a door-open/beep: the event correlated with
a burst of **new `:46030` connections to `20.105.96.214`** (`eic.lgthinq.com`'s IP — the same
IP the washer's `:46030` diagmon uses), while the persistent `:47878` keepalive (to
`52.158.31.24`) showed **no spike**. So the state-change telemetry almost certainly goes over
`:46030`, not `:47878` — i.e. **`:46030` is the right capture target** (the no-SNI problem to
solve is on `:46030`, same channel as the washer), and a door-open/beep is a reliable way to
provoke a capture window. (Caveat: this was connection-level only, not payload — it does not
prove those `:46030` connections carried a `report/diagmon` POST, only that they fired.)
**Goal.** A capture mode that decrypts ThinQ1 clients which connect by IP / without SNI.
**Researched design (2026-07-20, from mitmproxy docs).** Transparent mode is the answer —
**but not via router DNAT.** The mitmproxy docs are explicit: for transparent mode,
"Network Address Translation should not be applied before the traffic reaches mitmproxy,
since this would remove the target information." Our router-side DNAT rewrites dst to
`.200:46030` before mitm sees it, so `SO_ORIGINAL_DST` on `.200` returns the post-DNAT addr,
not the real LG IP — that's exactly why the fridge's no-SNI connections were dropped (regular
mode had no SNI to route on; transparent-mode-via-DNAT had no original-dst to recover).

The fix is a **different topology, not a different mitm mode**: route the appliance's traffic
to `.200` with its **original destination IP intact** (no DNAT), then run `mitmdump --mode
transparent` on `.200`. mitm recovers the real LG server per-connection via `SO_ORIGINAL_DST`,
so no-SNI is irrelevant and LG's rotating IPs "just work" (no hardcoded upstream). Two ways
the docs give to deliver original-dst-intact traffic to `.200`:
- **(a) Custom gateway / next-hop:** make `.200` the appliance's gateway (or a policy
  next-hop for the appliance's subnet), so packets route to `.200` with dst = real LG IP.
  `.200` must forward + run mitm transparent on the intercept port.
- **(b) Policy routing on the router:** a fw4 rule that routes the appliance's `:46030` to
  `.200` as next-hop (not DNAT). Preserves dst IP.
Then `mitmdump --mode transparent --set ssl_insecure=true` on `.200`; the appliance's TLS
(mitm presents a cert it accepts — no pinning, same as washer/dryer) decrypts regardless of
SNI. **Needs the live fridge to validate** (acceptance = a decrypted fridge `<Report>`).
**Also fixed (capture-ctl, 2026-07-20).** `nft_has` now matches the EXACT comment
(`comment "lg-mitm"`) not the substring `lg-mitm`, so a sibling rule tagged `lg-mitm-47878`
no longer false-reads as "DNAT already installed" (the bug that cost an hour during the first
fridge attempt). Verified empirically: a decoy `lg-mitm-47878` rule no longer masks the
`:46030` state.
**Acceptance.** The rig decrypts at least one `report/diagmon` POST from a no-SNI client
(fridge) → a usable `flows/fridge-*.log`. No appliance left offline after the capture.
**Verify.** A decrypted fridge `<Report>` (with `diagMonData`) appears in the capture;
`capture-ctl off` restores the appliance's direct LG path (verified by conntrack).
**Out of scope.** Decoding the fridge payload (that's TASK-061, once captured). Generalising
to non-fridge no-SNI appliances is the point of this task — design it reusable.

---

## M3 — Control (write path) — HIGH RISK, capture-gated

### TASK-030 ✅ Reverse-engineer the command delivery mechanism
**Done.** 2026-07-21. The `:47878` persistent channel is the control delivery mechanism —
raw-TCP msgpack-length-prefixed JSON (NOT TLS, NOT HTTP). Captured + decoded: the cloud pushes
`Control`/`Set` commands with per-model `Value` keys (e.g. `{"RETM":"4"}` = fridge temp 4°C).
The appliance acks `ReturnCode: 0000` + responds with a B64 binary state snapshot. Full
protocol in `PROTOCOL.md §4`. Capture: `flows/fridge-47878-control-20260721.log`.

### TASK-031 🟦 Implement the :47878 control server (safety-gated)
**Depends on:** TASK-030 (done), TASK-011
**Goal.** Implement server-side `:47878` message handling so our local server can deliver
commands to ThinQ1 appliances.
**Scope.**
- `server/control_channel.py` — a TCP server on `:47878` that:
  - Accepts the appliance's persistent connection (the appliance initiates outbound).
  - Speaks the msgpack-length-prefixed JSON framing (1-byte length prefix + JSON payload).
  - Responds to `DevInfo` (device info on connect), `Alive` (keepalive), `Mon`/`Start`
    (monitor/poll -> respond with state snapshot), `Mon`/`Stop`.
  - Exposes a `send_control(device_id, command, value)` API for pushing `Control`/`Set`.
  - Behind `allow_control` (default off, per CLAUDE.md safety rule #5).
- A command queue / API surface (`POST /debug/command` or an MQTT command topic).
- Tests against the captured `:47878` traffic (replay the fridge's Control/Set exchange).
- Wiring into `server/app.py` `main()` so the server serves both `:46030` and `:47878`.
**Acceptance.** A replay test against `flows/fridge-47878-control-20260721.log` validates
the message framing + the Control/Set command format. The server responds correctly to
DevInfo/Alive/Mon.
**Verify.** `python -m pytest tests/test_control_channel.py`; pyright clean.
**Out of scope.** Live control validation on a physical appliance (needs supervised test).
**Out of scope.** Exposing control to HA before it's proven here.
**Safety.** Do not implement start/heat commands until stop/pause is proven and the user
explicitly approves. Confirm with the user before each new command type.

---

## M4 — Home Assistant integration

### TASK-040 ✅ Choose & spike the HA bridge
**Done (spike).** 2026-07-20. Decision **D-2 = MQTT discovery** (recorded in CLAUDE.md): the
server publishes decoded state to MQTT using HA's MQTT-discovery convention; HA auto-creates
sensor entities, no custom integration. `server/ha_mqtt.py` implements it: one shared JSON
state topic per device, each sensor's `value_template` extracts a field. Validated
end-to-end against a local mosquitto (discovery + shared JSON state round-trip; the
load-bearing value_template/state contract is unit-tested — the bug a broker-only test
misses). Open follow-ons (below): wire into the ingest path, friendly-name resolution in the
decoder, and the normalized state schema.
**Depends on:** TASK-012 (sensors) / TASK-022
**Goal.** Spike the MQTT-discovery path end-to-end with one sensor. ✅
**Verify.** `python -m pytest tests/test_ha_mqtt.py` (MQTT_LIVE=1 for the broker round-trip).

### TASK-064 ✅ Wire the MQTT bridge into the diagmon ingest path
**Done (wiring).** 2026-07-20. `DeviceStateStore` takes an optional `on_state(devId, payload)`
sink, invoked after each ingested payload (UNKNOWN diagnostics skip it; a raising sink can't
break ingestion). `server.main()` builds a paho MQTT client when `LGM_MQTT_HOST` is set
(optional `LGM_MQTT_USER`/`LGM_MQTT_PASS`) and registers a sink that publishes HA discovery
once-per-device (gated by an `announced` set) + the shared JSON state. Off by default; any
setup failure (paho missing, broker unreachable) degrades gracefully — MQTT off, server keeps
serving (the bridge never takes the fake-cloud down). The device's `modelName` flows from the
report into the HA device name (so HA shows `WTWN3`, not the UUID). 45 tests; live demo
confirms ingest → 5 discovery configs + 1 shared state. (Live HA validation pending the
broker creds / a real cycle — TASK-041.)
**Depends on:** TASK-040
**Goal.** Drive publish_state from ingest. ✅
**Acceptance (full).** A real appliance report updates an HA entity live — pending live broker.

### TASK-065 ✅ Friendly-name resolution belongs in the decoder
**Done.** 2026-07-20. `model_json.decode_friendly` now strips LG's `@<GROUP>_<LABEL>_W`
enum markers from every value via `_clean_label()` (regex `^@(.*)_W$`), in one place —
covers enum/reference/range/bit/string uniformly. `enum_name`/`reference_name` restored to
their original contract (cleaning moved up to `decode_friendly`, so the `None` sentinel and
the non-enum `else` branch are both handled). `@WM_STATE_RUNNING_W` → `WM_STATE_RUNNING`;
plain labels (`Mix`, `No Error`, `0`) pass through. Tests: `_clean_label` unit cases +
`test_real_decode_has_no_enum_markers` enforcing both `@` prefix AND `_W` suffix absence
against the real WTWN3 modelJson (runs in CI — the fixture is committed). Reviewed via
/code-review (2 findings applied: the reference_name None-contract regression + the
else-branch leak — both resolved by the one-place cleaning). 42 tests, pyright clean.
**Depends on:** TASK-020
**Goal.** `monData_decoded` never carries `@…_W` markers. ✅
**Acceptance.** Decoded state has no `@…_W` strings; the bridge publishes verbatim. ✅

### TASK-041 ✅ Full sensor surface for all appliances
**Done (validated against real HA).** 2026-07-21. Replayed washer + dryer + fridge captures
through the server pointed at the real HA MQTT broker (core-mosquitto on .110:1883).
All three appliances published discovery + retained state: washer (State/Course/Remain/
Wash/SpinSpeed/WaterTemp/Error/…), dryer (State/Course/ProcessState/Remain/DryLevel/Error),
fridge (TempRefrigerator/TempFreezer/DoorOpenState/…). The MQTT bridge derives one sensor per
decoded field (appliance-agnostic, per-device discovery). Verified the retained state messages
landed on the broker for all 3 devIds. The fridge periodic shows some Unknown (the 170-byte
monData is richer than the modelJson protocol; a WIFI_ON/door-event capture decodes fully).
**Depends on:** TASK-040, TASK-022, TASK-064
**Goal.** Expose the whole normalised state schema as HA entities (run state, course,
remaining time, door, error, counters…) for washer + dryer, with correct device_class/units.
**Acceptance.** Both appliances appear as HA devices with a complete, correctly-typed sensor
set; entities update live.
**Verify.** Drive a cycle; watch entities track it in HA.
**Out of scope.** Controls (TASK-042).

### TASK-042 ⬜ Control entities (only if M3 succeeded)
**Depends on:** TASK-031, TASK-041
**Goal.** Surface proven control commands as HA entities (button/switch/select).
**Acceptance.** The HA control triggers the appliance (supervised test). Disabled if
`allow_control` is off.
**Verify.** Supervised manual test with user present.

### TASK-043 ✅ Packaging & install docs
**Done.** 2026-07-21. `docs/INSTALL.md` — step-by-step: gen cert, fetch modelJson,
configure the server (env-var table), route traffic (SNI capture-ctl vs no-SNI transparent
rig), start the server with MQTT, verify in HA. Covers bridge mode (validated) + notes on
standalone (pending TASK-050).
**Depends on:** TASK-041
**Goal.** Make it installable: HACS-compatible layout (if native) or documented MQTT setup
(if bridge), `manifest.json`/`hacs.json` as needed, and a user-facing install guide.
**Acceptance.** A clean HA instance can install and configure it by following the guide only.
**Verify.** Do exactly that on a fresh HA instance.

---

## M5 — Cloud-free & production hardening

### TASK-050 ✅ Sever-the-cloud validation
**Done (fridge, 2026-07-21).** The first proof that a ThinQ1 appliance operates fully
cloud-free on our local server. Setup: routed the fridge's :46030 to our standalone server
(transparent-mode route-as-next-hop); firewalled :47878 (blocked the real-cloud keepalive);
ran the server in standalone mode (hardened stubs, no forwarding to LG). The fridge
reconnected, hit report/diagmon + PowerSavingInfoSvc + FWInfoSettingSvc, accepted our
synthetic 200 responses (0 errors, 0 retries over 2+ min), and the server decoded its state
(12-field COMMON_PERIODIC: TempRefrigerator/TempFreezer/DoorOpenState). Clean teardown: the
fridge recovered direct-to-LG immediately. Scripts: fridge-sever-test-setup.sh /
-teardown.sh. The whole project premise (impersonate the LG cloud locally) is now validated.
**Depends on:** TASK-011
**Goal.** Prove the appliances fully operate with the LG cloud **firewalled/blackholed**, not
merely redirected. (Can be started early — this de-risks the whole premise.)
**Scope.** Block outbound to all LG hostnames/IPs at the router except to our server; run
both appliances through real cycles for a sustained period, including power-cycling them.
**Acceptance.** Appliances operate and reconnect to our server across reboots with LG
unreachable; no degraded behaviour over a multi-day soak. Findings written up.
**Verify.** Documented soak log; a reboot test transcript.

### TASK-051 ✅ Durable DNS + TLS strategy
**Done.** 2026-07-23. The routing strategy is fully documented + durable:
- SNI appliances: firewall DNAT (capture-ctl) — IP-agnostic (matches source IP + port,
  not destination), so LG's CNAME/A rotation doesn't break it. Documented in
  NETWORK_SETUP.md with iptables/pfSense translations.
- No-SNI appliances: transparent-mode route-as-next-hop (capture-fridge scripts).
- Cert lifecycle: gen-cert.sh generates a CA + *.lgthinq.com leaf (825-day expiry).
  Appliances accept any cert (no pinning) — documented + re-verified live (TASK-050).
- .capture.env.example fixed: uses valid example IPs (192.168.1.x) instead of the
  scrubbed placeholders that broke nft.
- Reboot durability: the nft rules are installed by capture-ctl on demand; for
  production standalone, the systemd unit (TASK-052) handles server restart. The
  router-side DNAT can be made persistent via /etc/config/firewall if desired.
**Depends on:** TASK-010, TASK-050 (done)
**Goal.** Make redirection and cert trust survive firmware quirks and reboots.
**Scope.**
- Document the production routing setup: for SNI appliances, firewall DNAT (capture-ctl's nft);
  for no-SNI appliances, the transparent-mode route-as-next-hop (capture-fridge scripts). Both
  must survive router reboots (make the nft rules + policy routes persistent in `/etc/config/firewall`
  or a startup script).
- Cert lifecycle: `gen-cert.sh` generates a CA + `*.lgthinq.com` leaf cert; document renewal
  (the 825-day cert expiry), and whether the appliance caches the cert across reboots.
- LG hostname rotation: `eic.lgthinq.com` is a CNAME into `*.aws-thinq-prd.net` with a rotating
  A record. The nft DNAT is IP-agnostic (matches source IP + port, not destination), so rotation
  doesn't break it. Document this.
- `.capture.env.example` should use valid example IPs (not the scrubbed placeholders that break nft).
**Acceptance.** Documented, reboot-durable config; a "what if it breaks" troubleshooting section.
**Verify.** Reboot router; confirm the DNAT rules survive and appliances reconnect.

### TASK-052 ✅ Service deployment & auto-start
**Done.** 2026-07-23. Shipped both deployment options:
- `deploy/lg-fake-cloud.service` — systemd unit (auto-restart on failure, env-var config,
  both ports exposed).
- `deploy/docker-compose.yml` + `deploy/Dockerfile` — containerized (both ports, volume
  for state, paho-mqtt pre-installed).
- `docs/INSTALL.md` updated with deployment instructions for both.
**Depends on:** TASK-011
**Goal.** Server runs unattended: systemd unit or Docker/compose, restart-on-failure, logs
rotated, state persisted across restarts.
**Scope.**
- A systemd unit (`lg-fake-cloud.service`) that starts `python -m server.app` with env vars
  (LGM_MQTT_HOST etc.), `Restart=on-failure`, `After=network-online.target`.
- OR a `docker-compose.yml` with the server + optional mosquitto.
- The state dir (`data/`) must persist across restarts (volume mount / bind).
- Log rotation (systemd journal handles it; for Docker, a logging driver).
- Document the deploy in `docs/INSTALL.md`.
**Acceptance.** `systemctl start lg-fake-cloud` / `docker compose up` brings the server up on
boot; `kill -9` → it restarts and resumes reporting within seconds.
**Verify.** Reboot the host; confirm the stack is up and appliances reporting without manual steps.

### TASK-053 ✅ New-device onboarding runbook
**Done.** 2026-07-21. `docs/ONBOARDING.md` walks through adding a new ThinQ1 appliance:
identify (router/HA), capture (SNI vs no-SNI rig), fetch modelJson, add the decoder module
(WM-family = thin reuse; new class = derive from capture), register, test.
**Depends on:** TASK-020/021, TASK-041
**Goal.** A repeatable guide to add a *third* ThinQ1 appliance (capture → model map → HA
entities).
**Acceptance.** `docs/ONBOARDING.md` that a future maintainer follows to add a device without
re-reading the whole codebase.
**Verify.** Dry-run the runbook against one of the existing appliances as if it were new.

---

## M6 — Full local integration (beyond cloud parity)

### TASK-066 ✅ modelJson ControlWifi → command vocabulary
**Depends on:** TASK-031
**Goal.** Extract the per-model command vocabulary from the modelJson `ControlWifi.action`
section (the exact `Cmd`/`CmdOpt`/`Value` template with per-field placeholders). Build a
data-driven command registry so each model knows what it can send.
**Scope.**
- Parse `ControlWifi.action.SetControl` from each modelJson → command template (field names,
  types from the `Value` section: Enum/Range).
- Build a `server/models/<model>.commands` data structure or a `ControlVocab` class.
- The fridge modelJson has the template inline; the washer needs RemoteStart + Reserve +
  course/options from the Config + Value sections.
**Acceptance.** Each model's command set is known and testable.
**Verify.** `python -m pytest tests/test_control_vocab.py`.

### TASK-067 ⬜ MQTT command discovery + handling (bidirectional)
**Depends on:** TASK-066, TASK-064
**Goal.** Publish HA command entities (buttons, selects, numbers) via MQTT discovery with
`command_topic`; subscribe to those topics; on command message → `control_channel.send_command()`.
**Scope.**
- `ha_mqtt.publish_command_discovery()` — one command entity per modelJson field (button for
  RemoteStart, number for TempRefrigerator, select for Course, etc.).
- `mqtt_bridge` subscribes to `homeassistant/<component>/lgthinq_<devId>/+/command` topics.
- On message → translate the HA command to a Control/Set `Value` dict → `control_channel.send_command()`.
- Behind `allow_control` (off by default).
**Acceptance.** A command published to the MQTT command topic reaches `control_channel.send_command()`.
**Verify.** Unit test with a FakeMQTT client; live test supervised.

### TASK-068 ⬜ Energy/cycle monitoring from diagData
**Depends on:** TASK-020
**Goal.** Decode the `WM_WASH_END` `diagData` blob (energy, water, cycle info) and expose as
HA sensors.
**Scope.**
- The diagData 69-byte blob contains energy/water/useDate (see `flows/washer-cycle-20260719.state.md`).
- Decode via the modelJson's `FridgeMonitoring`/`EnergyMonitoring` section (if present) or by
  the hand-derived byte map.
- Expose as: energy-per-cycle sensor, water-per-cycle sensor, cycle-count sensor.
**Acceptance.** A completed wash cycle produces decoded energy/water values in HA.
**Verify.** Replay test against `flows/washer-overnight-20260723.log`.

### TASK-069 ⬜ Scheduled-start surface (WM_RESERVE)
**Depends on:** TASK-040
**Goal.** Expose the washer's `WM_RESERVE` state ("scheduled, starts in Xh Ym") as a HA sensor.
**Scope.**
- The washer reports `WM_RESERVE` with `Remain_Time` counting down to the scheduled start.
- Add a sensor that shows "Scheduled (starts in 1h19m)" when the state is RESERVE.
- The cloud integration misses this (polls too slowly); this is a "more than cloud" feature.
**Acceptance.** When the washer is in reserve mode, the HA sensor shows the countdown.
**Verify.** Replay the overnight capture (contains WM_RESERVE events).

### TASK-070 ⬜ Real-time Mon/Start query on :47878
**Depends on:** TASK-031, TASK-067
**Goal.** Our server can actively query the appliance's current state by sending `Mon`/`Start`
on the `:47878` channel, rather than waiting for the appliance's periodic push.
**Scope.**
- `control_channel.query_state(dev_id)` — sends `Mon`/`Start`, waits for the B64 state
  response, decodes it via the modelJson.
- Wire to a `POST /debug/query` endpoint and/or an MQTT command (so HA can refresh on demand).
- The appliance responds within milliseconds (confirmed in the fridge capture).
**Acceptance.** Calling query_state returns the current decoded state immediately.
**Verify.** Live test: query the fridge, compare the response to the last periodic push.

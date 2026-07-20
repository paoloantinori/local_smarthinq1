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

### TASK-003 ⬜ Complete the endpoint catalogue in PROTOCOL.md
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

### TASK-011 ⬜ Implement the bootstrap endpoints (keep-alive contract)
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

### TASK-022 ⬜ Stable public state schema
**Depends on:** TASK-020, TASK-021
**Goal.** Define the normalised, appliance-agnostic state object the HA layer consumes
(so M4 doesn't depend on per-model internals). Version it.
**Acceptance.** Both models emit the same top-level schema; schema documented in
`docs/STATE_SCHEMA.md`. Adding a new appliance means adding a mapping, not changing consumers.
**Verify.** Both appliances' decoders validate against the schema.

### TASK-060 ⬜ Multi-model decoder registry & modelJson cache
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

### TASK-061 🟦 Fridge support — new ThinQ1 appliance class (capture-gated)
**Depends on:** TASK-060 (done), TASK-062 (the capture rig), and a fridge diagmon capture.
**Progress (2026-07-20).** Identity + modelJson already done — the hard half of "add a model":
- **Identity:** `modelName 2REB1GLPX1___`, `deviceId e256c140-e3b2-11e8-9fac-0051ed66db5b`,
  LAN IP `192.168.20.182`, MAC `00:51:ed:66:db:5b` (matches the deviceId suffix), firmware
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
**Remaining (blocked on TASK-062 + capture).** Only the *envelope* is unknown: the fridge's
`diagMonType` set / inner-XML / which binary field carries the state struct. Must come from a
real capture — **do not assume the washer's `WM_*` family**. Then write
`server/models/fridge_2REB1GLPX1.py` (envelope parser) + a registry entry, reusing
`model_json.py` for the binary decode via `STATE_FIELDS`.
**Why blocked.** The fridge could not be captured with the current rig — see **TASK-062** (the
fridge connects to LG by IP with no SNI, which the SNI-routed mitm can't handle). The capture
attempt is deferred to a future session.
**Acceptance / Verify.** As TASK-020, against the fridge capture: a decode timeline matching
what the fridge actually did.

### TASK-062 🚫 Capture rig for no-SNI / IP-based ThinQ1 clients (e.g. the fridge)
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
**Candidate approaches to evaluate (do not assume — research + test before committing).**
- mitmproxy **transparent mode** (`--mode transparent`) using `SO_ORIGINAL_DST`. ⚠️ The DNAT
  happens on the *router*, not on the mitm host (`.200`), so the original destination is
  rewritten before it reaches mitm — `SO_ORIGINAL_DST` on `.200` may not recover it. Verify
  whether original-dst survives, or whether the DNAT must terminate on the mitm host itself.
- mitmproxy **reverse mode** with an explicit upstream. ⚠️ LG **rotates IPs**, so a hardcoded
  upstream breaks when the appliance reconnects to a different IP. Consider resolving the
  current peer dynamically (e.g. from conntrack) per-capture, or routing by the connection's
  original dst.
- A raw TLS capture (e.g. tap the fridge's session keys via an on-device/log approach) if
  mitm interception proves infeasible — last resort.
**Also fix (capture-ctl).** `nft_has` greps the rule comment `lg-mitm`, which is a *substring*
of any `lg-mitm-<port>` tag — ad-hoc per-port rules (like the `:47878` attempt) falsely read
as "DNAT already installed" and skip the `:46030` install. Use a non-overlapping comment scheme
(or match on the exact rule, not a substring) before adding more per-port rules.
**Acceptance.** The rig decrypts at least one `report/diagmon` POST from a no-SNI client
(fridge) → a usable `flows/fridge-*.log`. No appliance left offline after the capture.
**Verify.** A decrypted fridge `<Report>` (with `diagMonData`) appears in the capture;
`capture-ctl off` restores the appliance's direct LG path (verified by conntrack).
**Out of scope.** Decoding the fridge payload (that's TASK-061, once captured). Generalising
to non-fridge no-SNI appliances is the point of this task — design it reusable.

---

## M3 — Control (write path) — HIGH RISK, capture-gated

### TASK-030 ⬜ Reverse-engineer the command delivery mechanism
**Depends on:** TASK-002(e)
**Goal.** From captured app→cloud→device control traffic, determine **how** a command reaches
a push-only ThinQ1 module: pending-command field in a periodic response, long-poll, separate
session, persistent channel? Document in `PROTOCOL.md §4`.
**Acceptance.** A written, evidence-backed description of the full command round-trip for at
least one command (e.g. remote stop), with the exact request/response bytes.
**Verify.** The description predicts the bytes of a *second*, independently captured command.
**Out of scope.** Implementing it (TASK-031). **This may conclude local control is
infeasible for these modules — that is a valid, valuable outcome; record it and stop M3.**

### TASK-031 ⬜ Implement local control (safety-gated)
**Depends on:** TASK-030 (feasible), TASK-011
**Goal.** Have the local server deliver a command to the appliance and observe it act.
**Scope.** Start with the least dangerous command (remote **stop/pause**). Behind an explicit
`allow_control: true` config flag, default off. Log every command issued.
**Acceptance.** Issuing the command via a local `POST /debug/command` causes the physical
appliance to respond, verified by eye and by the subsequent state report.
**Verify.** Manual, supervised, with the user present. Never automated in CI.
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

### TASK-041 ⬜ Full sensor surface for both appliances
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

### TASK-043 ⬜ Packaging & install docs
**Depends on:** TASK-041
**Goal.** Make it installable: HACS-compatible layout (if native) or documented MQTT setup
(if bridge), `manifest.json`/`hacs.json` as needed, and a user-facing install guide.
**Acceptance.** A clean HA instance can install and configure it by following the guide only.
**Verify.** Do exactly that on a fresh HA instance.

---

## M5 — Cloud-free & production hardening

### TASK-050 ⬜ Sever-the-cloud validation
**Depends on:** TASK-011
**Goal.** Prove the appliances fully operate with the LG cloud **firewalled/blackholed**, not
merely redirected. (Can be started early — this de-risks the whole premise.)
**Scope.** Block outbound to all LG hostnames/IPs at the router except to our server; run
both appliances through real cycles for a sustained period, including power-cycling them.
**Acceptance.** Appliances operate and reconnect to our server across reboots with LG
unreachable; no degraded behaviour over a multi-day soak. Findings written up.
**Verify.** Documented soak log; a reboot test transcript.

### TASK-051 ⬜ Durable DNS + TLS strategy
**Depends on:** TASK-010, TASK-050
**Goal.** Make redirection and cert trust survive firmware quirks and reboots.
**Scope.** Finalise the diversion as **firewall DNAT** (`capture-ctl`'s nft approach — DNS
diversion is abandoned, see `PROTOCOL.md` §2); document the cert lifecycle and any renewal;
capture what breaks if LG rotates hostnames (CNAME/A rotation already observed).
**Acceptance.** Documented, reboot-durable config; a "what if it breaks" troubleshooting
section.
**Verify.** Reboot appliance + server + router; everything reconnects unattended.

### TASK-052 ⬜ Service deployment & auto-start
**Depends on:** TASK-011
**Goal.** Server runs unattended: systemd unit or Docker/compose, restart-on-failure, logs
rotated, state persisted across restarts.
**Acceptance.** `systemctl`/`docker compose` brings the whole stack up on boot; kill -9 the
server → it restarts and resumes reporting.
**Verify.** Reboot the host; confirm the stack is up and appliances reporting without manual
steps.

### TASK-053 ⬜ New-device onboarding runbook
**Depends on:** TASK-020/021, TASK-041
**Goal.** A repeatable guide to add a *third* ThinQ1 appliance (capture → model map → HA
entities).
**Acceptance.** `docs/ONBOARDING.md` that a future maintainer follows to add a device without
re-reading the whole codebase.
**Verify.** Dry-run the runbook against one of the existing appliances as if it were new.

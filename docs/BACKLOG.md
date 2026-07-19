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

### TASK-010 ⬜ Server skeleton + TLS termination
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

### TASK-012 ⬜ Ingest & persist diagmon state
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

### TASK-013 ⬜ Bridge mode (forward + observe)
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

### TASK-020 ⬜ Decode washer (WTWN3) state model
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

### TASK-021 ⬜ Decode dryer (RC90U2) state model
**Depends on:** TASK-020, TASK-002(d)
**Goal.** Same as TASK-020 for the dryer (type 202); reuse the TASK-020 framework.
**Acceptance / Verify.** As TASK-020, against the dryer cycle capture.
**Out of scope.** Anything washer-specific already covered.

### TASK-022 ⬜ Stable public state schema
**Depends on:** TASK-020, TASK-021
**Goal.** Define the normalised, appliance-agnostic state object the HA layer consumes
(so M4 doesn't depend on per-model internals). Version it.
**Acceptance.** Both models emit the same top-level schema; schema documented in
`docs/STATE_SCHEMA.md`. Adding a new appliance means adding a mapping, not changing consumers.
**Verify.** Both appliances' decoders validate against the schema.

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

### TASK-040 ⬜ Choose & spike the HA bridge
**Depends on:** TASK-012 (sensors) / TASK-022
**Goal.** Decide between (A) **MQTT discovery** (server publishes state to MQTT; HA
auto-discovers — fastest, matches `rethink`) and (B) a **native Python custom integration**
(`DataUpdateCoordinator` polling the server's HTTP API — nicer UX, more work). Spike the
recommended one (A) end-to-end with one sensor.
**Acceptance.** One real washer sensor (e.g. run-state) visible in a test HA instance via the
chosen path. Decision recorded in CLAUDE.md as D-2 with rationale.
**Verify.** Screenshot/log of the entity updating in HA as the appliance reports.

### TASK-041 ⬜ Full sensor surface for both appliances
**Depends on:** TASK-040, TASK-022
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

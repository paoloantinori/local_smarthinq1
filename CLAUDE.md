# Project: cloud-free LG ThinQ1 → Home Assistant

Read this first, every session. It orients you; the detail lives in `docs/`.

## What we're building

A **local server that impersonates the LG ThinQ cloud** so my LG ThinQ1 (legacy) appliances
run with **no LG cloud**, plus a **Home Assistant** integration on top of it. Two appliances:

- Washer **`WTWN3`** — `deviceType 201` — `WASHER_DEVICE_ID` — ✅ decoded
- Dryer **`RC90U2_WW`** — `deviceType 202` — `DRYER_DEVICE_ID` — ✅ decoded
- Fridge **`2REB1GLPX1___`** — `REF` — `FRIDGE_DEVICE_ID` — ThinQ1
  confirmed + modelJson decoded; **capture blocked** on a no-SNI/IP-connect problem (TASK-062).

They are **ThinQ1**: XML over TLS to `*.lgthinq.com`, `lgehadm` API, `User-Agent: IOE Client`.
They **push** telemetry (`report/diagmon`, base64 XML) to the cloud. Crucially, in our
captures **the modules do not pin the TLS cert** — mitmproxy decrypts them — which is what
makes local impersonation possible. This is the whole premise; keep re-verifying it.

## Where things are

- `docs/ROADMAP.md` — milestones M0–M5 and exit criteria. Start here for "what next".
- `docs/BACKLOG.md` — the task list you execute (TASK-001…). Self-contained specs.
- `docs/PROTOCOL.md` — **living** record of the observed protocol. Update it as you learn.
- `docs/references.md` — prior art. **`anszom/rethink` and `sampsyo/wideq` are gold — read
  them before writing protocol code.**
- `docs/STATE_SCHEMA.md` — TBD (TASK-022 ⬜): the normalised state contract for HA.
- `server/` — the fake-cloud server + decoders (see "Code map" below).
- Capture rig: `capture-ctl` (nft-DNAT on/off toggle; design in `docs/superpowers/specs/2026-07-18-capture-toggle-design.md`), using the `lg_portfix.py` mitmproxy addon. `hosts` / `dns_rewrite.txt` are abandoned DNS-diversion artifacts (non-functional — see `docs/PROTOCOL.md` §2). Captures: `lavatrice_dump.txt` (boot/idle), `flows/washer-cycle-20260719.log` (full wash cycle), `flows/dryer-cycle-20260720.log` (full dry cycle).

## Code map

- `server/app.py` — HTTPS fake-cloud (`bridge` ↔ `standalone` modes); `responses.py` (XML
  response builders); `state.py` (per-device diagmon ingest store).
- `server/models/registry.py` — dispatches decode by `modelName` then `deviceType`, and
  resolves the per-model `modelJson` (runtime cache → committed fixture). Adding a model =
  one line in `_MODULES`.
- `server/models/wm_envelope.py` — the shared WM-family diagmon envelope (washer + dryer):
  base64→XML→binary double-decode, appliance-agnostic.
- `server/models/{washer_wtwn3,dryer_rc90u2}.py` — per-model identity + byte reads on top of
  the envelope; each declares `MODEL_JSON_FIXTURE` / `STATE_FIELDS`.
- `server/models/model_json.py` — applies a modelJson to a binary blob (mirrors wideq's
  `ModelInfo`); the full per-model decode (`monData_decoded`).
- `tools/fetch_model_json.py` — fetches a device's modelJson from LG (token via env, never
  argv) → `data/models/<modelName>.model.json`.

## Commands

- `python -m pytest -q` — all tests (33).
- `python -m pyright server/ tests/` — type check (must stay clean).
- `./capture-ctl on|off|status` — the capture rig (nft DNAT + mitmproxy on `:46030`).
- Decode a capture inline: `from server.models import registry; registry.decode_report(<xml>)`.

## How to work here (non-negotiable)

1. **Capture-driven.** Never invent a payload or endpoint from a guess. If you don't have a
   real capture for it, capture it first (M0) or say you can't. Every protocol claim traces
   to a file in `flows/` or `lavatrice_dump.txt`.
2. **Bridge before sever, read before write, one appliance before both** (see ROADMAP
   principles). The washer is the reference device.
3. **`PROTOCOL.md` is truth-in-progress.** When reality contradicts it, fix it in the same
   change. Label guesses `(UNCONFIRMED)`.
4. **Verify, then claim.** Each task has a *Verify* step. Run it, read the output, paste it.
   "It should work" is not done.
5. **Physical safety (M3+).** Any code path that can actuate an appliance (start, heat,
   spin) stays behind `allow_control` (default off) and needs Paolo's explicit OK per command
   type. Test control only supervised, never in CI.
6. Keep changes small and scoped to one TASK. Respect each task's *Out of scope*.

## Decisions

- **D-1 — Implementation language: Python.** The HA ecosystem, `wideq`, and the existing
  `lg_portfix.py` are Python; a single-language stack is simpler to maintain. We read
  `rethink` (TypeScript) as a **spec/reference**, not a fork. Revisit only if a hard blocker
  appears.
- **D-2 — HA bridge: MQTT discovery (decided TASK-040, 2026-07-20).** The server publishes
  decoded state to MQTT using HA's "MQTT discovery" convention; HA auto-creates sensor
  entities with no custom integration. Spiked in `server/ha_mqtt.py` (validated end-to-end
  against a local mosquitto: discovery + state round-trip). Chosen over a native Python HA
  integration (`DataUpdateCoordinator`) because it decouples the server from HA versioning
  and matches `anszom/rethink`. Wiring it to the live HA broker is a host/credentials change.

## Conventions

- Server code under `server/`; per-model decoders under `server/models/`; captures under
  `flows/`; persistent state/logs under `data/` (git-ignored).
- Prefer stdlib + a small, justified dependency set. Pin versions.
- Match existing file style. Comments only for non-obvious constraints, not narration.
- Before committing **code**, run `/code-review` and `/simplify` and apply their findings first. Data/docs-only commits (captures under `flows/`, notes, specs) are exempt — there is no code to review.

## Status

- **M0 done.** Capture rig (`capture-ctl`) verified; full washer + dryer cycles captured.
- **M2 substantially done.** Washer (TASK-020 ✅) and dryer (TASK-021 ✅) decode fully via
  modelJson; multi-model registry (TASK-060 ✅) + shared WM envelope (TASK-063 ✅) are in.
- **M1 (fake-cloud server) built; bridge mode validated** (2026-07-20, dryer) — our server
  terminates TLS + forwards to real LG + ingests/decodes state, with the appliance running
  normally through it. **Standalone (real LG firewalled) is the remaining de-risk.**
- **Fridge (TASK-061):** ThinQ1 confirmed + modelJson decoded; capture blocked on the no-SNI
  rig (TASK-062).
- **Next candidates:** the supervised sever test, the fridge no-SNI capture, or M3 control
  (capture-gated).

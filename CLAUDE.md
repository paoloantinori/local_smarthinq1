# Project: cloud-free LG ThinQ1 → Home Assistant

Read this first, every session. It orients you; the detail lives in `docs/`.

## What we're building

A **local server that impersonates the LG ThinQ cloud** so my LG ThinQ1 (legacy) appliances
run with **no LG cloud**, plus a **Home Assistant** integration on top of it. Two appliances:

- Washer **`WTWN3`** — `deviceType 201` — `d9bf16c0-c7c0-11ea-bec4-0051eda91d3d`
- Dryer **`RC90U2_WW`** — `deviceType 202` — `2ca6ccd0-7c25-11e9-ab15-7440be73f9ad`

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
- `docs/STATE_SCHEMA.md` — (created in TASK-022) the normalised state contract for HA.
- Capture rig: `capture-ctl` (nft-DNAT on/off toggle; design in `docs/superpowers/specs/2026-07-18-capture-toggle-design.md`), using the `lg_portfix.py` mitmproxy addon. `hosts` / `dns_rewrite.txt` are abandoned DNS-diversion artifacts (non-functional — see `docs/PROTOCOL.md` §2). Captures: `lavatrice_dump.txt` (boot/idle), `flows/washer-cycle-20260719.log` (a full wash cycle).

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
- **D-2 — HA bridge: TBD in TASK-040.** Leaning MQTT-discovery (like `rethink`) for speed;
  record the final call here with rationale.

## Conventions

- Server code under `server/`; per-model decoders under `server/models/`; captures under
  `flows/`; persistent state/logs under `data/` (git-ignored).
- Prefer stdlib + a small, justified dependency set. Pin versions.
- Match existing file style. Comments only for non-obvious constraints, not narration.
- Before committing **code**, run `/code-review` and `/simplify` and apply their findings first. Data/docs-only commits (captures under `flows/`, notes, specs) are exempt — there is no code to review.

## Status

M0 substantially done: `capture-ctl` (nft-DNAT rig) built and verified — TASK-001 ✅; a full
washer wash cycle captured 2026-07-19 (`flows/washer-cycle-20260719.log`) — TASK-002(c) ✅.
Next: dryer cycle + app-issued control (TASK-002 d/e), then M2 decode (TASK-020 — started,
byte-map notes in `flows/washer-cycle-20260719.state.md`).

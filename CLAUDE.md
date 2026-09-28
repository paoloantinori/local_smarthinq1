# Project: cloud-free LG ThinQ1 → Home Assistant

Read this first, every session. It orients you; the detail lives in `docs/`.

## What we're building

A **local server that impersonates the LG ThinQ cloud** so my LG ThinQ1 (legacy) appliances
run with **no LG cloud**, plus a **Home Assistant** integration on top of it. Three appliances:

- Washer **`WTWN3`** — `deviceType 201` — `WASHER_DEVICE_ID` — ✅ decoded
- Dryer **`RC90U2_WW`** — `deviceType 202` — `DRYER_DEVICE_ID` — ✅ decoded
- Fridge **`2REB1GLPX1___`** — `REF` — `FRIDGE_DEVICE_ID` — ThinQ1
  fully captured (TASK-062, transparent-mode route-as-next-hop for no-SNI appliances).

They are **ThinQ1**: XML over TLS to `*.lgthinq.com`, `lgehadm` API, `User-Agent: IOE Client`.
They **push** telemetry (`report/diagmon`, base64 XML) to the cloud. Crucially, in our
captures **the modules do not pin the TLS cert** — mitmproxy decrypts them — which is what
makes local impersonation possible. This is the whole premise; keep re-verifying it.

## Where things are

- `docs/ROADMAP.md` — milestones M0–M5 and exit criteria. Start here for "what next".
- `docs/BACKLOG.md` — the task list you execute (TASK-001…). Self-contained specs.
- `docs/PROTOCOL.md` — **living** record of the observed protocol. Update it as you learn.
- `docs/PAIRING_RUNBOOK.md`: LG-cloud weather + pairing playbook (diagmon-specific
  health checks, edge pinning, the 99%-stall signature, devId gotcha, corpus/tooling,
  standalone-pairing exit strategy). Read before any onboarding window.
- `docs/references.md` — prior art. **`anszom/rethink` and `sampsyo/wideq` are gold — read
  them before writing protocol code.**
- `docs/STATE_SCHEMA.md`: the normalised state contract for HA (v1.1: diagmon reports
  plus the `:47878` pump variant; TASK-022 ✅, estesa da TASK-078).
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
- `server/models/{washer_wtwn3,dryer_rc90u2,fridge_1reb1glpx1}.py`: per-model identity +
  byte reads on top of the envelope; each declares `MODEL_JSON_FIXTURE` / `STATE_FIELDS`.
- `server/models/model_json.py`: applies a modelJson to a binary blob (mirrors wideq's
  `ModelInfo`); the full per-model decode (`monData_decoded`). Also `clean_label`, the shared
  `@…_W` marker stripper (state + command paths).
- `server/models/control_vocab.py`: per-model command vocabulary from the modelJson
  `ControlWifi` section; `CommandEntity`/`WireCommand` turn an HA command into a `:47878`
  `Control`/`Set` Value (TASK-066/067).
- `server/control_channel.py`: the `:47878` server, **bilingual by family** (dispatch on the
  connection's first byte: fridge = raw msgpack-string framing; WM = TLS + `[4B len][JSON]`,
  TASK-078). Ingests the WM pump into the state store; `query_state()`/`start_polling()`
  (TASK-070: `LGM_POLL_INTERVAL`, 0 = off; `GET/POST /debug/query?dev=`; snapshot hex only
  with `LGM_SNAP_HEX`); `send_command()` pushes commands (behind `LGM_ALLOW_CONTROL`, off).
- `server/ha_mqtt.py` + `server/mqtt_bridge.py`: HA MQTT-discovery bridge: publishes decoded
  state sensors, an error-alert binary_sensor, and (when control is on) command entities;
  subscribes to command topics and routes them to `control_channel`.
- `server/wm_bridge.py`: the single-threaded MITM engine (End/bridge with
  on_client_data/on_up_data observers, idle_timeout), shared by the passthrough mode and
  the relay tool (TASK-080).
- `server/thinq_events.py`: ThinQ Connect API MQTT subscriber (TASK-081; PAT via
  `LGM_THINQ_PAT_FILE`, no-op without PAT or SDK). Pushes cloud events into the
  store + MQTT for HA; complements the :46030 decode.
- `tools/fetch_model_json.py` — fetches a device's modelJson from LG (token via env, never
  argv) → `data/models/<modelName>.model.json`.
- `capture-wm47878.sh` + `tools/wm47878_tls_relay.py` (con `capture-router-lib.sh` condivisa,
  TASK-076): il rig transparent-TLS per il canale `:47878` WM (relay pass-through, log in
  chiaro). `tools/wm47878_door_check.py` / `wm46030_timeline.py`: analisi del corpus.
- `deploy/haos-addon/`: the HAOS "app" packaging (TASK-071..073): `config.yaml` (form
  schema + `host_network`), `run.sh`, `routing-setup.sh` (per-port DNAT + UCI persistence),
  `test_run_sh.py`, `replay_flow.py`. **run.sh reads `/data/options.json` directly with jq,
  NOT via `bashio::config`** (which calls the Supervisor API and 403s on host_network).
  The add-on's env translation is safety-gated: `LGM_ALLOW_CONTROL` is exported only when
  the form says true (never as `"0"`).
- Store repo `paoloantinori/ha-addon-lg-thinq1`: lightweight public repo HA adds as an
  add-on store; its Dockerfile clones THIS repo at a pinned `MAIN_COMMIT` (bump to update
  the add-on).

## Commands

- `python -m pytest -q`: all tests (118). Run it bare: the 6 `run.sh` tests live in
  `deploy/haos-addon/test_run_sh.py`, so a scoped `pytest tests/` silently reports 112.
- `pyright server/ tests/ tools/` (type check, must stay clean; bare `pyright`: on this
  box it is the linuxbrew binary, `python -m pyright` has no module).
- `MQTT_LIVE=1 python -m pytest -q tests/test_ha_mqtt.py` — include the broker round-trip
  (~5s; skipped by default to keep the suite fast).
- `REPLAY_LIVE=1 python -m pytest -q deploy/haos-addon/replay_flow.py`: replay a captured
  flow at a running add-on container (skipped by default). Add-on build/test/runbook:
  `deploy/haos-addon/README.md`.

**Gotcha: do not remove `pyproject.toml`'s pytest config** (`asyncio_mode = "auto"` +
`addopts = "-p no:homeassistant"`). Two stray transitive test plugins (pytest-asyncio 1.4
strict mode; pytest-homeassistant-custom-component, which blocks real sockets) otherwise
break collection or the whole suite with confusing errors. If tests suddenly error at
collection, check this file first.
- `./capture-ctl on|off|status` — the capture rig (nft DNAT + mitmproxy on `:46030`).
- `bash gen-cert.sh` — generate the fake-cloud TLS cert (`data/cert.pem` + `data/key.pem`).
- `LGM_MQTT_HOST=<broker> LGM_MQTT_USER=<u> LGM_MQTT_PASS=<p> python -m server.app` —
  start the fake-cloud server with the HA MQTT bridge (bridge mode by default).
- `LGM_ALLOW_CONTROL=1`: opt-in to the `:47878` control path (publishes command entities +
  routes HA commands to the appliance). **Off by default**; physical-actuation gate (rule #5).
  Washer/dryer buttons stay unpublished until their wire format is captured + approved.
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
6. **`:47878` differs by family.** Fridge (REF): raw TCP, each message ONE msgpack string
   containing JSON (NOT a msgpack map), NOT TLS (`PROTOCOL.md` §4). Washer/dryer (WM):
   TLS + `[4-byte length][JSON]`, same vocabulary, decoded (`PROTOCOL.md` §4.4; no door
   bit in the WM state frame). The server is bilingual (TASK-078) BUT the MITM
   passthrough on WM breaks the LG app (LG fingerprints the TLS: JA3). Production
   architecture: `:46030` bridge + `:47878` direct to LG + ThinQ Connect API events.
7. Keep changes small and scoped to one TASK. Respect each task's *Out of scope*.
8. **HAOS Docker ENV does NOT pass to service processes.** The s6 supervisor strips
   Dockerfile ENV vars; `run.sh` must `export` everything. PAT, upstream IPs, and any
   other config must go through run.sh or options.json, never through Docker ENV.

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
- **M2 done.** Washer (TASK-020 ✅), dryer (TASK-021 ✅), and fridge (TASK-061 ✅) decode
  end-to-end via modelJson. Multi-model registry (TASK-060 ✅) + shared WM envelope
  (TASK-063 ✅) + label cleanup (TASK-065 ✅).
- **M1 done.** Fake-cloud server — bridge mode validated (dryer) + **standalone sever test
  passed** (fridge, TASK-050 ✅): the appliance operates cloud-free on our server.
- **M4 done (spiked).** HA MQTT-discovery bridge (TASK-040 ✅) — publishes per-device state
  to HA; validated against a real broker. Wired into the ingest path (TASK-064 ✅).
- **Fridge capture rig solved** (TASK-062 ✅): transparent-mode route-as-next-hop for no-SNI
  appliances. Both channels (`:46030` telemetry + `:47878` control) captured.
- **M3 (control): fridge protocol decoded + server-side implemented; WM `:47878` decoded
  (2026-09-25).** The fridge's `:47878` channel is raw-TCP msgpack-JSON (NOT TLS); the WM
  family's `:47878` is TLS + `[4B len][JSON]`, same vocabulary, `Mon Start` gates the 1 Hz
  pump, and the WM state frame carries NO door bit (verdict, `PROTOCOL.md` §4.4). Commands: `Control`/`Set` with per-model `Value` keys (e.g.
  `{"RETM":"4"}` = fridge temp). Server-side push is wired: MQTT command entities →
  `control_channel.send_command()` (TASK-066 vocab ✅, TASK-067 MQTT handling ✅), gated behind
  `LGM_ALLOW_CONTROL` (off by default). Only the fridge's `Set` selects publish; washer/dryer
  buttons stay hidden until their wire format is captured + approved (rule #5). Live supervised
  actuation test still pending.
- **Deployed (1.3.2).** Production architecture live: `:46030` bridge (decode +
   forward to LG, app works), `:47878` direct to LG (app online), ThinQ Connect API
   PAT events (real-time HA push). TASK-068/069/070/076/077/078/080/081 all done.
   Remaining: TASK-079 (healthcheck across corpus), TASK-069 live confirm at first
   scheduled start, washer cloud re-registration.

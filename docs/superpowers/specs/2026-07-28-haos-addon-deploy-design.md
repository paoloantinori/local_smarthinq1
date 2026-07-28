# Design: HAOS add-on deployment of the LG ThinQ1 fake-cloud

**Date:** 2026-07-28
**Status:** Approved (design phase)
**Goal:** Run the local fake-cloud server as a persistent, always-on Home Assistant
OS "app" on the rpi4, so the appliance reaches the cloud impersonator on a host that is always
up and always on the LAN (eliminating the "phantom IP" failure mode that took the washer
offline over the 2026-07-26..28 weekend).

## Context and motivation

The server works, but the capture/deployment rig has so far run on a developer laptop
(`192.168.20.CAPTURE`) that is not always reachable. Over 2026-07-26 to 28, mitmproxy died and the
host left the LAN, so the OpenWrt DNAT kept diverting the washer's traffic to an unreachable
IP; the appliance stayed "disconnected" in the LG app for ~3 days. The fix is structural: move
the fake-cloud to the most reliable always-on host on the LAN.

**Topology (confirmed with the user):**
- **rpi4** (aarch64): HAOS, HA core, and the Mosquitto MQTT broker (as an add-on). Always on.
  Has spare capacity for one more Python container.
- **miniPC**: runs other services incl. NPM proxy. Always on. Not used for this.
- **OpenWrt** router: owns the LAN `.20.x`, DHCP is statically reserved per host, so host IPs
  are effectively fixed.

## Architecture

```
┌─────────────────── rpi4 (HAOS, always-on) ───────────────────┐
│                                                               │
│  ┌──────────────┐    localhost:1883   ┌───────────────────┐  │
│  │  HA core     │◄────── MQTT ───────►│  lg-fake-cloud    │  │
│  │  (discovery) │   state + commands  │  (our app)        │  │
│  └──────────────┘                     │  host_network     │  │
│         ▲                             │  :46030 (TLS)     │  │
│         │                             │  :47878 (raw TCP) │  │
│  Mosquitto add-on                     └─────────┬─────────┘  │
│  (broker, :1883)                                │            │
└─────────────────────────────────────────────────┼────────────┘
                                                  │ LAN .20.x
                          ┌───────────────────────┴──────────┐
                          │ OpenWrt (manual DNAT)             │
                          │ .106:46030 → <rpi4-IP>:46030       │
                          │ .106:47878 → <rpi4-IP>:47878       │
                          └───────────────────────┬──────────┘
                                                  │
                                          ┌───────┴────────┐
                                          │ Washer/Dryer/  │
                                          │ Fridge (ThinQ1)│
                                          └────────────────┘
```

**Read path (already implemented):** appliance → `:46030` (intercepted by OpenWrt DNAT) →
decode via registry/modelJson → publish state to MQTT (`localhost:1883`) → HA discovers
sensors + error-alert binary_sensor.

**Write path (already implemented, gated):** HA publishes on a command_topic → `on_message`
translates to `Control`/`Set` → push to appliance on `:47878` (raw TCP msgpack). Behind
`LGM_ALLOW_CONTROL` (default off); washer/dryer buttons unpublished until captured+approved.

**What changes vs today:** only the host. The Python server is unchanged; the broker is local
(localhost) instead of external; the DNAT target is the rpi4 instead of `.200`. No new
application logic.

## Decisions

- **Host:** the fake-cloud runs as a HAOS "app" on the rpi4, co-located with HA + Mosquitto.
  MQTT to the broker is localhost (no cross-host hops for state/commands).
- **Networking:** `host_network: true` in the add-on, so `:46030` and `:47878` bind directly on
  the HAOS host (the DNAT reaches them without port mapping).
- **Routing:** the add-on does NOT touch the router. The OpenWrt DNAT is updated manually once
  to point at the rpi4 IP, and persisted across router reboots (TASK-051 durable routing) so a
  brief blackout does not detach the appliance.
- **Cert:** auto-generated inside the add-on via `gen-cert.sh` (CA + `*.lgthinq.com` leaf into
  `/data`). ThinQ1 modules do not pin/validate the cert (PROTOCOL.md §2), so no CA distribution
  is needed. Identical to today, just packaged.
- **MQTT auth:** a dedicated user created in the Mosquitto add-on; the app authenticates to
  `127.0.0.1:1883` with it. Credentials come from the add-on's config form (a secret field).
- **Default mode:** `standalone` (impersonates the cloud locally, no forwarding to LG). Bridge
  mode remains available as an option for debugging.
- **Physical safety (CLAUDE.md #5):** `allow_control` is a config-form option, default `false`.
  Live supervised control actuation is a separate, explicitly user-approved step, never in CI.

## Add-on structure

New directory in the repo: `deploy/haos-addon/`

```
deploy/haos-addon/
├── config.yaml          # metadata + options schema (config form) + host_network
├── Dockerfile           # FROM ghcr.io/home-assistant/base, installs deps
├── run.sh               # bashio: /data/options.json → env vars, then launch server
├── apparmor.txt         # required by the app structure
├── README.md            # user-facing install instructions
└── translations/en.yaml # optional form labels
```

### config.yaml (form schema)

- `name: LG ThinQ1 fake-cloud`, `slug: lg-thinq-fake-cloud`, `version`, `arch: [aarch64, amd64]`.
- `host_network: true`, `startup: services` (same level as the Mosquitto add-on; they start
  together-ish, since HA core is not an add-on and does not honor add-on `startup:` ordering),
  `boot: auto`.
- `map: []` (uses `/data` for state + cert; no config/ssl share needed).
- `options` + `schema`:
  - `mqtt_host` (default `"127.0.0.1"`), `mqtt_port` (default 1883), `mqtt_user`, `mqtt_password`
    (secret).
  - `mode` (default `"standalone"`, enum `["standalone","bridge"]`).
  - `allow_control` (default `false`, bool).
  - `upstream_host`, `upstream_port` (optional, for bridge mode).

### Dockerfile

```dockerfile
FROM ghcr.io/home-assistant/base:latest
RUN apk add --no-cache openssl          # for gen-cert.sh; bashio + tzdata already in base
COPY . /app
WORKDIR /app
RUN pip install --no-cache-dir paho-mqtt   # only non-stdlib dependency
CMD ["/run.sh"]
```
`gen-cert.sh` uses bash process substitution (`<(...)`); the base image ships bash (bashio
depends on it), so this works, but keep the `openssl` install explicit in case of a future
base-image swap.

Explicit `FROM` (not `ARG BUILD_FROM`) because Supervisor 2026.04.0 removed the automatic
`BUILD_FROM` fallback (verified against the official HA developer docs, 2026-07-28).

### run.sh (options → env vars)

```bash
#!/usr/bin/env bashio
set -e
[ -f /data/cert.pem ] || bash gen-cert.sh /data          # auto-generate cert if missing
export LGM_HOST=0.0.0.0 LGM_PORT=46030 LGM_CONTROL_PORT=47878
export LGM_STATE_DIR=/data LGM_CERT=/data/cert.pem LGM_KEY=/data/key.pem
export LGM_MODE="$(bashio::config 'mode')"
# SAFETY (CLAUDE.md #5): control_channel.ALLOW_CONTROL is `!= ""` (present = ON). So we must
# only export LGM_ALLOW_CONTROL when the form says true; exporting "0" would invert the gate
# and enable physical actuation. Leave it unset otherwise (server default = off).
bashio::config.true 'allow_control' && export LGM_ALLOW_CONTROL=1 || true
export LGM_MQTT_HOST="$(bashio::config 'mqtt_host')" LGM_MQTT_PORT="$(bashio::config 'mqtt_port')"
export LGM_MQTT_USER="$(bashio::config 'mqtt_user')" LGM_MQTT_PASS="$(bashio::config 'mqtt_password')"
# bridge mode upstream (only meaningful when mode=bridge)
export LGM_UPSTREAM_HOST="$(bashio::config 'upstream_host')" LGM_UPSTREAM_PORT="$(bashio::config 'upstream_port')"
exec python -m server.app
```

The Python server is **not modified**: it reads the same `LGM_*` env vars; bashio writes them
from the form. All 92 existing tests remain valid.

> **Safety-gate detail (spec-review finding C1, confirmed against code).** `control_channel.py`
> line `ALLOW_CONTROL = os.environ.get("LGM_ALLOW_CONTROL", "") != ""` treats any non-empty
> value as ON, including `"0"`. So `run.sh` must export the var *only when* `allow_control` is
> true, never as `"0"`. The implementation's Verify step (T4) MUST assert that with
> `allow_control=false`, `control_channel.ALLOW_CONTROL` is falsy and the MQTT bridge publishes
> no command entities.

## Routing (OpenWrt, manual + persistent)

- One-time update: point the existing DNAT from `.200` to the rpi4 IP, for both `:46030` and
  `:47878`, scoped to the appliance source IP (as `capture-ctl` does today), plus the masquerade
  hairpin.
- Persisted in `/etc/config/firewall` (or an init script) so it survives router reboots
  (TASK-051). A `deploy/haos-addon/routing-setup.sh` (target configurable) makes this repeatable
  and documented.

## Testing strategy

1. **Local (on the Fedora, no appliance):** build the add-on Docker image and run it; replay a
   captured `diagmon` flow (`flows/*.log`) and assert the server decodes and the add-on's
   form/options wiring is correct. Validates structure + `run.sh` translation without touching
   the home network.
2. **Supervised live (at home):** install on the rpi4, configure the form, update the DNAT, and
   observe the washer reconnect and state arrive in HA. The live control-actuation test
   (`allow_control=true`) is a separate, user-approved step.

**Pre-flight checks to perform during install (spec-review suggestions):**
- Re-verify the "Supervisor 2026.04.0 removed `BUILD_FROM`" claim at build time; the explicit
  `FROM ghcr.io/home-assistant/base` is safe regardless.
- From the add-on shell, confirm the Mosquitto listener is reachable: `nc -z 127.0.0.1 1883`
  (the official Mosquitto add-on may gate its listener behind `active`/`permit_join`).
- Confirm no other add-on binds `:46030` or `:47878` on the HAOS host (HA core itself does not).

## Out of scope (YAGNI)

- Bundling modelJson fixtures for not-yet-captured devices (washer/dryer/fridge already
  decoded). `/data/models/` populates on demand via `tools/fetch_model_json.py`.
- A dedicated add-on web UI beyond the config form. `GET /debug/state` stays reachable at
  `http://<rpi4>:46030/debug/state`.
- Multi-host/HA-cluster support.
- New appliances or reverse-mode for other channels.

## Implementation tasks (for the plan phase)

- T1: `deploy/haos-addon/config.yaml` (metadata + options schema).
- T2: `Dockerfile` + `run.sh` (bashio → env, cert auto-gen).
- T3: `deploy/haos-addon/routing-setup.sh` (DNAT to configurable target + OpenWrt persistence).
- T4: local test (build + replay a flow; assert form/options and server boot).
- T5: docs (`docs/INSTALL.md` HAOS section, README add-on mention).

## References

- Server config surface: 14 `LGM_*` env vars (inventory in `server/app.py`).
- Existing deploy artifacts: `deploy/{Dockerfile,docker-compose.yml,lg-fake-cloud.service}`
  (TASK-052).
- HAOS app structure + `host_network`/`map`/`options`: verified against
  developers.home-assistant.io/docs/add-ons/configuration (2026-07-28); noted the Supervisor
  2026.04.0 `BUILD_FROM` removal.
- D-2 (MQTT discovery over native integration) and the physical-safety rule (#5) stand.

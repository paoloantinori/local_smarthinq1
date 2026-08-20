# local_smarthinq1 — Local control for LG ThinQ1 appliances

De-cloud legacy LG ThinQ1 appliances: capture, decode, and replace the LG cloud
with a local server + Home Assistant integration. No LG account, no cloud dependency,
no app required.

## Supported appliances

| Appliance | Model | Type | Status |
|-----------|-------|------|--------|
| 👍 Washer | WTWN3 | 201 | Full decode; cloud-free validated |
| 👍 Dryer | RC90U2_WW | 202 | Full decode |
| 👍 Fridge | 1REB1GLPX1___ | 101 | Full decode; cloud-free validated; `:47878` control captured |

Other ThinQ1 appliances with the same protocol family likely work with minimal
adaptation — see [Onboarding](docs/ONBOARDING.md).

## How it works

ThinQ1 appliances maintain two persistent channels to LG's cloud:

- **`:46030` (telemetry)** — the appliance pushes binary state via `report/diagmon`
  POSTs (base64-encoded XML wrapping base64-encoded binary). We intercept, decrypt,
  and decode it using the per-model `modelJson` byte layout.
- **`:47878` (control)** — a persistent raw-TCP channel using msgpack-length-prefixed
  JSON. The cloud pushes commands (`Control`/`Set`) and the appliance acknowledges +
  responds with state snapshots. **This is the first public documentation of ThinQ1
  command delivery** — see [PROTOCOL.md §4](docs/PROTOCOL.md).

Our local server impersonates the LG cloud on both channels, decodes the appliance
state, and publishes it to Home Assistant via MQTT discovery. An optional **bridge
mode** forwards traffic to the real LG cloud while observing — useful for
reverse-engineering or running alongside the official app.

## Quick start

### Capture appliance traffic

```bash
cp .capture.env.example .capture.env   # set your appliance IPs
./capture-ctl on                       # divert + decrypt :46030
tail -f data/mitm.log                  # decoded state appears here
./capture-ctl off                      # restore direct-to-LG
```

For appliances that connect by raw IP without SNI (some fridges, ACs), use the
[transparent-mode rig](docs/NETWORK_SETUP.md#worked-example-openwrt-fw4--what-capture-ctl-does)
instead of `capture-ctl`.

### Run the server + see it in Home Assistant

Running Home Assistant OS? The recommended always-on install is the add-on: add
`https://github.com/paoloantinori/ha-addon-lg-thinq1` as an add-on repository in HA and
install "LG ThinQ1 fake-cloud" (details in [INSTALL.md](docs/INSTALL.md)).

```bash
bash gen-cert.sh                       # generate the TLS cert
LGM_MQTT_HOST=<broker> python -m server.app   # bridge mode + HA MQTT bridge
```

Appliances appear in HA via MQTT discovery. Inspect live state at
`GET https://<host>:46030/debug/state`.

Full setup guide: [INSTALL.md](docs/INSTALL.md).

## Components

| Component | What it does |
|-----------|-------------|
| `server/app.py` | HTTPS fake-cloud server (standalone or bridge mode) |
| `server/models/registry.py` | Multi-model dispatch by `modelName`/`deviceType` |
| `server/models/wm_envelope.py` | Shared WM-family diagmon envelope decoder |
| `server/models/model_json.py` | ModelJson-driven binary state decode |
| `server/ha_mqtt.py` + `mqtt_bridge.py` | HA MQTT-discovery bridge |
| `capture-ctl` | SNI capture rig (nft DNAT, OpenWrt fw4) |
| `fridge-*-*.sh` | No-SNI capture rig (transparent mode) |
| `deploy/haos-addon/` | HAOS add-on packaging (always-on on a Raspberry Pi) |
| `tools/fetch_model_json.py` | Fetch a device's modelJson from LG |

## Documentation

| Doc | Covers |
|-----|--------|
| [INSTALL.md](docs/INSTALL.md) | Full install + configuration guide |
| [ONBOARDING.md](docs/ONBOARDING.md) | Adding a new appliance (step by step) |
| [NETWORK_SETUP.md](docs/NETWORK_SETUP.md) | Firewall/routing for traffic capture |
| [PROTOCOL.md](docs/PROTOCOL.md) | The observed protocol (both channels, fully decoded) |
| [STATE_SCHEMA.md](docs/STATE_SCHEMA.md) | The decoded state contract for consumers |
| [references.md](docs/references.md) | Prior art + hardware/firmware notes |

## Prior art

- [`anszom/rethink`](https://github.com/anszom/rethink) — fully-local ThinQ server
  (TypeScript). The closest comparable project. Does not document `:47878`.
- [`sampsyo/wideq`](https://github.com/sampsyo/wideq) — original reverse-engineered
  ThinQ1 client (Python). Canonical for `modelJson` value decoding.

Full prior-art analysis: [`claudedocs/research_lg-thinq-local-control-prior-art_2026-07-21.md`](claudedocs/research_lg-thinq-local-control-prior-art_2026-07-21.md).

## Safety

Capturing is **read-only** — it decrypts and observes, never commands the appliance.
The control protocol (`:47878`) is decoded and wired to Home Assistant (MQTT command
entities → `:47878`), but every actuation path is gated behind an `allow_control` flag
(off by default) and requires explicit per-command-type approval. Washer/dryer buttons
(start, stop, power) stay unpublished until their wire format is captured and approved;
only the fridge's temperature/select commands are exposed, and only when `allow_control`
is on.

An **error alert** binary_sensor fires whenever an appliance reports a fault (e.g. the
washer's `DE2` door-lock error), so a door left ajar at a scheduled start surfaces
immediately rather than going unnoticed.

## Disclaimer

LG ThinQ is a trademark of LG Electronics. This project is not affiliated with LG.
It is provided for research and educational purposes, with no warranty. If your device
breaks, you get to keep both pieces.

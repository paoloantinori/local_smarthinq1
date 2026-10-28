# LG ThinQ1 fake-cloud (HAOS add-on)

Runs the local LG ThinQ1 fake-cloud server on Home Assistant OS, so legacy ThinQ1 appliances
report state to HA without the LG cloud. Always-on; survives reboots.

## Setup

1. Install the add-on (local add-on repository; see the project's `docs/INSTALL.md`).
2. Create a dedicated MQTT user in the Mosquitto add-on, e.g. `lgthinq`.
3. Configure this add-on's form:
   - **mqtt_host**: `127.0.0.1` (the Mosquitto add-on, same host)
   - **mqtt_user** / **mqtt_password**: the dedicated user
   - **mode**: `standalone` (default; impersonate the cloud locally)
   - **allow_control**: leave `false` unless doing a supervised control test
4. Start. The log should show `LG fake-cloud (standalone) on :46030`.
5. Route the appliance's traffic here (OpenWrt DNAT): see `routing-setup.sh`.

## Ports

- `:46030` (TLS) for ThinQ1 telemetry (`report/diagmon`).
- `:47878` (raw TCP) for the ThinQ1 control channel (only active if `allow_control` is on).

## Safety

`allow_control` is off by default. Enabling it allows HA to actuate the appliance (physical
control). Washer/dryer buttons are not published until their wire format is captured and
approved; only the fridge's temperature selects are exposed.

## Local build (dev)

The Docker build context is the **repo root** (not this directory), so `COPY . /app` brings in
`server/`, `gen-cert.sh`, and `tools/`:

```bash
docker build -f deploy/haos-addon/Dockerfile -t lg-thinq-fake-cloud:test .
```

Verified (TASK-072): the image builds on `ghcr.io/home-assistant/base`, the server boots in
standalone on `:46030` + `:47878`, auto-generates the cert, and decodes a real washer flow.

## Install on HAOS (TASK-074, supervised, at home)

Target host: the rpi4 running HAOS (IPv4 `192.168.20.HA`, hostname `homeassistant`, SSH
aliases `ssh ha` for the HA core container and `ssh hahost` for the HAOS node). The HAOS
Supervisor manages apps under `/mnt/data/supervisor/apps/` (the "apps" naming follows the
Supervisor 2026.04+ add-on-to-app rebrand; the CLI uses `ha apps` / `ha store`, not `addons`).

Two install paths; the prebuilt-image path is fastest for a first test, the store path is the
durable one.

### Path A (fastest, first test): prebuilt image

1. Build + export the image on a dev box (build context = repo root):
   ```bash
   docker build -f deploy/haos-addon/Dockerfile -t lg-thinq-fake-cloud:test .
   docker save lg-thinq-fake-cloud:test | gzip > lg-thinq-fake-cloud.tar.gz
   ```
2. Copy it to the rpi4 and load it:
   ```bash
   scp lg-thinq-fake-cloud.tar.gz hahost:/tmp/
   ssh hahost 'docker load < /tmp/lg-thinq-fake-cloud.tar.gz'
   ```
3. Load the app via the Supervisor (the `apps` directory + `config.yaml`), or run it directly
   for a smoke test with env vars (see "Smoke run without the Supervisor" below). For a full
   HAOS-managed install, prefer Path B.

### Path B (durable): local app repository

1. Create a dedicated lightweight git repo for the add-on (so HA clones only the add-on, not
   the whole project): it contains `repository.yaml` (top level) and a `lg_thinq_fake_cloud/`
   folder with `config.yaml`, `run.sh`, `apparmor.txt`, and a Dockerfile that builds the server
   (either `git clone`s the main repo at a pinned commit, like The-sultan/hassio-rethink-addon
   does for rethink, or `COPY`s a vendored server tree).
2. Add it as a store on the rpi4:
   ```bash
   ssh ha 'ha store add https://github.com/<you>/ha-addon-lg-thinq1'
   ssh ha 'ha store reload'
   ```
   (Or via HA UI: Settings -> Add-ons -> Add-on store -> Repositories -> add the URL.)
3. Install + configure the app from the HA UI (or `ha apps install lg_thinq_fake_cloud`).

### Configure + route (both paths)

4. Create a dedicated MQTT user in the Mosquitto add-on (e.g. `lgthinq`).
5. Configure the app form: `mqtt_host=127.0.0.1`, `mqtt_user`/`mqtt_password` = the dedicated
   user, `mode=standalone`, `allow_control=false`.
6. Start the app; the log should show `LG fake-cloud (standalone) on :46030`.
7. Route the appliance's traffic here (run from any host that can SSH to the OpenWrt router):
   ```bash
   ./deploy/haos-addon/routing-setup.sh on      192.168.20.WASHER 192.168.20.HA
   ./deploy/haos-addon/routing-setup.sh persist 192.168.20.WASHER 192.168.20.HA
   ```
   (`192.168.20.WASHER` is the washer; `192.168.20.HA` is the rpi4. Adjust per appliance.)
8. Verify: reboot the router, confirm the appliance reconnects and state appears in HA.
   `routing-setup.sh status 192.168.20.WASHER` reports the active rule count.

## Manual transparent relays (2026-10-25 production setup)

The live box runs TWO manual relays inside the app container (started 2026-10-25,
NOT managed by s6/run.sh): the router DNATs the appliance ports to them with a
port shift, they pass TCP/TLS through to a pinned LG edge while logging decoded
chunks (`/data/wm_relay.py`, from TASK-080 tooling):

```
:46031 -> 52.158.121.103:46030   (log /data/relay-46030-1025.log)
:47879 -> 52.158.121.103:47878   (log /data/relay-47878-1025.log)
```

**Gotcha: every app update/restart KILLS these loops** (docker exec processes die
with the container). After any `ha apps update|restart|start`, re-launch them on
hahost or the appliances lose their cloud path:

```bash
for p in "46031 46030 relay-46030-1025" "47879 47878 relay-47878-1025"; do
  set -- $p
  docker exec -d app_fddaa777_lg_thinq_fake_cloud sh -c \
    "while true; do python3 /data/wm_relay.py --listen 0.0.0.0:$1 \
     --upstream 52.158.121.103:$2 --cert /data/cert.pem --key /data/key.pem \
     --log-file /data/$3.log >> /data/$3.stderr 2>&1; sleep 3; done"
done
```

Appliances reconnect on their own within ~2 minutes (verified 2026-10-28 update
to 1.4.0: dryer diagmon at +1 min, washer 1 Hz pump at +90 s). The replay store
for standalone mode lives at `/data/replay/lg_responses.json` in the same data
dir (TASK-126 F2; run.sh exports LGM_REPLAY_STORE when present).

### DNAT flip gotcha: conntrack holds long-lived flows (2026-10-28)

Flipping a redirect with `uci set` + `fw4 reload` only affects NEW connections.
The appliance's :47878 TLS session is long-lived and keeps flowing through the
OLD translation (up to the 3599 s ESTABLISHED timeout) even after the ruleset
shows the new target: the "flip did nothing" symptom. Force the reconnect by
deleting the conntrack entry on the router:

```bash
conntrack -D --orig-src <appliance-ip> -p tcp --orig-port-dst 47878   # or -L first to see tuples
```

The module notices the dead path and reconnects in seconds; the new SYN then
matches the new rule. Same applies to :46030 flows. (The rules themselves are
dport-matched even when the appliance dials a real LG edge IP directly, so the
flip works without touching DNS.)

### Full-offline validation (2026-10-28, dryer RC90U2_WW)

Both ports diverted to the add-on (standalone + replay :46030, terminate-only
:47878). The dryer ran against US ONLY: boot conversation (PowerSavingInfoSvc,
FWInfoSettingSvc, diagmon) accepted our REPLAYED real-LG responses with zero
retries (the September synthetic bridge was rejected here); DevInfo acked, our
Mon Start accepted (0000), 1 Hz pump decoded into the store and published to HA
(`homeassistant/sensor/lgthinq_rc90u2ww/state`). LG was completely blind: the
phone app showed the appliance offline. First appliance operating fully
cloud-free on local infrastructure.

## Smoke run without the Supervisor (dev/debug)

The HAOS base image uses s6 init, which expects `/run.sh` at the image root (the Dockerfile
copies it there). For a quick local smoke test that bypasses the Supervisor API (which is only
reachable inside a real HAOS install), run the server directly with env vars against the
generated cert:

```bash
mkdir -p /tmp/lg-data && echo '{"options":{"mode":"standalone"}}' > /tmp/lg-data/options.json
docker run --rm -p 46030:46030 -p 47878:47878 -v /tmp/lg-data:/data:Z \
  --entrypoint python3 lg-thinq-fake-cloud:test -m server.app
```

Then replay a captured flow at it (`REPLAY_LIVE=1 python -m pytest deploy/haos-addon/replay_flow.py`).


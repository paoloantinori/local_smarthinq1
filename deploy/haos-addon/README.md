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

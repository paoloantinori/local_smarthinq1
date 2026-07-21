# Installation guide

This project is a **local server + MQTT bridge**, not a native HA integration. You run the
fake-cloud server (which impersonates the LG cloud and decodes appliance state), and it
publishes to your MQTT broker. HA auto-discovers the entities via MQTT discovery.

## Prerequisites

1. **An LG ThinQ1 appliance** that doesn't pin TLS (see [`PROTOCOL.md`](PROTOCOL.md) §2).
2. **A Linux host** on the same LAN as the appliance (the "capture/server host") with:
   - Python 3.11+
   - [mitmproxy](https://mitmproxy.org/) 12.x (`pip install mitmproxy`)
   - [paho-mqtt](https://pypi.org/project/paho-mqtt/) (`pip install paho-mqtt`)
3. **A router you control** that can route/divert traffic (OpenWrt fw4 or any nftables/
   iptables router). See [`NETWORK_SETUP.md`](NETWORK_SETUP.md).
4. **An MQTT broker** reachable from the server host (HA's built-in Mosquitto works).
5. **Home Assistant** with the MQTT integration configured.

## Step 1: generate the cert

The fake-cloud server presents a TLS cert the appliance accepts (ThinQ1 modules don't
pin/validate):

```bash
./gen-cert.sh    # writes data/cert.pem + data/key.pem
```

## Step 2: fetch your appliance's modelJson

The modelJson defines how to decode your appliance's binary state. Fetch it from LG:

```bash
pip install git+https://github.com/sampsyo/wideq   # one-time
# Get a refresh_token (wideq login, or from the HA smartthinq_sensors integration's config)
LG_REFRESH_TOKEN=<token> python tools/fetch_model_json.py <deviceId> <region> <language>
# → writes data/models/<modelName>.model.json
```

See [`ONBOARDING.md`](ONBOARDING.md) for finding your `deviceId`/region/language.

## Step 3: configure the server

The server reads its config from environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LGM_HOST` | `0.0.0.0` | Bind address |
| `LGM_PORT` | `46030` | ThinQ1 API port |
| `LGM_CERT` | `data/cert.pem` | TLS cert path |
| `LGM_KEY` | `data/key.pem` | TLS key path |
| `LGM_STATE_DIR` | `data` | Where JSONL logs + state live |
| `LGM_MODE` | `bridge` | `bridge` (forward to real LG + observe) or `standalone` (answer alone) |
| `LGM_MQTT_HOST` | *(unset = off)* | MQTT broker host (enables the HA bridge) |
| `LGM_MQTT_PORT` | `1883` | MQTT broker port |
| `LGM_MQTT_USER` | *(unset)* | Optional MQTT username |
| `LGM_MQTT_PASS` | *(unset)* | Optional MQTT password |

## Step 4: route the appliance's traffic to the server

**SNI appliances** (washer/dryer — connect by hostname): use `capture-ctl`:

```bash
cp .capture.env.example .capture.env   # set APPLIANCES to your appliance IPs
./capture-ctl on
```

**No-SNI appliances** (fridge/AC — connect by raw IP): use the transparent-mode rig:

```bash
sudo ./fridge-capture-setup.sh
```

See [`NETWORK_SETUP.md`](NETWORK_SETUP.md) for the full firewall/routing guide.

## Step 5: start the server

```bash
LGM_MQTT_HOST=<your-mqtt-broker> LGM_MQTT_USER=<user> LGM_MQTT_PASS=<pass> \
  python3 -m server.app
```

The server starts in bridge mode (forwards to real LG + decodes), publishes HA discovery +
state to MQTT. HA auto-creates sensor entities for your appliance.

## Step 6: verify in Home Assistant

- Open HA → Settings → Devices & Services → MQTT.
- Your appliance should appear as a device (named by its model, e.g. "WTWN3").
- Sensor entities (run state, course, remaining time, temperatures, door status, …) update
  live as the appliance reports.
- `GET https://<server-host>:46030/debug/state` returns the current decoded state as JSON.

## Going fully cloud-free (standalone mode)

Switch `LGM_MODE=standalone` and firewall real LG at the router. The server answers the
bootstrap endpoints alone (no forwarding to LG). **Not yet validated against a live
appliance** — the standalone sever test (TASK-050) is the remaining de-risk. See
[`BACKLOG.md`](BACKLOG.md).

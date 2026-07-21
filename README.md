# cloud-free LG ThinQ1 → Home Assistant

Capture, decode, and replace the LG cloud for legacy **ThinQ1** appliances,
so they run fully local — with a Home Assistant integration on top.

> **Status: working.** Three appliances (washer, dryer, fridge) decode end-to-end. The fridge
> operates **cloud-free** on a local standalone server (supervised sever test passed). An HA
> MQTT-discovery bridge publishes decoded state to Home Assistant. The `:47878` control channel
> is captured + decoded (the first public documentation of ThinQ1 command delivery — raw-TCP
> msgpack JSON, not TLS). Local control (actuating appliances) is not yet implemented — the
> protocol is known, the server-side is the remaining work.
> See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the milestone plan.

## What this is

Legacy LG ThinQ1 appliances (a washer, a dryer, and a fridge) talk to LG's cloud over TLS
using a legacy XML/`lgehadm` protocol. This project:

1. **Captures** that traffic with a targeted MITM (`capture-ctl`) — no appliance pinning,
   no LG credentials, no disruption to the rest of the LAN.
2. **Decodes** the binary state the appliances push (`diagmon`), per-model.
3. **Stands up** a local server that impersonates the LG cloud so the appliances run with
   no LG dependency, bridged to Home Assistant.

## How it works (capture)

Traffic is diverted at the firewall, **not** via DNS:

- `capture-ctl` installs an **nftables DNAT** rule (OpenWrt fw4) scoped to each appliance's
  IP on port `:46030`, redirecting it to the mitmproxy host + a hairpin masquerade so replies
  route back. Everything else on the LAN (Home Assistant, phones, …) is untouched.
- mitmproxy 12.x decrypts (ThinQ1 modules don't pin/validate the cert), with the
  `lg_portfix.py` addon rewriting the upstream port 443→46030 and `ssl_insecure=true`.
- `capture-ctl on|off|status` is the whole interface.

⚠️ DNS diversion (AdGuard rewrites, dnsmasq `address=`, `/etc/hosts` zone overrides) does
**not** work here: `eic.lgthinq.com` is a CNAME into AWS, which AdGuard/dnsmasq rewrites
can't override (AdguardTeam/AdGuardHome#3350), and DNS diversion pollutes AdGuard's cache in
a way that silently breaks appliances after the rig is off. Use firewall DNAT. See
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) §2.

## Prerequisites

- An **LG ThinQ1** appliance (legacy XML/`lgehadm`; **not** ThinQ2 JSON/MQTT).
- A **router you control** that can DNAT — `capture-ctl` ships an OpenWrt fw4 implementation;
  other routers need the two rules installed manually (see the network setup guide).
- A **Linux capture host** on the same LAN, with [mitmproxy](https://mitmproxy.org/) 12.x
  (`mitmdump`) and a stable IP.
- Verify your appliance doesn't pin/validate TLS (the make-or-break premise).

➡️ **Full network setup** (the two firewall rules, the hairpin-masquerade requirement,
iptables/pfSense translations, and troubleshooting): see **[`docs/NETWORK_SETUP.md`](docs/NETWORK_SETUP.md)**.

## Quick start

### Capture appliance traffic

```bash
cp .capture.env.example .capture.env   # set APPLIANCES, TARGET_IP, ports
./capture-ctl on                       # start mitm + divert the appliances
tail -f data/mitm.log                  # decrypted traffic
./capture-ctl off                      # restore appliances to real LG
```

### Run the server + see it in Home Assistant

See [`docs/INSTALL.md`](docs/INSTALL.md) for the full guide. In short:

```bash
bash gen-cert.sh                      # generate the TLS cert
LGM_MQTT_HOST=<your-broker> python -m server.app   # start (bridge mode + HA MQTT)
```

Appliances appear in HA via MQTT discovery. `GET https://<host>:46030/debug/state` shows the
current decoded state as JSON.

`capture-ctl` is OpenWrt-fw4-specific. On other routers, run mitmproxy on the capture host
directly and install the two firewall rules by hand — see `docs/NETWORK_SETUP.md`.

## Adapting to your appliances

This repo is built around an EU washer, dryer, and fridge. For yours: confirm ThinQ1, find your
device identity (`deviceType`/`deviceId`/LAN IP), check whether your entry host is a CNAME,
verify the `:46030` port + no-pinning, and wire up the firewall. Full guides:
- **Network / firewall setup:** [`docs/NETWORK_SETUP.md`](docs/NETWORK_SETUP.md)
- **Onboarding (add a new appliance):** [`docs/ONBOARDING.md`](docs/ONBOARDING.md)
- **Protocol + adapting:** [`docs/PROTOCOL.md`](docs/PROTOCOL.md) §6

## Repo layout

```
capture-ctl            capture rig: nft-DNAT on/off toggle (SNI appliances)
fridge-*-*.sh          capture rig: transparent mode (no-SNI appliances)
lg_portfix.py          mitmproxy addon (upstream 443→46030)
flows/                 captured traffic + decode notes
server/                fake-cloud server (app.py, state.py, responses.py) +
                       models/ (registry.py, wm_envelope.py, washer/dryer/fridge decoders,
                       model_json.py) + ha_mqtt.py + mqtt_bridge.py
tests/                 decoder + server replay tests (57 tests)
docs/                  ROADMAP, BACKLOG, PROTOCOL, NETWORK_SETUP, INSTALL, ONBOARDING,
                       STATE_SCHEMA, references, prior-art research
```
`data/` (pids/logs/certs) and `.capture.env` are git-ignored.

## Safety

Capturing is **read-only** — it decrypts and observes; it never commands the appliance. The
control protocol (`:47878` command-delivery) is decoded but **not wired to actuation** — any
control path (start, heat, spin) is a safety-gated milestone (M3) behind an `allow_control`
flag, off by default, tested only supervised.

## Prior art

- [`anszom/rethink`](https://github.com/anszom/rethink) — fully-local LG ThinQ server (TS); the closest thing to the end goal.
- [`sampsyo/wideq`](https://github.com/sampsyo/wideq) — original reverse-engineered ThinQ1 client (Python); canonical for `modelJson` value decoding.

See [`docs/references.md`](docs/references.md).

# cloud-free LG ThinQ1 → Home Assistant

Capture, decode, and (eventually) replace the LG cloud for legacy **ThinQ1** appliances,
so they run fully local — with a Home Assistant integration on top.

> **Status: work in progress.** The capture rig + a partial washer state decoder work.
> The local fake-cloud server, control (write path), and HA integration are not yet built.
> See [`docs/ROADMAP.md`](docs/ROADMAP.md) for the milestone plan.

## What this is

Two LG ThinQ1 appliances (a washer and a dryer) talk to LG's cloud over TLS using a legacy
XML/`lgehadm` protocol. This project:

1. **Captures** that traffic with a targeted MITM (`capture-ctl`) — no appliance pinning,
   no LG credentials, no disruption to the rest of the LAN.
2. **Decodes** the binary state the appliances push (`diagmon`), per-model.
3. **Will** stand up a local server that impersonates the LG cloud so the appliances run with
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
- An **OpenWrt fw4** router (the nft rules target fw4's `dstnat_lan`/`srcnat_lan`). Adapt
  the DNAT + masquerade for other routers.
- A **Linux capture host** with [mitmproxy](https://mitmproxy.org/) 12.x (`mitmdump`).
- Verify your appliance doesn't pin/validate TLS (the make-or-break premise).

## Quick start

```bash
cp .capture.env.example .capture.env   # set APPLIANCES, TARGET_IP, ports
./capture-ctl on                       # start mitm + divert the appliances
tail -f data/mitm.log                  # decrypted traffic
./capture-ctl off                      # restore appliances to real LG
```

## Adapting to your appliances

This repo is built around one EU washer + dryer. For yours: confirm ThinQ1, find your
device identity (`deviceType`/`deviceId`/LAN IP), check whether your entry host is a CNAME,
verify the `:46030` port + no-pinning, and adapt the firewall rules. Full guide:
[`docs/PROTOCOL.md`](docs/PROTOCOL.md) §6.

## Repo layout

```
capture-ctl            capture rig: nft-DNAT on/off toggle
lg_portfix.py          mitmproxy addon (upstream 443→46030)
flows/                 captured traffic + decode notes
server/models/         per-model state decoders (washer_wtwn3.py — partial)
tests/                 decoder replay tests
docs/                  ROADMAP, BACKLOG, PROTOCOL, references, design spec
```
`hosts` / `dns_rewrite.txt` are abandoned DNS-diversion artifacts (non-functional); `data/`
(pids/logs) and `.capture.env` are git-ignored.

## Safety

Capturing is **read-only** — it decrypts and observes; it never commands the appliance. Any
control path (start, heat, spin) is a separate, safety-gated milestone (M3) behind an
`allow_control` flag, off by default, tested only supervised.

## Prior art

- [`anszom/rethink`](https://github.com/anszom/rethink) — fully-local LG ThinQ server (TS); the closest thing to the end goal.
- [`sampsyo/wideq`](https://github.com/sampsyo/wideq) — original reverse-engineered ThinQ1 client (Python); canonical for `modelJson` value decoding.

See [`docs/references.md`](docs/references.md).

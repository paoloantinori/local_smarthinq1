# References & Prior Art

The wheel is partly invented. Read these before writing protocol code — most of the ThinQ1
work exists somewhere.

## Primary — local / cloud-free (the model to follow)

- **`anszom/rethink`** — <https://github.com/anszom/rethink> — reverse-engineered,
  **fully local** LG ThinQ server. This is the closest thing to our end goal.
  - `rethink-cloud`: local server that emulates LG's cloud and bridges devices to **MQTT**
    for Home Assistant. Supports **washing machines** (ThinQ1), some as "mostly working".
  - **Bridge mode**: forwards messages to the real LG servers while observing — i.e. the
    same idea as our current `mitmdump` rig, but productised. Study this closely.
  - `rethink-setup`: provisions a device without the official app (relevant to M5 — cutting
    the cloud at setup time).
  - Wiki (dynamic, fetch in a browser): `Thinq1CloudProtocol`, `SetupProtocol`,
    `TLVProtocol`, `AABBProtocol`, per-appliance pages incl. washers.
  - Stack is **TypeScript**. We reimplement in Python (see CLAUDE.md decision D-1) — read it
    as a spec, don't fork blindly.

## Protocol references (ThinQ1)

- **`sampsyo/wideq`** — <https://github.com/sampsyo/wideq> — the original reverse-engineered
  ThinQ1 client (Python). Canonical source for the `lgehadm` endpoints, the monitor/poll
  mechanism, `deviceType` enum, and `modelJson` value decoding. ThinQ1 = active polling.
- **`ollo69/ha-smartthinq-sensors`** — <https://github.com/ollo69/ha-smartthinq-sensors> —
  the mature HA custom integration (cloud-based) built on WideQ. Reference for **how to
  model washer/dryer state as HA entities** and for HACS packaging conventions. Note the
  cloud-polling ≥300 s or get-blocked constraint — the very pain our local server removes.
- **`ssut/wideq-js`** — <https://github.com/ssut/wideq-js> — Node port; sometimes clearer
  monitor-loop code.
- **`no2chem/wideq`** fork — ThinQ2 extensions (not our devices, but useful contrast).

## SSL unpinning / capture (already partly done here)

- **`zimmra/frida-rootbypass-and-sslunpinning-lg-thinq`** — Frida scripts to unpin the LG
  app's TLS (for capturing app↔cloud, i.e. the **control** direction we still lack). Tested
  against ThinQ app 4.1.46041. Only needed if we must sniff the app to learn the command
  format for M3.

## Home Assistant integration docs

- Official HA integration developer docs — <https://developers.home-assistant.io/> — config
  flow, entity platforms, `DataUpdateCoordinator`, device registry, `manifest.json`.
- HA MQTT discovery — <https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery> —
  the low-effort integration path if we expose state over MQTT à la `rethink`.

## Our own captures

- [`../capture-ctl`](../capture-ctl) — the capture rig: nft-DNAT on/off toggle (OpenWrt fw4),
  scoped per-appliance on `:46030`. Design: `docs/superpowers/specs/2026-07-18-capture-toggle-design.md`.
- [`../lavatrice_dump.txt`](../lavatrice_dump.txt) — first `mitmdump` capture (boot/idle).
- [`../flows/washer-cycle-20260719.log`](../flows/washer-cycle-20260719.log) — a full wash cycle (2026-07-19).
- [`../lg_portfix.py`](../lg_portfix.py) — mitmproxy addon: upstream port 443→46030 rewrite.
- [`../hosts`](../hosts) / [`../dns_rewrite.txt`](../dns_rewrite.txt) — **abandoned** DNS-diversion
  artifacts (non-functional against the `eic.lgthinq.com` CNAME — see `PROTOCOL.md` §2); kept
  for history only.

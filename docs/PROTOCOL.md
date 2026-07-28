# ThinQ1 Protocol — Observed Facts (living document)

Everything here is derived from real captures in this repo. **Do not treat anything as
final** — update this file whenever a new capture contradicts or extends it. Each claim
should be traceable to a capture file. Speculation is explicitly labelled `(UNCONFIRMED)`.

Source captures: [`../lavatrice_dump.txt`](../lavatrice_dump.txt) — `mitmdump` session,
2026-06-04 (boot/idle); [`../flows/washer-cycle-20260719.log`](../flows/washer-cycle-20260719.log)
— a full wash cycle, 2026-07-19.

---

## 1. Device inventory (the author's — substitute yours, see §6)

| Role | `modelName` | `deviceType` | `deviceId` | LAN IP (observed) |
|------|-------------|--------------|------------|-------------------|
| Washer / lavatrice | `WTWN3` | `201` | `WASHER_DEVICE_ID` | 192.168.20.WASHER |
| Dryer / asciugatrice | `RC90U2_WW` | `202` | `DRYER_DEVICE_ID` | 192.168.20.DRYER |
| Fridge / frigorifero | `1REB1GLPX1___` | `101` | `FRIDGE_DEVICE_ID` | 192.168.20.FRIDGE |

`deviceType` 201 = washer, 202 = dryer (LG ThinQ device-type enum, cf. wideq). Both are
**ThinQ1 (legacy) devices**: XML payloads, `x-lgedm-*` headers, `User-Agent: IOE Client`,
the `lgehadm` API surface. This is the older protocol — *not* the ThinQ2 MQTT/JSON stack.

## 2. Transport

- Devices talk **only outbound** to LG cloud hostnames over TLS. They open no listening
  ports after provisioning (confirmed by the `rethink` project's protocol notes).
- Primary host: `eic.lgthinq.com` (EU/WW entry point — other regions differ, see §6). As of
  2026-07 it is a **CNAME → `eic-lgthinq-com.aws-thinq-prd.net`** (LG moved the legacy API
  behind AWS) resolving to a rotating A record. Other hosts the module resolves:
  `route`/`common`/`noti`/`aic-service`/`aic-common`/`eu` `.lgthinq.com`, `*.lgcloud.com`.
- **The appliance modules do not pin/validate the TLS certificate** — mitmproxy decrypts
  them with its own CA. This is the project's core enabler. ⚠️ Per-device/per-firmware:
  **re-verify for your appliance** — some clients *do* validate (e.g. the Home Assistant
  cloud integration rejects mitm's cert). If your appliance pins, this whole approach fails.
- The ThinQ1 API listens on port **46030**, not 443. `lg_portfix.py` rewrites mitmproxy's
  *upstream* port 443→46030. mitmproxy 12.x also needs `--set ssl_insecure=true` (it verifies
  upstream certs by default; LG's chain isn't always verifiable from mitm's CA bundle).
- **Steering traffic to the MITM box:** **nftables DNAT scoped to each appliance's source IP
  on `:46030`**, plus hairpin masquerade (OpenWrt fw4). See `capture-ctl`,
  [`NETWORK_SETUP.md`](NETWORK_SETUP.md) (the full network/firewall guide, with iptables +
  pfSense translations and troubleshooting), and
  [`superpowers/specs/2026-07-18-capture-toggle-design.md`](superpowers/specs/2026-07-18-capture-toggle-design.md).
  ⚠️ **Do not use DNS diversion** (AdGuard `$dnsrewrite`, dnsmasq `address=`, or zone-wide
  `/etc/hosts` overrides): the CNAME above means AdGuard/dnsmasq rewrites cannot reliably
  override `eic.lgthinq.com` (AdguardTeam/AdGuardHome#3350), and DNS diversion pollutes
  AdGuard's cache in a way that persists after the rig is off and silently breaks appliances.
- The washer additionally uses `eic-dualstack.lgthinq.com` (AWS "dm-web" ELB, IPv6-preferring)
  and a persistent `:47878` channel for online registration/keepalive — separate from the
  `:46030` API. Capturing `:46030` yields the ThinQ1 telemetry; keeping the device's online
  icon lit *while* captured may require intercepting those paths too (open).
- ⚠️ **Not every ThinQ1 appliance is capturable with the current rig** (observed on the fridge,
  2026-07-20). The washer/dryer connect to the *hostname* `eic.lgthinq.com`, so their TLS
  ClientHello carries **SNI** → mitmproxy in regular mode can route/decrypt them. The fridge
  connects to LG **by raw IP** (and LG **rotates** the IPs — seen `68.219.0.211`,
  `52.158.31.24`) with **no SNI**, so regular-mode mitm has nothing to route on and silently
  drops the SYN. The fridge is also **quiet on `:46030` while its `:47878` keepalive is up**;
  it only floods `:46030` (re-registration) when `:47878` is disrupted. A `:47878` reverse-mode
  mitm was tried and did not handshake either. **Capturing such appliances needs a reverse- or
  transparent-mode rig** — see `BACKLOG.md` TASK-062 (open). Re-verify per-appliance: the
  make-or-break is whether the module emits SNI (hostname connect) or not (IP connect).
  **Solved (2026-07-21):** the no-SNI case is captured via **mitmproxy transparent mode** +
  **route-as-next-hop** (NOT DNAT — which destroys the original dst that transparent mode's
  `SO_ORIGINAL_DST` needs). `capture-fridge.sh` policy-routes the appliance's `:46030` to the
  capture host as next-hop (dst preserved); the capture host runs `mitmdump --mode transparent`
  + a local nft REDIRECT. Proven on the live fridge — decrypted `report/diagmon` captured.
- **Idle state changes ride `:47878`, not `:46030`** (observed 2026-07-20, dryer). With the
  appliance idle and its `:47878` keepalive up, a door open/close produced **no** `:46030`
  traffic — the state change went over the persistent `:47878` channel. `:46030` only burst
  when a cycle was running (or when `:47878` was disrupted, per the fridge notes above). So a
  `:46030`-only capture/intercept misses idle state changes; capturing `:47878` (the open
  problem in TASK-062) would be needed for those.

## 3. Endpoints observed (all `POST`, XML request + XML response)

Base path: most endpoints are under `/lgehadm/`; the push-notification endpoint
(`sendPushMessage`) is under `/api/product/`. Common request headers: `x-lgedm-userId: lgehadmUser`,
`x-lgedm-password: <base64ish token>`, `x-lgedm-deviceType`, `x-lgedm-deviceId`,
`Accept: text/xml`, `Content-Type: text/xml;charset=utf-8`.

| Endpoint | Purpose | Request contains | Response contains |
|----------|---------|------------------|-------------------|
| `api/Device/TotalDeviceInfoSvc` | multiplexed device-info service, selected by `<item>` | `countryCode`, `modelName`, `<item>` = `THINQ_TIME_SYNC_URI` **or** `DM_SETTING_INFO_GET_URI` | for time sync: `utcTime`, `timezone`; for settings: `settingInfoList` (Area, BlackBox), `pushDetailSettingList` |
| `api/Rtos/ContentsVerSvc` | firmware/modem version check | `demandType` (`MODEM_3k_SoC`), `modelName`, `countryCode` | `verName`, `downUrl` (OTA URL), `md5` |
| `report/diagmon` | **device → cloud state/telemetry push** | `Content-Type: application/vnd.diagmonlge.dm+xml`; `<Report>` with `devId`, `modelName`, `devType`, `trigger`, `diagMonType`, `diagMonData` (**base64**) | `200`, empty body |
| `api/product/sendPushMessage` | device→cloud push notification (e.g. cycle-complete) | `<lgedmRoot><messageCode>0000</messageCode><langCode>ko</langCode>` | `0000/OK` |
| `api/Grid/PowerSavingInfoSvc` | power-saving info query (dryer bootstrap) | `<countryCode>WW</countryCode>` | `returnCd 0108 / "No Saving Data."` (note: a *non*-`0000` code — the appliance accepts it) |
| `api/Rtos/FWInfoSettingSvc` | device reports its firmware part-numbers/checksums | `<fwInfoList><partNumber>SAA…</partNumber><checkSum>0000a3ed</checkSum></fwInfoList> …` | `0000/OK` |
| `api/Grid/ClosingDoorEventSvc` | fridge door-close event (UNCONFIRMED — seen in the fridge capture) | not yet examined | not yet examined |

All success responses use `<returnCd>0000</returnCd><returnMsg>OK</returnMsg>`.

### 3.1 `diagmon` payloads (the state we care about)

`diagMonData` is base64 → an XML `<lgedmRoot>`. **Inside**, further fields (`monData`,
`diagData`, `option`) are **base64 → binary** — i.e. a double decode. The binary values need
the per-model `modelJson` value map to interpret (M2 / TASK-020). Observed `diagMonType`s:

- **`EventMonitoring`** — **live cycle state.** `eventType` = `WM_STATE` (periodic state
  snapshots, binary `monData`) or `WM_WASH_END` (cycle completion, binary `diagData`
  summary). This is where run-state and phase live.
- **`WasherMonitoring`** — idle: `<tubInfo><event>2</event><count>..</count><maxCount>..`
  (tub-clean counter); during/after a cycle: `<energyMonInfo><course>7</course><power>1</power>
  <energyWater>4</energyWater><useDate>...</useDate>` (energy/water report).
- `ScomoCourse` — `diagMonData = MTAw` → `100` (idle course id).

A complete wash cycle **has been captured** (2026-07-19): see
[`../flows/washer-cycle-20260719.log`](../flows/washer-cycle-20260719.log) and the byte-decode
notes in [`../flows/washer-cycle-20260719.state.md`](../flows/washer-cycle-20260719.state.md).
CONFIRMED `monData` offsets (validated by replay tests): byte 5 = course, byte 18 = cycle-active
(1 active → 2 complete), byte 19 = phase-step. **Full decode achieved**: the WTWN3 `modelJson`
(`server/models/washer_wtwn3.model.json`, fetched via `tools/fetch_model_json.py`) decodes all
22 fields — State (RUNNING/END/POWER_OFF), Course (Mix), Remain_Time, Wash/SpinSpeed/WaterTemp/
RinseOption, Error, PreState, TCLCount. The diagmon `monData` shares the poll-monitor layout.

## 4. Control path — :47878 persistent channel (CAPTURED + DECODED)

**Fully captured 2026-07-21 (fridge, temp-setpoint changes via the LG app).** The `:47878`
channel is **raw TCP with msgpack-length-prefixed JSON messages** (NOT TLS, NOT HTTP — which
is why earlier reverse-mode mitm attempts failed). Captured via the transparent-mode
route-as-next-hop rig on `:47878` (same topology as the `:46030` rig, different port).

**Protocol:** each message is a msgpack-length prefix byte + a JSON object:
```json
{"Header":{"x-lgedm-deviceId":"<uuid>"},"Body":{"CmdWId":"<id>","Cmd":"<command>","CmdOpt":"<opt>","Value":{...},"Data":"<b64>"}}
```

**Cloud → appliance (commands):**
- `"Cmd":"DevInfo"` — on connect; appliance responds with `Data: "FwVer=QC_Modem_1.2.80,regFail=N"`.
- `"Cmd":"Alive"` — keepalive ping; appliance acks `ReturnCode: 0000`.
- `"Cmd":"Mon","CmdOpt":"Start"` — poll state; appliance acks + responds with `Format: B64, Data: <binary state snapshot>`.
- `"Cmd":"Mon","CmdOpt":"Stop"` — stop polling.
- **`"Cmd":"Control","CmdOpt":"Set","Value":{"RETM":"4"}`** — **the actual control command.** Sets the fridge temp to 4°C. The `Value` keys are per-model: `RETM` = fridge temp, `REFT` = freezer temp, `REIP` = IcePlus, `REEF` = EcoFriendly.

**Appliance → cloud (acks + state):**
- `{"Body":{"CmdWId":"<same>","ReturnCode":"0000"}}` — command acknowledgment.
- `{"Body":{"CmdWId":"<same>","ReturnCode":"0000","Format":"B64","Data":"AgQBAf///wAB/wH/AA=="}}` — state snapshot (binary, same struct as the modelJson `monData`). Byte 1 = fridge temp (`0x04` = 4°C).

**Implication for M3 (local control):** the command format is fully known. Local control = our
server maintains the `:47878` persistent channel (it's the appliance's outbound TCP — our
server accepts it) and pushes `Control`/`Set` commands as length-prefixed JSON. No new protocol
to crack — just implement the server-side of this message exchange. Capture:
`flows/fridge-47878-control-20260721.log`.

**Local control via MQTT (TASK-067, 2026-07-24).** Home Assistant → `:47878` is wired: the MQTT
bridge publishes one HA command entity per modelJson `Set` field (the fridge gets 4 selects;
fridge temp / freezer temp / IcePlus / EcoFriendly). Each entity has a `command_topic` at
`homeassistant/<component>/lgthinq_<devId>/<slug>/cmd`; the bridge subscribes to
`homeassistant/+/lgthinq_+/+/cmd`, parses the topic → `(devId, slug)`, looks up the entity, and
calls `control_channel.send_command(devId, value, cmd, cmd_opt)`. The select payload is the
*display label*, translated back to the wire ordinal (freezer `-19` → `{"REFT":"5"}`); unknown
payloads are rejected, not forwarded. Behind `allow_control` (off by default). **Washer/dryer
buttons (OperationStart/PowerOff) are not published yet**; they need `CmdOpt=Operation`/`Power`
whose Value wire format isn't captured, and they're physical-actuation (CLAUDE.md #5); the
plumbing (`send_command` takes cmd/cmd_opt) is ready for when they're approved.

## 5. Minimum "keep-alive" contract (hypothesis for M1)

For the appliance to be happy with no real cloud, the local server almost certainly must at
least answer, with well-formed `0000/OK` XML:

1. `THINQ_TIME_SYNC_URI` (time) — else clock/scheduling breaks.
2. `ContentsVerSvc` — return the *current* `verName` and **no** `downUrl`, so the device
   never attempts an OTA against a server we don't want to run. **(hypothesis — verify.)**
3. `DM_SETTING_INFO_GET_URI` — echo back sane settings.
4. `report/diagmon` — accept and `200`.

Confirm the *actual* minimum set empirically (TASK-006): serve these offline, cut the real
cloud, and watch whether the appliance still operates and reconnects across reboots.

## 6. Adapting to your own appliances

This repo documents *one* setup (EU washer + dryer, OpenWrt). To adapt to yours:

- **Confirm ThinQ1.** This works only for ThinQ1 (legacy: XML / `lgehadm` / `IOE Client`).
  ThinQ2 appliances (JSON/MQTT) are a different protocol — this does not apply.
- **Find your device's identity** — `deviceType` (201 washer, 202 dryer, …), `deviceId`,
  `modelName`, LAN IP: capture once with mitm, or read your router's DHCP leases / the LG app.
- **Check the entry host + CNAME.** `dig eic.lgthinq.com` (or your region's host) — if it's a
  CNAME into `*.aws-thinq-prd.net`, DNS diversion will fail; use firewall DNAT.
- **Verify the API port** (`:46030` here) for your region/device.
- **Verify no cert pinning/validation** for your appliance — the make-or-break premise.
- **Router.** `capture-ctl` uses OpenWrt fw4 nft (`dstnat_lan` / `srcnat_lan`); adapt the DNAT
  + hairpin-masquerade rules to your router's firewall.
- **Decode your model (multi-model support).** The server decodes a device by dispatching on
  its `modelName` (`server/models/registry.py`) and applying that model's `modelJson` when
  cached. To decode your appliance, fetch its modelJson:
  `LG_REFRESH_TOKEN=<tok> python tools/fetch_model_json.py <deviceId>` →
  `data/models/<modelName>.model.json` (token from a `wideq` login, or readable from the
  ollo69 `smartthinq_sensors` HA integration's config if you already run it). A model of a
  **new appliance class** (fridge, AC, … — not a washer/dryer) also needs a decoder module
  under `server/models/` + one registry line; its `diagMonType`/envelope shape must come from
  your own capture (capture-driven — don't assume it matches the washer's `WM_*` family).
- Substitute your values in `.capture.env` (`APPLIANCES`, `TARGET_IP`, ports).

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
| Washer / lavatrice | `WTWN3` | `201` | `d9bf16c0-c7c0-11ea-bec4-0051eda91d3d` | 192.168.20.106 |
| Dryer / asciugatrice | `RC90U2_WW` | `202` | `2ca6ccd0-7c25-11e9-ab15-7440be73f9ad` | 192.168.20.190 |

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
  on `:46030`**, plus hairpin masquerade (OpenWrt fw4). See `capture-ctl` and
  [`superpowers/specs/2026-07-18-capture-toggle-design.md`](superpowers/specs/2026-07-18-capture-toggle-design.md).
  ⚠️ **Do not use DNS diversion** (AdGuard `$dnsrewrite`, dnsmasq `address=`, or zone-wide
  `/etc/hosts` overrides): the CNAME above means AdGuard/dnsmasq rewrites cannot reliably
  override `eic.lgthinq.com` (AdguardTeam/AdGuardHome#3350), and DNS diversion pollutes
  AdGuard's cache in a way that persists after the rig is off and silently breaks appliances.
- The washer additionally uses `eic-dualstack.lgthinq.com` (AWS "dm-web" ELB, IPv6-preferring)
  and a persistent `:47878` channel for online registration/keepalive — separate from the
  `:46030` API. Capturing `:46030` yields the ThinQ1 telemetry; keeping the device's online
  icon lit *while* captured may require intercepting those paths too (open).

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
| `api/product/sendPushMessage` | device→cloud push notification (e.g. cycle-complete) | `<lgedmRoot><messageCode>0000</messageCode>…` | response not examined in captures |

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
CONFIRMED `monData` offsets (validated by a replay test, `tests/test_wtwn3.py`): byte 5 =
course, byte 18 = cycle-active (1 active → 2 complete), byte 19 = phase-step. Remaining bytes
need the per-model `modelJson` value map (M2 / TASK-020).

## 4. Control path — UNKNOWN

No control/command traffic has been captured. For ThinQ1, commands originate from the
app→cloud; how the cloud delivers a command to a push-only module (long-poll, a pending-
command field in a periodic response, a separate session, or a persistent channel) is the
central unknown of Milestone M3. **Do not design the control path until it is captured.**

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
- Substitute your values in `.capture.env` (`APPLIANCES`, `TARGET_IP`, ports).

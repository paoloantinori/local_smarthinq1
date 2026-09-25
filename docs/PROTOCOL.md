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
  `:46030`-only capture/intercept misses idle state changes; those ride `:47878` (capturable
  since TASK-062 was solved; see §4).

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

## 4. Control path, :47878 persistent channel (fridge: CAPTURED + DECODED; WM family: TLS, UNDECODED)

**Scope: everything in this section up to §4.4 is the FRIDGE (REF family).** The
washer/dryer (WM family) `:47878` is a different, TLS-encrypted service (§4.4). Pointing
WM `:47878` at the fridge-style msgpack server caused the 2026-09-25 outage (§4.4).

**Fully captured 2026-07-21 (fridge, temp-setpoint changes via the LG app).** The `:47878`
channel is **raw TCP** (NOT TLS, NOT HTTP, which is why earlier reverse-mode mitm attempts
failed). Captured via the transparent-mode route-as-next-hop rig on `:47878` (same topology
as the `:46030` rig, different port).

**Wire format (corrected 2026-09-24):** each message is **one msgpack `str`** whose payload
is the JSON object below. The msgpack layer is pure length framing; peers parse the JSON
text, not msgpack structures:

    [str prefix][length][UTF-8 JSON bytes]   e.g. d9 c2 7b 22 48 65 … = str8, 194-byte JSON

- Only the str8 form (`0xd9` + 1 length byte) is observed (all captured messages are
  147-222 bytes). Decoders must not assume the prefix: fixstr (`0xa0|n`) and str16
  (`0xda` + 2 length bytes) are valid too.
- Verified against the capture: in all 89 unredacted messages the length byte equals the
  JSON byte count exactly (deviceId = 36-char UUID). The capture logger elided the prefix
  byte, which is why raw dumps read `...<len>{"Header"...`.
- **NOT a msgpack map.** The first server implementation encoded `{"Header":…}` as a msgpack
  map; appliances never answered map-form messages. Fixed 2026-09-24 in
  `server/control_channel.py` (`encode_message`; `decode_messages` accepts both forms).
- Messages arrive concatenated in one TCP segment with no delimiter; the str length header
  is the framing. The appliance may ack one `Control/Set` with several identical
  `ReturnCode` messages (up to 4 identical acks observed in one segment).

```json
{"Header":{"x-lgedm-deviceId":"<uuid>"},"Body":{"CmdWId":"<id>","Cmd":"<command>","CmdOpt":"<opt>","Value":{...},"Data":"<b64>"}}
```

**Cloud → appliance (commands):**
- `"Cmd":"DevInfo"` — on connect; appliance responds with `Data: "FwVer=QC_Modem_1.2.80,regFail=N"`.
- `"Cmd":"Alive"` — keepalive ping; appliance acks `ReturnCode: 0000`.
- `"Cmd":"Mon","CmdOpt":"Start"`: poll state; appliance acks + responds with `Format: B64, Data: <binary state snapshot>`. The cloud re-issues it every poll cycle (not one-shot); each issue yields a fresh ack + snapshot.
- `"Cmd":"Mon","CmdOpt":"Stop"` — stop polling.
- **`"Cmd":"Control","CmdOpt":"Set","Value":{"RETM":"4"}`** — **the actual control command.** Sets the fridge temp to 4°C. The `Value` keys are per-model: `RETM` = fridge temp, `REFT` = freezer temp, `REIP` = IcePlus, `REEF` = EcoFriendly.

**Appliance → cloud (acks + state):**
- `{"Body":{"CmdWId":"<same>","ReturnCode":"0000"}}` — command acknowledgment.
- `{"Body":{"CmdWId":"<same>","ReturnCode":"0000","Format":"B64","Data":"AgQBAf///wAB/wH/AA=="}}` — state snapshot (binary, same struct as the modelJson `monData`). Byte 1 = fridge temp (`0x04` = 4°C).

**Implication for M3 (local control):** the command format is fully known. Local control = our
server maintains the `:47878` persistent channel (it's the appliance's outbound TCP; our
server accepts it) and pushes `Control`/`Set` commands as JSON inside a msgpack string. No new protocol
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

**Read-only monitoring (2026-09-24).** On `DevInfo` the fake cloud now sends `Mon Start`
automatically, matching the real cloud (the capture shows `Mon Start` re-issued throughout
the session), so the appliance pushes periodic `B64` snapshots with no app open and no
`allow_control` needed (`Control/Set` stay gated). Each snapshot is logged decoded-in-hex
as `[control] SNAP <dev> b64len=… hex=…` (first 200 bytes). Purpose: the door-bit hunt.
The decoded `:46030` telemetry has no door field (the washer exposes
state/course/cycle_active/phase_step; the only door-ish modelJson entries are an
`ERROR_DOOR` comment on the dryer and the `DoorLock` command bit on the washer `Option2`
bit 6, neither a telemetry state). An idle door-open bit, if any exists, must ride these
snapshot bytes (cf. §2: idle state changes ride `:47878`).

### 4.4 WM-family `:47878`: TLS + length-prefixed JSON (DECODED 2026-09-25)

**The channel is decoded.** The washer/dryer `:47878` is the fridge's protocol family
(§4.1-4.3) with different framing: **TLS** (no SNI, TLS 1.2; our cert is ACCEPTED, so
no-pinning holds on this channel too) carrying **`[4-byte big-endian length][JSON
{"Header":{"x-lgedm-deviceId":...},"Body":{...}}]`** messages. Observed vocabulary
(cleartext relay corpus, `flows/wm47878-cleartext-redacted-20260925.log`; 502 records):

- `DevInfo` (appliance→LG, on connect): `Data` = `RuleVer=…,FwVer=…,regFail=Y|N`.
- `Alive` (appliance→LG, every 60 s): **bare, no Data** (the keepalive carries no state).
- `Mon Start` (LG→appliance): **the push gate.** The washer's pump started 1.7 s after
  LG's `Mon Start` (18:28:04 → 18:28:06). The afternoon's mysterious push-enable +
  213 B record (see door test 2) is consistent with an on-demand `Mon Start`.
- The pump (appliance→LG, ~0.7-1.5 Hz while gated on): `ReturnCode + Format B64 + Data`;
  `Data` decodes to **the same 28-byte `monData` struct the `:46030` diagmon carries**
  (the modelJson's 22 fields; byte-identical while idle).
- `ReturnCode 0000` acks both ways.

Passive view before decoding (router capture, dryer → `20.105.96.214:47878`, Azure):
the client pushes one 256-byte-payload TLS record (~261 B on wire) every ~1.07 s, idle
included; every 60 s a 192-byte-payload ping/ack pair (the `Alive` and its ack).

**Outage mechanism (2026-09-25; evidence `flows/wm47878-outage-fins-20260925.pcap` +
`flows/wm47878-passive-20260925.pcap`).** With WM
`:47878` diverted to our msgpack server the sockets stayed ESTAB and the parser stayed
mute, but the FIN ack numbers show the appliances had sent 833,831 / 513,196 (three
connections, identical count) / 11,735,421 bytes. The WM keeps writing its per-second
blob into the socket regardless of TLS progress: against the real LG each write is an
app_data record; against a mute non-TLS server they accumulate unparseable (11.7 MB is
about a full night at ~0.8 writes/s). Our reader never logs anything because the first
unparseable frame parks the parser and the buffer grows silently (`decode_messages`
rewinds and waits forever); with the framing now known, the mechanism is exact: the
msgpack reader consumes the `00 00 00 xx` length bytes as fixints, never hits an
unsupported prefix, and simply never advances. Both WMs then boot-looped on `:46030`
re-registration for ~1 h; §2 predicted this for the fridge (disrupting `:47878` triggers
re-registration floods) and it holds for the WM family too.

**Safety rule (standing):** never divert WM (`192.168.20.106` / `.190`) `:47878` to the
addon/msgpack server as it stands: it speaks the fridge framing and would wedge the
module again (see the outage mechanism). TLS termination with our cert is now CONFIRMED
on both channels (46030 and 47878 relay sessions accepted); experiments use the
`.200` transparent rig (`capture-wm47878.sh`) or the deployed relay.

**Handshake + session facts (2026-09-25 passive capture, post-power-cycle;
capture: `flows/wm47878-passive-20260925.pcap`):**

- ClientHello is 178 B, **no SNI**, TLS 1.2 only (no `supported_versions`), 41 classic
  ECDHE/RSA suites, no session resumption (empty session id, no ticket ext). Client
  flights: 178 B ClientHello, then 342 B + 277 B. Identical on washer and dryer.
- LG presents `CN=*.lgthinq.com` (LG Electronics; issuer Thawte TLS RSA CA G1 → DigiCert
  Global Root G2, i.e. a PUBLIC chain, valid 2026-01/2027-02). Extracted cleartext from
  the capture. Endpoints seen: washer `52.158.31.24`, dryer `20.105.96.214` (rotating
  Azure pool, cf. §2).
- **The 1 Hz push is server-gated, and the gate is `Mon Start`** (decoded, see above).
  Three fresh connections (washer 11:48 and 11:58, dryer 11:52 in-place reconnect) all
  ran handshake + 60 s keepalive ONLY: no 261 B push until LG Mon-Starts the device.
  LG's post-handshake opening differs per connection (5×192 B app records to the washer
  at 11:48, a single 192 B to the dryer's reconnect): those early open records were
  pre-`Mon` traffic; what makes LG decide to send `Mon Start` (app presence is the
  leading candidate) remains unconfirmed.
- **Door test (11:58:30-12:01:30, 4 dryer door open/close cycles): ZERO traffic.** No
  47878 anomaly (keepalive cadence unbroken) and no 46030 activity (addon log empty in
  the window). On a push-disabled connection, door events are simply not reported.
- LG kills a superseded session when the client reconnects: FIN+PSH carrying a 53 B
  encrypted record (presumed close_notify), retransmitted with backoff while the client
  ignores it. LG also attempted delivery of a 213 B app record to the powered-off washer
  (3 retransmits, then FIN): server-initiated pushes to the appliance exist and we cannot
  read them passively.
- The washer at boot brings up BOTH channels in the same second (46030 POSTs + 47878 TLS
  handshake at 11:58:08-09 local). Its 46030 ladder hit the addon (bridge mode) with LG
  502ing intermittently; the standalone-fallback answered.

**Door test 2 (2026-09-25 ~14:00, push active at ~1.5 Hz; captures
`flows/wm47878-door-test-1243.pcap` and `flows/wm47878-door-test-1358.pcap`).** Four dryer
door open/close cycles
(user's recollection: ~13:58:45-13:59:45; the LG app was opened ~13:57 but its dryer page
never rendered, and no notification arrived): ZERO anomalies in the client stream (no
size change, no cadence break, no extra records). But the sequence around them: during the
cycles the appliance sent ONLY its regular 60 s keepalives (its sole path to real LG:
`:46030` goes to our addon); ~23 s after the 13:59:27 keepalive, LG enabled the push at
~1.5 Hz (13:59:50) and sent a 213 B server→appliance record (13:59:55), the same size
LG attempted on the washer at 11:52:27. **The then-hypothesis that door events ride
inside the keepalives is REFUTED by the decoding**: `Alive` records are bare (no Data).
The push-enable was LG sending `Mon Start` on demand (what makes LG decide is still
open; app presence remains the leading candidate), and the 213 B record is consistent
with a `Mon Start` carrying the device's full UUID.

Other observations of the day: the appliance rebuilds its TLS session periodically
(4 connections on 2026-09-25: predawn, 11:52, ~12:1x, 12:59), each fresh session starts
keepalive-only; the push, when on, ran at ~0.93 Hz (12:42-12:59 window) and ~1.5 Hz
(13:59:50+, right after the door burst), so the rate may encode active vs background
monitoring.

**Repair window (17:33-17:44, diversion lifted; evidence
`flows/reregistration-repair-20260925.pcap`).** The dryer re-registered GENUINELY against
real LG (clean 6-connection ladder to `52.158.31.24` + edge `40.90.217.74`) after the
12:16 registration had been swallowed by the standalone fallback during an LG outage
(TASK-077's incident: stale cloud record, app could not attach). Two facts for the
future: (1) the washer's `:46030` TLS handshake to real LG includes a **client
certificate** (533 B flight vs the usual 277): the module authenticates with a cert,
so a full 46030 impersonation with real upstream acceptance needs that credential;
(2) LG refused the washer's re-registration at application level and the module
degenerated into a resource spiral (complete handshakes at 17:36 → SYN/SYN-ACK/mute/RST
by 17:38, its `:47878` never came up at that boot): a wedged cloud record can wedge the
appliance itself, and the addon's synthetic 200s keep it functional but cloud-invisible.

**Door bit: CLOSED (verdict, 2026-09-25 18:33-18:38).** With the channel in cleartext
and the pump running (washer, post onboarding), 4 door open/close cycles produced **zero
change** in the 28-byte `monData` (222 pump frames in the window; ONE distinct value
across all 434 frames of the session) and **no event records** (only LG's ReturnCode
acks). Corroborated by the modelJson (no door field among the 22 state fields) and by
the LG app (which never showed a door state for the WM family). **The WM state frame
carries no door bit; if HA needs door state, it must come from an external sensor.**
(The fridge DOES report `DoorOpenState` in its richer COMMON_PERIODIC state, §TASK-050;
this verdict is WM-family only.)

**Next steps on this channel:** a WM-capable local `:47878` server is now designable
(TLS with our cert + `[4B len][JSON]` framing + `DevInfo`/`Alive` acks + `Mon Start` +
`ReturnCode/B64 Data` pump ingest would complete the cloud-free story for the WM family;
backlog). The dryer's own corpus is pending: its app-onboarding stalled at 99% on LG's
502 storm, so LG never Mon-Started it during the session; re-run when LG recovers.

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

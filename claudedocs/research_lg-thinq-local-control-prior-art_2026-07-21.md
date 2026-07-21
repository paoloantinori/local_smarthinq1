# Research Report: LG ThinQ1 Local Cloud Impersonation + :47878 Control Channel Prior Art

**Date**: 2026-07-21
**Depth**: exhaustive
**Confidence**: MEDIUM (the :47878 protocol appears genuinely novel — no prior documentation found; the broader local-impersonation space has a few active projects, but none document the raw-TCP msgpack control channel we captured)

## Executive Summary

No prior art documents the `:47878` persistent raw-TCP msgpack-JSON control channel we captured. Three projects attempt full local cloud impersonation (rethink, lg-local, ponder), but none document or implement the `:47878` command-delivery mechanism — they focus on the ThinQ2 MQTT path or the `:46030` HTTP telemetry. Our capture of the `:47878` `Control`/`Set` command protocol (msgpack-length-prefixed JSON over raw TCP) appears to be the **first public documentation** of how ThinQ1 commands are actually delivered to the appliance.

## Findings

### 1. Projects doing full local cloud impersonation

**anszom/rethink** [1] — the closest to our work. A TypeScript implementation of a "fully-local server for LG ThinQ devices." Supports ACs, fridges, washing machines, water heaters. Has a `rethink-cloud` service that emulates the cloud and translates to MQTT. Documents ThinQ1 + ThinQ2 in its wiki. **But:** the wiki's Communications overview [2] describes the general scheme (app↔cloud, device↔cloud) without mentioning `:47878` specifically. The rethink project appears to handle control via the ThinQ2 MQTT path or the `:46030` HTTP API — not the raw-TCP `:47878` channel. Its wiki documents wifi modules (LCW-007, LCWB-001, LCW-004) but does not describe the `:47878` persistent-channel protocol.

**rvanbaalen/lg-local** [3] — a Node.js/React/TypeScript implementation "inspired by and aiming to serve as a replacement for the rethink project." Features TLV parsing, MQTT integration, setup-protocol handling. **But:** its cloud protocol description mentions "MQTT-based communication with hex-encoded payloads" — this is the ThinQ2 path, not the ThinQ1 `:47878` raw-TCP channel. No mention of `:47878`, msgpack, or the `Control`/`Set` command vocabulary.

**arrudagates/ponder** [4] — "100% local implementation of the LG ThinQ server." 105 stars. **But:** minimal README (20 lines); could not extract protocol details from the scrape. No mention of `:47878` in any search result. The repo likely draws from the same rethink research.

### 2. The :47878 control channel — no prior documentation found

Searched exhaustively for:
- `"47878"` combined with LG ThinQ/SmartThinQ/lgthinq on GitHub → **zero relevant results**
- `"CmdWId"` / `"CmdOpt"` / `"Control"` / `"Set"` / `"Mon"` / `"Alive"` as LG ThinQ protocol terms → **zero results**
- `"eic-dualstack"` / `"47878"` / `"persistent channel"` / `"push channel"` in LG ThinQ context → **zero results**
- LG QC_Modem firmware analysis / TLS no-pinning → **zero relevant results**

**Confidence: HIGH that the `:47878` msgpack-JSON control protocol is undocumented in any public source.** Our capture (`flows/fridge-47878-control-20260721.log`) appears to be the first public record of this protocol.

### 3. The msgpack-length-prefixed JSON framing

The `:47878` protocol uses msgpack length prefixes (single-byte `\x93`/`\x95`/`\xc2` etc.) before JSON payloads. No prior documentation of this specific framing was found in any LG ThinQ context. The Header/Body structure with `CmdWId`/`Cmd`/`CmdOpt`/`Value` fields is not documented in any LG service manual, leak, or reverse-engineering writeup found by search.

### 4. Per-deviceType Value key maps

The fridge's `Control`/`Set` command uses keys like `RETM` (fridge temp), `REFT` (freezer temp), `REIP` (IcePlus), `REEF` (EcoFriendly). No public mapping of these keys per deviceType was found. The rethink wiki documents per-appliance support pages but does not expose the raw command keys.

### 5. Projects using the :46030 HTTP telemetry path

**sampsyo/wideq** [5] — the canonical ThinQ1 Python client. Fetches modelJson, decodes binary state via `Monitoring.protocol`. Does NOT implement local impersonation (it's a cloud-API client). Does NOT document `:47878`.

**ollo69/ha-smartthinq-sensors** [6] — the HA integration we used for modelJson access. Vendored wideq. Cloud-dependent (uses LG's API). Does NOT document `:47878`.

### 6. LG's own local-control efforts

**LG ThinQ Local Controller** [7] — registered with CSA-IOT (Matter). "A software component embedded in the LG Hub product." This is LG's official Matter-based local control — a different path entirely (Matter/Thread, not ThinQ1 raw protocol). Not applicable to ThinQ1 appliances without a hub.

### 7. The IoT Stack Exchange discussion

An IoT Stack Exchange thread [8] asks "Is it possible to use LG's ThinQ without internet access?" — the community answer is "no" (as of the thread date). Our project (and rethink) prove this wrong for ThinQ1 appliances.

## Confidence Assessment

| Finding | Confidence | Evidence |
|---------|------------|----------|
| `:47878` protocol is undocumented publicly | **HIGH** | Exhaustive search across GitHub, Google, wiki pages — zero hits for `47878` + LG ThinQ, zero for the command vocabulary (`CmdWId`/`CmdOpt`/`Mon`/`Alive`) |
| rethink is the most complete prior local-impersonation effort | **HIGH** | 168 stars, active wiki, multiple appliance support, TypeScript cloud emulation |
| No project implements `:47878` local control | **MEDIUM** | Can't rule out undocumented code in rethink/ponder (couldn't read their full source); but no search surface mentions it |
| The msgpack framing is novel documentation | **HIGH** | No search result mentions msgpack in an LG ThinQ context |
| Per-deviceType Value key maps exist nowhere publicly | **HIGH** | No search result exposes RETM/REFT/REIP/REEF keys |

## Sources

1. [anszom/rethink](https://github.com/anszom/rethink) — fully-local server for LG ThinQ devices (TypeScript). 168 stars, active wiki documenting ThinQ1 + ThinQ2.
2. [rethink wiki: Communications overview](https://github.com/anszom/rethink/wiki/Home) — ThinQ1/Thininq2 communications diagram + module docs (LCW-007/LCWB-001/LCW-004). Does NOT document :47878.
3. [rvanbaalen/lg-local](https://github.com/rvanbaalen/lg-local) — Node.js/React local cloud replacement, inspired by rethink. Handles MQTT cloud protocol (ThinQ2 path), not :47878.
4. [arrudagates/ponder](https://github.com/arrudagates/ponder) — "100% local" LG ThinQ server. 105 stars. Minimal docs; protocol details not extractable from search.
5. [sampsyo/wideq](https://github.com/sampsyo/wideq) — the canonical ThinQ1 reverse-engineered Python client. Cloud-API only; no local impersonation; no :47878.
6. [ollo69/ha-smartthinq-sensors](https://github.com/ollo69/ha-smartthinq-sensors) — HA custom integration, vendored wideq, cloud-dependent.
7. [LG ThinQ Local Controller (CSA-IOT)](https://csa-iot.org/csa_product/lg-thinq-local-controller/) — LG's official Matter-based local control for hub products.
8. [IoT Stack Exchange: ThinQ without internet?](https://iot.stackexchange.com/questions/6691/is-it-possible-to-use-lgs-thinq-without-internet-access) — community says "no" (now disproven).

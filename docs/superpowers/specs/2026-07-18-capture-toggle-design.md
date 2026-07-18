# Capture on/off toggle — design (implemented: nftables DNAT)

**Date:** 2026-07-18 · **Task:** TASK-001 · **Status:** Implemented & verified.

## Goal
One command on the capture host `bird` (192.168.20.200) that turns the LG ThinQ1
MITM capture rig on/off/status — diverting the two appliances' LG traffic to
mitmproxy while leaving every other LAN device (notably Home Assistant) untouched.
A true targeted MITM: **no DNS changes, no stored credentials.**

## Why nft DNAT, not DNS diversion (the journey)
- **Original rig (2026-06-04):** AdGuard `$dnsrewrite` DNS diversion. Worked then
  because `eic.lgthinq.com` was a direct A record.
- **LG migrated `eic.lgthinq.com` behind AWS:** it's now a CNAME →
  `eic-lgthinq-com.aws-thinq-prd.net` → rotating A. AdGuard Home rewrites **cannot
  override a CNAME** (AdguardTeam/AdGuardHome#3350), so `$dnsrewrite` silently
  stopped working. Confirmed by deep research + four failed rewrite attempts.
- **dnsmasq `address=` + AdGuard `[/domain/]` route** diverts correctly — but
  **DNS-based diversion pollutes AdGuard's cache**: the cached `.200` *persists*
  after the diversion is removed and the rig is stopped, so appliances keep
  resolving `eic.lgthinq.com → .200`; with mitm off, `.200:46030` refuses and the
  appliance goes offline for hours. This silently broke the washer until AdGuard
  was reloaded (`stop`+`start` flushes the cache; `restart` does NOT).
- **Conclusion:** avoid DNS diversion entirely. Divert at the firewall, per-device.

## Final mechanism: nftables DNAT (scoped) + mitmproxy
- **nft** (`inet fw4`, OpenWrt fw4): in `dstnat_lan`,
  `ip saddr { 192.168.20.106, 192.168.20.190 } tcp dport 46030 dnat to
  192.168.20.200:46030` — only the two appliances' `:46030`.
- **hairpin masquerade** in `srcnat_lan` (same saddr set, daddr `.200`, dport
  `46030`, `masquerade`) so LAN→LAN DNAT replies route back through the router.
- **mitmproxy 12.x** on `bird:46030` with `lg_portfix.py` (rewrites the upstream
  port 443→46030, since LG's ThinQ1 API is on `:46030`) and
  `--set ssl_insecure=true` (LG's upstream cert chain isn't always verifiable by
  mitm's CA bundle; for a capture rig we only need to decrypt, not validate LG).
- mitm 12.x has **no `upstream_dns`/`allow_remote_connections`** options; regular
  mode intercepts the appliances' raw-TLS origin connections via SNI. With no DNS
  diversion, mitm resolves upstream normally (no `/etc/hosts` loop concern).

## Ordering / safety
- **ON:** mitm up + self-test (curl through mitm to LG) → install DNAT → verify
  rules present (else roll back).
- **OFF:** remove DNAT → verify gone (else refuse to stop mitm, to avoid a dead
  `.200`) → stop mitm.

## Files
- `capture-ctl` — the toggle (`on|off|status`), nft-based; runs on `bird`.
- `.capture.env.example` / `.capture.env` (git-ignored) — config; **no creds**.
- `.gitignore` — ignores `data/`, `.capture.env`.
- Existing: `lg_portfix.py`. (`dns_rewrite.txt`, `hosts` are historical, unused by
  the nft mechanism.)

## Verified (2026-07-18)
- **Dryer (`.190`):** captured (`:46030` POSTs decrypted, LG `200`) **and online**
  — proves the rig is a transparent targeted MITM.
- **Home Assistant (`.110`):** zero traffic to mitm — correctly excluded.
- **Washer (`.106`):** `:46030` API traffic captured; see caveat.

## Washer caveat + full-MITM (open)
The washer also uses **`eic-dualstack.lgthinq.com`** (AWS "dm-web" ELB,
IPv6-preferring) for online registration/presence — a *different* host from
`eic.lgthinq.com:46030` (the ThinQ1 API we capture). The current rig intercepts
only `:46030`. Whether that alone keeps the washer online was never cleanly tested
(the AdGuard cache bug masked it). Full washer MITM = also intercept
`eic-dualstack` (`:443`, IPv4+IPv6); see the full-MITM analysis.

## Out of scope
TASK-002 (full cycle capture), M1 fake-cloud, M2 decoding, M4 HA integration.

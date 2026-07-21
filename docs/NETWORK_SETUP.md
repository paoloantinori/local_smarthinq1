# Network setup: steering appliance traffic to your MITM box

This is the part that's specific to *your* network. The repo's `capture-ctl` ships a working
implementation for **OpenWrt fw4**, but the mechanism is portable: it's two firewall rules
plus a TLS-terminating proxy. This doc explains what those rules do, why, and how to adapt
them to other routers — so you're not reverse-engineering it from the script.

**TL;DR for the impatient:** you need (1) a capture host on your LAN running mitmproxy on
`:46030`, (2) a router you control that can DNAT, and (3) two nft/iptables rules: one to
redirect each appliance's outbound `:46030` to the capture host, and one hairpin-masquerade
rule so the reply finds its way back. Do **not** try to do this with DNS — see "Why not DNS?"
below.

---

## The three prerequisites

1. **A capture host on the same LAN as the appliances**, at a stable IP (e.g. `192.168.20.CAPTURE`).
   It runs `mitmdump` (mitmproxy 12.x) listening on `:46030` with:
   - `--set ssl_insecure=true` — LG's upstream cert chain isn't always verifiable from mitm's
     CA bundle; for a *capture* rig you only need to decrypt, not validate LG.
   - `-s lg_portfix.py` — rewrites mitm's *upstream* port `443→46030` (ThinQ1 speaks `:46030`,
     not `:443`; see `docs/PROTOCOL.md` §2).
   - No client needs to trust mitm's CA: ThinQ1 modules **do not pin/validate** the cert
     (re-verify for your device — it's the make-or-break premise).
2. **A router you control**, reachable from the capture host over passwordless ssh if you want
   to use `capture-ctl` as-is (it sshes to the router to install/remove the rules). For other
   routers, you install the two rules manually (below).
3. **The appliances' LAN IPs.** Find them from your router's DHCP leases or the LG app. The
   diversion is scoped to those source IPs *only* — everything else on the LAN (Home
   Assistant, phones, laptops) is untouched. This is a targeted MITM, not a zone-wide hijack.

---

## What the two rules do (and why you need both)

ThinQ1 appliances open an outbound TLS connection to `eic.lgthinq.com:46030`. You redirect
that connection to your capture host. Two rules are required:

**Rule 1 — DNAT (destination NAT, in prerouting):** match traffic *from the appliance IP*,
*destination port 46030*, and rewrite the destination to `capture_host:46030`.

**Rule 2 — hairpin masquerade (source NAT, in postrouting):** match traffic *from the
appliance IP* going *to the capture host:46030*, and masquerade (SNAT) it.

**Why rule 2 is mandatory:** the appliance and the capture host are on the *same LAN*. After
DNAT, the packet reaches the capture host with the appliance's original source IP. The
capture host replies directly to the appliance (same-LAN, no router needed) — but the
appliance sent that packet to `eic.lgthinq.com`, not to the capture host, so it drops the
unsolicited-looking reply. Masquerading makes the reply come back *through the router* (which
conntrack then un-DNATs back to the appliance's expected `eic.lgthinq.com` source). **If you
skip rule 2, you get silent one-way failure: the appliance connects, mitm sees nothing, and
the capture log stays empty.**

This exact symptom is also caused by a different problem — see FAQ "mitm shows nothing / SYN
UNREPLIED" below.

---

## Worked example: OpenWrt fw4 (what `capture-ctl` does)

These are the literal rules `capture-ctl` installs (see the script's `nft_set` function).
Assume capture host `192.168.20.CAPTURE`, appliance `192.168.20.WASHER`:

```bash
# Rule 1 — DNAT: redirect the appliance's outbound :46030 to the capture host
nft add rule inet fw4 dstnat_lan \
  ip saddr 192.168.20.WASHER tcp dport 46030 \
  dnat to 192.168.20.CAPTURE:46030 comment '"lg-mitm"'

# Rule 2 — hairpin masquerade: make the reply route back through the router
nft add rule inet fw4 srcnat_lan \
  ip saddr 192.168.20.WASHER ip daddr 192.168.20.CAPTURE tcp dport 46030 \
  masquerade comment '"lg-mitm"'
```

To tear down, delete every rule tagged `lg-mitm` from both chains (by handle):

```bash
for c in dstnat_lan srcnat_lan; do
  nft -a list chain inet fw4 $c | grep 'lg-mitm' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 \
    | while read h; do nft delete rule inet fw4 $c handle $h; done
done
```

`capture-ctl on|off|status` does exactly this (plus managing mitmproxy) over ssh — use it if
you're on OpenWrt fw4. The `comment 'lg-mitm'` tag is how it finds its own rules to remove
them; keep a consistent tag if you write your own.

> **Gotcha (cost us an hour):** if you add ad-hoc per-port rules, don't tag them with a name
> that's a *substring* of `capture-ctl`'s tag (e.g. `lg-mitm-47878`). `capture-ctl` greps for
> `lg-mitm` to detect its rules, so a substring-tagged rule makes it think its DNAT is
> "already installed" and skip adding it. Use a non-overlapping tag.

---

## Adapting to other routers

The two-rule shape is identical everywhere; only the syntax/chain names change.

### iptables / iptables-nft (generic Linux)

```bash
IPT=iptables        # or iptables-nft; same syntax
APPLIANCE=192.168.20.WASHER
CAPTURE=192.168.20.CAPTURE

# Rule 1 — DNAT (prerouting)
$IPT -t nat -A PREROUTING -s $APPLIANCE -p tcp --dport 46030 \
  -j DNAT --to-destination $CAPTURE:46030

# Rule 2 — hairpin masquerade (postrouting)
$IPT -t nat -A POSTROUTING -s $APPLIANCE -d $CAPTURE -p tcp --dport 46030 -j MASQUERADE

# Allow forwarded traffic if your FORWARD policy is DROP:
$IPT -A FORWARD -s $APPLIANCE -d $CAPTURE -p tcp --dport 46030 -j ACCEPT
$IPT -A FORWARD -s $CAPTURE  -d $APPLIANCE -p tcp --sport 46030 -j ACCEPT
```

Tear down with `-D` instead of `-A` for each rule.

### pfSense / opnsense (FreeBSD pf)

Use a **port forward** (NAT → Port Forward) + a matching **Outbound NAT** rule (manual
outbound NAT, hybrid mode):

- **Port Forward:** interface LAN, protocol TCP, source = the appliance IP (alias), dest =
  any, dest port = 46030, redirect to = capture host :46030.
- **Outbound NAT (hairpin):** interface LAN, source = appliance IP, dest = capture host,
  port 46060 → 46030, translation = interface address (this is the masquerade).
- Add a LAN firewall rule allowing TCP appliance→capture-host:46030 if default-deny.

### Other / hosted routers

Translate the same two rules: **(1)** DNAT appliance-source `:46030` → capture-host `:46030`;
**(2)** SNAT/masquerade appliance→capture-host `:46030`. There is no third rule and no DNS
step.

---

## Why not DNS?

It's the obvious first idea — override `eic.lgthinq.com` to point at your capture host. It
**does not work** and we tried hard:

- `eic.lgthinq.com` is a **CNAME → `eic-lgthinq-com.aws-thinq-prd.net`** (LG moved the legacy
  API behind AWS). AdGuard Home and dnsmasq **cannot reliably override a domain that is a
  CNAME** (AdguardTeam/AdGuardHome#3350) — they return the upstream CNAME chain and its A
  record instead of your local address.
- Worse: DNS diversion **pollutes AdGuard's cache** in a way that *persists after the rig is
  off* and silently breaks appliances (they stay "offline"). `restart` doesn't clear it; you
  have to `stop` + `start` AdGuard.

Use firewall DNAT — it works at L3/L4, ignores DNS entirely, and is scoped to the appliance
IPs so nothing else on the LAN is affected.

---

## Verify it works

1. **Self-test the proxy** (independent of any appliance): from the capture host,
   ```bash
   curl --http1.1 -k -sS -o /dev/null -w '%{http_code}\n' \
     --resolve eic.lgthinq.com:46030:127.0.0.1 https://eic.lgthinq.com:46030/
   ```
   should print an HTTP code (not `000`). `capture-ctl on` does this automatically.
2. **Confirm the appliance's traffic arrives:** with the rig on, `tail -f data/mitm.log` —
   you should see `POST https://eic.lgthinq.com:46030/lgehadm/report/diagmon` lines when the
   appliance reports. (ThinQ1 appliances burst on `:46030` periodically or on state changes;
   see `docs/PROTOCOL.md` §2 for the cadence and the SNI caveat.)

---

## FAQ

**mitm shows nothing / the appliance's SYN goes UNREPLIED.** Two common causes:
- *Missing hairpin masquerade* (rule 2) — the appliance connects, mitm sees nothing. Add the
  masquerade rule.
- *The appliance connects by raw IP with no SNI* (not a hostname). mitmproxy in regular mode
  routes by SNI; with no SNI it silently drops the SYN. The washer/dryer are fine (they
  connect to the hostname `eic.lgthinq.com`); a fridge/AC may connect by IP. Diagnose on the
  router with `conntrack -L | grep <appliance-ip>`: if the reply direction shows your capture
  host's IP with `UNREPLIED`, it's the no-SNI case (open problem — see `docs/BACKLOG.md`
  TASK-062). If conntrack shows the appliance talking straight to a public LG IP (reply from
  your WAN IP), the DNAT isn't matching your appliance IP.

**Do I need to install mitm's CA on the appliance?** No — ThinQ1 modules don't validate the
cert (the whole premise). You only need the CA/cert for the *fake-cloud server* (`gen-cert.sh`)
if you move to standalone mode. For capture, mitm's default CA is fine.

**Does capturing disrupt the appliance / my LAN?** Capturing is read-only (decrypt + observe,
never commands). It's scoped to the configured appliance IPs; other LAN devices are untouched.
The appliance's cloud path is briefly redirected during capture; `capture-ctl off` restores
direct-to-LG. Some appliances also hold a persistent `:47878` keepalive channel separate from
the `:46030` API — capturing `:46030` gets the telemetry but the device's "online" icon may
flicker; that's expected and harmless.

**My appliance isn't on OpenWrt fw4 / I don't run OpenWrt.** Then you can't use `capture-ctl`
as-is (it targets fw4's `dstnat_lan`/`srcnat_lan` chains and sshes to the router). Install the
two rules manually for your router (see "Adapting to other routers"), and run mitmproxy on
the capture host directly:
```bash
mitmdump --listen-host 0.0.0.0 --listen-port 46030 -s lg_portfix.py --set ssl_insecure=true
```

**The appliance's `:46030` port / entry host differs.** `:46030` is the EU/WW ThinQ1 port
observed here; other regions may differ. Check `docs/PROTOCOL.md` §6 and `dig` your region's
`eic.lgthinq.com`. The DNAT rule's `tcp dport` and the `MITM_PORT` in `.capture.env` are the
knobs.

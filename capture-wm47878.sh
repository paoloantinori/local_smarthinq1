#!/usr/bin/env bash
# capture-wm47878.sh: route a WM appliance's :47878 (TLS, no SNI; PROTOCOL.md §4.4) to the
# .200 transparent TLS relay (tools/wm47878_tls_relay.py). Same topology as capture-fridge.sh
# (fwmark + policy route, dst preserved; NOT dnat). Mark/table/tag are distinct from every
# other rig script: capture-fridge.sh uses 0xf2/42, fridge-47878-capture-setup.sh 0xf3/43,
# this one 0xf4/44 (a shared mark/table would let one rig's `off` flush another's route).
#
# SAFETY (2026-09-25 outage, §4.4): this rig TERMINATES TLS and relays to the real LG. It is
# the ONLY sanctioned way to touch WM :47878. Never point WM :47878 at the addon/msgpack
# server. Route changes on the router go through the hassio session (one diversion at a
# time). During a window, avoid sending commands from the LG app: they pass through the
# relay untouched, but keep the capture clean.
#
# TOPOLOGY:
#   router (fw4): mark saddr <WM_IP> dport 47878 → table 44 default via .200 (dst preserved)
#   .200 (root): nft REDIRECT inbound :47878 from <WM_IP> → local :47878 where the relay
#         listens; SO_ORIGINAL_DST recovers the real LG IP for the upstream leg.
#
# USAGE:
#   ./capture-wm47878.sh on      # router side (this script). Prints the .200-side commands.
#   ./capture-wm47878.sh off
#   ./capture-wm47878.sh status
# Env: ROUTER_SSH (firewall), WM_IP (default 192.168.20.190, the dryer: it self-heals
#      fastest), MITM_HOST (192.168.20.200).
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
WM_IP="${WM_IP:-192.168.20.190}"
MITM_HOST="${MITM_HOST:-192.168.20.200}"
PORT="${PORT:-47878}"
TAG="lg-wm47878-route"
MARK="0xf4"
RT_TABLE="44"
NFT_TABLE="lgwm47878"   # the .200-side REDIRECT table (setup/teardown commands below)

ts(){ date +%H:%M:%S; }
log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(ts)" "$*"; }
die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(ts)" "$*" >&2; exit 1; }
ssh_r(){ ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

router_has(){ ssh_r "nft list chain inet fw4 mangle_prerouting 2>/dev/null | grep -q 'comment \"$TAG\"'"; }

cmd_on(){
  log "ON: routing WM $WM_IP :$PORT → next-hop $MITM_HOST (dst preserved)"
  ssh_r "nft list chain inet fw4 mangle_prerouting >/dev/null 2>&1 || nft add chain inet fw4 mangle_prerouting '{ type filter hook prerouting priority mangle; }'" \
    || die "could not ensure mangle_prerouting chain"
  if router_has; then log "route rule already installed"; else
    ssh_r "nft add rule inet fw4 mangle_prerouting ip saddr $WM_IP tcp dport $PORT mark set $MARK comment '$TAG'" \
      || die "failed to add mangle mark rule"
  fi
  ssh_r "ip rule list | grep -q 'fwmark $MARK lookup $RT_TABLE' || ip rule add fwmark $MARK lookup $RT_TABLE" \
    || die "failed to add ip rule"
  ssh_r "ip route show table $RT_TABLE | grep -qF 'default via $MITM_HOST' || ip route replace default via $MITM_HOST table $RT_TABLE" \
    || die "failed to add policy route"
  log "router route installed. Verify: ssh $ROUTER_SSH 'ip rule; ip route show table $RT_TABLE'"
  cat <<EOF

────────────────────────────────────────────────────────────────────────
NEXT (on the capture host $MITM_HOST, as ROOT):

  # 1. Redirect the appliance's inbound :$PORT to the relay listener:
  nft add table inet $NFT_TABLE 2>/dev/null
  nft 'add chain inet $NFT_TABLE pre { type nat hook prerouting priority -100; }' 2>/dev/null
  nft add rule inet $NFT_TABLE pre ip saddr $WM_IP tcp dport $PORT redirect to :$PORT

  # 2. Start the TLS relay (repo root on $MITM_HOST; logs cleartext both directions):
  python3 tools/wm47878_tls_relay.py --cert data/cert.pem --key data/key.pem \\
      --log-file data/wm47878-relay-\$(date +%Y%m%d%H%M).log

  # 3. Flush the appliance's conntrack at the router so it reconnects through the rig:
  ssh $ROUTER_SSH 'conntrack -D -s $WM_IP; conntrack -D -d $WM_IP'

  # 4. Power-cycle the appliance (or wait for its next reconnect) and watch the relay log:
  #    handshake → LG's opening records (U->C) → keepalives every 60 s → hopefully the
  #    1 Hz push (C->U). If the client rejects our cert you will see the TLS handshake
  #    fail and the appliance re-registering on :46030: run `off` IMMEDIATELY then.

When done: Ctrl-C the relay, then on $MITM_HOST as root:
  nft delete table inet $NFT_TABLE
and:
  ./capture-wm47878.sh off
────────────────────────────────────────────────────────────────────────
EOF
}

cmd_off(){
  log "OFF: removing WM route"
  ssh_r "nft -a list chain inet fw4 mangle_prerouting 2>/dev/null | grep 'comment \"$TAG\"' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 mangle_prerouting handle \$h; done" \
    || log "WARN: could not remove mangle rule (already gone?)"
  ssh_r "ip rule del fwmark $MARK lookup $RT_TABLE 2>/dev/null; ip route flush table $RT_TABLE 2>/dev/null; true"
  log "router route removed. On $MITM_HOST as root:  nft delete table inet $NFT_TABLE"
}

cmd_status(){
  echo "== WM :47878 TLS-relay rig =="
  printf 'router route  : %s\n' "$(if router_has; then echo "ON ($WM_IP :$PORT → next-hop $MITM_HOST)"; else echo off; fi)"
  ssh_r "ip rule list 2>/dev/null | grep 'fwmark $MARK' || echo '(no ip-rule)'; ip route show table $RT_TABLE 2>/dev/null | grep . || echo '(no route table $RT_TABLE)'"
}

usage(){ cat <<EOF
usage: ./capture-wm47878.sh {on|off|status}
Env: ROUTER_SSH ($ROUTER_SSH), WM_IP ($WM_IP), MITM_HOST ($MITM_HOST), PORT ($PORT).
See PROTOCOL.md §4.4 for the channel and the 2026-09-25 safety rule.
EOF
}

case "${1:-}" in
  on) cmd_on ;; off) cmd_off ;; status) cmd_status ;; -h|--help|help) usage ;; *) usage; exit 1 ;; esac

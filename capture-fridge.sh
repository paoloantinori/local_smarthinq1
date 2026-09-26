#!/usr/bin/env bash
# capture-fridge.sh: capture the fridge (no-SNI / IP-connecting ThinQ1 client) via mitmproxy
# TRANSPARENT mode. Designed for TASK-062.
#
# WHY THIS EXISTS (see docs/BACKLOG.md TASK-062 + PROTOCOL.md §2):
#   The fridge connects to LG by raw IP (rotating: 68.219.0.211, 52.158.31.24, ...) with NO
#   SNI. capture-ctl's regular-mode mitm routes by SNI → drops the fridge's TLS silently.
#   mitmproxy transparent mode recovers the real dst via SO_ORIGINAL_DST, so no-SNI is fine
#   and LG's rotating IPs need no hardcoded upstream — BUT (per mitmproxy docs) "NAT must not
#   be applied before the traffic reaches mitmproxy". So we do NOT DNAT on the router; we
#   ROUTE the fridge's :46030 to .200 as next-hop (dst IP intact), then REDIRECT on .200 to
#   mitm's transparent listener.
#
# TOPOLOGY:
#   router (fw4): route fridge .182 :46030 → next-hop .200 (NOT dnat; dst preserved)
#   .200 (this box, needs root for the local nft redirect): REDIRECT inbound :46030 from .182
#         → mitm transparent on :46030; SO_ORIGINAL_DST recovers the real LG IP.
#
# USAGE:
#   ./capture-fridge.sh on      # router side (this script). Prints the .200-side commands.
#   ./capture-fridge.sh off     # tear down the router side.
#   ./capture-fridge.sh status
#
#   Then on .200 as root, run the redirect printed by `on` (and the `off` redirect-removal
#   printed by `off`), and start mitm transparent:
#     mitmdump --mode transparent --listen-host 0.0.0.0 --listen-port 46030 \
#       --set ssl_insecure=true --set flow_detail=2
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.FRIDGE}"   # the no-SNI appliance
MITM_HOST="${MITM_HOST:-192.168.20.CAPTURE}"   # this box
PORT="${PORT:-46030}"
TAG="lg-fridge-route"                      # fw4 comment tag (non-overlapping with capture-ctl's)

ts(){ date +%H:%M:%S; }
log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(ts)" "$*"; }
die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(ts)" "$*" >&2; exit 1; }
ssh_r(){ ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }
source "$(dirname "$0")/capture-router-lib.sh"

# --- router side: route the fridge's :46030 to .200 as next-hop (dst preserved, NOT dnat) ---
# We use fw4's dstnat_lan? No — routing, not NAT. OpenWrt fw4 exposes a `mangle_prerouting`
# / we add an ip-rule + ip-route, OR the simplest portable form: a fw4 rule in the forward
# hook that sets the next-hop. The cleanest OpenWrt idiom that preserves dst is an nft
# `route` chain (output) — but for LAN-forwarded traffic we need a policy route. Use:
#   ip route add <fridge-dst-via-mitm>  — but dst is any LG IP. So: route the fridge's
#   :46030 flows via a fw4 `mangle_prerouting` mark + ip-rule. That's heavy.
#
# SIMPLEST RELIABLE APPROACH that preserves the original dst IP: make .200 the fridge's
# gateway for :46030 only, via nft `dup` (duplicate-to) or `redirect` at the router is NAT.
# After research: the robust, dst-preserving method on OpenWrt fw4 is an nft `route` chain
# (type route, hook output) is for locally-generated; for forwarded we use policy routing:
#   1. nft rule in fw4 mangle_prerouting: meta mark 0xf2 for (saddr fridge, dport 46030)
#   2. ip rule: fwmark 0xf2 lookup 42
#   3. ip route add table 42 default via 192.168.20.CAPTURE
# This forwards the fridge's :46030 to .200 as next-hop WITH the original dst (LG IP) intact.
MARK="0xf2"
RT_TABLE="42"

cmd_on(){
  log "ON: routing fridge $FRIDGE_IP :$PORT → next-hop $MITM_HOST (dst preserved)"
  router_add_mark_rule "$FRIDGE_IP" "$PORT" "$MARK" "$TAG" \
    || die "failed to add mangle mark rule"
  router_rule_installed "$TAG" && log "route rule already installed"
  router_ensure_ip_rule "$MARK" "$RT_TABLE" \
    || die "failed to add ip rule"
  router_ensure_policy_route "$RT_TABLE" "$MITM_HOST" \
    || die "failed to add policy route"
  log "router route installed. Verify with: ssh $ROUTER_SSH 'ip rule; ip route show table $RT_TABLE'"
  cat <<EOF

────────────────────────────────────────────────────────────────────────
NEXT (run on the capture host $MITM_HOST as ROOT):

  # 1. Redirect the fridge's inbound :46030 to mitm's transparent listener:
  nft add table inet lgfridge 2>/dev/null
  nft 'add chain inet lgfridge pre { type nat hook prerouting priority -100; }' 2>/dev/null
  nft add rule inet lgfridge pre ip saddr $FRIDGE_IP tcp dport $PORT redirect to :$PORT

  # 2. Start mitmproxy in transparent mode (SO_ORIGINAL_DST recovers the real LG IP):
  mitmdump --mode transparent --listen-host 0.0.0.0 --listen-port $PORT \\
    --set ssl_insecure=true --set flow_detail=2

  # 3. Flush the fridge's conntrack at the router so it reconnects through the new path:
  ssh $ROUTER_SSH 'conntrack -D -s $FRIDGE_IP; conntrack -D -d $FRIDGE_IP'

Then provoke a fridge event (open/close the door). A decrypted POST .../report/diagmon
should appear in mitm's output. When done, Ctrl-C mitm and run:
  ./capture-fridge.sh off
and on $MITM_HOST as root:
  nft delete table inet lgfridge
────────────────────────────────────────────────────────────────────────
EOF
}

cmd_off(){
  log "OFF: removing fridge route"
  router_route_off "$MARK" "$RT_TABLE" "$TAG"
  log "router route removed. On $MITM_HOST as root:  nft delete table inet lgfridge"
  cat <<EOF

On the capture host $MITM_HOST as ROOT:
  nft delete table inet lgfridge
EOF
}

cmd_status(){
  echo "== fridge capture rig (transparent) =="
  printf 'router route  : %s\n' "$(if router_rule_installed "$TAG"; then echo "ON (fridge $FRIDGE_IP :$PORT → next-hop $MITM_HOST)"; else echo off; fi)"
  ssh_r "ip rule list 2>/dev/null | grep 'fwmark $MARK' || echo '(no ip-rule)'; ip route show table $RT_TABLE 2>/dev/null | grep . || echo '(no route table $RT_TABLE)'"
}

usage(){ cat <<EOF
usage: ./capture-fridge.sh {on|off|status}
  on     route the fridge's :$PORT to $MITM_HOST as next-hop (dst preserved), print the
         $MITM_HOST-side commands (run those as root), then start mitm transparent.
  off    remove the router route; print the $MITM_HOST-side teardown.
  status show the router-side state.
Env: ROUTER_SSH ($ROUTER_SSH), FRIDGE_IP ($FRIDGE_IP), MITM_HOST ($MITM_HOST), PORT ($PORT).
EOF
}

case "${1:-}" in
  on) cmd_on ;; off) cmd_off ;; status) cmd_status ;; -h|--help|help) usage ;; *) usage; exit 1 ;; esac

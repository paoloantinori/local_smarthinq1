#!/usr/bin/env bash
# fridge-47878-capture-teardown.sh — reverse fridge-47878-capture-setup.sh.
# RUN AS ROOT on the capture host (192.168.20.CAPTURE).
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.FRIDGE}"
PORT="${PORT:-47878}"
TAG="lg-fridge-47878"
MARK="0xf3"
RT_TABLE="43"

cd "$(dirname "$0")"
log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
_REAL_USER="${SUDO_USER:-${USER:-your-username}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

# stop mitm
if [ -f data/fridge-47878-mitm.pid ]; then
  p="$(cat data/fridge-47878-mitm.pid 2>/dev/null)"
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then log "stopping mitm (pid $p)"; kill "$p" 2>/dev/null; sleep 1; kill -9 "$p" 2>/dev/null; fi
  rm -f data/fridge-47878-mitm.pid
fi

# .200 redirect table
log "removing local nft redirect"
nft delete table inet lg47878 2>/dev/null && log "  lg47878 table removed" || log "  (already gone)"

# router: remove policy route + mangle mark
log "router: removing :47878 policy route + mangle mark"
ssh_r "nft -a list chain inet fw4 mangle_prerouting 2>/dev/null | grep 'comment \"$TAG\"' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 mangle_prerouting handle \"\$h\"; done"
ssh_r "ip rule del fwmark $MARK lookup $RT_TABLE 2>/dev/null; ip route flush table $RT_TABLE 2>/dev/null; true"

# flush fridge conntrack (reconnect direct to LG)
log "flushing $FRIDGE_IP conntrack (restoring direct-to-LG)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "CAPTURE RIG TORN DOWN. Verifying fridge recovers…"
sleep 5
direct=$(ssh_r "conntrack -L 2>/dev/null | grep '$FRIDGE_IP' | grep -E 'dport=(47878|46030)' | grep -v 'dst=$FRIDGE_IP' | head -1")
if [ -n "$direct" ]; then log "OK: fridge reconnected direct to LG: $direct"
else log "WARN: no direct conntrack yet (may take a moment). Re-check: ssh $ROUTER_SSH 'conntrack -L | grep $FRIDGE_IP'"
fi

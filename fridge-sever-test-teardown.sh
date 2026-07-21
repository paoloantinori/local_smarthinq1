#!/usr/bin/env bash
# fridge-sever-test-teardown.sh — reverse fridge-sever-test-setup.sh (TASK-050).
# RUN AS ROOT on the capture host (192.168.20.200).
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.182}"
TAG="lg-fridge-route"
BLOCK_TAG="lg-fridge-block"
MARK="0xf2"
RT_TABLE="42"

cd "$(dirname "$0")"
log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
_REAL_USER="${SUDO_USER:-${USER:-pantinor}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

# stop server
if [ -f data/sever-test.pid ]; then
  p="$(cat data/sever-test.pid 2>/dev/null)"
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then log "stopping server (pid $p)"; kill "$p" 2>/dev/null; sleep 1; kill -9 "$p" 2>/dev/null; fi
  rm -f data/sever-test.pid
fi

# .200 redirect table
log "removing local nft redirect"
nft delete table inet lgsever 2>/dev/null && log "  lgsever table removed" || log "  (already gone)"

# router: remove block + policy-route
log "router: removing :47878 block + policy route"
for tag_chain in "$BLOCK_TAG:forward" "$TAG:mangle_prerouting"; do
  tag="${tag_chain%%:*}"; chain="${tag_chain##*:}"
  ssh_r "nft -a list chain inet fw4 $chain 2>/dev/null | grep 'comment \"$tag\"' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 $chain handle \"\$h\"; done"
done
ssh_r "ip rule del fwmark $MARK lookup $RT_TABLE 2>/dev/null; ip route flush table $RT_TABLE 2>/dev/null; true"

# flush fridge conntrack (reconnect direct to LG)
log "flushing $FRIDGE_IP conntrack (restoring direct-to-LG)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "SEVER TEST TORN DOWN. Verifying fridge recovers…"
sleep 5
direct=$(ssh_r "conntrack -L 2>/dev/null | grep '$FRIDGE_IP' | grep -E 'dport=(47878|46030)' | grep -v 'dst=$FRIDGE_IP' | head -1")
if [ -n "$direct" ]; then log "OK: fridge reconnected direct to LG: $direct"
else log "WARN: no direct fridge→LG conntrack yet (may take a moment). Re-check: ssh $ROUTER_SSH 'conntrack -L | grep $FRIDGE_IP'"
fi

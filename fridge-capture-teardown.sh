#!/usr/bin/env bash
# fridge-capture-teardown.sh — reverse fridge-capture-setup.sh (TASK-062).
# RUN AS ROOT on the capture host (192.168.20.200). Removes BOTH sides and restores the
# fridge's direct-to-LG path. Safe to re-run.
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.182}"
PORT="${PORT:-46030}"
TAG="lg-fridge-route"
MARK="0xf2"
RT_TABLE="42"

cd "$(dirname "$0")"

log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
_REAL_USER="${SUDO_USER:-${USER:-pantinor}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

# ── stop mitm (if running) ────────────────────────────────────────────────────────────────
if [ -f data/fridge-mitm.pid ]; then
  p="$(cat data/fridge-mitm.pid 2>/dev/null)"
  if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then log "stopping mitm (pid $p)"; kill "$p" 2>/dev/null; sleep 1; kill -9 "$p" 2>/dev/null; fi
  rm -f data/fridge-mitm.pid
fi

# ── this host: remove the local REDIRECT table ─────────────────────────────────────────────
log "this host: removing local nft redirect table"
nft delete table inet lgfridge 2>/dev/null && log "  lgfridge table removed" || log "  (lgfridge table already gone)"

# ── router side: remove the policy route + ip rule + mangle mark rule ──────────────────────
log "router: removing policy route + ip rule + mangle mark"
ssh_r "nft -a list chain inet fw4 mangle_prerouting 2>/dev/null | grep 'comment \"$TAG\"' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 mangle_prerouting handle \"\$h\"; done" \
  && log "  mangle mark rule removed" || log "  (mangle rule already gone)"
ssh_r "ip rule del fwmark $MARK lookup $RT_TABLE 2>/dev/null; ip route flush table $RT_TABLE 2>/dev/null; true"

# ── flush the fridge's conntrack so it reconnects direct to LG ─────────────────────────────
log "flushing $FRIDGE_IP conntrack (restores direct-to-LG path)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "capture rig is DOWN. Verifying the fridge reaches LG directly…"
sleep 4
direct=$(ssh_r "conntrack -L 2>/dev/null | grep '$FRIDGE_IP' | grep -E 'dport=(47878|46030)' | grep -v 'dst=$FRIDGE_IP' | head -1")
if [ -n "$direct" ]; then
  log "OK: fridge has a direct connection to LG: $direct"
else
  log "WARN: no direct fridge→LG conntrack entry yet (may take a moment to reconnect). Re-check with:"
  log "  ssh $ROUTER_SSH 'conntrack -L | grep $FRIDGE_IP'"
fi

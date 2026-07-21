#!/usr/bin/env bash
# fridge-capture-setup.sh — bring up the fridge transparent-MITM capture (TASK-062).
# RUN AS ROOT on the capture host (192.168.20.CAPTURE). Sets up BOTH sides:
#   router (via ssh firewall): policy-route the fridge's :46030 → here (dst preserved)
#   this host:                local nft REDIRECT :46030 → mitm transparent, then start mitm.
# Idempotent: safe to re-run. See docs/BACKLOG.md TASK-062 + docs/PROTOCOL.md §2 for the why.
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.FRIDGE}"
MITM_HOST="${MITM_HOST:-192.168.20.CAPTURE}"
PORT="${PORT:-46030}"
TAG="lg-fridge-route"
MARK="0xf2"
RT_TABLE="42"
LOG="${LOG:-data/fridge-mitm.log}"

cd "$(dirname "$0")"
mkdir -p data

log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
# Run the router ssh as the *invoking* user (not root), so it uses that user's ~/.ssh
# (pubkey auth to the router). Root's own ~/.ssh has no firewall alias/key.
_REAL_USER="${SUDO_USER:-${USER:-your-username}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

[ "$(id -u)" = 0 ] || die "run as root (needs the local nft redirect + mitm bind on :$PORT)."

# ── router side: route the fridge's :46030 → here as next-hop, dst preserved ──────────────
log "router: policy-routing $FRIDGE_IP :$PORT → next-hop $MITM_HOST (dst preserved)"
ssh_r "nft list chain inet fw4 mangle_prerouting >/dev/null 2>&1 || nft add chain inet fw4 mangle_prerouting '{ type filter hook prerouting priority mangle; }'" \
  || die "could not ensure mangle_prerouting chain on router"
ssh_r "nft list chain inet fw4 mangle_prerouting 2>/dev/null | grep -q 'comment \"$TAG\"' || nft add rule inet fw4 mangle_prerouting ip saddr $FRIDGE_IP tcp dport $PORT mark set $MARK comment '$TAG'" \
  || die "failed to add mangle mark rule"
ssh_r "ip rule list | grep -q 'fwmark $MARK lookup $RT_TABLE' || ip rule add fwmark $MARK lookup $RT_TABLE" \
  || die "failed to add ip rule"
ssh_r "ip route show table $RT_TABLE 2>/dev/null | grep -q . || ip route add default via $MITM_HOST table $RT_TABLE" \
  || die "failed to add policy route"

# ── this host: REDIRECT the fridge's inbound :46030 → mitm's transparent listener ─────────
log "this host: local nft REDIRECT $FRIDGE_IP :$PORT → mitm transparent :$PORT"
nft delete table inet lgfridge 2>/dev/null  # idempotent: start clean
nft add table inet lgfridge || die "nft add table failed"
nft 'add chain inet lgfridge pre { type nat hook prerouting priority -100; }' || die "nft add chain failed"
nft add rule inet lgfridge pre ip saddr "$FRIDGE_IP" tcp dport "$PORT" redirect to :"$PORT" || die "nft redirect rule failed"

# ── start mitm transparent (background, as the invoking user — mitmdump lives in their env) ─
if ss -ltn | grep -qw "$PORT"; then die ":$PORT already in use (stop any other mitm first)."; fi
# mitmdump is typically in the user's ~/.local/bin (user-level pip); root won't see it.
MITMDUMP="$(sudo -u "$_REAL_USER" bash -lc 'command -v mitmdump || echo mitmdump')"
log "starting $MITMDUMP transparent on :$PORT (log: $LOG) as user $_REAL_USER"
sudo -u "$_REAL_USER" nohup "$MITMDUMP" --mode transparent --listen-host 0.0.0.0 --listen-port "$PORT" \
  --set ssl_insecure=true --set flow_detail=3 > "$LOG" 2>&1 &
echo $! > data/fridge-mitm.pid
for i in $(seq 1 30); do ss -ltn | grep -qw "$PORT" && break; sleep 0.5; done
ss -ltn | grep -qw "$PORT" || { kill "$(cat data/fridge-mitm.pid)" 2>/dev/null; die "mitm did not start; see $LOG"; }

# ── flush the fridge's conntrack so its next :46030 goes through the new path ──────────────
log "flushing $FRIDGE_IP conntrack (forces reconnect through the capture path)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "capture rig is UP. mitm pid $(cat data/fridge-mitm.pid)."
log "Now provoke a fridge event (open/close the door) and watch:  tail -f $LOG"
log "Look for: POST https://<lg-ip>:$PORT/lgehadm/report/diagmon"
log "Tear down with:  ./fridge-capture-teardown.sh"

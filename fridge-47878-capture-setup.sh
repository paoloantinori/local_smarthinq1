#!/usr/bin/env bash
# fridge-47878-capture-setup.sh — capture the fridge's :47878 control channel (M3 prerequisite).
# RUN AS ROOT on the capture host (192.168.20.200).
#
# The fridge's commands are delivered via :47878 (confirmed 2026-07-21, PROTOCOL.md §4).
# This rig captures :47878 the same way the :46030 rig captures telemetry: transparent-mode
# route-as-next-hop (dst preserved) + local REDIRECT + mitm transparent on :47878.
#
# Provoke a command (e.g. change a fridge temp in the LG app) to see the command delivery.
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.182}"
MITM_HOST="${MITM_HOST:-192.168.20.200}"
PORT="${PORT:-47878}"
TAG="lg-fridge-47878"
MARK="0xf3"
RT_TABLE="43"
LOG="${LOG:-data/fridge-47878.log}"

cd "$(dirname "$0")"
mkdir -p data

log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
_REAL_USER="${SUDO_USER:-${USER:-pantinor}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

[ "$(id -u)" = 0 ] || die "run as root."

# ── router: policy-route fridge :47878 → here (dst preserved, same approach as :46030) ─────
log "router: policy-routing $FRIDGE_IP :$PORT → next-hop $MITM_HOST (dst preserved)"
ssh_r "nft list chain inet fw4 mangle_prerouting >/dev/null 2>&1 || nft add chain inet fw4 mangle_prerouting '{ type filter hook prerouting priority mangle; }'" || die "mangle chain"
ssh_r "nft list chain inet fw4 mangle_prerouting 2>/dev/null | grep -q 'comment \"$TAG\"' || nft add rule inet fw4 mangle_prerouting ip saddr $FRIDGE_IP tcp dport $PORT mark set $MARK comment '$TAG'" || die "mark rule"
ssh_r "ip rule list | grep -q 'fwmark $MARK lookup $RT_TABLE' || ip rule add fwmark $MARK lookup $RT_TABLE" || die "ip rule"
ssh_r "ip route show table $RT_TABLE 2>/dev/null | grep -q . || ip route add default via $MITM_HOST table $RT_TABLE" || die "policy route"

# ── this host: REDIRECT fridge's inbound :47878 → mitm transparent on :47878 ───────────────
log "this host: local nft REDIRECT $FRIDGE_IP :$PORT → mitm transparent :$PORT"
nft delete table inet lg47878 2>/dev/null
nft add table inet lg47878 || die "nft table"
nft 'add chain inet lg47878 pre { type nat hook prerouting priority -100; }' || die "nft chain"
nft add rule inet lg47878 pre ip saddr "$FRIDGE_IP" tcp dport "$PORT" redirect to :"$PORT" || die "redirect"

# ── start mitm transparent on :47878 (as the invoking user) ────────────────────────────────
if ss -ltn | grep -qw "$PORT"; then die ":$PORT already in use."; fi
MITMDUMP="$(sudo -u "$_REAL_USER" bash -lc 'command -v mitmdump || echo mitmdump')"
log "starting $MITMDUMP transparent on :$PORT (log: $LOG) as user $_REAL_USER"
sudo -u "$_REAL_USER" nohup "$MITMDUMP" --mode transparent --listen-host 0.0.0.0 --listen-port "$PORT" \
  --set ssl_insecure=true --set flow_detail=3 > "$LOG" 2>&1 &
echo $! > data/fridge-47878-mitm.pid
for i in $(seq 1 30); do ss -ltn | grep -qw "$PORT" && break; sleep 0.5; done
ss -ltn | grep -qw "$PORT" || { kill "$(cat data/fridge-47878-mitm.pid)" 2>/dev/null; die "mitm did not start; see $LOG"; }

# ── flush the fridge's conntrack (forces :47878 reconnect through the capture path) ────────
log "flushing $FRIDGE_IP conntrack (fridge will reconnect :47878 through mitm)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "CAPTURE RIG IS UP on :47878. mitm pid $(cat data/fridge-47878-mitm.pid)."
log "Now issue a command from the LG app (e.g. change fridge temp). Watch: tail -f $LOG"
log "Tear down: sudo ./fridge-47878-capture-teardown.sh"

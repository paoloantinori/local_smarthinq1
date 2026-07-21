#!/usr/bin/env bash
# fridge-sever-test-setup.sh — TASK-050 standalone sever test for the fridge.
# RUN AS ROOT on the capture host (192.168.20.200).
#
# This routes the fridge's :46030 to our fake-cloud server (standalone mode — no forwarding
# to real LG) AND firewalls :47878 (the fridge's keepalive) so the fridge can ONLY reach our
# server. The test: does the fridge operate + reconnect cloud-free?
#
# Topology (same as the transparent capture rig, but server instead of mitm):
#   router: policy-route fridge :46030 → .200 next-hop (dst preserved)
#   router: DROP fridge :47878 (block the real-cloud keepalive)
#   .200:    local nft REDIRECT :46030 → server on :46030 (standalone mode)
set -uo pipefail

ROUTER_SSH="${ROUTER_SSH:-firewall}"
FRIDGE_IP="${FRIDGE_IP:-192.168.20.182}"
MITM_HOST="${MITM_HOST:-192.168.20.200}"
PORT="${PORT:-46030}"
TAG="lg-fridge-route"
BLOCK_TAG="lg-fridge-block"
MARK="0xf2"
RT_TABLE="42"
LOG="${LOG:-data/sever-test.log}"

cd "$(dirname "$0")"
mkdir -p data

log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(date +%H:%M:%S)" "$*" >&2; exit 1; }
_REAL_USER="${SUDO_USER:-${USER:-pantinor}}"
ssh_r(){ sudo -u "$_REAL_USER" ssh -o ConnectTimeout=8 "$ROUTER_SSH" "$@"; }

[ "$(id -u)" = 0 ] || die "run as root."

# ── router: policy-route fridge :46030 → here (same as capture rig) ────────────────────────
log "router: policy-routing $FRIDGE_IP :$PORT → next-hop $MITM_HOST"
ssh_r "nft list chain inet fw4 mangle_prerouting >/dev/null 2>&1 || nft add chain inet fw4 mangle_prerouting '{ type filter hook prerouting priority mangle; }'" || die "mangle chain"
ssh_r "nft list chain inet fw4 mangle_prerouting 2>/dev/null | grep -q 'comment \"$TAG\"' || nft add rule inet fw4 mangle_prerouting ip saddr $FRIDGE_IP tcp dport $PORT mark set $MARK comment '$TAG'" || die "mark rule"
ssh_r "ip rule list | grep -q 'fwmark $MARK lookup $RT_TABLE' || ip rule add fwmark $MARK lookup $RT_TABLE" || die "ip rule"
ssh_r "ip route show table $RT_TABLE 2>/dev/null | grep -q . || ip route add default via $MITM_HOST table $RT_TABLE" || die "policy route"

# ── router: BLOCK fridge :47878 (so the real-cloud keepalive can't bypass) ─────────────────
log "router: BLOCKING $FRIDGE_IP :47878 (severing the real-cloud keepalive)"
ssh_r "nft list chain inet fw4 forward 2>/dev/null | grep -q 'comment \"$BLOCK_TAG\"' || nft add rule inet fw4 forward ip saddr $FRIDGE_IP tcp dport 47878 drop comment '$BLOCK_TAG'" || die "block rule"

# ── .200: local REDIRECT :46030 → server ──────────────────────────────────────────────────
log "this host: local nft REDIRECT $FRIDGE_IP :$PORT → server :$PORT"
nft delete table inet lgsever 2>/dev/null
nft add table inet lgsever || die "nft table"
nft 'add chain inet lgsever pre { type nat hook prerouting priority -100; }' || die "nft chain"
nft add rule inet lgsever pre ip saddr "$FRIDGE_IP" tcp dport "$PORT" redirect to :"$PORT" || die "redirect"

# ── start the server in STANDALONE mode ────────────────────────────────────────────────────
if ss -ltn | grep -qw "$PORT"; then die ":$PORT already in use."; fi
log "starting server in STANDALONE mode on :$PORT (log: $LOG)"
# NB: use 'env' to pass vars through sudo (sudo -u resets the environment by default).
sudo -u "$_REAL_USER" env LGM_MODE=standalone LGM_CERT=data/cert.pem LGM_KEY=data/key.pem \
  LGM_STATE_DIR=data/sever-test \
  nohup python3 -m server.app > "$LOG" 2>&1 &
echo $! > data/sever-test.pid
for i in $(seq 1 20); do ss -ltn | grep -qw "$PORT" && break; sleep 0.5; done
ss -ltn | grep -qw "$PORT" || { kill "$(cat data/sever-test.pid)" 2>/dev/null; die "server did not start; see $LOG"; }

# ── flush the fridge's conntrack (forces reconnect through the new path) ───────────────────
log "flushing $FRIDGE_IP conntrack (fridge will reconnect to our server)"
ssh_r "conntrack -D -s $FRIDGE_IP 2>/dev/null; conntrack -D -d $FRIDGE_IP 2>/dev/null; true"

log "SEVER TEST IS UP. The fridge can now ONLY reach our server (standalone)."
log "Watch: tail -f $LOG  (look for POST /lgehadm/report/diagmon)"
log "Tear down: sudo ./fridge-sever-test-teardown.sh"

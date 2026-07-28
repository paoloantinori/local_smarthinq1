#!/usr/bin/env bash
# Point a ThinQ1 appliance's :46030 + :47878 at the HAOS host (the fake-cloud add-on).
# Generalizes capture-ctl's nft DNAT to a configurable target. Run via SSH to the OpenWrt
# router (ROUTER_SSH, default "firewall"). One-time setup for the always-on deployment (TASK-073).
#
# Usage:
#   ./routing-setup.sh on      <appliance-ip> <target-ip> [router-ssh]
#   ./routing-setup.sh off     <appliance-ip>               [router-ssh]
#   ./routing-setup.sh status  <appliance-ip>               [router-ssh]
#   ./routing-setup.sh persist <appliance-ip> <target-ip>   [router-ssh]
#
# <target-ip> is REQUIRED (no default) so the script can never silently point at the stale
# old target (the 2026-07-26..28 outage was exactly rules left pointing at a dead IP).
#
# Notes:
# - capture-ctl diverts only :46030; :47878 is a separate raw-TCP msgpack channel (PROTOCOL.md
#   section 4). This script loops over BOTH ports, adding one dnat + one masquerade per port.
# - The masquerade hairpin is required because UCI firewall redirect does NOT synthesize the
#   srcnat masquerade for LAN-to-LAN (same-zone) DNAT; without it the appliance DNATs to the
#   target but the reply black-holes.
set -euo pipefail

COMMENT="lg-fake-cloud"
ACTION="${1:?usage: on|off|status|persist}"
APPLIANCE="${2:-}"
TARGET="${3:-}"
ROUTER="${ROUTER_SSH:-firewall}"

ssh_r(){ ssh -o ConnectTimeout=5 "$ROUTER" "$1"; }

# Exact, end-anchored comment match. The TASK-062 substring bug was caused by loose matching
# (a rule tagged 'lg-fake-cloud-foo' falsely read as present), so match the literal
# `comment "lg-fake-cloud"` followed by space-or-EOL, inner double-quotes literal.
nft_count() {
  ssh_r "nft list ruleset 2>/dev/null | grep -c 'comment \"$COMMENT\"\$'" | tr -d ' \n'
}

case "$ACTION" in
  on)
    [ -n "$APPLIANCE" ] && [ -n "$TARGET" ] || { echo "on needs <appliance-ip> <target-ip>"; exit 1; }
    for port in 46030 47878; do
      ssh_r "nft add rule inet fw4 dstnat_lan ip saddr $APPLIANCE tcp dport $port dnat to $TARGET:$port comment '$COMMENT'"
      ssh_r "nft add rule inet fw4 srcnat_lan ip saddr $APPLIANCE ip daddr $TARGET tcp dport $port masquerade comment '$COMMENT'"
    done
    echo "DNAT installed: $APPLIANCE -> $TARGET (46030, 47878). Count now: $(nft_count)"
    ;;

  off)
    [ -n "$APPLIANCE" ] || { echo "off needs <appliance-ip>"; exit 1; }
    # Delete every rule tagged with our EXACT comment, in both chains, by handle.
    ssh_r "for c in dstnat_lan srcnat_lan; do nft -a list chain inet fw4 \$c | grep -E 'comment \"$COMMENT\"\$' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 \$c \$h; done; done"
    # Post-check: refuse to leave a half-state (the outage was rules left pointing at a dead target).
    remaining="$(nft_count)"
    if [ "$remaining" != "0" ]; then
      echo "FAIL: $remaining rules with '$COMMENT' still present after OFF; appliance may point at a dead target." >&2
      exit 1
    fi
    echo "DNAT removed for $APPLIANCE (both ports). Verified clean."
    ;;

  status)
    echo "$(nft_count) rules with '$COMMENT'"
    ;;

  persist)
    [ -n "$APPLIANCE" ] && [ -n "$TARGET" ] || { echo "persist needs <appliance-ip> <target-ip>"; exit 1; }
    # 1. UCI firewall redirect sections (survive reboots). Per port.
    for port in 46030 47878; do
      name="lg_fake_cloud_$port"
      ssh_r "uci -q delete firewall.$name; uci set firewall.$name=redirect"
      ssh_r "uci set firewall.$name.name='$name'"
      ssh_r "uci set firewall.$name.src='lan'; uci set firewall.$name.dest='lan'"
      ssh_r "uci set firewall.$name.src_ip='$APPLIANCE'; uci set firewall.$name.src_dport='$port'"
      ssh_r "uci set firewall.$name.dest_ip='$TARGET'; uci set firewall.$name.dest_port='$port'"
      ssh_r "uci set firewall.$name.target='DNAT'; uci set firewall.$name.proto='tcp'"
      ssh_r "uci set firewall.$name.family='ipv4'; uci set firewall.$name.enabled='1'"
    done
    ssh_r "uci commit firewall"
    # 2. The masquerade hairpin is NOT synthesized by UCI redirect for LAN->LAN. Persist it as an
    # nft include file so it reloads on boot alongside fw4.
    ssh_r "mkdir -p /etc/nftables.d"
    ssh_r "cat > /etc/nftables.d/30-lg-fake-cloud.nft <<'NFT'
table inet fw4 {
  chain srcnat_lan {
    ip saddr $APPLIANCE ip daddr $TARGET tcp dport { 46030, 47878 } masquerade comment \"$COMMENT\"
  }
}
NFT"
    ssh_r "fw4 reload 2>/dev/null || /etc/init.d/firewall reload"
    echo "Persisted $APPLIANCE -> $TARGET (UCI redirects + masquerade nft include). Survives reboots."
    echo "Verify after a router reboot: nft list ruleset | grep $COMMENT  (expect both dnat + masquerade)."
    ;;

  *)
    echo "unknown action: $ACTION"; echo "usage: $0 on|off|status|persist ..."; exit 1
    ;;
esac

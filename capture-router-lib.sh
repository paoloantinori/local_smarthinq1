#!/usr/bin/env bash
# capture-router-lib.sh -- shared router helpers for the capture rigs (TASK-076).
#
# The policy-route dance (fwmark + ip rule + policy table, dst preserved) used to be
# copy-pasted in capture-fridge.sh, fridge-47878-capture-setup.sh and
# capture-wm47878.sh; the copies already caused one mark/table collision (two rigs on
# 0xf3/43, caught in review). This lib is the single implementation.
#
# SOURCING CONTRACT: the sourcing script must define, BEFORE calling these functions:
#   ROUTER_SSH   the ssh alias of the router
#   ssh_r()      how to run a command on the router (plain ssh for user rigs;
#                sudo -u "$_REAL_USER" ssh for rigs running as root)
# The lib resolves ssh_r from the caller's scope at call time.
#
# Functions (all take explicit args; no globals besides ssh_r/ROUTER_SSH):
#   lib_log / lib_die                    shared message helpers
#   router_rule_installed TAG            true when a rule with this comment exists
#   router_add_mark_rule IP PORT MARK TAG  idempotent fwmark rule in mangle_prerouting
#   router_ensure_ip_rule MARK TABLE     idempotent ip rule
#   router_ensure_policy_route TABLE VIA idempotent, self-healing (replace + exact via)
#   router_route_off MARK TABLE TAG      remove rule + ip rule + flush table
#   router_flush_conntrack IP            both directions

lib_ts(){ date +%H:%M:%S; }
lib_log(){ printf '\033[1;34m[%s]\033[0m %s\n' "$(lib_ts)" "$*"; }
lib_die(){ printf '\033[1;31m[%s] FAIL:\033[0m %s\n' "$(lib_ts)" "$*" >&2; exit 1; }

router_rule_installed(){ # TAG -> 0 when installed
  ssh_r "nft list chain inet fw4 mangle_prerouting 2>/dev/null | grep -q 'comment \"$1\"'"
}

router_ensure_mangle_chain(){
  ssh_r "nft list chain inet fw4 mangle_prerouting >/dev/null 2>&1 || nft add chain inet fw4 mangle_prerouting '{ type filter hook prerouting priority mangle; }'"
}

router_add_mark_rule(){ # SRC_IP PORT MARK TAG
  router_ensure_mangle_chain || return 1
  if router_rule_installed "$4"; then return 0; fi
  ssh_r "nft add rule inet fw4 mangle_prerouting ip saddr $1 tcp dport $2 mark set $3 comment '$4'"
}

router_ensure_ip_rule(){ # MARK TABLE
  ssh_r "ip rule list | grep -q 'fwmark $1 lookup $2' || ip rule add fwmark $1 lookup $2"
}

router_ensure_policy_route(){ # TABLE VIA
  # exact via-match + replace: a stale wrong default in the table is corrected,
  # not silently kept (the weak grep -q . variant kept stale routes).
  ssh_r "ip route show table $1 2>/dev/null | grep -qF 'default via $2' || ip route replace default via $2 table $1"
}

router_route_off(){ # MARK TABLE TAG
  ssh_r "nft -a list chain inet fw4 mangle_prerouting 2>/dev/null | grep 'comment \"$3\"' | grep -oE 'handle [0-9]+' | cut -d' ' -f2 | while read h; do nft delete rule inet fw4 mangle_prerouting handle \"\$h\"; done"
  ssh_r "ip rule del fwmark $1 lookup $2 2>/dev/null; ip route flush table $2 2>/dev/null; true"
}

router_flush_conntrack(){ # IP
  ssh_r "conntrack -D -s $1 2>/dev/null; conntrack -D -d $1 2>/dev/null; true"
}

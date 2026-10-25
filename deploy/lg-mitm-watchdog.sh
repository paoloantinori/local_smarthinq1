#!/usr/bin/env bash
# lg-mitm-watchdog: keeps the relay+bridge wrappers alive inside the lg_thinq addon.
# Runs on bird (systemd user timer, every 2 min). If the addon container restarted,
# its exec'd wrappers die and the DNAT would black-hole the appliances' cloud:
# this re-arms them. Scoped to the 25/10 rig (relays 46031/47879, bridge log tail).
set -u
ADDON=app_fddaa777_lg_thinq_fake_cloud
LOG="$HOME/.cache/lg-mitm-watchdog.log"
mkdir -p "$(dirname "$LOG")"

ha() { ssh -o ConnectTimeout=8 -oVisualHostKey=no ha "$@"; }

ports=$(ha "docker exec $ADDON sh -c 'netstat -ltn 2>/dev/null | grep -cE \":46031|:47879\"'" 2>/dev/null)
if [ "$ports" = "0" ]; then
  # clean slate re-arm: kill any stray wrappers first (code-review 25/10: a
  # non-idempotent re-arm stacked duplicate wrappers fighting for the bind)
  ha "docker exec $ADDON sh -c 'pkill -f wm_relay.py; true'"
  ha "docker exec -d $ADDON sh -c 'while true; do python3 /data/wm_relay.py --listen 0.0.0.0:46031 --upstream 52.158.121.103:46030 --cert /data/cert.pem --key /data/key.pem --log-file /data/relay-46030-1025.log; sleep 3; done & while true; do python3 /data/wm_relay.py --listen 0.0.0.0:47879 --upstream 52.158.121.103:47878 --cert /data/cert.pem --key /data/key.pem --log-file /data/relay-47878-1025.log; sleep 3; done & wait'" \
    && echo "$(date '+%F %T') relays re-armed (ports=$ports)" >> "$LOG"
elif [ "$ports" != "2" ]; then
  echo "$(date '+%F %T') relay check inconclusive (ports='$ports'), skipping" >> "$LOG"
fi

# count EXCLUDES the grep itself: grep -cF counts its own argv (code-review 25/10
# finding 1: the dead bridge was never re-armed because the count was always >=1)
bridge=$(ha "docker exec $ADDON ps aux 2>/dev/null | grep wm_local_bridge.py | grep -v grep | wc -l" 2>/dev/null)
if [ "${bridge:-0}" -eq 0 ]; then
  ha "docker exec -d $ADDON sh -c 'while true; do python3 /data/wm_local_bridge.py >> /data/wm_local_bridge.stderr 2>&1; sleep 5; done'" \
    && echo "$(date '+%F %T') bridge re-armed" >> "$LOG"
fi

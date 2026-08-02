#!/usr/bin/env bashio

# HAOS entrypoint for the LG ThinQ1 fake-cloud.
# Translates the add-on config form (/data/options.json) into the LGM_* env vars that
# server/app.py reads, auto-generates the TLS cert if missing, and launches the server.
# The Python server is NOT modified.
#
# DESIGN: read /data/options.json directly with jq, NOT via bashio::config.
# bashio::config calls the Supervisor API, which fails persistently with
# "403 forbidden / no token" on host_network installs (confirmed empirically and by the
# The-sultan/hassio-rethink-addon add-on, which reverted to the same jq approach).
# bashio::log.* is safe to use (it does not call the Supervisor API).

set -e

# OPTIONS may be overridden (e.g. by tests); default to the HAOS Supervisor-written file.
OPTIONS="${OPTIONS:-/data/options.json}"
[ -f "$OPTIONS" ] || echo '{}' > "$OPTIONS"   # defensive: empty options so defaults apply

# _opt <key> <default>: read a scalar option straight from options.json, falling back to
# <default> when the key is absent or null. (Empty strings are preserved.)
_opt() { jq -r --arg k "$1" --arg d "$2" '.options[$k] // $d' "$OPTIONS"; }
_opt_true() { [ "$(_opt "$1" false)" = "true" ]; }

# 1. TLS cert: auto-generate into /data (persistent) if absent. ThinQ1 modules do not pin the
#    cert (PROTOCOL.md section 2), so a locally-generated CA + *.lgthinq.com leaf is accepted.
#    Guarded on gen-cert.sh existing so the env-var translation is testable in isolation.
if [ ! -f /data/cert.pem ] || [ ! -f /data/key.pem ]; then
  if [ -f /app/gen-cert.sh ]; then
    bashio::log.info "Generating *.lgthinq.com cert into /data..."
    bash /app/gen-cert.sh /data
  fi
fi

# 2. Server bind + paths.
export LGM_HOST=0.0.0.0
export LGM_PORT=46030
export LGM_CONTROL_PORT=47878
export LGM_STATE_DIR=/data
export LGM_CERT=/data/cert.pem
export LGM_KEY=/data/key.pem

# 3. Mode + upstream (bridge mode forwards to LG; standalone impersonates locally).
export LGM_MODE="$(_opt mode standalone)"
export LGM_UPSTREAM_HOST="$(_opt upstream_host eic.lgthinq.com)"
export LGM_UPSTREAM_PORT="$(_opt upstream_port 46030)"

# 4. MQTT bridge (localhost Mosquitto add-on). Export only when non-empty (server/mqtt_bridge
#    treats an unset LGM_MQTT_HOST as "MQTT off"; an empty/null value would make it try to
#    connect("null") instead of the intended fast path).
_export_if_set() {  # $1 = var name, $2 = value
  [ -n "$2" ] && [ "$2" != "null" ] && export "$1=$2" || true
}
_mqtt_host="$(_opt mqtt_host 127.0.0.1)"; _export_if_set LGM_MQTT_HOST "$_mqtt_host"
_mqtt_port="$(_opt mqtt_port 1883)";       _export_if_set LGM_MQTT_PORT "$_mqtt_port"
_mqtt_user="$(_opt mqtt_user '')";         _export_if_set LGM_MQTT_USER "$_mqtt_user"
_mqtt_pass="$(_opt mqtt_password '')";     _export_if_set LGM_MQTT_PASS "$_mqtt_pass"

# 5. SAFETY (CLAUDE.md #5): control_channel.ALLOW_CONTROL is `os.environ.get(...,"") != ""`,
#    so ANY non-empty value (incl. "0") means ON. Export ONLY when the form is true; leave
#    unset otherwise so the server stays on its safe default (off).
if _opt_true allow_control; then
  export LGM_ALLOW_CONTROL=1
  bashio::log.warning "allow_control is ON: physical actuation path enabled."
fi

bashio::log.info "Starting LG fake-cloud (${LGM_MODE}) on :${LGM_PORT} (+ control :${LGM_CONTROL_PORT})"
cd /app
exec python -m server.app

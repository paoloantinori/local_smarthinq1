#!/usr/bin/env bashio

# HAOS entrypoint for the LG ThinQ1 fake-cloud.
# Translates the add-on config form (/data/options.json, read via bashio) into the LGM_* env
# vars that server/app.py already reads, auto-generates the TLS cert if missing, and launches
# the server. The Python server is NOT modified.

set -e

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
export LGM_MODE="$(bashio::config 'mode')"
export LGM_UPSTREAM_HOST="$(bashio::config 'upstream_host')"
export LGM_UPSTREAM_PORT="$(bashio::config 'upstream_port')"

# 4. MQTT bridge (localhost Mosquitto add-on). bashio::config returns the literal string "null"
#    for an unset key, which is truthy and would make server/mqtt_bridge.py try connect("null")
#    instead of the intended MQTT-off fast path. So export only when non-empty and not "null".
_export_if_set() {  # $1 = var name, $2 = value
  [ -n "$2" ] && [ "$2" != "null" ] && export "$1=$2" || true
}
_mqtt_host="$(bashio::config 'mqtt_host')";   _export_if_set LGM_MQTT_HOST "$_mqtt_host"
_mqtt_port="$(bashio::config 'mqtt_port')";   _export_if_set LGM_MQTT_PORT "$_mqtt_port"
_mqtt_user="$(bashio::config 'mqtt_user')";   _export_if_set LGM_MQTT_USER "$_mqtt_user"
_mqtt_pass="$(bashio::config 'mqtt_password')"; _export_if_set LGM_MQTT_PASS "$_mqtt_pass"

# 5. SAFETY (CLAUDE.md #5): control_channel.ALLOW_CONTROL is `os.environ.get(...,"") != ""`,
#    so ANY non-empty value (incl. "0") means ON. Export ONLY when the form is true; leave
#    unset otherwise so the server stays on its safe default (off).
if bashio::config.true 'allow_control'; then
  export LGM_ALLOW_CONTROL=1
  bashio::log.warning "allow_control is ON: physical actuation path enabled."
fi

bashio::log.info "Starting LG fake-cloud (${LGM_MODE}) on :${LGM_PORT} (+ control :${LGM_CONTROL_PORT})"
cd /app
exec python -m server.app

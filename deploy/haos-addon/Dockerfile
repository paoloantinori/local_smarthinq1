# HAOS app image for the LG ThinQ1 fake-cloud.
# Explicit FROM: Supervisor 2026.04.0 removed the automatic BUILD_FROM fallback, so we pin
# the base image directly. Re-verify this claim at build time (spec S2).
FROM ghcr.io/home-assistant/base:latest

# The HAOS base image is Alpine-based but ships NO Python (add-ons bring their own runtime;
# verified against ghcr.io/home-assistant/base:latest, Alpine 3.24). bash + tzdata are present
# (bashio depends on bash); openssl is needed by gen-cert.sh; jq by bashio fallback + tests.
RUN apk add --no-cache python3 py3-pip openssl jq

WORKDIR /app
COPY . /app

# HAOS base uses s6 init, which invokes /run.sh at the IMAGE ROOT as the entrypoint (not
# /app/run.sh). The build context is the repo root, so run.sh lands at
# /app/deploy/haos-addon/run.sh; copy it to /run.sh and make it executable.
RUN cp /app/deploy/haos-addon/run.sh /run.sh && chmod a+x /run.sh

# paho-mqtt is the only non-stdlib dependency (the MQTT bridge).
RUN pip install --no-cache-dir --break-system-packages paho-mqtt

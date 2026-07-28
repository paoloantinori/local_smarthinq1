# HAOS app image for the LG ThinQ1 fake-cloud.
# Explicit FROM: Supervisor 2026.04.0 removed the automatic BUILD_FROM fallback, so we pin
# the base image directly. Re-verify this claim at build time (spec S2).
FROM ghcr.io/home-assistant/base:latest

# openssl: gen-cert.sh needs it to build the CA + leaf. jq: bashio fallback + the run.sh test.
# bash + tzdata are already in the base image (bashio depends on bash).
RUN apk add --no-cache openssl jq

WORKDIR /app
COPY . /app

# paho-mqtt is the only non-stdlib dependency (the MQTT bridge).
RUN pip install --no-cache-dir paho-mqtt

CMD [ "/run.sh" ]

FROM python:3.12-slim

WORKDIR /app

# No third-party deps required for the core server (stdlib only).
# paho-mqtt is only needed if the MQTT bridge is enabled.
RUN pip install --no-cache-dir paho-mqtt

COPY . .

# Generate certs if not present (mount a volume at /app/data to persist).
RUN if [ ! -f data/cert.pem ]; then bash gen-cert.sh || true; fi

EXPOSE 46030 47878

CMD ["python", "-m", "server.app"]

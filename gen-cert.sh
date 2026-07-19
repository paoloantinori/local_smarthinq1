#!/usr/bin/env bash
# Generate a CA + a *.lgthinq.com leaf cert for the fake-cloud server.
# The appliance accepts it because ThinQ1 modules do not pin/validate the TLS cert
# (PROTOCOL.md §2) — but re-verify for YOUR device before relying on it.
#
# Usage: ./gen-cert.sh [output_dir]   (default: data/)
set -euo pipefail

DIR="${1:-data}"
mkdir -p "$DIR"

# 1) A local CA.
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -subj "/CN=LG ThinQ fake-cloud CA" \
  -keyout "$DIR/ca.key" -out "$DIR/ca.pem"

# 2) A leaf for the LG ThinQ hostnames (SAN-covered).
openssl req -newkey rsa:2048 -nodes \
  -subj "/CN=*.lgthinq.com" \
  -keyout "$DIR/key.pem" -out "$DIR/cert.csr"

openssl x509 -req -in "$DIR/cert.csr" \
  -CA "$DIR/ca.pem" -CAkey "$DIR/ca.key" -CAcreateserial -days 825 \
  -out "$DIR/cert.pem" \
  -extfile <(printf "subjectAltName=DNS:*.lgthinq.com,DNS:eic.lgthinq.com,DNS:*.lgcloud.com")

rm -f "$DIR/cert.csr"
echo "Wrote $DIR/cert.pem + $DIR/key.pem (server) and $DIR/ca.pem (the CA)."
echo "Run the server:  LGM_CERT=$DIR/cert.pem LGM_KEY=$DIR/key.pem python3 -m server.app"

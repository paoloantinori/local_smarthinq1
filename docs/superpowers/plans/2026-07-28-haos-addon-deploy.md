# HAOS Add-on Deployment Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Package the existing LG ThinQ1 fake-cloud server as a Home Assistant OS app (add-on) that runs always-on on the rpi4, plus a repeatable OpenWrt routing script that points the appliance traffic at it.

**Architecture:** A new deploy/haos-addon/ directory holds a HAOS app (config.yaml form + Dockerfile + run.sh). run.sh uses bashio to translate the HA UI form into the LGM_* env vars the unchanged Python server already reads, and auto-generates the TLS cert. A separate routing-setup.sh generalizes capture-ctl's nft DNAT to a configurable target IP (the rpi4) and persists it on OpenWrt.

**Tech Stack:** HAOS add-on (base image ghcr.io/home-assistant/base, bashio, Alpine), Python 3 (server unchanged, stdlib + paho-mqtt), nftables on OpenWrt.

**Spec:** docs/superpowers/specs/2026-07-28-haos-addon-deploy-design.md

**Scope:** TASK-071, TASK-072, TASK-073 (the codeable, locally-testable parts). TASK-074 (supervised live install on the rpi4) and TASK-075 (live control test) are human-supervised steps done at home; they are NOT in this plan.

---

## File Structure

Created files (all under deploy/haos-addon/ unless noted):

- deploy/haos-addon/config.yaml: HAOS app metadata + options/schema (the config form) + host_network/startup/boot.
- deploy/haos-addon/Dockerfile: image build (base image, openssl, paho-mqtt, copy server).
- deploy/haos-addon/run.sh: bashio entrypoint (options to LGM_* env vars, cert auto-gen, launch server). The safety-critical file.
- deploy/haos-addon/apparmor.txt: required by the app structure (default-permissive profile).
- deploy/haos-addon/README.md: user-facing install + config instructions.
- deploy/haos-addon/routing-setup.sh: OpenWrt nft DNAT to a configurable target (the rpi4) + persistence helper.
- deploy/haos-addon/test_run_sh.py: unit test asserting run.sh env-var translation (the safety gate especially).
- deploy/haos-addon/replay_flow.py: replay a captured flow against the running add-on.

Modified files:
- docs/INSTALL.md: add the HAOS add-on install path.
- README.md: mention the add-on in Components.

Unchanged: all of server/ (the Python server reads the same LGM_* env vars; 92 tests stay valid).

---

## Chunk 1: The add-on skeleton + the safety-critical run.sh

### Task 1: Create config.yaml

Files: Create deploy/haos-addon/config.yaml

Step 1: Create the config.yaml (see spec section "config.yaml (form schema)" for the exact content; key fields: name, slug, version, arch [aarch64, amd64], host_network: true, startup: services, boot: auto, the options {mqtt_host, mqtt_port, mqtt_user, mqtt_password, mode, allow_control, upstream_host, upstream_port} and matching schema, with allow_control default false).

Step 2: Validate the YAML parses. Run: python -c "import yaml; yaml.safe_load(open('deploy/haos-addon/config.yaml'))". Expected: no output.

Step 3: Commit. git add deploy/haos-addon/config.yaml && git commit -m "TASK-071: HAOS add-on config.yaml (form schema + host_network)".

### Task 2: Write the test for run.sh env-var translation (TDD, safety gate first)

Files: Create deploy/haos-addon/test_run_sh.py

This is the load-bearing test. It proves run.sh exports the right LGM_* vars from a given options JSON, and critically that allow_control=false does NOT export LGM_ALLOW_CONTROL (the server reads != "", so exporting "0" would invert the gate and enable physical actuation, violating CLAUDE.md #5).

The test stubs bashio::config and bashio::config.true to read a fake /data/options.json, sources run.sh with the 'exec python' line stripped, and captures the resulting LGM_* env vars. Four cases: basic options translated; allow_control=false leaves LGM_ALLOW_CONTROL unset; allow_control=true exports LGM_ALLOW_CONTROL=1; bridge mode exports upstream host/port. (Full code in the spec's run.sh section; the test file mirrors the four assertions listed there.)

Step 2: Run: python -m pytest deploy/haos-addon/test_run_sh.py -v. Expected: FAIL (run.sh absent).
Step 3: Commit the failing test.

### Task 3: Implement run.sh (make the test pass)

Files: Create deploy/haos-addon/run.sh

run.sh content (see spec "run.sh (options to env vars)"): generate cert if missing via 'bash /app/gen-cert.sh /data'; export LGM_HOST/PORT/CONTROL_PORT/STATE_DIR/CERT/KEY; export LGM_MODE, LGM_UPSTREAM_HOST/PORT from bashio::config; export LGM_MQTT_* from bashio::config; SAFETY: 'if bashio::config.true allow_control; then export LGM_ALLOW_CONTROL=1; fi' (never export "0"); then 'cd /app && exec python -m server.app'.

Step 2: chmod +x deploy/haos-addon/run.sh.
Step 3: Run the test; expected 4 PASS.
Step 4: Commit.

### Task 4: Dockerfile + apparmor.txt + README

Files: Create deploy/haos-addon/Dockerfile, apparmor.txt, README.md.

Dockerfile: FROM ghcr.io/home-assistant/base:latest; RUN apk add --no-cache openssl jq; COPY . /app; RUN pip install --no-cache-dir paho-mqtt; CMD ["/run.sh"]. (Explicit FROM because Supervisor 2026.04.0 removed the BUILD_FROM fallback; re-verify at build time, spec S2.)

apparmor.txt: default-permissive profile (network inet stream; /app/** r; /data/** rw; python/bash/openssl ix).

README.md: setup steps (install, create Mosquitto user, configure form with mqtt_host=127.0.0.1, mode=standalone, allow_control=false), ports (46030 TLS, 47878 raw TCP), safety note (allow_control off by default).

Commit.

### Task 5: Plan-review checkpoint for Chunk 1

Dispatch plan-document-reviewer for Chunk 1 (Tasks 1-4) with the spec path. Fix issues, re-dispatch until Approved. Then proceed to Chunk 2.

---

## Chunk 2: Local build + flow-replay validation (TASK-072)

### Task 6: Local Docker build of the add-on

Step 1: cd deploy/haos-addon && docker build -t lg-thinq-fake-cloud:test . Expected: builds; openssl+jq+paho-mqtt install. If ghcr.io/home-assistant/base is unavailable, fall back to python:3.12-slim and apt-get, recording the deviation (spec S2).
Step 2: Smoke-run: mkdir -p /tmp/lg-data; write /tmp/lg-data/options.json with the standalone/false options; docker run --rm -p 46030:46030 -p 47878:47878 -v /tmp/lg-data:/data lg-thinq-fake-cloud:test. Expected: cert generated, then "LG fake-cloud (standalone) on :46030"; cert.pem/key.pem in /tmp/lg-data.

### Task 7: Flow-replay test

Files: Create deploy/haos-addon/replay_flow.py

Posts a real captured <Report> (first one in flows/washer-overnight-20260725.log) to https://127.0.0.1:46030/lgehadm/report/diagmon over TLS with verification disabled, asserts 200, then checks /debug/state contains the modelName. Cross-checks the same report decodes via registry.decode_report directly (known-good).

Run: python -m pytest deploy/haos-addon/replay_flow.py -v against the container from Task 6. Expected: PASS. Commit.

### Task 8: Plan-review checkpoint for Chunk 2

Dispatch plan-document-reviewer for Chunk 2. Fix until Approved.

---

## Chunk 3: Routing script (TASK-073)

### Task 9: routing-setup.sh

Files: Create deploy/haos-addon/routing-setup.sh

Generalizes capture-ctl's nft DNAT to a configurable target. Actions: on <appliance> <target> adds nft rules in inet fw4 dstnat_lan (dnat to target:port) and srcnat_lan (masquerade) for ports 46030+47878, comment "lg-fake-cloud"; off <appliance> deletes by handle; status counts rules; persist <appliance> <target> writes UCI firewall redirects (survives reboots, TASK-051) and reloads. Runs via SSH to ROUTER_SSH (default "firewall").

Step 2: bash -n deploy/haos-addon/routing-setup.sh (syntax check). Step 3: commit.

### Task 10: Docs (INSTALL.md + README.md)

INSTALL.md: add "## HAOS add-on (recommended for always-on)" with build/load, config-form fields, Mosquitto user, and routing-setup.sh on/persist. README.md Components: add the deploy/haos-addon/ row. Commit.

### Task 11: Plan-review checkpoint for Chunk 3 + final verification

Dispatch plan-document-reviewer for Chunk 3. Then: python -m pytest -q && pyright server/ tests/ deploy/. Expected: tests pass (new test_run_sh.py + replay_flow.py add to the count); pyright 0 errors.

---

## Out of this plan (supervised, at home)

- TASK-074: install on the real rpi4, configure, switch the DNAT, observe the appliance reconnect and state arrive in HA. Human-supervised.
- TASK-075: live control test (allow_control=true), per-command-type approval. Human-supervised, separate safety gate.

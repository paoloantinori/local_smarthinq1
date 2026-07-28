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

Step 2 (stub details, load-bearing): the test stubs bashio as a bash function whose NAME INCLUDES the literal '::' (bash allows ':' in identifiers). bashio invokes `bashio::config 'mode'`, which bash word-splits so the function receives $1=config, $2=mode; and `bashio::config.true 'allow_control'` => $1=config.true, $2=allow_control. So declare `bashio() { case "$1" in config) jq -r ".options.$2 // empty" "$OPTS";; config.true) [ "$(jq -r '.options.'"$2"' // false' "$OPTS")" = "true" ];; log.*) :;; esac; }`. A stub that reads $1 as the key (wrong) silently never matches and every assertion passes vacuously, so the test MUST include one non-empty expected-value assertion (e.g. LGM_MQTT_HOST == "127.0.0.1") as the broken-stub detector.

The safety-gate assertion must be `env.get("LGM_ALLOW_CONTROL", "") == ""` for the false case (NOT `"LGM_ALLOW_CONTROL" not in env`), because asserting against the empty-string default is what locks the `!= ""` semantics in server/control_channel.py:167. "not in env" would silently accept the exact bug (exporting "0") the gate exists to prevent. Add a THIRD case beyond true/false: allow_control ABSENT (key omitted from options.json entirely, the realistic HAOS default), which must also leave LGM_ALLOW_CONTROL unset (bashio::config.true on "null" returns non-zero).

Step 3: Run: python -m pytest deploy/haos-addon/test_run_sh.py -v. Expected: FAIL (run.sh absent).
Step 4: Commit the failing test.

### Task 3: Implement run.sh (make the test pass)

Files: Create deploy/haos-addon/run.sh

run.sh content (see spec "run.sh (options to env vars)"): generate cert if missing via 'bash /app/gen-cert.sh /data'; export LGM_HOST/PORT/CONTROL_PORT/STATE_DIR/CERT/KEY; export LGM_MODE, LGM_UPSTREAM_HOST/PORT from bashio::config.

MQTT block (C1 fix): bashio::config returns the literal string "null" for an unset key, which is truthy and would break server/mqtt_bridge.py's "unset LGM_MQTT_HOST = MQTT off" contract (it would try to connect("null",1883)). So read into a var and export ONLY when non-empty and not "null": `h="$(bashio::config 'mqtt_host')"; [ -n "$h" ] && [ "$h" != "null" ] && export LGM_MQTT_HOST="$h" || true` (same pattern for mqtt_user/mqtt_pass/port).

SAFETY: 'if bashio::config.true allow_control; then export LGM_ALLOW_CONTROL=1; fi' (never export "0"); then 'cd /app && exec python -m server.app'.

Step 2: chmod +x deploy/haos-addon/run.sh.
Step 3: Run the test; expected 4 PASS.
Step 4: Commit.

### Task 4: Dockerfile + apparmor.txt + README

Files: Create deploy/haos-addon/Dockerfile, apparmor.txt, README.md.

Dockerfile: FROM ghcr.io/home-assistant/base:latest; RUN apk add --no-cache openssl jq; COPY . /app; RUN pip install --no-cache-dir paho-mqtt; CMD ["/run.sh"]. (Explicit FROM because Supervisor 2026.04.0 removed the BUILD_FROM fallback; re-verify at build time, spec S2.) BUILD CONTEXT IS THE REPO ROOT, not deploy/haos-addon: run `docker build -f deploy/haos-addon/Dockerfile .` from the repo root, so COPY . /app brings in gen-cert.sh + server/ + tools/. Building with context=deploy/haos-addon would copy only the add-on dir and the server would be missing at runtime.

apparmor.txt: default-permissive profile (network inet stream; /app/** r; /data/** rw; python/bash/openssl ix).

README.md: setup steps (install, create Mosquitto user, configure form with mqtt_host=127.0.0.1, mode=standalone, allow_control=false), ports (46030 TLS, 47878 raw TCP), safety note (allow_control off by default).

Commit.

### Task 5: Plan-review checkpoint for Chunk 1

Dispatch plan-document-reviewer for Chunk 1 (Tasks 1-4) with the spec path. Fix issues, re-dispatch until Approved. Then proceed to Chunk 2.

---

## Chunk 2: Local build + flow-replay validation (TASK-072)

### Task 6: Local Docker build of the add-on

Step 1: Build from the REPO ROOT (not from deploy/haos-addon, which would use the wrong context and omit server/ + gen-cert.sh + tools/, per Task 4): `docker build -f deploy/haos-addon/Dockerfile -t lg-thinq-fake-cloud:test .` Expected: builds; openssl+jq+paho-mqtt install. If ghcr.io/home-assistant/base is unavailable, fall back to python:3.12-slim and apt-get, recording the deviation (spec S2).
Step 2: Precondition: confirm :46030 and :47878 are free on the host (a leftover mitm from `capture-ctl on` will collide): `ss -ltn | grep -E ':46030|:47878'` must be empty; run `./capture-ctl off` first if needed.

Step 3: Smoke-run. Write the EXACT options.json (field names must match config.yaml's schema and run.sh's bashio reads):
echo '{"options":{"mode":"standalone","allow_control":false,"mqtt_host":"127.0.0.1","mqtt_port":1883,"mqtt_user":"","mqtt_password":"","upstream_host":"","upstream_port":""}}' > /tmp/lg-data/options.json
then: docker run --rm -p 46030:46030 -p 47878:47878 -v /tmp/lg-data:/data lg-thinq-fake-cloud:test. Expected: cert generated, then "LG fake-cloud (standalone) on :46030"; cert.pem/key.pem in /tmp/lg-data. (mqtt_user/pass empty is fine for the smoke run; broker-connect failures are non-fatal at boot.)

### Task 7: Flow-replay test

Files: Create deploy/haos-addon/replay_flow.py

Gated behind REPLAY_LIVE=1 (matches the repo's MQTT_LIVE convention in tests/test_ha_mqtt.py); pytest.skip otherwise so the default `python -m pytest -q` suite is not broken when Docker is absent.

Extract the report with re.search(r"<Report>.*?</Report>", text, re.S) (DOTALL + non-greedy are load-bearing: the blocks span multiple lines, and greedy .* would slurp all 20 blocks). Copy the exact pattern from tests/test_server.py:_first_report. Collapse whitespace with re.sub(r"\s+", "", m.group(0)) and post the SAME bytes object to both the server and registry.decode_report (so the cross-check is meaningful).

Assertion (C2 fix): /debug/state is keyed by devId, NOT modelName (server/state.py:56 stores latest[dev_id]). So assert modelName appears as a VALUE: `any(p.get("modelName") == "WTWN3" for p in state.values())`, or assert the devId key is present. Do NOT assert "modelName in state" (modelName is never a top-level key). TLS: ctx.check_hostname=False + CERT_NONE is correct for our self-signed server (matches app.py's own bridge client); comment it so a future reader does not "fix" it.

Run: python -m pytest deploy/haos-addon/replay_flow.py -v against the container from Task 6. Expected: PASS. Commit.

### Task 8: Plan-review checkpoint for Chunk 2

Dispatch plan-document-reviewer for Chunk 2. Fix until Approved.

---

## Chunk 3: Routing script (TASK-073)

### Task 9: routing-setup.sh

Files: Create deploy/haos-addon/routing-setup.sh

Generalizes capture-ctl's nft DNAT to a CONFIGURABLE target (the rpi4 IP). NOTE: capture-ctl only diverts :46030 today; :47878 is a separate raw-TCP msgpack channel (PROTOCOL.md section 4), never diverted by capture-ctl. So this script must loop over BOTH ports explicitly with a per-port dnat + a per-port masquerade (one masquerade per port, matching the same port the dnat rewrote to, so hairpin reply traffic for :47878 is not left unrouted).

TARGET is a REQUIRED positional argument with NO default (so it can never silently point at the stale .200 or the scrubbed placeholder). ROUTER_SSH defaults to "firewall".

on <appliance> <target>: for p in 46030 47878, add:
  nft add rule inet fw4 dstnat_lan ip saddr <appliance> tcp dport $p dnat to <target>:$p comment 'lg-fake-cloud'
  nft add rule inet fw4 srcnat_lan ip saddr <appliance> ip daddr <target> tcp dport $p masquerade comment 'lg-fake-cloud'

off <appliance>: delete by handle in BOTH chains. The comment match must be EXACT and end-anchored (the TASK-062 substring bug was caused by loose matching; capture-ctl line 46): grep -E 'comment "lg-fake-cloud"( |$)' (comment + space-or-EOL, inner double-quotes literal), extract handle [0-9]+, delete per chain. AFTER the delete loop, re-run the nft_has check; if ANY rule with the tag survives, print "DNAT rules still present after OFF; appliance may point at a dead target" and exit non-zero (the 2026-07-26..28 outage was exactly rules left pointing at a dead IP). Assert BOTH ports gone.

persist <appliance> <target>: write UCI firewall redirect sections (survives reboots, TASK-051). Per port, a redirect section: src=lan, dest=lan, target=DNAT, src_ip=<appliance>, src_dport=<port>, dest_ip=<target>, dest_port=<port>, proto=tcp, family=ipv4, enabled=1, name=lg_fake_cloud_<port>. CRITICAL: UCI redirect does NOT synthesize the srcnat masquerade for LAN-to-LAN (same-zone) DNAT (it assumes WAN->LAN). So persist must ALSO install the masquerade rule durably, via an nft file in /etc/nftables.d/ (or an init.d script that re-runs the srcnat_lan add on boot). Without this, the appliance DNATs to the rpi4 but the reply black-holes (rpi4 replies with its own src-ip, which the appliance's conntrack does not expect). Verify: reboot the router, then `nft list ruleset | grep lg-fake-cloud` shows BOTH the dnat AND the masquerade for both ports, and the appliance reconnects (not just "rule present").

status <appliance>: count rules with the tag (both ports).

Step 2: bash -n deploy/haos-addon/routing-setup.sh (syntax check). Step 3: commit.

### Task 10: Docs (INSTALL.md + README.md)

INSTALL.md: add a NEW subsection `### Home Assistant OS add-on (recommended for always-on)` UNDER the existing "Production deployment" section (which already hosts `### systemd` and `### Docker` subsections; do not add a top-level heading that collides with the step flow). Content: load via local add-on repository (cp -r deploy/haos-addon, add repo in HA UI), the config-form fields (mqtt_host=127.0.0.1, mode=standalone, allow_control=false, Mosquitto user/pass), the one-time `routing-setup.sh on <appliance> <rpi4-ip>` then `routing-setup.sh persist <appliance> <rpi4-ip>`, and the verify step (reboot router, confirm reconnection). README.md: add a row to the existing Components table (columns: Component | What it does) -> `deploy/haos-addon/ | HAOS add-on packaging (always-on on rpi4)`. Commit.

### Task 11: Plan-review checkpoint for Chunk 3 + final verification

Dispatch plan-document-reviewer for Chunk 3. Then: python -m pytest -q && pyright server/ tests/ deploy/. Expected: tests pass (new test_run_sh.py + replay_flow.py add to the count); pyright 0 errors.

---

## Out of this plan (supervised, at home)

- TASK-074: install on the real rpi4, configure, switch the DNAT, observe the appliance reconnect and state arrive in HA. Human-supervised.
- TASK-075: live control test (allow_control=true), per-command-type approval. Human-supervised, separate safety gate.

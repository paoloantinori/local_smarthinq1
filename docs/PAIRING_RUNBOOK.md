# LG ThinQ1 pairing runbook (and cloud-weather survival guide)

Everything here comes from the 2026-09-25/26 events: the 47878 outage, the swallowed
registrations, the app losing both appliances, the edge/endpoint rot, and the two
onboarding sessions captured in cleartext. Evidence lives in `flows/` (redacted) and in
the live relay corpus on the Pi (see §5). Protocol background: `PROTOCOL.md` §2 (edge
rot), §4 (channels), §4.4 (the decoded WM 47878).

## 1. When pairing fails: read the failure BEFORE touching anything

The failure signature tells you whose fault it is. In the app, onboarding that stalls
at ~99% means: **the appliance completed its whole registration ladder and only the
final diagmon confirmation failed.** That is LG being sick, not the device.

Evidence (2026-09-26 08:33, washer, cleartext corpus): TotalDeviceInfoSvc 200,
ContentsVerSvc 200, PowerSavingInfoSvc 200, FWInfoSettingSvc 200, DevInfo ok on 47878,
then `report/diagmon` 502, app stuck at 99%. Same ladder on a healthy diagmon completes
in minutes. **Do NOT hard-reset an appliance whose ladder is green**: you gain nothing
(and a reset regenerates its identity, see §4).

Already-paired devices stay ONLINE with diagmon down: their online status rides the
47878 keepalive, not 46030 (fridge 2026-09-26: 47878 alive all morning, zero 46030,
app shows it online). Only NEW pairings need the diagmon rung.

## 2. Pre-pairing health check (diagmon-specific, stable green)

LG's edge pool is heterogeneous and rot is PER-ENDPOINT (`PROTOCOL.md` §2): a port can
be half-healthy (one endpoint 200, another 502), and it oscillates on tens of minutes.

1. Resolve the pool from PUBLIC resolvers (the home resolver caches one edge; that
   cached edge may be the rotten one, which is exactly what happened 2026-09-25).
2. Probe each IP per-endpoint. The signal that matters for pairing is **diagmon**:
   `POST /lgehadm/report/diagmon` on `:46030`. Other endpoints being 200 says nothing
   about diagmon.
3. Wait for diagmon **stably green** (two probe series minutes apart) before starting.
   A single green probe can land inside an oscillation trough.

## 3. Recording relay: pin a verified edge before the window

For a cleartext-recording session (onboarding corpus, door tests, vocabulary hunting):

- Run `wm47878_tls_relay.py` instances pinned (`--upstream IP:PORT`) to a verified
  edge, NOT the hostname: the resolver can rotate you onto a rotten edge mid-session.
- The deployed setup (2026-09-26): two instances in the HAOS addon container, one for
  46030 (listen 46031) and one for 47878 (listen 47879), cert `/data/cert.pem`,
  logs `/data/relay-46030.log` and `/data/relay-47878.log` (persistent volume),
  upstream pinned to `52.158.121.103` (verified healthy that day; re-verify per §2).
- Router: `lg-relay` DNAT rules for `.106`/`.190` from both service ports to the relay
  ports, with hairpin masquerade (same pattern as the production `lg-fake-cloud`
  rules), then **flush conntrack** for both IPs so live flows re-establish through the
  relay instead of riding old mappings.
- Mark the session in the logs (`=== PAIRING SESSION START ... ===`) so corpus
  segments are attributable.
- Rollback: remove the two `lg-relay` nft rules by comment, kill the two relay
  processes. Local HA entities are blind while the 46030 diversion is up; restore
  promptly after the window.

## 4. The devId gotcha: hard reset regenerates the identity

A hard reset wipes provisioning and the module onboards with a **new deviceId**
(2026-09-25: washer `d9bf16c0…` → new UUID; the old cloud record becomes
unreachable-by-reconciliation, which is why re-registering the OLD identity failed and
the module spiraled). Consequences:

- Cross-reference corpora and logs by **modelName**, never by devId.
- Every reset dirties any devId→appliance mapping you keep; expect churn.
- If an appliance must be hard-reset, plan for re-onboarding (which needs §2's health
  check anyway).

## 5. Corpus and tooling

- Live corpus (secret, contains `x-lgedm-userId`/password headers and device
  identities): `/data/relay-46030.log`, `/data/relay-47878.log` in the HAOS addon's
  persistent volume on the Pi. Format: one TLS-cleartext application record per line
  (`HH:MM:SS.mmm C->U|U->C len=N hex=…`); 47878 records are `[4B BE length][JSON]`,
  46030 records are plain HTTP text; session markers `=== … ===` separate windows.
- Repo copy (redacted: deviceIds and UUID MAC nodes replaced, length prefixes
  recomputed): `flows/wm47878-cleartext-redacted-20260925.log`. Redaction discipline
  is mandatory for anything leaving the Pi.
- Analysis tools (in `tools/`, salvaged from the ephemeral container `/tmp`):
  `wm47878_door_check.py` (distinct pump-frame values: the door-verdict tool),
  `wm46030_timeline.py` (request/response ladder with LG's real statuses).

## 6. Exit strategy: simulating the pairing without LG

For the EOL scenario (LG retires the legacy ThinQ1 cloud), the local stack can absorb
the appliance side of pairing:

- The 46030 ladder is fully simulable in standalone mode: every rung's REAL LG response
  is in the corpus (the vocabulary the server currently answers synthetically can be
  replaced with the observed shapes).
- The 47878 WM channel is decoded (`PROTOCOL.md` §4.4): a WM-capable server
  (`DevInfo`/`Alive` acks, `Mon Start`, pump ingest) is the only missing piece and is
  queued as TASK-078.
- The LG app itself is not simulable, but it is not needed either: Home Assistant (via
  the MQTT bridge) is the replacement surface. Pairing becomes: appliance boots, our
  server answers its ladder, HA shows it. No cloud, no app, no 99%.

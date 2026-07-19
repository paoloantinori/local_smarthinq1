# Roadmap — Cloud-free LG ThinQ1 → Home Assistant

**Goal.** Run my LG ThinQ1 appliances (washer `WTWN3` / type 201, dryer `RC90U2_WW` /
type 202) with **no LG cloud**, and expose them to Home Assistant: read state, and (stretch)
control them. Achieved by standing up a **local server that impersonates the LG cloud** the
appliances already talk to, then bridging that to HA.

See [`PROTOCOL.md`](PROTOCOL.md) for what we know, [`references.md`](references.md) for prior
art (esp. `anszom/rethink`), [`BACKLOG.md`](BACKLOG.md) for the task-level breakdown.

## Guiding principles

- **Capture before code.** Never design an endpoint or a decoder from a guess — capture the
  real traffic first, then implement against it. Every protocol claim traces to a capture.
- **Bridge, then sever.** Each milestone runs first in *bridge mode* (local server in front
  of the real cloud, observing) before *standalone mode* (cloud cut). Prove parity, then cut.
- **Read path before write path.** Monitoring (safe, read-only) is fully solved before we
  touch control (can start a real appliance — physical consequences).
- **One appliance at a time.** Land the washer end-to-end before generalising to the dryer.

## Milestones

| ID | Milestone | Outcome / exit criteria | Depends on |
|----|-----------|-------------------------|------------|
| **M0** | **Capture & characterise** | Reproducible capture rig documented; full lifecycle captured (boot, idle, **a full wash cycle**, app-initiated control); device inventory + endpoint catalogue complete in `PROTOCOL.md`. | — |
| **M1** | **Local fake-cloud (read)** | Python server terminates TLS, answers the mandatory bootstrap endpoints, and ingests `diagmon` pushes. Runs in bridge mode with byte-parity vs real cloud on known endpoints. | M0 |
| **M2** | **State decoding** | Washer (then dryer) `diagMonData`/monitor payloads decoded into a structured, documented state model (cycle, phase, remaining time, door, error, …), driven by per-model value maps. | M1 |
| **M3** | **Control (write)** | Command path reverse-engineered from real app traffic and reproduced locally: at minimum remote **stop/pause**; ideally start-course + option set. Gated behind explicit safety review. | M2, (capture from M0) |
| **M4** | **Home Assistant integration** | HA sees the appliances as devices with entities (sensors + any controls), via the chosen bridge (MQTT discovery or native Python integration). Installable, documented. | M2 (control entities need M3) |
| **M5** | **Cloud-free & production hardening** | Appliances fully operate with LG cloud unreachable, across reboots; server auto-starts (systemd/Docker); DNS/TLS strategy is durable and documented; onboarding guide for a new device. | M1–M4 |

### Sequencing notes

- M2 and M4's *sensor* half can proceed in parallel once M1 lands — you can ship read-only
  HA sensors before control exists. Keep control entities behind M3.
- M3 is the riskiest and least-known unit of work. **Spike it early** (capture app→device
  control during M0) even though it's delivered late, so we know whether local control is
  even feasible for these modules before investing in M4 polish.
- M5's "sever the cloud" experiment can be run cheaply during M1 (TASK-006) to de-risk the
  whole premise; the milestone is where it's made durable.

## Definition of Done (project)

Both appliances usable from HA — state always, control where feasible — with the LG cloud
firewalled off, surviving appliance and server reboots, and a written runbook that lets
Future-Me re-set-up from scratch and add a third device.

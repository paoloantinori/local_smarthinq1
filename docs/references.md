# References & Prior Art

The wheel is partly invented. Read these before writing protocol code — most of the ThinQ1
work exists somewhere. **Exhaustive prior-art research completed 2026-07-21** — see
`claudedocs/research_lg-thinq-local-control-prior-art_2026-07-21.md` for the full report.
Key finding: **the `:47878` control channel we captured is undocumented in any public source.**
No prior project documents or implements it.

## Primary — local / cloud-free (the model to follow)

- **`anszom/rethink`** — <https://github.com/anszom/rethink> — reverse-engineered,
  **fully local** LG ThinQ server (TypeScript). 168⭐. The closest thing to our end goal.
  - `rethink-cloud`: local server that emulates LG's cloud and bridges devices to **MQTT**
    for Home Assistant. Supports ACs, fridges (ThinQ2 AABB), washers/dryers (ThinQ1 + ThinQ2).
  - **Bridge mode**: forwards to the real LG cloud while observing — same concept as our rig,
    but productised (web UI, packet injection, live monitoring).
  - `rethink-setup`: provisions a device without the official app.
  - **Tools**: `packet-parser.ts` (TLV live-decoder), `packet-sender.ts`, `lgcloud-monitor.ts`
    (connects to real LG cloud to observe app→device notifications), `rethink-capture.ts`
    (JSONL capture), `mcp-server.ts` (MCP server exposing the RE toolkit to an LLM agent).
  - **Wiki** (fetch in a browser): `Adding-support-for-a-new-device`, `SetupProtocol`,
    `TLVProtocol`, `AABBProtocol`, `LCW-007` (hardware module), per-appliance pages.
  - **Does NOT document `:47878`** — handles control via ThinQ2 MQTT or `:46030` HTTP only.
  - Stack is **TypeScript**. We reimplement in Python (CLAUDE.md D-1) — read as a spec.

- **`rvanbaalen/lg-local`** — <https://github.com/rvanbaalen/lg-local> — Node.js/React/TypeScript
  local cloud replacement, inspired by rethink. TLV parser, MQTT, setup-protocol handling.
  ThinQ2 MQTT path; does NOT document `:47878`.

- **`arrudagates/ponder`** — <https://github.com/arrudagates/ponder> — "100% local" LG ThinQ
  server (105⭐). Minimal docs; draws from rethink research. Does NOT document `:47878`.

## Hardware / firmware (LCW-007 wifi module — from rethink wiki)

The wifi module inside ThinQ1 appliances is documented in the rethink wiki's
[LCW-007 page](https://github.com/anszom/rethink/wiki/LCW-007). Key facts:
- **Realtek RTL8711AM** (ARM-based Wi-Fi SoC, "ameba" family). External SPI flash.
- **Firmware is NOT encrypted** — readable via SPI flash programmer (e.g. flashrom on RPi).
- **SWD debug interface is unlocked** — full runtime access (memory, breakpoints, firmware
  modification). OpenOCD config at <https://github.com/pvvx/RTL00MP3>.
- **Debug UART console** (115200 baud, enabled via setup protocol `setCertInfo`): command shell
  with `clip debug 1`, `ptm-dump` (non-volatile key-value store), `fwinfo`, etc.
- 6-pin connector: 5V power + 3.3V UART to the appliance. Debug connector on the back.
- FCC docs: <https://fccid.io/BEJ-LCW007>. Datasheet: RTL8711AM on fccid.io.
- **Our appliances use `QC_Modem` firmware** (a variant/derivative) — re-verify which SoC.

This is a **hardware-level lead** for cases where network captures can't reveal something
(e.g. the `:47878` protocol's internal state machine, or why some appliances connect by IP).

## ThinQ2 protocol variants (different generation, but cross-reference value)

rethink documents two ThinQ2 binary protocols (NOT the ThinQ1 XML/modelJson path we use):
- **TLV protocol** — type/length/value fields with CRC16. Used by ACs.
  See rethink wiki `TLVProtocol` + `tools/packet-parser.ts` for live-decoding.
- **AABB protocol** — fixed-layout packets bracketed by `0xAA…0xBB` with a simple checksum.
  Used by ThinQ2 fridges and newer washers. rethink has `fridge_common.ts` / `washer_common.ts`.
  Many AABB devices send status as a binary block with unchanged fields = `0xFF` on writes,
  or doubled (old+new state) on notifications.
**Cross-reference**: rethink's ThinQ2 fridge byte tables (AABB) may overlap with our ThinQ1
fridge modelJson decode for common fields (temps, door state) — worth comparing.

## Protocol references (ThinQ1 — our path)

- **`sampsyo/wideq`** — <https://github.com/sampsyo/wideq> — the original reverse-engineered
  ThinQ1 client (Python). Canonical for `lgehadm` endpoints, monitor/poll, `deviceType` enum,
  `modelJson` value decoding. Does NOT document `:47878`.
- **`ollo69/ha-smartthinq-sensors`** — <https://github.com/ollo69/ha-smartthinq-sensors> —
  the mature HA integration (cloud-based, vendored wideq). Source of our refresh_token +
  modelJson access path. Does NOT document `:47878`.
- **`tinkerborg/thinq2-python`** — <https://github.com/tinkerborg/thinq2-python> — ThinQ2
  client; useful contrast for the generational protocol differences.

## SSL unpinning / capture

- **`zimmra/frida-rootbypass-and-sslunpinning-lg-thinq`** — Frida scripts to unpin the LG
  app's TLS (for capturing app↔cloud). Tested against ThinQ app 4.1.46041.
  **No longer needed for the fridge** — we capture the control channel (`:47878`) via the
  transparent-mode rig (TASK-062). May still be needed for app-side capture.

## Home Assistant integration docs

- Official HA integration developer docs — <https://developers.home-assistant.io/>.
- HA MQTT discovery — <https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery> —
  the path we chose (D-2): per-device discovery derived from decoded fields.

## Our own captures + rigs

- `capture-ctl` — SNI/DNAT rig for `:46030` (washer/dryer, hostname-connecting appliances).
- `capture-fridge.sh` + `fridge-capture-{setup,teardown}.sh` — transparent-mode rig
  (route-as-next-hop, no-SNI appliances). See `docs/NETWORK_SETUP.md`.
- `fridge-47878-capture-{setup,teardown}.sh` — transparent-mode rig for `:47878`
  (the control channel — raw TCP, not TLS).
- `fridge-sever-test-{setup,teardown}.sh` — standalone sever test rig.
- Captures: `flows/washer-cycle-20260719.log`, `flows/dryer-cycle-20260720.log`,
  `flows/fridge-20260721.log`, `flows/fridge-47878-control-20260721.log` (the control capture).
- `lg_portfix.py` — mitmproxy addon (upstream 443→46030). `hosts`/`dns_rewrite.txt` — abandoned.

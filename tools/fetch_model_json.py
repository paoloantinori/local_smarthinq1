#!/usr/bin/env python3
"""Fetch a device's modelJson from LG for full state decoding (M2/TASK-020 completion).

The modelJson defines the byte layout (Monitoring.protocol) + friendly-name maps (Value) for a
specific model. It's fetched from a public object URL listed in the device record, but finding
that URL requires an authenticated LG session. This script uses sampsyo/wideq for the auth.

SETUP (one-time):
  pip install git+https://github.com/sampsyo/wideq
  # Get a refresh_token by running wideq's interactive login once (it opens a browser):
  python -m wideq -c <country> -l <language>      # e.g. WW / en-US, or IT / it-IT

USAGE:
  LG_REFRESH_TOKEN=<refresh_token> python tools/fetch_model_json.py <device_id> [country] [language] \\
      > server/models/washer_wtwn3.model.json
  # device_id is the appliance's deviceId (e.g. d9bf16c0-... for the washer).
  # The token is read from the env var so it never appears in argv/ps.

Then decode captured state with the real map:
  from server.models import model_json
  model = json.load(open("server/models/washer_wtwn3.model.json"))
  model_json.decode_with_model_json(mondata_bytes, model)   # → {State, Remain_Time, Course, ...}

ALTERNATIVE (no wideq): if you can read the device's `modelJsonUrl` another way (the LG app,
a captured session), fetch it directly — the URL itself needs no auth:
  curl -s <modelJsonUrl> > server/models/washer_wtwn3.model.json
"""
import json
import os
import sys


def main() -> None:
    token = os.environ.get("LG_REFRESH_TOKEN")
    if not token or len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    device_id = sys.argv[1]
    country = sys.argv[2] if len(sys.argv) > 2 else "WW"
    language = sys.argv[3] if len(sys.argv) > 3 else "en-US"
    try:
        from wideq import Client  # type: ignore[import-not-found]  # optional dep
    except ImportError:
        sys.exit("wideq not installed. Run: pip install git+https://github.com/sampsyo/wideq")
    client = Client.from_token(token, country, language)
    device = client.get_device(device_id)
    if not device:
        sys.exit(f"device {device_id} not found in this LG account")
    model = client.model_info(device)
    json.dump(model.data, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()

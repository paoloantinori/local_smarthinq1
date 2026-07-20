#!/usr/bin/env python3
"""Fetch a device's modelJson from LG and cache it for the multi-model decoder (TASK-060/020).

The modelJson defines a model's byte layout (``Monitoring.protocol``) + friendly-name maps
(``Value``). ``server/models/registry.py`` resolves it per ``modelName`` and
``server/models/model_json.py`` applies it to decode that device's binary state generically.

SETUP (one-time):
  pip install git+https://github.com/sampsyo/wideq
  # Get a refresh_token by running wideq's interactive login once (it opens a browser):
  python -m wideq -c <country> -l <language>      # e.g. WW / en-US, or IT / it-IT

USAGE (writes to the cache by default; status goes to stderr, stdout stays clean):
  LG_REFRESH_TOKEN=<refresh_token> python tools/fetch_model_json.py <device_id> [country] [language]
  → data/models/<modelName>.model.json   (modelName is read from the fetched record)
The token is read from the env var so it never appears in argv/ps. The cache file lives under
``/data/`` (git-ignored) — a user's own modelJson is never committed. The author's washer
fixture is committed separately at ``server/models/washer_wtwn3.model.json``.

Escape hatch (emit the JSON on stdout — to pipe, inspect, or commit a fixture for your device):
  LG_REFRESH_TOKEN=<refresh_token> python tools/fetch_model_json.py <device_id> --stdout | jq .

ALTERNATIVE (no wideq): if you can read the device's ``modelJsonUrl`` another way (the LG
app, a captured session), fetch it directly — the URL itself needs no auth:
  curl -s <modelJsonUrl> > data/models/<modelName>.model.json
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, REPO_ROOT)

from server.models.registry import cache_path  # noqa: E402  (single owner of the cache path)


def main() -> None:
    token = os.environ.get("LG_REFRESH_TOKEN")
    positional = [a for a in sys.argv[1:] if not a.startswith("-")]
    to_stdout = "--stdout" in sys.argv
    if not token or len(positional) < 1:
        print(__doc__)
        sys.exit(1)
    device_id = positional[0]
    country = positional[1] if len(positional) > 1 else "WW"
    language = positional[2] if len(positional) > 2 else "en-US"
    try:
        from wideq import Client  # type: ignore[import-not-found]  # optional dep
    except ImportError:
        sys.exit("wideq not installed. Run: pip install git+https://github.com/sampsyo/wideq")
    client = Client.from_token(token, country, language)
    device = client.get_device(device_id)
    if not device:
        sys.exit(f"device {device_id} not found in this LG account")
    model = client.model_info(device)
    data = model.data
    # modelName is top-level for some classes (washer) but nested under Info for others
    # (fridge) — read both so the cache file is keyed correctly for any appliance.
    model_name = (data.get("modelName") or (data.get("Info") or {}).get("modelName") or "").strip()
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if to_stdout:
        sys.stdout.write(payload)
        return
    if not model_name:
        sys.exit("could not read modelName from the modelJson; pass --stdout to emit anyway")
    out = cache_path(model_name)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(payload)
    sys.stderr.write(f"wrote {out}\n")


if __name__ == "__main__":
    main()

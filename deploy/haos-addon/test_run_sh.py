"""Test run.sh's options -> LGM_* env-var translation (TASK-071).

The safety-critical assertion: allow_control=false (or absent) must NOT export
LGM_ALLOW_CONTROL, because server/control_channel.py reads
`os.environ.get("LGM_ALLOW_CONTROL","") != ""` (any non-empty value, including "0",
means ON, which would enable physical actuation, violating CLAUDE.md #5).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ADDON_DIR = Path(__file__).parent
ROOT = ADDON_DIR.parent.parent
sys.path.insert(0, str(ROOT))


def _run_sh_env(options: dict) -> dict[str, str]:
    """Run run.sh's export logic with a fake options.json and capture the LGM_* env vars.

    Stubs the bashio functions. Note: bash does NOT word-split a function name on '::'
    (verified: `bashio::config "x"` looks for a function literally named bashio::config, with
    $1=x; it does not fall through to a `bashio()` function). So the stub defines each
    bashio::* variant by its full dotted name. Sources run.sh with `exec python` stripped, and
    prints the resulting LGM_* env.
    """
    opts_path = ADDON_DIR / "_test_options.json"
    opts_path.write_text(json.dumps({"options": options}))
    stub = ADDON_DIR / "_test_stub.sh"
    # bashio invokes `bashio::config 'mode'` -> bash word-splits so the function gets
    # $1=config, $2=mode. And `bashio::config.true 'x'` -> $1=config.true, $2=x. The function
    # name must contain the literal '::' (bash allows ':' in identifiers); reading $1 as the
    # key (wrong) silently never matches and every assertion passes vacuously.
    stub.write_text(rf"""
    set -e
    OPTS={opts_path}
    # bash does NOT word-split a function name like 'bashio::config' on '::'. It looks for a
    # function literally named that. So we must define each bashio::* variant with the literal
    # '::' in its name, taking the key as $1.
    bashio::config()        {{ jq -r '.options.'"$1"' // empty' "$OPTS"; }}
    bashio::config.true()   {{ [ "$(jq -r '.options.'"$1"' // false' "$OPTS")" = "true" ]; }}
    bashio::log.info()      {{ :; }}
    bashio::log.warning()   {{ :; }}
    # Strip the `exec python` and `cd /app` lines (don't launch the server / change dir; both
    # are container-only). The cert-gen block no-ops safely when /app/gen-cert.sh is absent
    # (run.sh guards on it). Cert gen itself is validated by the Docker smoke run in TASK-072.
    sed -e '/^exec /d' -e '/^cd \/app$/d' {ADDON_DIR}/run.sh > {ADDON_DIR}/_run_exports.sh
    set +e && source {ADDON_DIR}/_run_exports.sh
    env -0 | tr '\0' '\n' | grep '^LGM_' || true
    """)
    out = subprocess.run(["bash", str(stub)], capture_output=True, text=True,
                         env={**os.environ, "PATH": os.environ["PATH"]})
    if out.returncode != 0 and not out.stdout:
        raise AssertionError(f"stub failed (rc={out.returncode}):\n{out.stderr}")
    env: dict[str, str] = {}
    for line in out.stdout.splitlines():
        if line.startswith("LGM_") and "=" in line:
            k, _, v = line.partition("=")
            env[k] = v
    return env


def test_basic_options_translated() -> None:
    """Broken-stub detector: a real option must resolve to its non-empty value (not '')."""
    env = _run_sh_env({
        "mqtt_host": "127.0.0.1", "mqtt_port": 1883, "mqtt_user": "lg",
        "mqtt_password": "secret", "mode": "standalone", "allow_control": False,
        "upstream_host": "eic.lgthinq.com", "upstream_port": 46030,
    })
    assert env["LGM_MQTT_HOST"] == "127.0.0.1", env
    assert env["LGM_MQTT_PORT"] == "1883", env
    assert env["LGM_MQTT_USER"] == "lg", env
    assert env["LGM_MQTT_PASS"] == "secret", env
    assert env["LGM_MODE"] == "standalone", env
    assert env["LGM_HOST"] == "0.0.0.0" and env["LGM_PORT"] == "46030", env
    assert env["LGM_CONTROL_PORT"] == "47878", env
    assert env["LGM_STATE_DIR"] == "/data", env
    assert env["LGM_CERT"] == "/data/cert.pem" and env["LGM_KEY"] == "/data/key.pem", env


def test_allow_control_false_leaves_gate_unset() -> None:
    """SAFETY (CLAUDE.md #5): allow_control=false must leave LGM_ALLOW_CONTROL at its empty
    default. Asserting == '' (not `not in env`) locks the `!= ""` semantics in
    control_channel.py:167, so exporting '0' would fail this test."""
    env = _run_sh_env({"mqtt_host": "127.0.0.1", "mqtt_port": 1883, "mqtt_user": "x",
                       "mqtt_password": "y", "mode": "standalone", "allow_control": False})
    assert env.get("LGM_ALLOW_CONTROL", "") == "", (
        f"LGM_ALLOW_CONTROL must be empty-default when allow_control=false; got {env.get('LGM_ALLOW_CONTROL')!r}"
    )


def test_allow_control_absent_leaves_gate_unset() -> None:
    """The realistic HAOS default: the key is omitted entirely from options.json. bashio::config.true
    on a missing key returns non-zero, so the gate must stay unset."""
    env = _run_sh_env({"mqtt_host": "127.0.0.1", "mqtt_port": 1883, "mqtt_user": "x",
                       "mqtt_password": "y", "mode": "standalone"})
    assert env.get("LGM_ALLOW_CONTROL", "") == "", env


def test_allow_control_true_exports_gate() -> None:
    env = _run_sh_env({"mqtt_host": "127.0.0.1", "mqtt_port": 1883, "mqtt_user": "x",
                       "mqtt_password": "y", "mode": "standalone", "allow_control": True})
    assert env.get("LGM_ALLOW_CONTROL") == "1", env


def test_bridge_mode_exports_upstream() -> None:
    env = _run_sh_env({"mqtt_host": "127.0.0.1", "mqtt_port": 1883, "mqtt_user": "x",
                       "mqtt_password": "y", "mode": "bridge", "allow_control": False,
                       "upstream_host": "eic.lgthinq.com", "upstream_port": 46030})
    assert env["LGM_MODE"] == "bridge", env
    assert env["LGM_UPSTREAM_HOST"] == "eic.lgthinq.com", env
    assert env["LGM_UPSTREAM_PORT"] == "46030", env


def test_mqtt_host_unset_leaves_mqtt_env_unset() -> None:
    """If mqtt_host is unset, bashio returns 'null' (truthy). run.sh must NOT export it as
    'null', or server/mqtt_bridge.py would try connect('null',1883) instead of the intended
    MQTT-off fast path. Empty user/pass are fine to export."""
    env = _run_sh_env({"mode": "standalone", "allow_control": False})
    assert env.get("LGM_MQTT_HOST", "") != "null", (
        f"LGM_MQTT_HOST must not be 'null'; got {env.get('LGM_MQTT_HOST')!r}"
    )


if __name__ == "__main__":
    for fn in (test_basic_options_translated, test_allow_control_false_leaves_gate_unset,
               test_allow_control_absent_leaves_gate_unset, test_allow_control_true_exports_gate,
               test_bridge_mode_exports_upstream, test_mqtt_host_unset_leaves_mqtt_env_unset):
        fn(); print(f"PASS {fn.__name__}")
    print("\nrun.sh env-translation tests passed.")

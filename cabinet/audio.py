#!/usr/bin/env python3
"""Audio policy engine (AUD-1, AUD-2, spec §5.6).

Enforces per-class volume/mute on PipeWire (via the pulse compatibility
layer: pactl -f json). Classes are assigned at spawn time by cabd through
the PULSE_PROP_media.role env var; anything without a role is class
"system". Policy is applied on boot, on every state.sync, and reactively
when new sink-inputs appear (pactl subscribe).
"""
import json
import subprocess

DEFAULT_POLICY = {
    "master": 70,
    "classes": {
        "games": {"volume": 100, "muted": False},
        "trailers": {"volume": 100, "muted": True},
        "screensaver": {"volume": 100, "muted": True},
        "system": {"volume": 50, "muted": False},
    },
}
KNOWN_CLASSES = ("games", "trailers", "screensaver", "system")


def _pactl(*args):
    return subprocess.run(["pactl", *args], capture_output=True, text=True)


def policy_from_bundle(bundle):
    data = bundle.get("audio.yaml") or {}
    if not isinstance(data, dict):
        return DEFAULT_POLICY
    merged = {"master": data.get("master", DEFAULT_POLICY["master"]),
              "classes": {}}
    for cls in KNOWN_CLASSES:
        base = dict(DEFAULT_POLICY["classes"][cls])
        base.update((data.get("classes") or {}).get(cls) or {})
        merged["classes"][cls] = base
    return merged


def list_sink_inputs():
    r = _pactl("-f", "json", "list", "sink-inputs")
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout)
    except ValueError:
        return []


def classify(sink_input):
    role = (sink_input.get("properties") or {}).get("media.role", "")
    return role if role in KNOWN_CLASSES else "system"


def apply_to_input(sink_input, policy):
    cls = classify(sink_input)
    cfg = policy["classes"][cls]
    idx = str(sink_input["index"])
    _pactl("set-sink-input-volume", idx, f"{int(cfg['volume'])}%")
    _pactl("set-sink-input-mute", idx, "1" if cfg["muted"] else "0")
    return cls


def apply_master(policy):
    _pactl("set-sink-volume", "@DEFAULT_SINK@", f"{int(policy['master'])}%")


def apply_all(policy):
    """Returns {index: class} for logging/tests."""
    apply_master(policy)
    return {si["index"]: apply_to_input(si, policy) for si in list_sink_inputs()}

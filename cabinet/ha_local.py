"""Publish the cabinet directly to its LAN Home Assistant MQTT broker."""
import asyncio
import copy
import json

try:
    import integrations
except ImportError:  # source checkout; install.sh copies the shared module
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
    import integrations


def command_method(kind, payload):
    if kind == "screensaver":
        return {"ON": "screensaver.on", "OFF": "screensaver.off"}.get(payload)
    return {"turn_off": "game.kill", "stop": "game.kill"}.get(payload)


async def run(get_config, get_state, get_health, dispatch, log):
    loop = asyncio.get_running_loop()
    publisher = None
    previous = None

    def on_command(kind, payload, leaf):
        method = command_method(kind, payload)
        if method:
            loop.call_soon_threadsafe(lambda: asyncio.create_task(dispatch(method)))

    try:
        while True:
            try:
                cfg = copy.deepcopy(get_config() or {})
                identity = json.dumps(cfg, sort_keys=True)
                if identity != previous:
                    if publisher:
                        await asyncio.to_thread(publisher.disconnect)
                    previous = identity
                    ha = cfg.setdefault("homeassistant", {})
                    ha["enabled"] = bool(ha.get("cabinet_enabled"))
                    publisher = integrations.HomeAssistantPublisher(cfg, log, on_command)
                if publisher.enabled:
                    if publisher.client is None:
                        await asyncio.to_thread(publisher.connect)
                    snap = integrations.snapshot_from(get_state(), True,
                                                      cfg.get("public_base_url"))
                    health = await asyncio.to_thread(get_health)
                    await asyncio.to_thread(publisher.publish, snap, health)
            except Exception as exc:
                log(f"local Home Assistant: {type(exc).__name__}")
            await asyncio.sleep(5)
    finally:
        if publisher:
            await asyncio.to_thread(publisher.disconnect)

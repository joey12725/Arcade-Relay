#!/usr/bin/env python3
"""Outbound integrations: Discord presence and Home Assistant.

Both want the same thing — "what is the cabinet playing right now" — so
there is one snapshot type and one change detector, and publishers subscribe
to it. Adding a third consumer later means writing a publisher, not another
poller.

  Discord         — webhook post when a game starts / stops. A webhook, not
                    Rich Presence: Rich Presence talks over a local IPC socket
                    to a running Discord *desktop client*, and the cabinet
                    does not have one. A webhook posts from the server, works
                    through the tunnel, and needs no bot account.
  Home Assistant  — two transports, because neither one alone does the job:
                      MQTT discovery for the sensors, the screensaver switch,
                      the stop button and connectivity. These become real,
                      controllable entities and survive an HA restart.
                      REST for the media_player. HA's MQTT integration has NO
                      media_player platform, so a discovery topic for one is
                      accepted by the broker and then ignored — the media card
                      has to be pushed to /api/states instead.

Everything here is optional and off by default. A misconfigured integration
must never affect the cabinet: publishers swallow their own errors, log, and
the next state change tries again.
"""
import asyncio
import json
import os
import pathlib
import time

import aiohttp
import yaml

CONFIG_REL = "integrations.yaml"

DEFAULTS = {
    "discord": {
        "enabled": False,
        "webhook_url": "",
        "post_on_start": True,
        "post_on_stop": False,
        "username": "CAB-01",
        "mention": "",
    },
    "homeassistant": {
        "enabled": False,
        "cabinet_enabled": False,
        "mqtt_host": "",
        "mqtt_port": 1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "discovery_prefix": "homeassistant",
        "node_id": "arcade_cabinet",
        "device_name": "Arcade Cabinet",
        # HA places the device here on first discovery (user moves win later)
        "suggested_area": "",
        # REST is what produces the media_player entity; MQTT has no such
        # platform. Optional — the MQTT sensors work without it.
        "rest_url": "",
        "rest_token": "",
    },
    # Where a browser (or Home Assistant) can reach this server for box art.
    # Art URLs are absolute in both integrations, so without this they are
    # unresolvable from anywhere but localhost.
    "public_base_url": "",
}


def load_config(state_dir):
    p = pathlib.Path(state_dir) / CONFIG_REL
    data = {}
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
    out = json.loads(json.dumps(DEFAULTS))     # deep copy
    for section, values in data.items():
        if isinstance(values, dict) and isinstance(out.get(section), dict):
            out[section].update(values)
        else:
            out[section] = values
    return out


def redact(cfg):
    """Config safe to hand to the web UI. Secrets become a sentinel so the
    form can show 'set' without ever shipping the value to a browser."""
    out = json.loads(json.dumps(cfg))
    for section, key in (("discord", "webhook_url"),
                         ("homeassistant", "mqtt_password"),
                         ("homeassistant", "rest_token")):
        if out.get(section, {}).get(key):
            out[section][key] = "••••••••"
            out[section][key + "_set"] = True
        else:
            out.setdefault(section, {})[key + "_set"] = False
    return out


class Snapshot:
    """What the cabinet is doing, in the shape both publishers want."""

    __slots__ = ("online", "running", "system", "title", "started_at",
                 "elapsed_s", "screensaver", "lifeline", "art_url")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def state(self):
        if not self.online:
            return "off"
        if self.running:
            return "playing"
        if self.screensaver:
            return "idle"
        return "on"

    def identity(self):
        """What counts as 'the same session'. Elapsed time deliberately
        excluded — otherwise every heartbeat looks like a change and Discord
        gets a message per beat."""
        return (self.online, self.running, self.screensaver, self.lifeline)

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__} | {
            "state": self.state}


def snapshot_from(cab_state, online, base_url=None, art_path=None):
    running = cab_state.get("running")
    title = system = None
    if running:
        parts = [p for p in str(running).split("/") if p]
        title = parts[-1] if parts else str(running)
        system = cab_state.get("system") or (parts[-2] if len(parts) > 1 else None)
        # strip the extension for display; nobody wants "(USA).sfc" on Discord
        title = os.path.splitext(title)[0]
    art = None
    if base_url and art_path:
        art = base_url.rstrip("/") + art_path
    return Snapshot(online=online, running=running, system=system, title=title,
                    started_at=cab_state.get("started_at"),
                    elapsed_s=cab_state.get("elapsed_s"),
                    screensaver=bool(cab_state.get("screensaver")),
                    lifeline=bool(cab_state.get("lifeline")),
                    art_url=art)


def human_elapsed(seconds):
    if seconds is None:
        return None
    s = int(seconds)
    h, m = divmod(s // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s % 60:02d}s"


# ----------------------------------------------------------------- discord

class DiscordPublisher:
    """Posts an embed to a channel webhook on session start / stop."""

    def __init__(self, cfg, log):
        self.cfg = cfg.get("discord") or {}
        self.log = log

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled") and self.cfg.get("webhook_url"))

    def _embed(self, snap, stopping, session):
        if stopping:
            title = "Stopped playing"
            desc = (f"**{session.title}**" if session and session.title
                    else "Session ended")
            colour = 0x9D86B3
        else:
            title = "Now playing"
            desc = f"**{snap.title}**"
            colour = 0x0B3FD1
        embed = {"title": title, "description": desc, "color": colour,
                 "footer": {"text": "CAB-01"}}
        fields = []
        src = (session if stopping else snap)
        if src and src.system:
            fields.append({"name": "System", "value": src.system.upper(),
                           "inline": True})
        if stopping and session and session.elapsed_s:
            fields.append({"name": "Played for",
                           "value": human_elapsed(session.elapsed_s),
                           "inline": True})
        if fields:
            embed["fields"] = fields
        if not stopping and snap.art_url:
            embed["thumbnail"] = {"url": snap.art_url}
        return embed

    async def publish(self, snap, prev, session=None):
        """`prev` decides whether a transition happened; `session` carries the
        session's own last-known detail.

        They are different snapshots on purpose. `prev` is the last state
        *change*, so during a two-hour game it stays pinned at the moment the
        game started — reading play time off it reports a couple of seconds.
        `session` is the most recent heartbeat that still had a game running,
        which is where the real elapsed time lives.
        """
        if not self.enabled:
            return
        started = bool(snap.running) and not (prev and prev.running)
        stopped = bool(prev and prev.running) and not snap.running
        if started and not self.cfg.get("post_on_start", True):
            return
        if stopped and not self.cfg.get("post_on_stop", False):
            return
        if not (started or stopped):
            return
        body = {"username": self.cfg.get("username") or "CAB-01",
                "embeds": [self._embed(snap, stopped, session or prev)]}
        if self.cfg.get("mention"):
            body["content"] = self.cfg["mention"]
        await self._post(body)

    async def _post(self, body):
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(self.cfg["webhook_url"], json=body) as r:
                    if r.status >= 400:
                        text = (await r.text())[:200]
                        self.log(f"discord webhook {r.status}: {text}")
                        return False
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.log(f"discord webhook failed: {e!r}")
            return False

    async def test(self):
        if not self.cfg.get("webhook_url"):
            raise ValueError("no webhook URL configured")
        return await self._post({
            "username": self.cfg.get("username") or "CAB-01",
            "embeds": [{"title": "Arcade cabinet connected",
                        "description": "Test message from the control plane.",
                        "color": 0x1FA83C}]})


# --------------------------------------------------------- home assistant

class HomeAssistantPublisher:
    """MQTT discovery: one media_player plus sensors, and a command topic.

    Discovery payloads are retained so the entities survive an HA restart;
    availability is an LWT so HA shows the cabinet as unavailable if arcaded
    dies rather than freezing on the last state it saw.
    """

    def __init__(self, cfg, log, on_command=None):
        self.cfg = cfg.get("homeassistant") or {}
        self.base_url = cfg.get("public_base_url") or ""
        self.log = log
        self.on_command = on_command
        self.client = None
        self._announced = False

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled") and self.cfg.get("mqtt_host"))

    @property
    def node(self):
        return self.cfg.get("node_id") or "arcade_cabinet"

    @property
    def prefix(self):
        return self.cfg.get("discovery_prefix") or "homeassistant"

    def topic(self, leaf):
        return f"arcade/{self.node}/{leaf}"

    def _device(self):
        dev = {"identifiers": [self.node],
               "name": self.cfg.get("device_name") or "Arcade Cabinet",
               "manufacturer": "arcade", "model": "CAB-01"}
        if self.cfg.get("suggested_area"):
            dev["suggested_area"] = self.cfg["suggested_area"]
        return dev

    def _discovery(self):
        """(topic, payload) pairs describing every entity we expose.

        Note what is NOT here: a media_player. Home Assistant's MQTT
        integration has no media_player platform — a
        `homeassistant/media_player/.../config` topic is accepted by the
        broker and then silently ignored by HA, which looks like it worked
        right up until you go looking for the entity. The media card comes
        from HomeAssistantRestPublisher instead; everything below is a
        platform MQTT discovery really supports.
        """
        dev, avail = self._device(), [{"topic": self.topic("availability")}]
        state_t = self.topic("state")
        out = []
        # A button, so stopping the game is reachable from a dashboard even
        # without the REST media player.
        out.append((
            f"{self.prefix}/button/{self.node}/stop/config",
            {"name": "Stop game", "unique_id": f"{self.node}_stop",
             "device": dev, "availability": avail,
             "command_topic": self.topic("command"),
             "payload_press": "turn_off", "icon": "mdi:stop"},
        ))
        # Screensaver as a switch, so it is controllable from a dashboard.
        out.append((
            f"{self.prefix}/switch/{self.node}/screensaver/config",
            {"name": "Screensaver", "unique_id": f"{self.node}_screensaver",
             "device": dev, "availability": avail,
             "state_topic": state_t,
             "value_template": "{{ 'ON' if value_json.screensaver else 'OFF' }}",
             "command_topic": self.topic("screensaver/set"),
             "icon": "mdi:television-shimmer"},
        ))
        for key, name, unit, icon, cls in (
            ("title", "Now playing", None, "mdi:gamepad-variant", None),
            ("system", "System", None, "mdi:chip", None),
            ("elapsed_s", "Play time", "s", "mdi:timer-outline", "duration"),
            ("cpu_pct", "CPU", "%", "mdi:cpu-64-bit", None),
            ("gpu_pct", "GPU", "%", "mdi:expansion-card", None),
            ("mem_pct", "Memory", "%", "mdi:memory", None),
            ("cpu_temp_c", "CPU temperature", "°C", None, "temperature"),
            ("disk_used_pct", "Disk used", "%", "mdi:harddisk", None),
        ):
            payload = {"name": name, "unique_id": f"{self.node}_{key}",
                       "device": dev, "availability": avail,
                       "state_topic": state_t,
                       "value_template": "{{ value_json.%s }}" % key}
            if unit:
                payload["unit_of_measurement"] = unit
            if icon:
                payload["icon"] = icon
            if cls:
                payload["device_class"] = cls
            if unit == "%" or cls == "temperature":
                payload["state_class"] = "measurement"
            out.append((f"{self.prefix}/sensor/{self.node}/{key}/config", payload))
        out.append((
            f"{self.prefix}/binary_sensor/{self.node}/online/config",
            {"name": "Cabinet online", "unique_id": f"{self.node}_online",
             "device": dev, "availability": avail, "state_topic": state_t,
             "value_template": "{{ 'ON' if value_json.online else 'OFF' }}",
             "device_class": "connectivity"},
        ))
        return out

    def connect(self):
        """Blocking connect — callers run it in a thread."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.log("home assistant: python3-paho-mqtt not installed")
            return False
        try:
            client = mqtt.Client(client_id=f"arcaded-{self.node}")
            if self.cfg.get("mqtt_username"):
                client.username_pw_set(self.cfg["mqtt_username"],
                                       self.cfg.get("mqtt_password") or None)
            # Last will: if arcaded dies, HA marks the cabinet unavailable
            # instead of showing whatever it was playing forever.
            client.will_set(self.topic("availability"), "offline", retain=True)
            client.on_message = self._on_message
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            self.client = client
            client.connect(self.cfg["mqtt_host"],
                           int(self.cfg.get("mqtt_port") or 1883), keepalive=60)
            client.loop_start()
            self._announced = False
            return True
        except Exception as e:  # noqa: BLE001 — a broker problem is not ours
            self.client = None
            self.log(f"home assistant: mqtt connect failed: {e!r}")
            return False

    def _on_connect(self, client, _userdata, _flags, rc):
        if rc != 0:
            self.log(f"home assistant: broker rejected connection ({rc})")
            return
        self._announced = False
        client.subscribe(self.topic("command"))
        client.subscribe(self.topic("screensaver/set"))
        client.subscribe(f"{self.prefix}/status")
        client.publish(self.topic("availability"), "online", qos=1, retain=True)
        self.log("home assistant: MQTT connected")

    def _on_disconnect(self, _client, _userdata, _rc):
        self._announced = False

    def _on_message(self, _client, _userdata, msg):
        if msg.topic == f"{self.prefix}/status":
            self._announced = False
            return
        # Never replay a stale retained stop/screensaver command on reconnect.
        if msg.retain:
            return
        if self.on_command is None:
            return
        try:
            payload = msg.payload.decode(errors="replace").strip()
        except Exception:  # noqa: BLE001
            return
        leaf = msg.topic.rsplit("/", 1)[-1]
        kind = "screensaver" if msg.topic.endswith("screensaver/set") else "media"
        self.on_command(kind, payload, leaf)

    def announce(self):
        if self.client is None or not self.client.is_connected() or self._announced:
            return
        for topic, payload in self._discovery():
            self.client.publish(topic, json.dumps(payload), qos=1, retain=True)
        self._announced = True
        self.log(f"home assistant: announced {len(self._discovery())} entities")

    def publish(self, snap, health=None):
        if self.client is None:
            return
        self.announce()
        m = ((health or {}).get("metrics") or {}).get("current") or {}
        mem_pct = None
        if m.get("mem_total"):
            mem_pct = round(100.0 * (m.get("mem_used") or 0) / m["mem_total"], 1)
        body = snap.as_dict()
        body.update({
            "cpu_pct": None if m.get("cpu_pct") is None else round(m["cpu_pct"], 1),
            "gpu_pct": m.get("gpu_pct"),
            "mem_pct": mem_pct,
            "cpu_temp_c": m.get("cpu_temp_c"),
            "disk_used_pct": (health or {}).get("disk_used_pct"),
        })
        # HA's media_player wants these specific attribute names
        body["media_title"] = snap.title
        body["media_content_type"] = "game"
        body["source"] = snap.system
        if snap.art_url:
            body["entity_picture"] = snap.art_url
        self.client.publish(self.topic("state"), json.dumps(body), retain=True)

    def disconnect(self):
        if self.client is None:
            return
        try:
            self.client.publish(self.topic("availability"), "offline", retain=True)
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.client = None


# ------------------------------------------------- home assistant (media)

class HomeAssistantRestPublisher:
    """Pushes a real `media_player.<node>` entity into Home Assistant.

    Exists because MQTT discovery has no media_player platform (see
    HomeAssistantPublisher._discovery). HA's REST API will happily hold a
    state for any entity_id, and its media control card renders from the
    standard attributes — media_title, entity_picture, media_duration — so
    this is what actually produces "a media device in Home Assistant".

    Two honest limitations, both documented in the UI:
      - a REST-pushed entity is not backed by an integration, so HA cannot
        call services on it. Control comes from the MQTT button/switch.
      - the state is lost on an HA restart. We re-push on every heartbeat,
        so it reappears within a few seconds.
    """

    # media_player.SUPPORT_* bitmask; PAUSE|STOP|PLAY|TURN_OFF
    SUPPORTED = 1 | 4096 | 16384 | 256

    def __init__(self, cfg, log):
        self.cfg = cfg.get("homeassistant") or {}
        self.log = log

    @property
    def enabled(self):
        return bool(self.cfg.get("enabled") and self.cfg.get("rest_url")
                    and self.cfg.get("rest_token"))

    @property
    def entity_id(self):
        node = self.cfg.get("node_id") or "arcade_cabinet"
        return f"media_player.{node}"

    def _payload(self, snap, health=None):
        attrs = {
            "friendly_name": self.cfg.get("device_name") or "Arcade Cabinet",
            "media_content_type": "game",
            "supported_features": self.SUPPORTED,
            "device_class": "receiver",
        }
        if snap.title:
            attrs["media_title"] = snap.title
        if snap.system:
            attrs["source"] = snap.system.upper()
            attrs["media_series_title"] = snap.system.upper()
        if snap.art_url:
            attrs["entity_picture"] = snap.art_url
        if snap.elapsed_s is not None:
            # HA renders a running position when both are present and
            # media_position_updated_at is fresh
            attrs["media_position"] = int(snap.elapsed_s)
            attrs["media_position_updated_at"] = (
                time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
        return {"state": snap.state, "attributes": attrs}

    async def publish(self, snap, health=None):
        if not self.enabled:
            return False
        url = (self.cfg["rest_url"].rstrip("/")
               + "/api/states/" + self.entity_id)
        headers = {"Authorization": "Bearer " + self.cfg["rest_token"],
                   "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, json=self._payload(snap, health),
                                  headers=headers) as r:
                    if r.status >= 400:
                        self.log(f"home assistant rest {r.status}: "
                                 + (await r.text())[:200])
                        return False
            return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.log(f"home assistant rest failed: {e!r}")
            return False


# -------------------------------------------------------------- the manager

class Manager:
    """Owns both publishers and the change detection between them."""

    def __init__(self, state_dir, log, command_handler=None):
        self.state_dir = state_dir
        self.log = log
        self.command_handler = command_handler
        self.cfg = load_config(state_dir)
        self.prev = None          # last state *change*
        self.last_running = None  # last tick that had a game up
        self._lock = asyncio.Lock()
        self._loop = None          # set in start(); _on_command runs off-loop
        self.last_error = None
        self.discord = DiscordPublisher(self.cfg, log)
        self.ha = HomeAssistantPublisher(self.cfg, log, self._on_command)
        self.ha_rest = HomeAssistantRestPublisher(self.cfg, log)

    def _on_command(self, kind, payload, leaf):
        # Runs on paho's network thread, so the coroutine has to be handed
        # back to the event loop rather than awaited here.
        handler = self.command_handler
        if handler is None:
            return
        try:
            loop = self._loop or asyncio.get_event_loop()
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(handler(kind, payload, leaf)))
        except RuntimeError:
            pass          # loop gone (shutdown); nothing to do

    async def reload(self):
        """Re-read config and reconnect anything whose settings changed."""
        async with self._lock:
            self._loop = asyncio.get_running_loop()
            self.cfg = load_config(self.state_dir)
            self.discord = DiscordPublisher(self.cfg, self.log)
            was = self.ha
            was.disconnect()
            self.ha = HomeAssistantPublisher(self.cfg, self.log, self._on_command)
            self.ha_rest = HomeAssistantRestPublisher(self.cfg, self.log)
            if self.ha.enabled:
                await asyncio.to_thread(self.ha.connect)
            self.prev = None      # force a fresh publish under the new config
            self.last_running = None

    async def start(self):
        self._loop = asyncio.get_running_loop()
        if self.ha.enabled:
            await asyncio.to_thread(self.ha.connect)

    async def update(self, snap, health=None):
        """Called whenever cabinet state may have changed."""
        changed = self.prev is None or snap.identity() != self.prev.identity()
        try:
            if self.ha.client is not None:
                # HA gets every tick: elapsed time and the metric sensors move
                # continuously, and MQTT retained state is cheap.
                await asyncio.to_thread(self.ha.publish, snap, health)
            elif self.ha.enabled:
                await asyncio.to_thread(self.ha.connect)
            if self.ha_rest.enabled:
                await self.ha_rest.publish(snap, health)
            if changed:
                await self.discord.publish(snap, self.prev, self.last_running)
        except Exception as e:  # noqa: BLE001 — telemetry never breaks control
            self.last_error = repr(e)
            self.log(f"integrations update failed: {e!r}")
        if changed:
            self.prev = snap
        # Tracked every tick, not just on change: this is what supplies the
        # final play time when the session ends.
        if snap.running:
            self.last_running = snap

    def status(self):
        return {
            "discord": {"enabled": self.discord.enabled},
            "homeassistant": {"enabled": self.ha.enabled,
                              "connected": self.ha.client is not None,
                              "node_id": self.ha.node,
                              "media_player": self.ha_rest.enabled,
                              "entity_id": self.ha_rest.entity_id},
            "last_error": self.last_error,
            "updated": time.time(),
        }

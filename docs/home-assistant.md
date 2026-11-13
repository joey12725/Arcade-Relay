# Home Assistant and Discord

Both integrations are optional and off by default. Configure them in the web
UI's **Broadcast** tab or through the integrations API; the config lives in the
state repo as `integrations.yaml` like everything else, with secrets masked
when the UI reads them back.

## Home Assistant

The cabinet shows up as one device, `arcade_cabinet`, with eleven entities:

| Entity | Kind |
| --- | --- |
| Stop game | button |
| Screensaver | switch |
| Cabinet online | binary sensor (MQTT last will) |
| Now playing, System, Play time | sensors |
| CPU, GPU, Memory, CPU temperature, Disk used | sensors |

Two publishers share the same discovery definitions (`server/integrations.py`):

- **Server publisher** (`homeassistant.enabled`): arcaded publishes MQTT
  discovery to your broker. Use it when the server can reach the broker.
- **Cabinet publisher** (`homeassistant.cabinet_enabled`): `cabinet/ha_local.py`
  runs inside cabd and talks to a broker on the cabinet's own LAN. This is the
  right choice when the server lives elsewhere (behind the tunnel) and Home
  Assistant sits next to the cabinet. It keeps working from cached config if
  the server link drops.

```yaml
homeassistant:
  enabled: false          # server-side publisher
  cabinet_enabled: true   # publish from the cabinet's LAN instead
  mqtt_host: 192.168.1.20
  mqtt_port: 1883
  mqtt_username: arcade
  mqtt_password: "..."
  discovery_prefix: homeassistant
```

Discovery messages are retained with QoS 1 and re-sent when HA announces its
birth message, so an HA restart repopulates the device on its own. Retained
*commands* are ignored, so a reconnect never replays a stale "stop game".

Home Assistant's MQTT integration has no `media_player` platform. For a media
card, set `rest_url` and a long-lived `rest_token` and arcaded pushes a
`media_player` state to `/api/states` alongside the MQTT entities.

Typical automation: dim the screensaver at night.

```yaml
- alias: Cabinet night mode
  trigger: {platform: time, at: "22:30:00"}
  action:
    - service: rest_command.arcade_brightness   # POST /screensaver/set {"brightness": 40}
```

## Discord

A channel webhook posts an embed when a game starts and when it stops: title,
system, play time and the box-art thumbnail. A webhook, not Rich Presence:
Rich Presence needs a Discord desktop client running next to the game, and the
cabinet does not have one.

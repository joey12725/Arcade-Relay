# Local Home Assistant connection

The cabinet now publishes directly to the Raspberry Pi's MQTT broker at
192.168.1.20:1883. Home Assistant responds at http://192.168.1.20:8123.
Previously the cloud arcaded process tried to reach this private address and
reported disconnected.

`cabinet/ha_local.py` runs inside cabd and uses the shared discovery definitions
from `server/integrations.py`. Provisioning installs that shared module and
python3-paho-mqtt. MQTT publishes and commands use the LAN; the cabinet continues
with its cached configuration if the control-plane connection drops.

The server's committed `integrations.yaml` has `homeassistant.enabled: false`
(cloud publisher disabled) and `homeassistant.cabinet_enabled: true` (local
publisher enabled), with the existing MQTT credentials and Pi address preserved.
The state was pushed to the cabinet; credentials are not copied into this report.
Changing the integration UI's cloud Enabled toggle should not be used to manage
the local publisher. Manage `cabinet_enabled` through the integrations API.

Device identifier: `arcade_cabinet`. Device name: Arcade Cabinet. Suggested area:
Living Room. Eleven MQTT entities: Stop game button, Screensaver switch, Cabinet
online binary sensor, Now playing, System, Play time, CPU, GPU, Memory, CPU
temperature, and Disk used. No synthetic REST media_player is created.

Discovery configurations are retained with QoS 1. MQTT reconnection and HA birth
messages trigger rediscovery. Availability uses an MQTT last will. Retained
commands are ignored to avoid replaying stale actions on reconnect.

Validation:

- Authenticated against the Pi using the existing saved credentials.
- Subscribed through an independent MQTT client and received all 11 discovery
  configurations with the same device identity, availability `online`, and live
  cabinet health/state.
- Published Screensaver OFF through the Pi and observed cabd execute it locally.
- Four local HA tests passed (discovery grouping, reconnect/retained command
  behavior, command allowlist, operation with cloud publisher disabled).
- Ten controller/hardware/launch regression tests passed; Python compilation,
  shell syntax, and diff checks passed.
- cabd and Pegasus are active after deployment.

Still unverified: Home Assistant's authenticated device/entity registry. The
browser is at the HA login page and no API token is configured. MQTT acceptance
and retained discovery do not by themselves prove that HA has consumed the
discovery entries. The requested Home Assistant skill was not found in the
installed catalog or filesystem; this used the existing cabinet integration and
the official MQTT discovery reference:
https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery

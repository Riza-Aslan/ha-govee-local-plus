# Govee Local Plus for Home Assistant

[![License](https://img.shields.io/github/license/Riza-Aslan/ha-govee-local-plus)](LICENSE)
[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Riza-Aslan&repository=ha-govee-local-plus&category=integration)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-Riza--Aslan-ea4aaa?logo=github-sponsors&logoColor=white)](https://github.com/sponsors/Riza-Aslan)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/rizaaslan)

Govee lights in Home Assistant, fully local: no Govee account, no API key, no cloud.
One integration instead of the built-in *Govee Light Local* plus a bridge.

| | built-in | this |
|---|---|---|
| on/off, brightness, RGB, colour temperature | yes | yes |
| scenes | ~12 generic ones | **all scenes of the Govee Home app** for your model (155 on an H61E5) |
| your own colour layouts (e.g. "last 3 segments red") | no | **yes**, set up in the UI, shown as effects |

Scenes come from Govee's public scene library (one anonymous request per model, kept for
30 days and used indefinitely when offline). Switching them on is local, over the LAN API.

## Install

1. In the **Govee Home app** open each light > settings and switch on **LAN Control**.
   Not every model has it; without it the light cannot be controlled locally.
2. Install through HACS: use the button above, or *HACS > ⋮ > Custom repositories >*
   `Riza-Aslan/ha-govee-local-plus`, category *Integration*. Restart Home Assistant.
3. **Settings > Devices & services > Add integration > Govee Local Plus.**

**UDP port 4002 must be free.** Only one program can listen on it. Remove or disable the
built-in *Govee Light Local* integration and any Govee-to-MQTT bridge first, otherwise setup
tells you the port is in use.

### The light is not found

Discovery uses multicast. Some networks drop it (IGMP snooping, Wi-Fi multicast handling, lights
in another VLAN), and a light that rebooted can fall out of the multicast group while ping and
direct requests still work. The setup then asks for the **IP address** of the light and probes it
directly. You can change the addresses later under *Configure > Light addresses*.

Checklist: LAN Control on · UDP 4001, 4002, 4003 allowed between Home Assistant and the light ·
light and Home Assistant reachable from each other (same VLAN or a firewall rule for those ports).

## Your own effects

*Settings > Devices & services > Govee Local Plus > Configure.* No JSON needed:

1. **Add an effect**: a name and a background colour (for every segment you do not colour below).
2. **Colour areas**: pick a colour and the segments that get it, e.g. `1-3, 7`. Add as many areas as you like.
3. The effect appears in the effect list of every light that has enough segments.

Segments are numbered **from 1**, like in the Govee Home app. A light always gets all its
segments painted (background first), because a strip falls back to stored colours for segments
that were not addressed.

*Effects as JSON (advanced)* shows and edits everything as text, handy for backups and sharing:

```json
{
  "Signal red": {"background": [0, 0, 0], "fills": [{"color": [234, 51, 35], "segments": [11, 12]}]},
  "Green half": {"fills": [{"color": [117, 251, 76], "segments": [7, 8, 9, 10, 11, 12]}]}
}
```

## Updating from 0.1.x

Options are converted automatically on the first start (the old text option counted segments from 0,
the new data from 1). Effect names stay the same, so automations keep working. If an old
effect text cannot be read, it stays untouched in the options and is logged.

## Diagnostics

*Download diagnostics* on the integration lists lights, segment counts, bound network addresses and
loaded scene counts. IP addresses and device ids are redacted.

## Limits

- The light does not report which scene runs; the effect shown in Home Assistant is the one last sent.
- Scene packets are an undocumented part of the LAN API; Govee can change them.
- Music modes and the animated DIY scenes of the app are not supported (static layouts are, as effects).
- Tested on a Govee H61E5 strip. Other models should work (the device library knows 270 of them),
  but scenes can need model specific tweaks. Please open an issue with the diagnostics attached.

## Credits

Based on the `govee_light_local` integration of Home Assistant Core (Apache 2.0) and the
`govee-local-api` library. Packet formats follow wez/govee2mqtt, justabaka/govee-lan-scene-command-generator
and krobipd/ioBroker.govee-smart (all MIT). Not affiliated with Govee.

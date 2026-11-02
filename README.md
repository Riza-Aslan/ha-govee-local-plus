# Govee Local Plus for Home Assistant

[![License](https://img.shields.io/github/license/Riza-Aslan/ha-govee-local-plus)](LICENSE)
[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-Riza--Aslan-ea4aaa?logo=github-sponsors&logoColor=white)](https://github.com/sponsors/Riza-Aslan)
[![PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/rizaaslan)

One integration for Govee lights, fully local, no Govee account and no API key.
It replaces the built-in `govee_light_local` and the Govee to MQTT bridge.

| | built-in | this |
|---|---|---|
| on/off, brightness, RGB, colour temperature | yes | yes |
| scenes | ~12 generic ones | **all scenes of the Govee Home app** for your model (155 on an H61E5) |
| your own layouts (e.g. "last 3 segments red") | no | **yes**, as effects, painted locally per segment |

Scenes come from Govee's public scene library (one anonymous request per model, cached
for 30 days); activating them is local (`ptReal` over the LAN API). Without internet the
cached copy or the few built-in scenes are used.

## Install

1. Enable **LAN control** for each light in the Govee Home app (device settings).
2. HACS > custom repository `Riza-Aslan/ha-govee-local-plus` (Integration), download, restart.
3. Add **Govee Local Plus** under Settings > Devices & services.

UDP port **4002** must be free: remove or stop the built-in Govee light local integration and
the Govee to MQTT bridge first, only one program can listen on it.

## Custom segment effects

Settings > Govee Local Plus > Configure. One JSON object, segments count from 0:

```json
{
  "Signal-Rot": {"fills": [{"color": [234, 51, 35], "segments": [10, 11]}]},
  "Signal-Rot-Hell": {
    "background": [252, 237, 198],
    "fills": [{"color": [234, 51, 35], "segments": [10, 11]}]
  },
  "Grün-20": {"fills": [{"color": [117, 251, 76], "segments": [9, 10]}]}
}
```

Every segment of the strip is painted (background first), because a strip falls back to
its stored colours for segments that are not addressed. They show up in the effect list
of the light next to the library scenes. Invalid JSON is rejected in the form; a bad value
that gets in anyway only disables the custom effects, the lights keep working.

## Limits

- The light does not report which scene runs, the effect shown in HA is what was last sent.
- Scene packets are an undocumented part of the LAN API; Govee can change them.
- Music modes and the animated DIY scenes of the app are not supported (static layouts are).
- Tested on a Govee H61E5 strip. Other models should work; please open an issue.

## Credits

Based on the `govee_light_local` integration of Home Assistant Core (Apache 2.0) and the
`govee-local-api` library. Packet formats follow wez/govee2mqtt, justabaka/govee-lan-scene-command-generator
and krobipd/ioBroker.govee-smart (all MIT). Not affiliated with Govee.

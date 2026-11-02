"""Light entity, setup and options, against a fake controller (no sockets)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from govee_local_api import GoveeLightCapabilities, GoveeLightFeatures
from govee_local_api.light_capabilities import GOVEE_LIGHT_CAPABILITIES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.light import ATTR_EFFECT, ATTR_EFFECT_LIST
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant

from custom_components.govee_local_plus.catalog import LibraryScene
from custom_components.govee_local_plus.const import CONF_CUSTOM_EFFECTS, DOMAIN

ENTITY = "light.h61e5"

STAIRS = json.dumps(
    {
        "Signal-Rot": {"fills": [{"color": [234, 51, 35], "segments": [10, 11]}]},
        "Grün-20": {"fills": [{"color": [117, 251, 76], "segments": [9, 10]}]},
    }
)
LIBRARY = [
    LibraryScene("Aurora", 12238, b"\x01\x02\x03"),
    LibraryScene("Sunrise", 0, b""),
]


class FakeDevice:
    """Just enough of GoveeDevice for the entity."""

    def __init__(self) -> None:
        self.sku = "H61E5"
        self.fingerprint = "AA:BB:CC:DD:EE:FF:00:11"
        self.ip = "10.0.0.40"
        self.capabilities: GoveeLightCapabilities = GOVEE_LIGHT_CAPABILITIES["H61E5"]
        self.temperature_range = MagicMock(min_kelvin=2000, max_kelvin=9000)
        self.on = False
        self.brightness = 40
        self.rgb_color = (1, 2, 3)
        self.temperature_color = 0
        self.lastseen = datetime.now(UTC)
        self.update_callback = None
        self.turn_on = AsyncMock(side_effect=self._turn_on)
        self.turn_off = AsyncMock(side_effect=self._turn_off)
        self.set_brightness = AsyncMock()
        self.set_rgb_color = AsyncMock()
        self.set_temperature = AsyncMock()
        self.set_scene = AsyncMock()
        self.set_scene_code = AsyncMock()
        self.send_raw_command = AsyncMock()

    async def _turn_on(self) -> None:
        self.on = True

    async def _turn_off(self) -> None:
        self.on = False

    def set_update_callback(self, callback) -> None:
        self.update_callback = callback


class FakeController:
    """Replaces GoveeController: returns one device, binds nothing."""

    device = FakeDevice()

    def __init__(self, **kwargs) -> None:
        self.protocols = [object()]
        self.bind_failures = []
        self.listening_addresses = ["10.0.0.2"]
        self.networks = ["10.0.0.0/24"]
        self.callback = None

    async def start(self, require_all=True) -> None:
        return None

    def send_update_message(self) -> None:
        return None

    def set_device_discovered_callback(self, cb) -> None:
        self.callback = cb

    def cleanup(self) -> asyncio.Event:
        event = asyncio.Event()
        event.set()
        return event

    @property
    def devices(self):
        return [FakeController.device]


@pytest.fixture
def device() -> FakeDevice:
    FakeController.device = FakeDevice()
    return FakeController.device


@pytest.fixture
async def setup(hass: HomeAssistant, device: FakeDevice):
    """Set the integration up with fakes and return the config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_CUSTOM_EFFECTS: STAIRS}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.govee_local_plus.coordinator.GoveeController",
            FakeController,
        ),
        patch(
            "custom_components.govee_local_plus.async_get_listening_addresses",
            AsyncMock(return_value=["10.0.0.2/24"]),
        ),
        patch(
            "custom_components.govee_local_plus.catalog.SceneCatalog.async_get",
            AsyncMock(return_value=LIBRARY),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "light", service, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True
    )


async def test_entity_and_effect_list(hass: HomeAssistant, setup) -> None:
    state = hass.states.get(ENTITY)
    assert state is not None
    effects = state.attributes[ATTR_EFFECT_LIST]
    # custom first, library second, then the built-in leftovers
    assert effects[:5] == ["none", "Signal-Rot", "Grün-20", "Aurora", "Sunrise"]
    assert len(effects) == len(set(effects))


async def test_custom_effect_sends_background_then_fill(
    hass: HomeAssistant, setup, device: FakeDevice
) -> None:
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Grün-20"})
    sent = [c.args[0] for c in device.send_raw_command.await_args_list]
    assert sent == [
        "330515010000000000000000ff090000000000d4",
        "3305150175fb4c000000000000060000000000e6",
    ]
    device.turn_on.assert_awaited_once()  # power first
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] == "Grün-20"


async def test_library_scene_goes_through_scene_code(
    hass: HomeAssistant, setup, device: FakeDevice
) -> None:
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Aurora"})
    device.set_scene_code.assert_awaited_once_with(12238, b"\x01\x02\x03")
    device.send_raw_command.assert_not_awaited()


async def test_effect_is_set_before_brightness(
    hass: HomeAssistant, setup, device: FakeDevice
) -> None:
    """A later brightness must not be overwritten by the scene, so it goes last."""
    order: list[str] = []
    device.send_raw_command.side_effect = lambda *_: order.append("effect")
    device.set_brightness.side_effect = lambda *_: order.append("brightness")
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Signal-Rot", "brightness": 51})
    assert order == ["effect", "effect", "brightness"]  # background, fill, then brightness
    device.set_brightness.assert_awaited_once_with(20)


async def test_rgb_clears_effect(hass: HomeAssistant, setup, device: FakeDevice) -> None:
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Signal-Rot"})
    await _call(hass, "turn_on", rgb_color=[10, 20, 30])
    device.set_rgb_color.assert_awaited_once_with(10, 20, 30)
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] is None


async def test_none_effect_restores_color(
    hass: HomeAssistant, setup, device: FakeDevice
) -> None:
    device.on = True
    device.rgb_color = (9, 8, 7)
    device.brightness = 50
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Signal-Rot"})
    device.rgb_color = (234, 51, 35)  # what the strip shows during the effect
    await _call(hass, "turn_on", **{ATTR_EFFECT: "none"})
    device.set_rgb_color.assert_awaited_with(9, 8, 7)
    assert hass.states.get(ENTITY).attributes[ATTR_EFFECT] is None


async def test_switching_effects_keeps_the_first_color(
    hass: HomeAssistant, setup, device: FakeDevice
) -> None:
    device.on = True
    device.rgb_color = (9, 8, 7)
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Signal-Rot"})
    device.rgb_color = (234, 51, 35)
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Grün-20"})
    await _call(hass, "turn_on", **{ATTR_EFFECT: "none"})
    device.set_rgb_color.assert_awaited_with(9, 8, 7)


async def test_unknown_effect_does_nothing(
    hass: HomeAssistant, setup, device: FakeDevice, caplog
) -> None:
    await _call(hass, "turn_on", **{ATTR_EFFECT: "Gibt es nicht"})
    device.send_raw_command.assert_not_awaited()
    device.set_scene_code.assert_not_awaited()
    assert "not available" in caplog.text


async def test_turn_off(hass: HomeAssistant, setup, device: FakeDevice) -> None:
    await _call(hass, "turn_on")
    assert hass.states.get(ENTITY).state == STATE_ON
    await _call(hass, "turn_off")
    device.turn_off.assert_awaited_once()


async def test_invalid_options_do_not_take_the_light_down(
    hass: HomeAssistant, device: FakeDevice, caplog
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, options={CONF_CUSTOM_EFFECTS: "{kaputt"}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.govee_local_plus.coordinator.GoveeController",
            FakeController,
        ),
        patch(
            "custom_components.govee_local_plus.async_get_listening_addresses",
            AsyncMock(return_value=["10.0.0.2/24"]),
        ),
        patch(
            "custom_components.govee_local_plus.catalog.SceneCatalog.async_get",
            AsyncMock(return_value=LIBRARY),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ENTITY) is not None
    assert "Custom effects ignored" in caplog.text
    assert "Signal-Rot" not in hass.states.get(ENTITY).attributes[ATTR_EFFECT_LIST]


async def test_options_flow_validates_and_reloads(hass: HomeAssistant, setup) -> None:
    entry = setup
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"].value == "form"

    bad = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_CUSTOM_EFFECTS: "{nope"}
    )
    assert bad["errors"] == {"base": "invalid_effects"}

    new = json.dumps({"Nur-Rot": {"fills": [{"color": [255, 0, 0], "segments": [0]}]}})
    with patch(
        "custom_components.govee_local_plus.coordinator.GoveeController", FakeController
    ), patch(
        "custom_components.govee_local_plus.async_get_listening_addresses",
        AsyncMock(return_value=["10.0.0.2/24"]),
    ), patch(
        "custom_components.govee_local_plus.catalog.SceneCatalog.async_get",
        AsyncMock(return_value=LIBRARY),
    ):
        done = await hass.config_entries.options.async_configure(
            bad["flow_id"], {CONF_CUSTOM_EFFECTS: new}
        )
        await hass.async_block_till_done()
    assert done["type"].value == "create_entry"
    effects = hass.states.get(ENTITY).attributes[ATTR_EFFECT_LIST]
    assert "Nur-Rot" in effects and "Signal-Rot" not in effects


async def test_single_instance(hass: HomeAssistant, setup) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"].value == "abort"
    assert result["reason"] == "single_instance_allowed"


async def test_h61e5_has_twelve_segments() -> None:
    caps = GOVEE_LIGHT_CAPABILITIES["H61E5"]
    assert caps.features & GoveeLightFeatures.SEGMENT_CONTROL
    assert caps.segments_count == 12

"""Setup flow, options menu, migration and diagnostics."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.govee_local_plus import async_remove_config_entry_device
from custom_components.govee_local_plus.const import (
    CONF_CUSTOM_EFFECTS,
    CONF_DEVICE_IPS,
    CONF_EFFECTS,
    DOMAIN,
)
from custom_components.govee_local_plus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_light import ENTITY, LIBRARY, STAIRS, FakeController, FakeDevice

PATCH_ADDR = "custom_components.govee_local_plus.async_get_listening_addresses"
PATCH_CTRL = "custom_components.govee_local_plus.coordinator.GoveeController"
PATCH_CATALOG = "custom_components.govee_local_plus.catalog.SceneCatalog.async_get"


class ProbeController:
    """Stand-in for the controller used by the setup search."""

    found = True
    queued: list[str] = []
    asked: list[str] = []

    def __init__(self, **kwargs) -> None:
        self.devices = []
        self.listening_addresses = ["10.0.0.2"]
        self.networks = ["10.0.0.0/24"]
        self.bind_failures = []
        self._ips: list[str] = []

    async def start(self, require_all=True) -> None:
        # the real controller sends the multicast search as soon as it starts
        if ProbeController.found == "multicast":
            self.devices = [object()]

    def add_device_to_discovery_queue(self, ip: str) -> bool:
        self._ips.append(ip)
        ProbeController.queued.append(ip)
        return True

    def send_discovery_message(self) -> None:
        ProbeController.asked = list(self._ips)
        # a light answers either to multicast (no address queued) or to its address
        if ProbeController.found == "multicast" and not self._ips:
            self.devices = [object()]
        if ProbeController.found is True and self._ips:
            self.devices = [object()]

    def cleanup(self) -> asyncio.Event:
        event = asyncio.Event()
        event.set()
        return event


@pytest.fixture
def probe():
    ProbeController.found = True
    ProbeController.queued = []
    ProbeController.asked = []
    with (
        patch("custom_components.govee_local_plus.config_flow.GoveeController", ProbeController),
        patch("custom_components.govee_local_plus.config_flow.DISCOVERY_TIMEOUT", 0.2),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        # creating the entry sets it up right away: keep that off the network too
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        yield ProbeController


# -- setup flow --------------------------------------------------------------


async def test_user_step_explains_before_searching(hass: HomeAssistant, probe) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert probe.asked == []  # nothing was sent yet


async def test_multicast_hit_creates_entry_without_addresses(
    hass: HomeAssistant, probe
) -> None:
    probe.found = "multicast"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    done = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["result"].options == {}
    assert done["result"].version == 2


async def test_no_answer_leads_to_manual_address(hass: HomeAssistant, probe) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    manual = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert manual["type"] is FlowResultType.FORM
    assert manual["step_id"] == "manual"

    done = await hass.config_entries.flow.async_configure(
        manual["flow_id"], {CONF_DEVICE_IPS: ["10.0.0.40"]}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert done["result"].options == {CONF_DEVICE_IPS: ["10.0.0.40"]}
    assert probe.asked == ["10.0.0.40"]  # asked directly, not by multicast


async def test_manual_bad_and_silent_addresses(hass: HomeAssistant, probe) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    probe.found = False
    manual = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    bad = await hass.config_entries.flow.async_configure(
        manual["flow_id"], {CONF_DEVICE_IPS: ["10.0.0.999"]}
    )
    assert bad["errors"] == {"base": "invalid_ips"}

    silent = await hass.config_entries.flow.async_configure(
        manual["flow_id"], {CONF_DEVICE_IPS: ["10.0.0.40"]}
    )
    assert silent["type"] is FlowResultType.FORM
    assert silent["errors"] == {"base": "cannot_connect"}


async def test_port_in_use_is_explained(hass: HomeAssistant, probe) -> None:
    class Busy(ProbeController):
        async def start(self, require_all=True) -> None:
            raise OSError(98, "Address in use")

    with patch("custom_components.govee_local_plus.config_flow.GoveeController", Busy):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        done = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "port_in_use"


# -- options menu ------------------------------------------------------------


@pytest.fixture
async def entry(hass: HomeAssistant, device: FakeDevice):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, version=2, options={CONF_EFFECTS: json.loads(json.dumps(STAIRS))}
    )
    entry.add_to_hass(hass)
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


async def _menu(hass, entry, choice):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


async def _settle(hass):
    await hass.async_block_till_done()


async def test_menu_offers_edit_and_delete_only_with_effects(hass: HomeAssistant, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert set(result["menu_options"]) == {
        "add_effect", "edit_effect", "delete_effect", "addresses", "json"
    }


async def test_add_effect_with_two_colour_areas(hass: HomeAssistant, entry, device) -> None:
    form = await _menu(hass, entry, "add_effect")
    assert form["step_id"] == "add_effect"

    fill = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "Flur blau", "background": [0, 0, 20]}
    )
    assert fill["step_id"] == "fill"

    # the first area asks for more
    fill = await hass.config_entries.options.async_configure(
        fill["flow_id"], {"color": [0, 0, 255], "segments": "1-3, 7", "add_another": True}
    )
    assert fill["step_id"] == "fill"
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        done = await hass.config_entries.options.async_configure(
            fill["flow_id"], {"color": [255, 0, 0], "segments": "12", "add_another": False}
        )
        await _settle(hass)

    assert done["type"] is FlowResultType.CREATE_ENTRY
    saved = entry.options[CONF_EFFECTS]["Flur blau"]
    assert saved["background"] == [0, 0, 20]
    assert saved["fills"] == [
        {"color": [0, 0, 255], "segments": [1, 2, 3, 7]},
        {"color": [255, 0, 0], "segments": [12]},
    ]
    assert "Signal-Rot" in saved or "Signal-Rot" in entry.options[CONF_EFFECTS]  # others kept


async def test_new_effect_paints_what_was_entered(hass: HomeAssistant, entry, device) -> None:
    form = await _menu(hass, entry, "add_effect")
    fill = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "Test", "background": [0, 0, 0]}
    )
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        await hass.config_entries.options.async_configure(
            fill["flow_id"], {"color": [9, 8, 7], "segments": "2"}
        )
        await _settle(hass)
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ENTITY, "effect": "Test"}, blocking=True
    )
    sent = [c.args[0] for c in FakeController.device.send_raw_command.await_args_list]
    colored = [p for p in sent if p[8:14] == "090807"]  # R G B of the area
    assert len(colored) == 1
    assert colored[0][24:38] == "02000000000000"  # segment 2 -> bit 1 -> 0x02


async def test_effect_name_rules(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "add_effect")
    for name, error in (("  ", "name_required"), ("none", "name_reserved"), ("Signal-Rot", "name_exists")):
        bad = await hass.config_entries.options.async_configure(
            form["flow_id"], {"name": name, "background": [0, 0, 0]}
        )
        assert bad["errors"] == {"name": error}


async def test_bad_segments_keep_the_form(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "add_effect")
    fill = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "X", "background": [0, 0, 0]}
    )
    for text in ("0", "3-1", "abc", "99"):
        bad = await hass.config_entries.options.async_configure(
            fill["flow_id"], {"color": [1, 2, 3], "segments": text}
        )
        assert bad["step_id"] == "fill"
        assert bad["errors"] == {"segments": "invalid_segments"}


async def test_empty_effect_is_not_saved(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "add_effect")
    fill = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "Leer", "background": [0, 0, 0]}
    )
    done = await hass.config_entries.options.async_configure(
        fill["flow_id"], {"color": [1, 2, 3], "segments": ""}
    )
    assert done["type"] is FlowResultType.ABORT
    assert done["reason"] == "no_fills"
    assert "Leer" not in entry.options[CONF_EFFECTS]


async def test_edit_effect_prefills_and_renames(hass: HomeAssistant, entry) -> None:
    pick = await _menu(hass, entry, "edit_effect")
    form = await hass.config_entries.options.async_configure(
        pick["flow_id"], {"effect": "Grün-20"}
    )
    assert form["step_id"] == "effect"
    fill = await hass.config_entries.options.async_configure(
        form["flow_id"], {"name": "Grün-neu", "background": [0, 0, 0]}
    )
    # the stored area is offered again with its segments in the readable form
    suggested = fill["data_schema"].schema
    assert any(str(k) == "segments" for k in suggested)
    assert fill["step_id"] == "fill"
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        await hass.config_entries.options.async_configure(
            fill["flow_id"], {"color": [117, 251, 76], "segments": "10-11"}
        )
        await _settle(hass)
    effects = entry.options[CONF_EFFECTS]
    assert "Grün-20" not in effects and "Grün-neu" in effects
    assert effects["Grün-neu"]["fills"][0]["segments"] == [10, 11]


async def test_delete_effects(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "delete_effect")
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        done = await hass.config_entries.options.async_configure(
            form["flow_id"], {"effects": ["Signal-Rot"]}
        )
        await _settle(hass)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert list(entry.options[CONF_EFFECTS]) == ["Grün-20"]


async def test_addresses_option(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "addresses")
    bad = await hass.config_entries.options.async_configure(
        form["flow_id"], {CONF_DEVICE_IPS: ["nope"]}
    )
    assert bad["errors"] == {"base": "invalid_ips"}
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        done = await hass.config_entries.options.async_configure(
            bad["flow_id"], {CONF_DEVICE_IPS: ["10.0.0.40", "10.0.0.40"]}
        )
        await _settle(hass)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_DEVICE_IPS] == ["10.0.0.40"]
    assert entry.options[CONF_EFFECTS]  # effects survive


async def test_json_editor_shows_and_validates(hass: HomeAssistant, entry) -> None:
    form = await _menu(hass, entry, "json")
    shown = json.loads(
        next(iter(form["data_schema"].schema)).description["suggested_value"]
    )
    assert shown["Grün-20"]["fills"][0]["segments"] == [10, 11]  # numbered from 1

    bad = await hass.config_entries.options.async_configure(
        form["flow_id"], {"effects_json": "{nope"}
    )
    assert bad["errors"] == {"base": "invalid_effects"}

    new = json.dumps({"Nur-Rot": {"fills": [{"color": [255, 0, 0], "segments": [1]}]}})
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        done = await hass.config_entries.options.async_configure(
            bad["flow_id"], {"effects_json": new}
        )
        await _settle(hass)
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert list(entry.options[CONF_EFFECTS]) == ["Nur-Rot"]
    assert entry.options[CONF_EFFECTS]["Nur-Rot"]["background"] == [0, 0, 0]


# -- migration ---------------------------------------------------------------


async def test_v1_entry_is_migrated(hass: HomeAssistant, device: FakeDevice) -> None:
    legacy = json.dumps(
        {"Grün-20": {"fills": [{"color": [117, 251, 76], "segments": [9, 10]}]}}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        version=1,
        options={CONF_CUSTOM_EFFECTS: legacy, CONF_DEVICE_IPS: "10.0.0.40, 10.0.0.41"},
    )
    entry.add_to_hass(hass)
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 2
    assert CONF_CUSTOM_EFFECTS not in entry.options
    assert entry.options[CONF_EFFECTS]["Grün-20"]["fills"][0]["segments"] == [10, 11]
    assert entry.options[CONF_DEVICE_IPS] == ["10.0.0.40", "10.0.0.41"]
    assert FakeController.last_queued == ["10.0.0.40", "10.0.0.41"]
    # the entity paints the same packets as before the migration
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": ENTITY, "effect": "Grün-20"}, blocking=True
    )
    sent = [c.args[0] for c in FakeController.device.send_raw_command.await_args_list]
    assert sent == [
        "330515010000000000000000ff090000000000d4",
        "3305150175fb4c000000000000060000000000e6",
    ]


async def test_broken_v1_effects_stay_as_text(hass: HomeAssistant, device, caplog) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, version=1, options={CONF_CUSTOM_EFFECTS: "{kaputt"}
    )
    entry.add_to_hass(hass)
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_CUSTOM_EFFECTS] == "{kaputt"  # nothing was thrown away
    assert "Could not convert" in caplog.text


async def test_future_version_is_refused(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, version=3)
    entry.add_to_hass(hass)
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# -- effects that do not fit a light -----------------------------------------


async def test_effect_needing_more_segments_is_not_offered(
    hass: HomeAssistant, device: FakeDevice
) -> None:
    effects = {"Zu lang": {"fills": [{"color": [1, 2, 3], "segments": [13]}]}}
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=DOMAIN, version=2, options={CONF_EFFECTS: effects}
    )
    entry.add_to_hass(hass)
    with (
        patch(PATCH_CTRL, FakeController),
        patch(PATCH_ADDR, AsyncMock(return_value=["10.0.0.2/24"])),
        patch(PATCH_CATALOG, AsyncMock(return_value=LIBRARY)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert "Zu lang" not in hass.states.get(ENTITY).attributes["effect_list"]  # H61E5 has 12


# -- diagnostics and device removal -----------------------------------------


async def test_diagnostics_hide_addresses(hass: HomeAssistant, entry, device) -> None:
    entry.runtime_data.catalog._scenes["H61E5"] = LIBRARY
    data = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(data, default=str)
    assert "10.0.0.40" not in text and "AA:BB:CC" not in text
    assert data["devices"][0]["ip"] == REDACTED
    assert data["devices"][0]["sku"] == "H61E5"
    assert data["scene_counts"] == {"H61E5": 2}
    assert data["effects"] == ["Signal-Rot", "Grün-20"]


async def test_device_removal_only_when_light_is_gone(hass: HomeAssistant, entry, device) -> None:
    from homeassistant.helpers import device_registry as dr

    dev = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert not await async_remove_config_entry_device(hass, entry, dev)  # still on the network
    FakeController.device = FakeDevice()
    FakeController.device.fingerprint = "11:22:33:44:55:66:77:88"
    assert await async_remove_config_entry_device(hass, entry, dev)

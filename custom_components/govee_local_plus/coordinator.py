"""Coordinator for Govee Local Plus."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
import logging
from typing import override

from govee_local_api import GoveeController, GoveeDevice

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .catalog import SceneCatalog
from .const import (
    CONF_DISCOVERY_INTERVAL_DEFAULT,
    CONF_LISTENING_PORT_DEFAULT,
    CONF_MULTICAST_ADDRESS_DEFAULT,
    CONF_TARGET_PORT_DEFAULT,
    DOMAIN,
    SCAN_INTERVAL,
)
from .effects import SegmentEffect

_LOGGER = logging.getLogger(__name__)

# Pause between raw packets of one effect (the strip needs a moment per frame).
PACKET_GAP = 0.05

type GoveeLocalPlusConfigEntry = ConfigEntry[GoveeLocalPlusCoordinator]


def log_bound_addresses(controller: GoveeController) -> None:
    """Log the addresses and networks the controller is bound to."""
    _LOGGER.debug(
        "Listening on port %d: %s",
        CONF_LISTENING_PORT_DEFAULT,
        ", ".join(
            f"{address} ({network})" if network else f"{address} (no network mask)"
            for address, network in zip(
                controller.listening_addresses, controller.networks, strict=True
            )
        ),
    )
    for address, error in controller.bind_failures:
        _LOGGER.debug("Not listening on %s: %s", address, error.strerror or error)


class GoveeLocalPlusCoordinator(DataUpdateCoordinator[list[GoveeDevice]]):
    """Owns the UDP controller, the scene catalog and the custom effects."""

    config_entry: GoveeLocalPlusConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: GoveeLocalPlusConfigEntry,
        listening_addresses: list[str],
        effects: dict[str, SegmentEffect],
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=config_entry,
            name="GoveeLocalPlus",
            update_interval=SCAN_INTERVAL,
        )
        self.effects = effects
        self.catalog = SceneCatalog(hass)
        self._controller = GoveeController(
            loop=hass.loop,
            logger=_LOGGER,
            listening_addresses=listening_addresses,
            broadcast_address=CONF_MULTICAST_ADDRESS_DEFAULT,
            broadcast_port=CONF_TARGET_PORT_DEFAULT,
            listening_port=CONF_LISTENING_PORT_DEFAULT,
            discovery_enabled=True,
            discovery_interval=CONF_DISCOVERY_INTERVAL_DEFAULT,
            update_enabled=False,
        )
        self._rebind_lock = asyncio.Lock()

    async def start(self) -> None:
        """Bind the sockets and start discovery."""
        await self._controller.start(require_all=False)
        self._controller.send_update_message()
        log_bound_addresses(self._controller)

    @callback
    def set_discovery_callback(
        self, discovery_callback: Callable[[GoveeDevice, bool], bool]
    ) -> None:
        """Set the callback for newly discovered lights."""
        self._controller.set_device_discovered_callback(discovery_callback)

    def cleanup(self) -> asyncio.Event:
        """Stop the controller."""
        return self._controller.cleanup()

    @callback
    def request_status(self) -> None:
        """Ask all devices for their state (answers arrive via the callbacks)."""
        self._controller.send_update_message()

    async def turn_on(self, device: GoveeDevice) -> None:
        """Turn on."""
        await device.turn_on()

    async def turn_off(self, device: GoveeDevice) -> None:
        """Turn off."""
        await device.turn_off()

    async def set_brightness(self, device: GoveeDevice, brightness: int) -> None:
        """Set brightness, 0-100."""
        await device.set_brightness(brightness)

    async def set_rgb_color(
        self, device: GoveeDevice, red: int, green: int, blue: int
    ) -> None:
        """Set an RGB colour."""
        await device.set_rgb_color(red, green, blue)

    async def set_temperature(self, device: GoveeDevice, temperature: int) -> None:
        """Set a colour temperature in Kelvin."""
        await device.set_temperature(temperature)

    async def set_builtin_scene(self, device: GoveeDevice, scene: str) -> None:
        """Activate one of the few scenes the stock library knows."""
        await device.set_scene(scene)

    async def set_library_scene(
        self, device: GoveeDevice, code: int, param: bytes
    ) -> None:
        """Activate a scene of the public library."""
        await device.set_scene_code(code, param)

    async def run_effect(
        self, device: GoveeDevice, effect: SegmentEffect, segment_count: int
    ) -> None:
        """Paint a custom effect, one raw packet after the other."""
        for index, packet in enumerate(effect.packets(segment_count)):
            if index:
                await asyncio.sleep(PACKET_GAP)
            await device.send_raw_command(packet.hex())

    @property
    def devices(self) -> list[GoveeDevice]:
        """Return the discovered lights."""
        return self._controller.devices

    async def async_rebind_failed_addresses(self, _now: datetime) -> None:
        """Retry the addresses that did not bind."""
        async with self._rebind_lock:
            if not self._controller.bind_failures or not self._controller.protocols:
                return
            if await self._controller.rebind_failed():
                log_bound_addresses(self._controller)

    async def async_wait_rebind(self) -> None:
        """Wait for an in-flight rebind to finish."""
        async with self._rebind_lock:
            pass

    @override
    async def _async_update_data(self) -> list[GoveeDevice]:
        # A controller that lost every endpoint stays inoperative until restarted.
        if not self._controller.protocols:
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="no_live_endpoints"
            )
        self._controller.send_update_message()
        return self.devices

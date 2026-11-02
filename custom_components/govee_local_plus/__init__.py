"""Govee Local Plus: one local integration for Govee lights, incl. all scenes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from errno import EADDRINUSE, EADDRNOTAVAIL, EMFILE, ENETDOWN, ENETUNREACH, ENOBUFS
import logging

from govee_local_api.controller import LISTENING_PORT

from homeassistant.components import network
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_CUSTOM_EFFECTS, DISCOVERY_TIMEOUT, DOMAIN, REBIND_INTERVAL
from .coordinator import GoveeLocalPlusConfigEntry, GoveeLocalPlusCoordinator
from .effects import EffectConfigError, parse_effects

PLATFORMS: list[Platform] = [Platform.LIGHT]

# Bind errors that clear up on their own; anything else needs a human.
TRANSIENT_BIND_ERRNOS = frozenset(
    {EADDRINUSE, EADDRNOTAVAIL, EMFILE, ENETDOWN, ENETUNREACH, ENOBUFS}
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> bool:
    """Set up Govee Local Plus from a config entry."""
    listening_addresses = await async_get_listening_addresses(hass)
    if not listening_addresses:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="no_listening_addresses"
        )

    try:
        effects = parse_effects(entry.options.get(CONF_CUSTOM_EFFECTS))
    except EffectConfigError as err:
        # Do not take the lights down for a typo in the options.
        _LOGGER.error("Custom effects ignored: %s", err)
        effects = {}

    coordinator = GoveeLocalPlusCoordinator(
        hass=hass,
        config_entry=entry,
        listening_addresses=listening_addresses,
        effects=effects,
    )

    async def await_cleanup() -> None:
        await coordinator.async_wait_rebind()
        cleanup_complete_event = coordinator.cleanup()
        with suppress(TimeoutError):
            await asyncio.wait_for(cleanup_complete_event.wait(), 1)

    entry.async_on_unload(await_cleanup)

    try:
        await coordinator.start()
    except OSError as ex:
        if ex.errno == EADDRINUSE:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="port_in_use",
                translation_placeholders={"port": str(LISTENING_PORT)},
            ) from ex
        if ex.errno in TRANSIENT_BIND_ERRNOS:
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="bind_failed",
                translation_placeholders={"error": ex.strerror or str(ex)},
            ) from ex
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="bind_failed",
            translation_placeholders={"error": ex.strerror or str(ex)},
        ) from ex

    entry.async_on_unload(
        async_track_time_interval(
            hass,
            coordinator.async_rebind_failed_addresses,
            REBIND_INTERVAL,
            name="Govee Local Plus rebind",
        )
    )

    await coordinator.async_config_entry_first_refresh()

    try:
        async with asyncio.timeout(delay=DISCOVERY_TIMEOUT):
            while not coordinator.devices:
                await asyncio.sleep(delay=1)
    except TimeoutError as ex:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="no_devices_found"
        ) from ex

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> None:
    """Reload so new custom effects show up in the effect list."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_get_listening_addresses(hass: HomeAssistant) -> list[str]:
    """Get the enabled IPv4 source addresses, with network mask."""
    adapters = await network.async_get_adapters(hass)
    return sorted(
        {
            f"{ipv4['address']}/{ipv4['network_prefix']}"
            for adapter in adapters
            if adapter["enabled"]
            for ipv4 in adapter["ipv4"]
        }
    )

"""Govee Local Plus: one local integration for Govee lights, incl. all scenes."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from errno import EADDRINUSE, EADDRNOTAVAIL, EMFILE, ENETDOWN, ENETUNREACH, ENOBUFS
import ipaddress
import logging
from collections.abc import Iterable

from govee_local_api.controller import LISTENING_PORT

from homeassistant.components import network
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_CUSTOM_EFFECTS,
    CONF_DEVICE_IPS,
    CONF_EFFECTS,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    REBIND_INTERVAL,
)
from .coordinator import GoveeLocalPlusConfigEntry, GoveeLocalPlusCoordinator
from .effects import EffectConfigError, migrate_legacy_effects, parse_effects

PLATFORMS: list[Platform] = [Platform.LIGHT]

# Bind errors that clear up on their own; anything else needs a human.
TRANSIENT_BIND_ERRNOS = frozenset(
    {EADDRINUSE, EADDRNOTAVAIL, EMFILE, ENETDOWN, ENETUNREACH, ENOBUFS}
)

_LOGGER = logging.getLogger(__name__)


def validate_ips(values: Iterable[str] | None) -> list[str]:
    """Return the IPv4 addresses without duplicates; raise ValueError on a bad one."""
    ips: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text:
            ips.append(str(ipaddress.IPv4Address(text)))
    return list(dict.fromkeys(ips))


def parse_device_ips(value: str | None) -> list[str]:
    """Split free text (v1 option) into addresses; raise ValueError on a bad one."""
    parts = (value or "").replace(";", ",").replace("\n", ",").replace(" ", ",")
    return validate_ips(parts.split(","))


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
        effects = parse_effects(entry.options.get(CONF_EFFECTS))
    except EffectConfigError as err:
        # Do not take the lights down for a bad option.
        _LOGGER.error("Custom effects ignored: %s", err)
        effects = {}

    try:
        device_ips = validate_ips(entry.options.get(CONF_DEVICE_IPS))
    except ValueError as err:
        _LOGGER.error("Light addresses ignored: %s", err)
        device_ips = []

    coordinator = GoveeLocalPlusCoordinator(
        hass=hass,
        config_entry=entry,
        listening_addresses=listening_addresses,
        effects=effects,
        device_ips=device_ips,
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


async def async_migrate_entry(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> bool:
    """Migrate options: v1 had JSON text with segments from 0, v2 has plain data from 1."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = dict(entry.options)
        legacy = options.get(CONF_CUSTOM_EFFECTS)
        if legacy:
            try:
                options[CONF_EFFECTS] = migrate_legacy_effects(legacy)
            except EffectConfigError as err:
                _LOGGER.error(
                    "Could not convert the effects of the old version, "
                    "they stay in the options as text: %s",
                    err,
                )
            else:
                del options[CONF_CUSTOM_EFFECTS]
        ips = options.get(CONF_DEVICE_IPS)
        if isinstance(ips, str):
            try:
                options[CONF_DEVICE_IPS] = parse_device_ips(ips)
            except ValueError as err:
                _LOGGER.error("Dropping invalid light addresses %r: %s", ips, err)
                del options[CONF_DEVICE_IPS]
        hass.config_entries.async_update_entry(entry, options=options, version=2)
        _LOGGER.debug("Migrated %s to version 2", entry.title)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> None:
    """Reload so new effects and addresses take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow deleting a device unless its light is currently on the network."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return True
    live = {device.fingerprint for device in runtime.devices}
    return not any(
        domain == DOMAIN and ident in live for domain, ident in device_entry.identifiers
    )


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

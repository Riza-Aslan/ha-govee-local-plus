"""Diagnostics for Govee Local Plus (addresses and device ids are redacted)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_DEVICE_IPS
from .coordinator import GoveeLocalPlusConfigEntry

TO_REDACT = {CONF_DEVICE_IPS, "ip", "fingerprint", "listening"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeLocalPlusConfigEntry
) -> dict[str, Any]:
    """Return what is needed to debug discovery and scenes without private data."""
    coordinator = entry.runtime_data
    devices = [
        {
            "sku": device.sku,
            "ip": device.ip,
            "fingerprint": device.fingerprint,
            "on": device.on,
            "last_seen": device.lastseen.isoformat(),
            "features": str(device.capabilities.features) if device.capabilities else None,
            "segments": device.capabilities.segments_count if device.capabilities else None,
        }
        for device in coordinator.devices
    ]
    return async_redact_data(
        {
            "options": dict(entry.options),
            "network": coordinator.bind_summary,
            "devices": devices,
            "effects": list(coordinator.effects),
            "scene_counts": coordinator.catalog.counts,
        },
        TO_REDACT,
    )

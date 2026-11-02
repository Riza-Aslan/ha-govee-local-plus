"""Govee Local Plus lights."""

from __future__ import annotations

import logging
from typing import Any, override

from govee_local_api import GoveeDevice, GoveeLightFeatures

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    filter_supported_color_modes,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .catalog import LibraryScene
from .const import DEVICE_TIMEOUT, DOMAIN, MANUFACTURER, NONE_EFFECT
from .coordinator import GoveeLocalPlusConfigEntry, GoveeLocalPlusCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GoveeLocalPlusConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the lights."""
    coordinator = config_entry.runtime_data

    async def add_device(device: GoveeDevice) -> None:
        library = await coordinator.catalog.async_get(device.sku)
        async_add_entities([GoveeLight(coordinator, device, library)])

    for device in coordinator.devices:
        await add_device(device)

    def discovery_callback(device: GoveeDevice, is_new: bool) -> bool:
        if is_new:
            config_entry.async_create_task(
                hass, add_device(device), f"{DOMAIN} add {device.fingerprint}"
            )
        return True

    coordinator.set_discovery_callback(discovery_callback)


class GoveeLight(CoordinatorEntity[GoveeLocalPlusCoordinator], LightEntity):
    """A Govee light, with the effects of the app available locally."""

    _attr_translation_key = "govee_light"
    _attr_has_entity_name = True
    _attr_name = None
    _attr_effect: str | None = None

    def __init__(
        self,
        coordinator: GoveeLocalPlusCoordinator,
        device: GoveeDevice,
        library: list[LibraryScene],
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = device.fingerprint
        self._last_color_state: (
            tuple[Any, int | None, tuple[int | None, ...] | None] | None
        ) = None

        capabilities = device.capabilities
        color_modes = {ColorMode.ONOFF}
        self._segment_count = 0
        if capabilities:
            if GoveeLightFeatures.COLOR_RGB & capabilities.features:
                color_modes.add(ColorMode.RGB)
            if GoveeLightFeatures.COLOR_KELVIN_TEMPERATURE & capabilities.features:
                color_modes.add(ColorMode.COLOR_TEMP)
                self._attr_max_color_temp_kelvin = device.temperature_range.max_kelvin
                self._attr_min_color_temp_kelvin = device.temperature_range.min_kelvin
            if GoveeLightFeatures.BRIGHTNESS & capabilities.features:
                color_modes.add(ColorMode.BRIGHTNESS)
            if GoveeLightFeatures.SEGMENT_CONTROL & capabilities.features:
                self._segment_count = capabilities.segments_count

        self._attr_supported_color_modes = filter_supported_color_modes(color_modes)
        self._fixed_color_mode: ColorMode | None = (
            next(iter(self._attr_supported_color_modes))
            if len(self._attr_supported_color_modes) == 1
            else None
        )

        # Effects: custom layouts first, then the library, then the few
        # built-in scenes as a fallback when the library is not reachable.
        self._custom = dict(coordinator.effects) if self._segment_count else {}
        self._library = {s.name: s for s in library}
        self._builtin = (
            [
                name
                for name in capabilities.scenes
                if name.title() not in self._library and name not in self._library
            ]
            if capabilities and GoveeLightFeatures.SCENES & capabilities.features
            else []
        )
        names = [*self._custom, *self._library]
        names += [n for n in self._builtin if n not in names]
        if names:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [NONE_EFFECT, *names]
        else:
            self._attr_supported_features = LightEntityFeature(0)
            self._attr_effect_list = None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.fingerprint)},
            name=device.sku,
            manufacturer=MANUFACTURER,
            model_id=device.sku,
            serial_number=device.fingerprint,
        )

    @property
    @override
    def available(self) -> bool:
        """Return True if the device answered recently."""
        if not super().available:
            return False
        return dt_util.utcnow() - self._device.lastseen < DEVICE_TIMEOUT

    @property
    @override
    def is_on(self) -> bool:
        """Return True if on."""
        return self._device.on

    @property
    @override
    def brightness(self) -> int:
        """Return the brightness, 0-255."""
        return int((self._device.brightness / 100.0) * 255.0)

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """Return the colour temperature."""
        return self._device.temperature_color

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB colour."""
        return self._device.rgb_color

    @property
    @override
    def color_mode(self) -> ColorMode:
        """Return the colour mode."""
        if self._fixed_color_mode:
            return self._fixed_color_mode
        if (
            self._device.temperature_color is not None
            and self._device.temperature_color > 0
        ):
            return ColorMode.COLOR_TEMP
        return ColorMode.RGB

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on.

        Order matters: power, then colour or effect, then brightness. A
        brightness command sent before a scene would be overwritten by it.
        """
        coordinator = self.coordinator
        device = self._device
        effect = kwargs.get(ATTR_EFFECT)

        if not self.is_on:
            await coordinator.turn_on(device)

        if ATTR_RGB_COLOR in kwargs:
            self._attr_effect = None
            self._last_color_state = None
            await coordinator.set_rgb_color(device, *kwargs[ATTR_RGB_COLOR])
        elif ATTR_COLOR_TEMP_KELVIN in kwargs:
            self._attr_effect = None
            self._last_color_state = None
            await coordinator.set_temperature(
                device, int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            )
        elif effect:
            await self._async_apply_effect(effect)

        if ATTR_BRIGHTNESS in kwargs:
            await coordinator.set_brightness(
                device, int((float(kwargs[ATTR_BRIGHTNESS]) / 255.0) * 100.0)
            )

        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        await self.coordinator.turn_off(self._device)
        self.async_write_ha_state()

    async def _async_apply_effect(self, effect: str) -> None:
        if not self._attr_effect_list or effect not in self._attr_effect_list:
            _LOGGER.warning("Effect %r is not available for %s", effect, self.entity_id)
            return
        if effect == NONE_EFFECT:
            self._attr_effect = None
            await self._restore_last_color_state()
            return

        self._save_last_color_state()
        if effect in self._custom:
            await self.coordinator.run_effect(
                self._device, self._custom[effect], self._segment_count
            )
        elif effect in self._library:
            scene = self._library[effect]
            await self.coordinator.set_library_scene(self._device, scene.code, scene.param)
        else:
            await self.coordinator.set_builtin_scene(self._device, effect)
        self._attr_effect = effect

    @override
    async def async_added_to_hass(self) -> None:
        """Register the update callback."""
        await super().async_added_to_hass()
        self._device.set_update_callback(self._update_callback)

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Unregister the update callback."""
        self._device.set_update_callback(None)
        await super().async_will_remove_from_hass()

    @callback
    def _update_callback(self, device: GoveeDevice) -> None:
        self.async_write_ha_state()

    def _save_last_color_state(self) -> None:
        # Only plain colour state is worth restoring; keep the one from before
        # the first effect, not from an effect that replaced another.
        if self._attr_effect is not None and self._last_color_state is not None:
            return
        mode = self.color_mode
        self._last_color_state = (
            mode,
            self.brightness,
            (self.color_temp_kelvin,)
            if mode == ColorMode.COLOR_TEMP
            else self.rgb_color,
        )

    async def _restore_last_color_state(self) -> None:
        if not self._last_color_state:
            return
        mode, brightness, color = self._last_color_state
        if color:
            if mode == ColorMode.RGB:
                await self.coordinator.set_rgb_color(self._device, *color)
            elif mode == ColorMode.COLOR_TEMP and color[0]:
                await self.coordinator.set_temperature(self._device, color[0])
        if brightness:
            await self.coordinator.set_brightness(
                self._device, int((float(brightness) / 255.0) * 100.0)
            )
        self._last_color_state = None

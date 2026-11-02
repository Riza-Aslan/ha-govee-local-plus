"""Config and options flow for Govee Local Plus."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
from typing import Any

from govee_local_api import GoveeController
import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig

from . import async_get_listening_addresses
from .const import (
    CONF_CUSTOM_EFFECTS,
    CONF_LISTENING_PORT_DEFAULT,
    CONF_MULTICAST_ADDRESS_DEFAULT,
    CONF_TARGET_PORT_DEFAULT,
    DISCOVERY_TIMEOUT,
    DOMAIN,
)
from .coordinator import log_bound_addresses
from .effects import EffectConfigError, parse_effects

_LOGGER = logging.getLogger(__name__)

EXAMPLE_EFFECTS = (
    '{"Signal-Rot": {"background": [0, 0, 0], '
    '"fills": [{"color": [234, 51, 35], "segments": [10, 11]}]}}'
)


async def _async_has_devices(hass: HomeAssistant) -> bool:
    """Return True if at least one light answers on the LAN."""
    listening_addresses = await async_get_listening_addresses(hass)
    if not listening_addresses:
        _LOGGER.debug("No enabled IPv4 addresses to listen on")
        return False

    controller = GoveeController(
        loop=hass.loop,
        logger=_LOGGER,
        listening_addresses=listening_addresses,
        broadcast_address=CONF_MULTICAST_ADDRESS_DEFAULT,
        broadcast_port=CONF_TARGET_PORT_DEFAULT,
        listening_port=CONF_LISTENING_PORT_DEFAULT,
        discovery_enabled=True,
        discovery_interval=1,
        update_enabled=False,
    )
    try:
        await controller.start(require_all=False)
    except OSError as ex:
        _LOGGER.error("Start failed, errno: %d", ex.errno)
        return False

    log_bound_addresses(controller)
    try:
        async with asyncio.timeout(delay=DISCOVERY_TIMEOUT):
            while not controller.devices:
                await asyncio.sleep(delay=1)
    except TimeoutError:
        _LOGGER.debug("No devices found")

    found = bool(controller.devices)
    cleanup_complete = controller.cleanup()
    with suppress(TimeoutError):
        await asyncio.wait_for(cleanup_complete.wait(), 1)
    return found


class GoveeLocalPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry for the whole LAN."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a flow started by the user."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)

        if user_input is None:
            return self.async_show_form(step_id="user")

        if not await _async_has_devices(self.hass):
            return self.async_abort(reason="no_devices_found")
        return self.async_create_entry(title="Govee Local Plus", data={})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GoveeLocalPlusOptionsFlow()


class GoveeLocalPlusOptionsFlow(OptionsFlow):
    """Edit the custom segment effects."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and validate the effects JSON."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"example": EXAMPLE_EFFECTS, "error": ""}
        if user_input is not None:
            try:
                parse_effects(user_input.get(CONF_CUSTOM_EFFECTS))
            except EffectConfigError as err:
                errors["base"] = "invalid_effects"
                placeholders["error"] = str(err)
            else:
                return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(CONF_CUSTOM_EFFECTS): TextSelector(
                    TextSelectorConfig(multiline=True)
                )
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, user_input or self.config_entry.options
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

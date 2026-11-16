"""Config and options flow for Govee Local Plus."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from errno import EADDRINUSE
import json
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
from homeassistant.helpers.selector import (
    ColorRGBSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from . import async_get_listening_addresses, validate_ips
from .const import (
    CONF_DEVICE_IPS,
    CONF_EFFECTS,
    CONF_LISTENING_PORT_DEFAULT,
    CONF_MULTICAST_ADDRESS_DEFAULT,
    CONF_TARGET_PORT_DEFAULT,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    NONE_EFFECT,
)
from .coordinator import log_bound_addresses
from .effects import (
    MAX_SEGMENTS,
    EffectConfigError,
    format_segment_spec,
    load_effects_json,
    parse_effects,
    parse_segment_spec,
)

_LOGGER = logging.getLogger(__name__)

CONF_NAME = "name"
CONF_BACKGROUND = "background"
CONF_COLOR = "color"
CONF_SEGMENTS = "segments"
CONF_ADD_ANOTHER = "add_another"
CONF_EFFECT = "effect"
CONF_EFFECTS_JSON = "effects_json"

FOUND = "found"
NOTHING = "nothing"
PORT_IN_USE = "port_in_use"


async def _async_probe(hass: HomeAssistant, ips: list[str]) -> str:
    """Look for lights (multicast, plus a direct ask for each address in ``ips``)."""
    listening_addresses = await async_get_listening_addresses(hass)
    if not listening_addresses:
        _LOGGER.debug("No enabled IPv4 addresses to listen on")
        return NOTHING

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
        _LOGGER.error("Could not listen for Govee lights: %s", ex)
        return PORT_IN_USE if ex.errno == EADDRINUSE else NOTHING

    log_bound_addresses(controller)
    for ip in ips:
        controller.add_device_to_discovery_queue(ip)
    if ips:
        controller.send_discovery_message()
    try:
        async with asyncio.timeout(delay=DISCOVERY_TIMEOUT):
            while not controller.devices:
                await asyncio.sleep(delay=1)
    except TimeoutError:
        _LOGGER.debug("No devices found")

    result = FOUND if controller.devices else NOTHING
    cleanup_complete = controller.cleanup()
    with suppress(TimeoutError):
        await asyncio.wait_for(cleanup_complete.wait(), 1)
    return result


class GoveeLocalPlusConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry for the whole LAN."""

    VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Explain what is needed, then search."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        await self.async_set_unique_id(DOMAIN, raise_on_progress=False)

        if user_input is None:
            return self.async_show_form(step_id="user")

        result = await _async_probe(self.hass, [])
        if result == FOUND:
            return self.async_create_entry(title="Govee Local Plus", data={})
        if result == PORT_IN_USE:
            return self.async_abort(reason="port_in_use")
        return await self.async_step_manual()

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Nothing answered to the multicast search: ask for the address."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                ips = validate_ips(user_input.get(CONF_DEVICE_IPS))
            except ValueError as err:
                errors["base"] = "invalid_ips"
                placeholders["error"] = str(err)
            else:
                if not ips:
                    errors["base"] = "invalid_ips"
                    placeholders["error"] = "no address entered"
                else:
                    result = await _async_probe(self.hass, ips)
                    if result == FOUND:
                        return self.async_create_entry(
                            title="Govee Local Plus",
                            data={},
                            options={CONF_DEVICE_IPS: ips},
                        )
                    if result == PORT_IN_USE:
                        return self.async_abort(reason="port_in_use")
                    errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {vol.Required(CONF_DEVICE_IPS): TextSelector(TextSelectorConfig(multiple=True))}
        )
        return self.async_show_form(
            step_id="manual",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return GoveeLocalPlusOptionsFlow()


def _spec(segments: list[int]) -> str:
    return format_segment_spec(segments)


class GoveeLocalPlusOptionsFlow(OptionsFlow):
    """Manage custom effects and light addresses."""

    def __init__(self) -> None:
        """Initialize the working state."""
        self._effects: dict[str, Any] = {}
        self._editing: str | None = None
        self._name = ""
        self._background: list[int] = [0, 0, 0]
        self._fills: list[dict[str, Any]] = []
        self._queue: list[dict[str, Any]] = []

    # -- helpers ---------------------------------------------------------

    def _load(self) -> None:
        self._effects = json.loads(json.dumps(self.config_entry.options.get(CONF_EFFECTS) or {}))

    def _segment_hint(self) -> str:
        runtime = getattr(self.config_entry, "runtime_data", None)
        count = runtime.max_segments if runtime else 0
        return (
            f"Your light with the most segments has {count}."
            if count
            else f"Segments are numbered from 1 (up to {MAX_SEGMENTS})."
        )

    def _save(self, **changes: Any) -> ConfigFlowResult:
        options = dict(self.config_entry.options)
        options.update(changes)
        # Drop the leftovers of version 1 so they cannot come back.
        options.pop("custom_effects", None)
        return self.async_create_entry(data=options)

    # -- menu ------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the menu."""
        self._load()
        options = ["add_effect"]
        if self._effects:
            options += ["edit_effect", "delete_effect"]
        options += ["addresses", "json"]
        return self.async_show_menu(step_id="init", menu_options=options)

    # -- add / edit ------------------------------------------------------

    async def async_step_add_effect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name and background of a new effect."""
        self._load()
        self._editing = None
        self._fills = []
        self._queue = []
        return await self._async_effect_form(user_input)

    async def async_step_edit_effect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the effect to edit."""
        self._load()
        if user_input is not None:
            self._editing = user_input[CONF_EFFECT]
            body = self._effects[self._editing]
            self._name = self._editing
            self._background = list(body["background"])
            self._fills = []
            self._queue = [dict(fill) for fill in body["fills"]]
            return await self.async_step_effect()
        return self.async_show_form(
            step_id="edit_effect",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EFFECT): SelectSelector(
                        SelectSelectorConfig(
                            options=list(self._effects), mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )

    async def async_step_effect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name and background (shared by add and edit)."""
        return await self._async_effect_form(user_input)

    async def _async_effect_form(
        self, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = "name_required"
            elif name == NONE_EFFECT:
                errors[CONF_NAME] = "name_reserved"
            elif name in self._effects and name != self._editing:
                errors[CONF_NAME] = "name_exists"
            else:
                self._name = name
                self._background = [int(c) for c in user_input[CONF_BACKGROUND]]
                return await self.async_step_fill()

        defaults = user_input or {
            CONF_NAME: self._name if self._editing else "",
            CONF_BACKGROUND: self._background if self._editing else [0, 0, 0],
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_BACKGROUND, default=[0, 0, 0]): ColorRGBSelector(),
            }
        )
        return self.async_show_form(
            step_id="effect" if self._editing else "add_effect",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
            description_placeholders={"hint": self._segment_hint()},
        )

    async def async_step_fill(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One colour on some segments; repeats while the user adds more."""
        errors: dict[str, str] = {}
        placeholders = {"hint": self._segment_hint(), "error": "", "effect": self._name}
        current = self._queue[0] if self._queue else None

        if user_input is not None:
            if self._queue:
                self._queue.pop(0)
            try:
                segments = parse_segment_spec(user_input.get(CONF_SEGMENTS))
            except EffectConfigError as err:
                if current is not None:
                    self._queue.insert(0, current)
                errors[CONF_SEGMENTS] = "invalid_segments"
                placeholders["error"] = str(err)
            else:
                if segments:
                    self._fills.append(
                        {
                            "color": [int(c) for c in user_input[CONF_COLOR]],
                            "segments": segments,
                        }
                    )
                # An empty segment field drops this colour area.
                if user_input.get(CONF_ADD_ANOTHER) or self._queue:
                    return await self.async_step_fill()
                return self._finish_effect()

        defaults: dict[str, Any]
        if user_input is not None:
            defaults = user_input
        elif current is not None:
            defaults = {
                CONF_COLOR: current["color"],
                CONF_SEGMENTS: _spec(current["segments"]),
                CONF_ADD_ANOTHER: len(self._queue) > 1,
            }
        else:
            defaults = {CONF_COLOR: [255, 255, 255], CONF_SEGMENTS: "", CONF_ADD_ANOTHER: False}

        schema = vol.Schema(
            {
                vol.Required(CONF_COLOR, default=[255, 255, 255]): ColorRGBSelector(),
                vol.Optional(CONF_SEGMENTS, default=""): TextSelector(),
                vol.Optional(CONF_ADD_ANOTHER, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="fill",
            data_schema=self.add_suggested_values_to_schema(schema, defaults),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _finish_effect(self) -> ConfigFlowResult:
        if not self._fills:
            # Nothing was painted: treat as "stay on the fill form" via an error step.
            return self.async_abort(reason="no_fills")
        effects = dict(self._effects)
        if self._editing:
            effects.pop(self._editing, None)
        effects[self._name] = {"background": self._background, "fills": self._fills}
        try:
            parse_effects(effects)
        except EffectConfigError:
            return self.async_abort(reason="invalid_effect")
        return self._save(**{CONF_EFFECTS: effects})

    # -- delete ----------------------------------------------------------

    async def async_step_delete_effect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove effects."""
        self._load()
        if user_input is not None:
            keep = {n: b for n, b in self._effects.items() if n not in user_input[CONF_EFFECTS]}
            return self._save(**{CONF_EFFECTS: keep})
        return self.async_show_form(
            step_id="delete_effect",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EFFECTS): SelectSelector(
                        SelectSelectorConfig(options=list(self._effects), multiple=True)
                    )
                }
            ),
        )

    # -- addresses -------------------------------------------------------

    async def async_step_addresses(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the fixed light addresses."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                ips = validate_ips(user_input.get(CONF_DEVICE_IPS))
            except ValueError as err:
                errors["base"] = "invalid_ips"
                placeholders["error"] = str(err)
            else:
                return self._save(**{CONF_DEVICE_IPS: ips})
        current = user_input or {
            CONF_DEVICE_IPS: self.config_entry.options.get(CONF_DEVICE_IPS) or []
        }
        schema = vol.Schema(
            {vol.Optional(CONF_DEVICE_IPS): TextSelector(TextSelectorConfig(multiple=True))}
        )
        return self.async_show_form(
            step_id="addresses",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
            description_placeholders=placeholders,
        )

    # -- JSON (power users, backup, sharing) -----------------------------

    async def async_step_json(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit all effects as JSON, segments numbered from 1."""
        self._load()
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                effects = load_effects_json(user_input.get(CONF_EFFECTS_JSON))
            except EffectConfigError as err:
                errors["base"] = "invalid_effects"
                placeholders["error"] = str(err)
            else:
                return self._save(**{CONF_EFFECTS: effects})
        text = (
            user_input[CONF_EFFECTS_JSON]
            if user_input
            else json.dumps(self._effects, indent=2, ensure_ascii=False) if self._effects else ""
        )
        schema = vol.Schema(
            {vol.Optional(CONF_EFFECTS_JSON): TextSelector(TextSelectorConfig(multiline=True))}
        )
        return self.async_show_form(
            step_id="json",
            data_schema=self.add_suggested_values_to_schema(schema, {CONF_EFFECTS_JSON: text}),
            errors=errors,
            description_placeholders=placeholders,
        )

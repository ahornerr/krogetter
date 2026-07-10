"""Config flow for Krogetter."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    SelectSelector,
    SelectSelectorConfig,
    SelectOptionDict,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import KrogetterAPI
from .const import CONF_API_URL, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL, DOMAIN

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_API_URL, default="http://localhost:8585"): str,
})


class KrogetterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Krogetter."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Initial setup step — ask for API server URL."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_url = user_input[CONF_API_URL]
            session = async_get_clientsession(self.hass)
            api = KrogetterAPI(session, api_url)
            if await api.health_check():
                return self.async_create_entry(title="Krogetter", data=user_input)
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "KrogetterOptionsFlowHandler":
        """Get the options flow handler."""
        return KrogetterOptionsFlowHandler(config_entry)


class KrogetterOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow with a menu to add items, remove items, and configure settings."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        super().__init__(config_entry)

    def _get_api(self) -> KrogetterAPI:
        """Get the API client from hass data."""
        data = self.hass.data[DOMAIN][self.config_entry.entry_id]
        return data["api"]

    def _get_coordinator(self):
        """Get the coordinator from hass data."""
        data = self.hass.data[DOMAIN][self.config_entry.entry_id]
        return data["coordinator"]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show the main menu."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["add_item", "remove_item", "settings"],
        )

    async def async_step_add_item(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new tracked item."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api = self._get_api()
            try:
                await api.add_item(
                    url=user_input["url"],
                    label=user_input.get("label"),
                    zip_code=user_input.get("zip_code"),
                    delivery=user_input.get("delivery", False),
                    store_id=user_input.get("store_id"),
                )
            except Exception:
                errors["base"] = "add_failed"
            else:
                coordinator = self._get_coordinator()
                await coordinator.async_request_refresh()
                return self.async_create_entry(title="", data={})

        schema = vol.Schema({
            vol.Required("url"): TextSelector(
                TextSelectorConfig(type=TextSelectorType.URL)
            ),
            vol.Optional("label"): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Optional("zip_code"): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
            vol.Optional("delivery", default=False): BooleanSelector(),
            vol.Optional("store_id"): TextSelector(
                TextSelectorConfig(type=TextSelectorType.TEXT)
            ),
        })
        return self.async_show_form(
            step_id="add_item", data_schema=schema, errors=errors
        )

    async def async_step_remove_item(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a tracked item."""
        errors: dict[str, str] = {}
        if user_input is not None:
            upc = user_input["upc"]
            api = self._get_api()
            try:
                await api.remove_item(upc)
            except Exception:
                errors["base"] = "remove_failed"
            else:
                coordinator = self._get_coordinator()
                await coordinator.async_request_refresh()
                return self.async_create_entry(title="", data={})

        # Build selector from current items
        api = self._get_api()
        try:
            items = await api.get_items()
        except Exception:
            items = []

        options = [
            SelectOptionDict(value=item["upc"], label=item["label"])
            for item in items
        ]

        if not options:
            return self.async_abort(reason="no_items")

        schema = vol.Schema({
            vol.Required("upc"): SelectSelector(
                SelectSelectorConfig(options=options)
            ),
        })
        return self.async_show_form(
            step_id="remove_item", data_schema=schema, errors=errors
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure poll interval."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        schema = vol.Schema({
            vol.Optional(CONF_POLL_INTERVAL, default=current): NumberSelector(
                NumberSelectorConfig(min=60, max=86400, step=60, mode=NumberSelectorMode.BOX)
            ),
        })
        return self.async_show_form(step_id="settings", data_schema=schema)

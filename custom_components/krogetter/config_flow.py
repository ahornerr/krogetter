"""Config flow for Krogetter."""
from __future__ import annotations
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from .const import DOMAIN, CONF_API_URL, CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
from .api import KrogetterAPI

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_API_URL, default="http://localhost:8585"): str,
})

class KrogetterConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            api_url = user_input[CONF_API_URL]
            session = async_get_clientsession(self.hass)
            api = KrogetterAPI(session, api_url)
            if await api.health_check():
                return self.async_create_entry(title="Krogetter", data=user_input)
            errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_schema(config_entry) -> vol.Schema:
        return vol.Schema({
            vol.Optional(CONF_POLL_INTERVAL, default=config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=60)),
        })

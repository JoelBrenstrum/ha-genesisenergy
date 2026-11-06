"""Config flow for Genesis Energy integration."""

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    CONF_BACKFILL_DAYS,
    DEFAULT_BACKFILL_DAYS,
    DOMAIN,
    MAX_BACKFILL_DAYS,
    SENSOR_NAME,
)
from .usage import backfill_days

BACKFILL_VALIDATOR = vol.All(vol.Coerce(int), vol.Range(min=0, max=MAX_BACKFILL_DAYS))


class GenesisEnergyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Define the config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return GenesisEnergyOptionsFlow()

    async def async_step_user(self, user_input=None):
        """Show user form."""
        if user_input is not None:
            return self.async_create_entry(
                title=SENSOR_NAME,
                data={
                    CONF_EMAIL: user_input[CONF_EMAIL],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_BACKFILL_DAYS: user_input[CONF_BACKFILL_DAYS],
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): cv.string,
                    vol.Required(CONF_PASSWORD): cv.string,
                    vol.Required(
                        CONF_BACKFILL_DAYS, default=DEFAULT_BACKFILL_DAYS
                    ): BACKFILL_VALIDATOR,
                }
            ),
        )


class GenesisEnergyOptionsFlow(config_entries.OptionsFlowWithReload):
    """Allow changing how much history is imported."""

    async def async_step_init(self, user_input=None):
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = backfill_days(self.config_entry.data, self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_BACKFILL_DAYS, default=current): BACKFILL_VALIDATOR}
            ),
        )

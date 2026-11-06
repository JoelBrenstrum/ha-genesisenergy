"""Support for Genesis Energy sensors."""

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .api import GenesisEnergyApi, GenesisEnergyAuthError
from .const import PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""
    api = GenesisEnergyApi(entry.data.get("email"), entry.data.get("password"))

    try:
        await api.get_refresh_token()
    except GenesisEnergyAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except aiohttp.ClientError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = api
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

"""Genesis Energy sensors."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.components.recorder.util import get_instance
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import GenesisEnergyApi
from .const import DOMAIN, SENSOR_NAME
from .usage import backfill_days, build_statistics, fetch_start_date, parse_start, previous_sum

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)


@dataclass(frozen=True)
class UsageKind:
    """Describe one Genesis Energy fuel type."""

    key: str
    icon: str
    device_class: str
    fetch: Callable[[GenesisEnergyApi, date, date], Awaitable[list[Any]]]


USAGE_KINDS = (
    UsageKind("energy", "mdi:meter-electric", "energy", GenesisEnergyApi.get_energy_usage),
    UsageKind("gas", "mdi:fire", "gas", GenesisEnergyApi.get_gas_usage),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up the Genesis Energy sensor platform."""
    async_add_entities(
        GenesisEnergyUsageSensor(entry, entry.runtime_data, kind) for kind in USAGE_KINDS
    )


class GenesisEnergyUsageSensor(SensorEntity):
    """Import Genesis Energy hourly usage and cost into long-term statistics."""

    _attr_name = SENSOR_NAME
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = "total"
    _attr_native_value = None

    def __init__(self, entry: ConfigEntry, api: GenesisEnergyApi, kind: UsageKind) -> None:
        """Initialize the sensor."""
        self._entry = entry
        self._api = api
        self._kind = kind
        self._lock = asyncio.Lock()
        self._attr_unique_id = f"{DOMAIN}_{kind.key}_usage"
        self._attr_icon = kind.icon
        self._attr_device_class = kind.device_class
        self._consumption_id = f"{DOMAIN}:{kind.key}_consumption_daily"
        self._cost_id = f"{DOMAIN}:{kind.key}_cost_daily"
        self._backfilled_key = f"backfilled_days_{kind.key}"

    async def async_added_to_hass(self) -> None:
        """Start the first import without blocking setup, since a backfill can take minutes."""
        self._entry.async_create_background_task(
            self.hass, self.async_update(), f"{DOMAIN} {self._kind.key} import"
        )

    async def async_update(self) -> None:
        """Import any new usage."""
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._async_import()
            except Exception:
                _LOGGER.exception("Failed to import Genesis %s usage", self._kind.key)

    async def _async_import(self) -> None:
        history_days = backfill_days(self._entry.data, self._entry.options)
        backfill_pending = history_days > self._entry.data.get(self._backfilled_key, 0)
        latest = await self._async_last_stats(1)

        today = dt_util.now().date()
        start = fetch_start_date(
            today,
            datetime.fromtimestamp(latest[0]["start"], tz=UTC) if latest else None,
            history_days,
            backfill_pending,
        )
        _LOGGER.debug("Fetching Genesis %s usage from %s to %s", self._kind.key, start, today)
        usage = await self._kind.fetch(self._api, start, today)
        if not usage:
            _LOGGER.warning("No Genesis %s usage returned from %s", self._kind.key, start)
            return

        await self._async_add_statistics(usage)
        _LOGGER.debug("Imported %s hours of Genesis %s usage", len(usage), self._kind.key)

        if backfill_pending:
            self.hass.config_entries.async_update_entry(
                self._entry, data={**self._entry.data, self._backfilled_key: history_days}
            )

    async def _async_last_stats(self, count: int, statistic_id: str | None = None):
        statistic_id = statistic_id or self._consumption_id
        result = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, count, statistic_id, True, {"sum"}
        )
        return result.get(statistic_id, [])

    async def _async_add_statistics(self, usage: list[Any]) -> None:
        first_start = parse_start(usage[0])
        kw_sum = previous_sum(await self._async_last_stats(200), first_start)
        cost_sum = previous_sum(await self._async_last_stats(200, self._cost_id), first_start)
        kw_statistics, cost_statistics = build_statistics(usage, kw_sum, cost_sum)

        for statistic_id, unit, unit_class, statistics in (
            (self._consumption_id, "kWh", "energy", kw_statistics),
            (self._cost_id, "NZD", None, cost_statistics),
        ):
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=statistic_id.replace(":", " "),
                    source=DOMAIN,
                    statistic_id=statistic_id,
                    unit_class=unit_class,
                    unit_of_measurement=unit,
                ),
                [StatisticData(**s) for s in statistics],
            )

"""Constants for the Genesis Energy Energy sensors."""

from homeassistant.const import Platform

DOMAIN = "genesisenergy"
SENSOR_NAME = "Genesis Energy"

PLATFORMS = [
    Platform.SENSOR,
]

CONF_BACKFILL_DAYS = "backfill_days"
DEFAULT_BACKFILL_DAYS = 90
MAX_BACKFILL_DAYS = 1095

"""Constants for the NAD C 338 integration."""

DOMAIN = "nad_c338"
LOG_PREFIX = "[NAD-C338]"
PORT = 30001
MODEL = "NADC338"
MIN_VOLUME = -80.0
MAX_VOLUME = 12.0
DEFAULT_VOLUME_STEP = 2.0
SOURCES = ("Stream", "Wireless", "TV", "Phono", "Coax1", "Coax2", "Opt1", "Opt2")
CONF_MAX_VOLUME = "max_volume"
CONF_VOLUME_STEP = "volume_step"
CONF_CAST_ENTITY = "cast_entity"

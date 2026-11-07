"""NAD C 338 integration lifecycle."""

from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from .client import NadClient, NadConnectionError, NadModelError
from .const import LOG_PREFIX

type NadConfigEntry = ConfigEntry[NadClient]

_PLATFORMS = [Platform.MEDIA_PLAYER]


async def async_setup_entry(hass: HomeAssistant, entry: NadConfigEntry) -> None:
    """Set up an amplifier and its status entity."""
    client = NadClient(
        entry.data[CONF_HOST],
        task_factory=partial(entry.async_create_background_task, hass),
    )
    try:
        await client.start()
    except NadConnectionError as err:
        raise ConfigEntryNotReady(
            f"{LOG_PREFIX} Unable to read amplifier status"
        ) from err
    except NadModelError as err:
        raise ConfigEntryError(f"{LOG_PREFIX} The device is not a NAD C 338") from err

    entry.runtime_data = client
    forwarded = False
    try:
        await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
        forwarded = True
    finally:
        if not forwarded:
            await client.close()


async def async_unload_entry(hass: HomeAssistant, entry: NadConfigEntry) -> bool:
    """Unload the amplifier and stop its connection."""
    if not await hass.config_entries.async_unload_platforms(entry, _PLATFORMS):
        return False
    await entry.runtime_data.close()
    return True

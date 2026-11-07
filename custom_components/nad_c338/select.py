"""Display brightness selection for the NAD C 338."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NadConfigEntry
from .client import NadClient, NadCommandError, NadConnectionError
from .const import CONF_MAX_VOLUME, DOMAIN, LOG_PREFIX

_OPTIONS = ("off", "low", "normal", "bright")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the display brightness entity."""
    async_add_entities([NadBrightnessSelect(entry)])


class NadBrightnessSelect(SelectEntity):
    """Represent the observed display brightness."""

    _attr_has_entity_name = True
    _attr_translation_key = "brightness"
    _attr_should_poll = False

    def __init__(self, entry: NadConfigEntry) -> None:
        """Initialize the entity."""
        self._client: NadClient = entry.runtime_data
        self._maximum: float | None = entry.options.get(CONF_MAX_VOLUME)
        self._attr_options = list(_OPTIONS)
        self._attr_unique_id = f"{entry.entry_id}_brightness"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        """Subscribe to amplifier updates."""
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Return whether a recognized brightness is available."""
        return (
            self._client.state.available and self._client.state.brightness is not None
        )

    @property
    def current_option(self) -> str | None:
        """Return the reported brightness, or unknown."""
        value = self._client.state.brightness
        return _OPTIONS[value] if value is not None else None

    async def async_select_option(self, option: str) -> None:
        """Send a brightness selection without deliberately waking the amplifier."""
        if option not in _OPTIONS:
            raise ServiceValidationError(
                f"{LOG_PREFIX} Unsupported brightness: {option}"
            )
        try:
            await self._client.async_set_brightness(
                _OPTIONS.index(option), self._maximum
            )
        except (NadConnectionError, NadCommandError) as err:
            raise HomeAssistantError(
                f"{LOG_PREFIX} Unable to control brightness: {err}"
            ) from err

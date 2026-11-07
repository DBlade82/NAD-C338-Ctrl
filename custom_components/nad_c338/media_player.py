"""Media player status and controls for the NAD C 338."""

import math
from collections.abc import Awaitable
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import NadConfigEntry
from .client import NadClient, NadCommandError, NadConnectionError
from .const import (
    CONF_MAX_VOLUME,
    CONF_VOLUME_STEP,
    DEFAULT_VOLUME_STEP,
    DOMAIN,
    LOG_PREFIX,
    MAX_VOLUME,
    MIN_VOLUME,
    SOURCES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the amplifier entity."""
    async_add_entities([NadMediaPlayer(entry)])


class NadMediaPlayer(MediaPlayerEntity):
    """Represent and control the amplifier."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )

    def __init__(self, entry: NadConfigEntry) -> None:
        """Initialize the entity."""
        self._client: NadClient = entry.runtime_data
        self._maximum: float | None = entry.options.get(CONF_MAX_VOLUME)
        self._volume_step: float = entry.options.get(
            CONF_VOLUME_STEP, DEFAULT_VOLUME_STEP
        )
        self._attr_source_list = list(SOURCES)
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="NAD C 338",
            manufacturer="NAD",
            model="C 338",
            sw_version=self._client.state.version,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to device updates while the entity is registered."""
        await super().async_added_to_hass()
        self.async_on_remove(self._client.subscribe(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        """Return whether current power is known and the connection is healthy."""
        return self._client.state.available and self._client.state.power is not None

    @property
    def state(self) -> MediaPlayerState | None:
        """Return the amplifier power state."""
        power = self._client.state.power
        if power is None:
            return None
        return MediaPlayerState.ON if power else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        """Return volume normalized to the configured maximum."""
        volume = self._client.state.volume
        if volume is None:
            return None
        maximum = self._maximum if self._maximum is not None else MAX_VOLUME
        if maximum == MIN_VOLUME:
            return float(volume >= MIN_VOLUME)
        return min(1.0, max(0.0, (volume - MIN_VOLUME) / (maximum - MIN_VOLUME)))

    @property
    def is_volume_muted(self) -> bool | None:
        """Return the reported mute state."""
        return self._client.state.mute

    @property
    def source(self) -> str | None:
        """Return the reported source, including unknown source names."""
        return self._client.state.source

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Retain reported status even when Home Assistant hides off-state fields."""
        return {
            "volume_db": self._client.state.volume,
            "amplifier_source": self._client.state.source,
            "amplifier_muted": self._client.state.mute,
            "firmware_version": self._client.state.version,
        }

    async def _execute(self, command: Awaitable[None]) -> None:
        """Surface expected protocol failures to the service caller."""
        try:
            await command
        except (NadConnectionError, NadCommandError) as err:
            raise HomeAssistantError(
                f"{LOG_PREFIX} Unable to control amplifier: {err}"
            ) from err

    async def async_turn_on(self) -> None:
        """Turn on the amplifier, applying the configured volume limit."""
        await self._execute(self._client.async_turn_on(self._maximum))

    async def async_turn_off(self) -> None:
        """Put the amplifier in standby."""
        await self._execute(self._client.async_turn_off())

    async def async_set_volume_level(self, volume: float) -> None:
        """Set a normalized volume, scaled to the configured maximum."""
        if (
            not isinstance(volume, (int, float))
            or isinstance(volume, bool)
            or not math.isfinite(volume)
            or not 0 <= volume <= 1
        ):
            raise ServiceValidationError(
                f"{LOG_PREFIX} Volume must be a finite value between 0 and 1"
            )
        maximum = self._maximum if self._maximum is not None else MAX_VOLUME
        volume_db = MIN_VOLUME + volume * (maximum - MIN_VOLUME)
        await self._execute(self._client.async_set_volume(volume_db, self._maximum))

    async def async_volume_up(self) -> None:
        """Increase volume by the configured dB step."""
        await self._execute(
            self._client.async_step_volume(self._volume_step, self._maximum)
        )

    async def async_volume_down(self) -> None:
        """Decrease volume by the configured dB step."""
        await self._execute(
            self._client.async_step_volume(-self._volume_step, self._maximum)
        )

    async def async_mute_volume(self, mute: bool) -> None:
        """Set the amplifier mute state."""
        await self._execute(self._client.async_set_mute(mute))

    async def async_select_source(self, source: str) -> None:
        """Select one of the tested amplifier sources."""
        if source not in SOURCES:
            raise ServiceValidationError(f"{LOG_PREFIX} Unsupported source: {source}")
        await self._execute(self._client.async_select_source(source, self._maximum))

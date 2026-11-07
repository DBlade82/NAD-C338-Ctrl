"""UI configuration for the NAD C 338 integration."""

from __future__ import annotations

import ipaddress
import math
from typing import Any

import probatio as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .client import NadClient, NadConnectionError, NadModelError
from .const import (
    CONF_CAST_ENTITY,
    CONF_MAX_VOLUME,
    CONF_VOLUME_STEP,
    DEFAULT_VOLUME_STEP,
    DOMAIN,
    MAX_VOLUME,
    MIN_VOLUME,
)

_HOST_SCHEMA = vol.Schema({vol.Required(CONF_HOST): str})


def _normalize_host(value: str) -> str:
    host = value.strip().rstrip(".").lower()
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _valid_step(value: Any, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and minimum <= value <= maximum
        and value * 2 == round(value * 2)
    )


class NadConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a NAD C 338 without inventing a stable device identifier."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NadOptionsFlow:
        """Return the options flow."""
        return NadOptionsFlow()

    def _host_is_configured(self, host: str, exclude: str | None = None) -> bool:
        return any(
            entry.entry_id != exclude and _normalize_host(entry.data[CONF_HOST]) == host
            for entry in self._async_current_entries()
        )

    async def _validate_host(self, host: str) -> str | None:
        client = NadClient(host)
        try:
            await client.connect()
        except NadConnectionError:
            return "cannot_connect"
        except NadModelError:
            return "invalid_model"
        finally:
            await client.close()
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an amplifier by host."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            if not host:
                errors["host"] = "invalid_host"
            elif self._host_is_configured(host):
                return self.async_abort(reason="already_configured")
            elif error := await self._validate_host(host):
                errors["base"] = error
            elif self._host_is_configured(host):
                return self.async_abort(reason="already_configured")
            else:
                return self.async_create_entry(
                    title="NAD C 338", data={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="user", data_schema=_HOST_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the configured host."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = _normalize_host(user_input[CONF_HOST])
            if not host:
                errors["host"] = "invalid_host"
            elif self._host_is_configured(host, entry.entry_id):
                return self.async_abort(reason="already_configured")
            elif error := await self._validate_host(host):
                errors["base"] = error
            elif self._host_is_configured(host, entry.entry_id):
                return self.async_abort(reason="already_configured")
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: host}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_HOST_SCHEMA, entry.data),
            errors=errors,
        )


class NadOptionsFlow(config_entries.OptionsFlowWithReload):
    """Configure the optional volume maximum and volume step."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and validate volume settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            maximum = user_input.get(CONF_MAX_VOLUME)
            step = user_input.get(CONF_VOLUME_STEP, DEFAULT_VOLUME_STEP)
            cast_entity = user_input.get(CONF_CAST_ENTITY)
            if maximum is not None and not _valid_step(maximum, MIN_VOLUME, MAX_VOLUME):
                errors[CONF_MAX_VOLUME] = "invalid_volume"
            if not _valid_step(step, 0.5, MAX_VOLUME - MIN_VOLUME):
                errors[CONF_VOLUME_STEP] = "invalid_volume"
            if cast_entity is not None:
                registered = er.async_get(self.hass).async_get(cast_entity)
                if (
                    registered is None
                    or registered.domain != "media_player"
                    or registered.platform != "cast"
                ):
                    errors[CONF_CAST_ENTITY] = "invalid_cast_entity"
            if not errors:
                data = {CONF_VOLUME_STEP: float(step)}
                if maximum is not None:
                    data[CONF_MAX_VOLUME] = float(maximum)
                if cast_entity is not None:
                    data[CONF_CAST_ENTITY] = cast_entity
                return self.async_create_entry(data=data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_MAX_VOLUME): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_VOLUME,
                        max=MAX_VOLUME,
                        step=0.5,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_VOLUME_STEP, default=DEFAULT_VOLUME_STEP
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.5,
                        max=MAX_VOLUME - MIN_VOLUME,
                        step=0.5,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_CAST_ENTITY): EntitySelector(
                    EntitySelectorConfig(
                        filter={"domain": "media_player", "integration": "cast"}
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options if user_input is None else user_input
            ),
            errors=errors,
        )

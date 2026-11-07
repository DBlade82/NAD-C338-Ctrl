"""Tests for Home Assistant configuration flows."""

import asyncio
import shutil
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from conftest import LOCALHOST, FakeAmplifier
from homeassistant import loader
from homeassistant.auth import auth_manager_from_config
from homeassistant.config_entries import ConfigEntries, ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry, device_registry, entity_registry, frame

import custom_components.nad_c338 as integration
from custom_components.nad_c338 import config_flow
from custom_components.nad_c338.client import NadClient, NadConnectionError
from custom_components.nad_c338.const import CONF_MAX_VOLUME, CONF_VOLUME_STEP, DOMAIN


@pytest_asyncio.fixture
async def hass(tmp_path: Path) -> AsyncIterator[HomeAssistant]:
    """Create a Home Assistant instance with the local custom integration."""
    shutil.copytree(
        Path("custom_components") / DOMAIN,
        tmp_path / "custom_components" / DOMAIN,
    )
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    frame.async_setup(instance)
    await area_registry.async_load(instance)
    device_registry.async_setup(instance)
    await device_registry.async_load(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    await entity_registry.async_load(instance)
    instance.auth = await auth_manager_from_config(instance, [], [])
    try:
        yield instance
    finally:
        async with asyncio.timeout(10):
            for entry in instance.config_entries.async_entries(DOMAIN):
                await instance.config_entries.async_unload(entry.entry_id)
            await instance.async_stop(force=True)


async def test_user_flow_and_duplicate_host(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validated host creates one entry, irrespective of case."""
    monkeypatch.setattr(
        config_flow.NadConfigFlow, "_validate_host", AsyncMock(return_value=None)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "Amp.Example."}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "amp.example"

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    second = await hass.config_entries.flow.async_configure(
        second["flow_id"], {CONF_HOST: "AMP.example"}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"


def entity_state(hass: HomeAssistant, entity_id: str) -> State:
    """Get an existing entity state."""
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def use_simulated_device(monkeypatch: pytest.MonkeyPatch, port: int) -> None:
    """Direct both setup and config validation to the simulated amplifier."""

    class LocalClient(NadClient):
        def __init__(
            self,
            host: str,
            *,
            task_factory: Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
            | None = None,
        ) -> None:
            super().__init__(LOCALHOST, port, task_factory=task_factory)

    monkeypatch.setattr(config_flow, "NadClient", LocalClient)
    monkeypatch.setattr(integration, "NadClient", LocalClient)


async def test_real_setup_status_options_and_unload(
    hass: HomeAssistant,
    amplifier: tuple[FakeAmplifier, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run a real Home Assistant flow and status entity with a simulated device."""
    device, port = amplifier
    use_simulated_device(monkeypatch, port)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "localhost"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    states = hass.states.async_all("media_player")
    assert len(states) == 1
    state = states[0]
    assert state.state == "off"
    assert state.attributes["volume_db"] == -64.0
    assert state.attributes["amplifier_source"] == "Wireless"
    assert state.attributes["firmware_version"] == "1.76"

    device.values["Power"] = "On"
    device.values["Volume"] = "-30.0"
    await device.broadcast(b"Main.Power=On\r\nMain.Volume=-30.0\r\n")
    async with asyncio.timeout(2):
        while entity_state(hass, state.entity_id).state != "on":
            await asyncio.sleep(0.01)
    state = entity_state(hass, state.entity_id)
    assert state.attributes["volume_db"] == -30.0
    assert state.attributes["volume_level"] == pytest.approx(50 / 92)

    options = await hass.config_entries.options.async_init(entry.entry_id)
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {CONF_MAX_VOLUME: -40.25, CONF_VOLUME_STEP: 2.0}
    )
    assert options["type"] is FlowResultType.FORM
    assert options["errors"] is not None
    assert options["errors"][CONF_MAX_VOLUME] == "invalid_volume"
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {CONF_MAX_VOLUME: -40.0, CONF_VOLUME_STEP: 2.0}
    )
    assert options["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = entity_state(hass, state.entity_id)
    assert state.attributes["volume_level"] == 1.0
    assert state.attributes["volume_db"] == -30.0

    options = await hass.config_entries.options.async_init(entry.entry_id)
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {CONF_MAX_VOLUME: -80.0, CONF_VOLUME_STEP: 2.0}
    )
    assert options["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = entity_state(hass, state.entity_id)
    assert state.attributes["volume_level"] == 1.0

    options = await hass.config_entries.options.async_init(entry.entry_id)
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {CONF_VOLUME_STEP: 2.0}
    )
    assert options["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    state = entity_state(hass, state.entity_id)
    assert state.attributes["volume_level"] == pytest.approx(50 / 92)

    for writer in tuple(device.writers):
        writer.close()
    async with asyncio.timeout(2):
        while entity_state(hass, state.entity_id).state != "unavailable":
            await asyncio.sleep(0.01)
    device.values["Volume"] = "-45.0"
    async with asyncio.timeout(3):
        while entity_state(hass, state.entity_id).state != "on":
            await asyncio.sleep(0.01)
    state = entity_state(hass, state.entity_id)
    assert state.attributes["volume_db"] == -45.0
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not any(
        task.get_name() in {"NAD C 338 reader", "NAD C 338 supervisor"}
        for task in asyncio.all_tasks()
    )
    assert all(
        request.endswith(b"?") and b"=" not in request for request in device.requests
    )


async def test_invalid_model_and_reconfigure_duplicate(
    hass: HomeAssistant,
    amplifier: tuple[FakeAmplifier, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject other devices and prevent duplicate hosts during reconfiguration."""
    device, port = amplifier
    use_simulated_device(monkeypatch, port)
    device.values["Model"] = "OTHER"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "first.example"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] is not None
    assert result["errors"]["base"] == "invalid_model"
    device.values["Model"] = "NADC338"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "first.example"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    first = result["result"]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "second.example"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": first.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "SECOND.example."}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert first.data[CONF_HOST] == "first.example"

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": first.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "third.example"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert first.data[CONF_HOST] == "third.example"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_initial_connection_failure_requests_retry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home Assistant retries an entry when its amplifier cannot be reached."""
    monkeypatch.setattr(
        config_flow.NadConfigFlow, "_validate_host", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        NadClient, "start", AsyncMock(side_effect=NadConnectionError("offline"))
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "amplifier.example"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None and entry.reason.startswith("[NAD-C338]")
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("value", "minimum", "maximum", "expected"),
    [
        (-80.0, -80.0, 12.0, True),
        (12.0, -80.0, 12.0, True),
        (-40.25, -80.0, 12.0, False),
        (12.5, -80.0, 12.0, False),
        (float("nan"), -80.0, 12.0, False),
        (0.0, 0.5, 92.0, False),
        (2.0, 0.5, 92.0, True),
    ],
)
def test_volume_option_validation(
    value: float, minimum: float, maximum: float, expected: bool
) -> None:
    """Accept only finite supported half-decibel settings."""
    assert config_flow._valid_step(value, minimum, maximum) is expected

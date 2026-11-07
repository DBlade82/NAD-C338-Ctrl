"""Tests for Home Assistant amplifier controls."""

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from test_config_flow import use_simulated_device
from test_control_client import ControlDevice, writes

import custom_components.nad_c338 as integration
from custom_components.nad_c338 import NadConfigEntry, config_flow
from custom_components.nad_c338 import client as client_module
from custom_components.nad_c338.client import (
    NadClient,
    NadCommandError,
    NadConnectionError,
    NadState,
)
from custom_components.nad_c338.const import CONF_MAX_VOLUME, CONF_VOLUME_STEP, DOMAIN
from custom_components.nad_c338.media_player import NadMediaPlayer

pytest_plugins = ("test_config_flow", "test_control_client")


@pytest.fixture
def client() -> MagicMock:
    """Provide an amplifier client with mocked control methods."""
    client = MagicMock()
    client.state = NadState(
        available=True,
        power=True,
        volume=-30.0,
        source="External source",
        mute=False,
        version="1.76",
    )
    for method in (
        "start",
        "close",
        "async_turn_on",
        "async_turn_off",
        "async_set_volume",
        "async_step_volume",
        "async_set_mute",
        "async_select_source",
    ):
        setattr(client, method, AsyncMock())
    return client


def make_entity(
    client: MagicMock, options: dict[str, float] | None = None
) -> NadMediaPlayer:
    """Create an entity with specified entry options."""
    entry = MagicMock()
    entry.runtime_data = client
    entry.options = options or {}
    entry.entry_id = "entry"
    return NadMediaPlayer(cast(NadConfigEntry, entry))


def test_features_and_reported_state(client: MagicMock) -> None:
    """Expose the supported controls without hiding unknown device sources."""
    entity = make_entity(client)
    assert entity.supported_features == (
        MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.SELECT_SOURCE
    )
    assert entity.source_list == [
        "Stream",
        "Wireless",
        "TV",
        "Phono",
        "Coax1",
        "Coax2",
        "Opt1",
        "Opt2",
    ]
    assert entity.source == "External source"
    assert entity.extra_state_attributes["volume_db"] == -30.0
    assert entity.volume_level == pytest.approx(50 / 92)


@pytest.mark.parametrize(
    ("maximum", "slider", "expected_db"),
    [
        (None, 0.0, -80.0),
        (None, 1.0, 12.0),
        (-40.0, 0.5, -60.0),
        (-80.0, 0.0, -80.0),
        (-80.0, 1.0, -80.0),
    ],
)
async def test_slider_mapping(
    client: MagicMock, maximum: float | None, slider: float, expected_db: float
) -> None:
    """Map endpoints and the degenerate scale without changing the configured limit."""
    options = {} if maximum is None else {CONF_MAX_VOLUME: maximum}
    entity = make_entity(client, options)
    await entity.async_set_volume_level(slider)
    client.async_set_volume.assert_awaited_once_with(expected_db, maximum)
    if maximum == -80.0:
        assert entity.volume_level == 1.0
        client.state.volume = -81.0
        assert entity.volume_level == 0.0


@pytest.mark.parametrize(
    "volume", [-0.01, 1.01, float("nan"), float("inf"), True, "0.5"]
)
async def test_invalid_slider(client: MagicMock, volume: Any) -> None:
    """Reject invalid volume values without issuing a command."""
    with pytest.raises(ServiceValidationError):
        await make_entity(client).async_set_volume_level(volume)
    client.async_set_volume.assert_not_awaited()


async def test_actions_and_options(client: MagicMock) -> None:
    """Forward actions and options, including the default step and no limit."""
    entity = make_entity(client)
    await entity.async_turn_on()
    await entity.async_turn_off()
    await entity.async_volume_up()
    await entity.async_volume_down()
    await entity.async_mute_volume(True)
    await entity.async_mute_volume(False)
    await entity.async_select_source("TV")
    client.async_turn_on.assert_awaited_once_with(None)
    client.async_turn_off.assert_awaited_once_with()
    assert client.async_step_volume.await_args_list == [((2.0, None),), ((-2.0, None),)]
    assert client.async_set_mute.await_args_list == [((True,),), ((False,),)]
    client.async_select_source.assert_awaited_once_with("TV", None)

    limited = make_entity(client, {CONF_MAX_VOLUME: -40.0, CONF_VOLUME_STEP: 3.5})
    await limited.async_turn_on()
    await limited.async_volume_up()
    await limited.async_volume_down()
    await limited.async_select_source("Opt2")
    client.async_turn_on.assert_awaited_with(-40.0)
    assert client.async_step_volume.await_args_list[-2:] == [
        ((3.5, -40.0),),
        ((-3.5, -40.0),),
    ]
    client.async_select_source.assert_awaited_with("Opt2", -40.0)
    assert limited.volume_level == 1.0


async def test_invalid_source(client: MagicMock) -> None:
    """Reject even a currently reported source if it is not tested."""
    with pytest.raises(ServiceValidationError, match="Unsupported source"):
        await make_entity(client).async_select_source("External source")
    client.async_select_source.assert_not_awaited()


@pytest.mark.parametrize(
    "source", ["Stream", "Wireless", "TV", "Phono", "Coax1", "Coax2", "Opt1", "Opt2"]
)
async def test_tested_sources(client: MagicMock, source: str) -> None:
    """Forward each tested protocol source unchanged."""
    await make_entity(client).async_select_source(source)
    client.async_select_source.assert_awaited_once_with(source, None)


async def test_standby_volume_commands_return_normally(client: MagicMock) -> None:
    """Leave standby handling to the client without changing observed state."""
    client.state.power = False
    entity = make_entity(client)
    await entity.async_set_volume_level(0.5)
    await entity.async_volume_up()
    await entity.async_volume_down()
    client.async_set_volume.assert_awaited_once_with(-34.0, None)
    assert client.async_step_volume.await_count == 2
    assert entity.state == "off"
    assert entity.extra_state_attributes["volume_db"] == -30.0


@pytest.mark.parametrize(
    ("entity_method", "client_method", "args"),
    [
        ("async_turn_on", "async_turn_on", ()),
        ("async_turn_off", "async_turn_off", ()),
        ("async_set_volume_level", "async_set_volume", (0.5,)),
        ("async_volume_up", "async_step_volume", ()),
        ("async_volume_down", "async_step_volume", ()),
        ("async_mute_volume", "async_set_mute", (True,)),
        ("async_select_source", "async_select_source", ("TV",)),
    ],
)
@pytest.mark.parametrize("error_type", [NadConnectionError, NadCommandError])
async def test_known_client_failures_surface(
    client: MagicMock,
    entity_method: str,
    client_method: str,
    args: tuple[Any, ...],
    error_type: type[Exception],
) -> None:
    """Translate connection and command failures for every action."""
    error = error_type("failed")
    getattr(client, client_method).side_effect = error
    with pytest.raises(HomeAssistantError, match="failed") as caught:
        await getattr(make_entity(client), entity_method)(*args)
    assert caught.value.__cause__ is error
    assert str(caught.value).startswith("[NAD-C338]")


async def test_unexpected_error_is_not_swallowed(client: MagicMock) -> None:
    """Let unexpected client bugs propagate unchanged."""
    client.async_turn_off.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        await make_entity(client).async_turn_off()


async def test_home_assistant_service_dispatch(
    hass: HomeAssistant, client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise registered controls and surfaced failures through Home Assistant."""
    monkeypatch.setattr(integration, "NadClient", MagicMock(return_value=client))
    monkeypatch.setattr(
        config_flow.NadConfigFlow, "_validate_host", AsyncMock(return_value=None)
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "amplifier.example"}
    )
    await hass.async_block_till_done()
    states = hass.states.async_all("media_player")
    assert len(states) == 1
    entity_id = states[0].entity_id
    assert states[0].attributes["volume_db"] == -30.0
    assert states[0].attributes["source"] == "External source"
    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": entity_id, "volume_level": 0.5},
        blocking=True,
    )
    client.async_set_volume.assert_awaited_once_with(-34.0, None)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": entity_id, "source": "External source"},
            blocking=True,
        )
    client.async_select_source.assert_not_awaited()
    client.async_turn_off.side_effect = NadConnectionError("offline")
    with pytest.raises(HomeAssistantError, match="offline"):
        await hass.services.async_call(
            "media_player", "turn_off", {"entity_id": entity_id}, blocking=True
        )


async def test_real_client_home_assistant_services(
    control_device: tuple[ControlDevice, NadClient],
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control a simulated amplifier through Home Assistant and the real client."""
    device, connected_client = control_device
    use_simulated_device(monkeypatch, connected_client.port)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "localhost"}
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    options = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        options["flow_id"], {CONF_MAX_VOLUME: -25.0, CONF_VOLUME_STEP: 3.5}
    )
    await hass.async_block_till_done()
    state: State | None = hass.states.async_all("media_player")[0]
    assert state is not None
    entity_id = state.entity_id
    assert state.state == "off"
    assert state.attributes["volume_db"] == -64.0
    device.requests.clear()

    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": entity_id, "volume_level": 0.5},
        blocking=True,
    )
    assert writes(device) == []
    assert device.values["Volume"] == "-64.0"

    device.wake_volume = "-10.0"
    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": entity_id, "source": "TV"},
        blocking=True,
    )
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0", "Main.Source=TV"]
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"
    assert state.attributes["source"] == "TV"
    assert state.attributes["volume_db"] == -25.0
    assert state.attributes["volume_level"] == 1.0

    await hass.services.async_call(
        "media_player",
        "volume_set",
        {"entity_id": entity_id, "volume_level": 0.5},
        blocking=True,
    )
    assert device.values["Volume"] == "-52.5"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["volume_db"] == -52.5
    assert state.attributes["volume_level"] == pytest.approx(0.5)

    await hass.services.async_call(
        "media_player",
        "volume_mute",
        {"entity_id": entity_id, "is_volume_muted": True},
        blocking=True,
    )
    assert device.values["Mute"] == "On"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes["is_volume_muted"] is True

    prior_writes = writes(device)
    device.ignore_queries.update({"Power", "Model"})
    timeout = client_module._TIMEOUT
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    with pytest.raises(
        HomeAssistantError, match="Status query failed: Power"
    ) as caught:
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": 0.7},
            blocking=True,
        )
    assert isinstance(caught.value.__cause__, NadConnectionError)
    assert writes(device) == prior_writes
    assert device.values["Volume"] == "-52.5"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"
    device.values["Volume"] = "-49.0"
    model_queries = device.requests.count("Main.Model?")
    device.ignore_queries.clear()
    monkeypatch.setattr(client_module, "_TIMEOUT", timeout)
    async with asyncio.timeout(10):
        while True:
            state = hass.states.get(entity_id)
            if (
                state is not None
                and state.state == "on"
                and state.attributes.get("volume_db") == -49.0
            ):
                break
            await asyncio.sleep(0.01)
    assert device.requests.count("Main.Model?") > model_queries
    assert state.attributes["source"] == "TV"
    assert state.attributes["is_volume_muted"] is True

    device.ignore_writes.add("Power")
    device.ignore_queries.add("Model")
    with pytest.raises(HomeAssistantError, match="Standby not confirmed") as caught:
        await hass.services.async_call(
            "media_player", "turn_off", {"entity_id": entity_id}, blocking=True
        )
    assert isinstance(caught.value.__cause__, NadCommandError)
    assert device.values["Power"] == "On"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"

    device.values["Volume"] = "-48.0"
    model_queries = device.requests.count("Main.Model?")
    device.ignore_writes.clear()
    device.ignore_queries.clear()
    async with asyncio.timeout(10):
        while True:
            state = hass.states.get(entity_id)
            if (
                state is not None
                and state.state == "on"
                and state.attributes.get("volume_db") == -48.0
            ):
                break
            await asyncio.sleep(0.01)
    assert device.requests.count("Main.Model?") > model_queries
    assert state.attributes["source"] == "TV"
    assert state.attributes["is_volume_muted"] is True

    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert device.values["Power"] == "Off"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["volume_db"] == -48.0

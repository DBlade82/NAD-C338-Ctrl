"""Tests for observed and controlled amplifier display brightness."""

import asyncio
import logging

import pytest
from conftest import LOCALHOST, FakeAmplifier
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from test_client import wait_for
from test_config_flow import entity_state, use_simulated_device
from test_control_client import ControlDevice, writes

from custom_components.nad_c338 import client as client_module
from custom_components.nad_c338.client import (
    NadClient,
    NadCommandError,
    NadConnectionError,
)
from custom_components.nad_c338.const import DOMAIN

pytest_plugins = ("test_config_flow", "test_control_client")


@pytest.mark.parametrize("power", ["On", "Off"])
@pytest.mark.parametrize("level", range(4))
async def test_set_all_levels_directly(
    control_device: tuple[ControlDevice, NadClient], power: str, level: int
) -> None:
    """Write all levels in either power state, then separately query the result."""
    device, client = control_device
    device.values["Power"] = power
    device.check_setter_barrier = True
    await client.async_set_brightness(level, None)
    expected = [
        "Main.Power?",
        f"Main.Brightness={level}",
        "Main.Power?",
        "Main.Brightness?",
    ]
    if power == "Off":
        expected.append("Main.Power?")
    assert device.requests == expected
    assert not device.early_data
    assert device.values["Power"] == power
    assert client.state.brightness == level


@pytest.mark.parametrize("delayed", [False, True])
@pytest.mark.parametrize("silent", [False, True])
async def test_unexpected_wake_applies_limit(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    delayed: bool,
    silent: bool,
) -> None:
    """An observed unexpected wake receives the existing post-wake safeguards."""
    device, client = control_device
    device.brightness_wakes = not delayed
    device.brightness_query_wakes = delayed
    device.wake_volume = "-10.0"
    device.wake_silence = 0.13 if silent else 0
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 1.0)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.001)
    await client.async_set_brightness(1, -40.0)
    assert writes(device) == ["Main.Brightness=1", "Main.Volume=-40.0"]
    assert client.state.power is True
    assert client.state.volume == -40.0
    assert client.state.brightness == 1
    assert "unexpectedly woke" in caplog.text


async def test_unexpected_wake_without_limit_warns_accurately(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Do not imply that a maximum exists when no maximum is configured."""
    device, client = control_device
    device.brightness_wakes = True
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.001)
    await client.async_set_brightness(1, None)
    assert writes(device) == ["Main.Brightness=1"]
    assert "no volume limit is configured" in caplog.text
    assert "exceed the configured limit" not in caplog.text


async def test_seven_second_wake_silence_still_clamps(
    control_device: tuple[ControlDevice, NadClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real-duration wake pause longer than the normal timeout is tolerated."""
    device, client = control_device
    device.brightness_wakes = True
    device.wake_volume = "-10.0"
    device.wake_silence = 7.0
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.001)
    await client.async_set_brightness(1, -40.0)
    assert device.requests.count("Main.Power?") >= 3
    assert writes(device) == ["Main.Brightness=1", "Main.Volume=-40.0"]
    assert client.state.volume == -40.0


async def test_missing_echo_still_attempts_wake_safety_then_fails(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Try to limit a silent wake after a lost echo, but report the failed write."""
    device, client = control_device
    device.brightness_wakes = True
    device.wake_volume = "-10.0"
    device.wake_silence = 0.13
    device.ignore_echoes.add("Brightness")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 1.0)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.001)
    with pytest.raises(NadConnectionError, match="Command failed: Brightness"):
        await client.async_set_brightness(1, -40.0)
    assert writes(device) == ["Main.Brightness=1", "Main.Volume=-40.0"]
    assert device.values["Volume"] == "-40.0"
    assert "may have woken" in caplog.text
    assert not client.state.available


async def test_unresponsive_standby_write_fails_without_retry(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wake that stays silent past the deadline is reported as unverified."""
    device, client = control_device
    device.brightness_wakes = True
    device.wake_silence = 1.0
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.03)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0.15)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.001)
    with pytest.raises(NadConnectionError, match="Power not verified"):
        await client.async_set_brightness(1, -40.0)
    assert writes(device) == ["Main.Brightness=1"]
    assert "may have woken" in caplog.text
    assert not client.state.available


async def test_brightness_serializes_with_other_commands(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    """Keep control traffic behind an in-flight status query."""
    device, client = control_device
    device.block_query = "Source"
    query = asyncio.create_task(client._query("Source"))
    await device.query_started.wait()
    command = asyncio.create_task(client.async_set_brightness(1, None))
    await asyncio.sleep(0.01)
    assert device.requests == ["Main.Source?"]
    device.release_query.set()
    await asyncio.gather(query, command)
    assert device.requests[1:3] == ["Main.Power?", "Main.Brightness=1"]


async def test_setter_echo_is_not_published_as_actual_brightness(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    """A rejected write does not momentarily publish its untrusted echo."""
    device, client = control_device
    device.ignore_writes.add("Brightness")
    device.block_query_after_write = "Power"
    command = asyncio.create_task(client.async_set_brightness(1, None))
    await device.query_started.wait()
    assert client.state.brightness == 2
    device.release_query.set()
    with pytest.raises(NadCommandError, match="Brightness change not confirmed"):
        await command


@pytest.mark.parametrize("failure", ["echo", "confirmation"])
async def test_failure_is_visible_without_retries(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    """An uncertain standby write invalidates state and warns without retrying."""
    device, client = control_device
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    error: type[Exception]
    if failure == "echo":
        device.ignore_echoes.add("Brightness")
        error = NadConnectionError
    else:
        device.ignore_writes.add("Brightness")
        error = NadCommandError
    with pytest.raises(error):
        await client.async_set_brightness(1, -40.0)
    assert writes(device) == ["Main.Brightness=1"]
    assert not client.state.available
    assert "may have woken" in caplog.text


async def test_optional_unknown_updates_and_reconnect(
    amplifier: tuple[FakeAmplifier, int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing and unknown brightness never block core state or cause warnings."""
    device, port = amplifier
    device.ignore.add("Brightness")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_BRIGHTNESS_PROBE_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_POLL_INTERVAL", 0.05)
    caplog.set_level(logging.WARNING, logger=client_module.__name__)
    client = NadClient(LOCALHOST, port)
    try:
        await client.start()
        assert client.state.available and client.state.power is False
        assert client.state.brightness is None
        await wait_for(lambda: device.requests.count(b"Main.Power?") >= 3)
        assert device.requests.count(b"Main.Brightness?") == 1
        await device.broadcast(b"Main.Brightness=3\r\n")
        await wait_for(lambda: client.state.brightness == 3)
        await device.broadcast(b"Main.Brightness=unexpected\r\n")
        await wait_for(lambda: client.state.brightness is None)
        assert not caplog.records
        for writer in tuple(device.writers):
            writer.close()
        await wait_for(lambda: not client.state.available)
        assert client.state.brightness is None
        device.values["Brightness"] = "1"
        device.ignore.clear()
        await wait_for(lambda: client.state.available and device.connections >= 2)
        assert client.state.brightness == 1
    finally:
        await client.close()


async def test_missing_brightness_does_not_block_normal_controls(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Probe once on connect, then poll and control without optional delays."""
    device, client = control_device
    await client.close()
    device.ignore_queries.add("Brightness")
    device.values["Power"] = "On"
    monkeypatch.setattr(client_module, "_BRIGHTNESS_PROBE_TIMEOUT", 0.03)
    monkeypatch.setattr(client_module, "_POLL_INTERVAL", 0.03)
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.2)
    await client.start()
    assert client.state.available and client.state.brightness is None
    device.requests.clear()
    await wait_for(lambda: device.requests.count("Main.Power?") >= 2)
    assert "Main.Brightness?" not in device.requests
    await client.async_set_mute(True)
    assert client.state.available and client.state.mute is True
    assert writes(device) == ["Main.Mute=On"]


async def test_home_assistant_select_services_and_updates(
    control_device: tuple[ControlDevice, NadClient],
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose the select on the existing device and dispatch service failures."""
    device, connected_client = control_device
    use_simulated_device(monkeypatch, connected_client.port)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "localhost"}
    )
    await hass.async_block_till_done()
    state = hass.states.async_all("select")[0]
    media = hass.states.async_all("media_player")[0]
    assert media.state == "off"
    assert state.state == "normal"
    assert state.attributes["options"] == ["off", "low", "normal", "bright"]
    from homeassistant.helpers import entity_registry

    registry = entity_registry.async_get(hass)
    select_entry = registry.async_get(state.entity_id)
    media_entry = registry.async_get(media.entity_id)
    assert select_entry is not None and media_entry is not None
    assert select_entry.device_id == media_entry.device_id
    device.requests.clear()
    for level, option in enumerate(("off", "low", "normal", "bright")):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": state.entity_id, "option": option},
            blocking=True,
        )
        assert writes(device)[-1] == f"Main.Brightness={level}"
        assert entity_state(hass, state.entity_id).state == option
    assert writes(device) == [f"Main.Brightness={level}" for level in range(4)]
    for writer in tuple(device.writers):
        writer.write(b"Main.Brightness=1\r\n")
        await writer.drain()
    await wait_for(lambda: entity_state(hass, state.entity_id).state == "low")
    for writer in tuple(device.writers):
        writer.write(b"Main.Brightness=unknown\r\n")
        await writer.drain()
    await wait_for(lambda: entity_state(hass, state.entity_id).state == "unavailable")
    for writer in tuple(device.writers):
        writer.write(b"Main.Brightness=1\r\n")
        await writer.drain()
    await wait_for(lambda: entity_state(hass, state.entity_id).state == "low")
    device.ignore_writes.add("Brightness")
    with pytest.raises(HomeAssistantError, match="Brightness change not confirmed"):
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": state.entity_id, "option": "off"},
            blocking=True,
        )
    assert writes(device) == [
        "Main.Brightness=0",
        "Main.Brightness=1",
        "Main.Brightness=2",
        "Main.Brightness=3",
        "Main.Brightness=0",
    ]
    assert entity_state(hass, state.entity_id).state == "unavailable"
    device.ignore_writes.clear()
    device.values["Brightness"] = "2"
    await wait_for(lambda: entity_state(hass, state.entity_id).state == "normal", 4)


async def test_unreported_brightness_only_disables_select(
    amplifier: tuple[FakeAmplifier, int],
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing optional probe leaves the media player healthy and usable."""
    device, port = amplifier
    device.ignore.add("Brightness")
    monkeypatch.setattr(client_module, "_BRIGHTNESS_PROBE_TIMEOUT", 0.03)
    use_simulated_device(monkeypatch, port)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "localhost"}
    )
    await hass.async_block_till_done()
    media = hass.states.async_all("media_player")[0]
    selection = hass.states.async_all("select")[0]
    assert media.state == "off"
    assert selection.state == "unavailable"
    await device.broadcast(b"Main.Brightness=2\r\n")
    await wait_for(lambda: entity_state(hass, selection.entity_id).state == "normal")

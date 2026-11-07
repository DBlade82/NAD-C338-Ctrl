"""Simulated TCP tests for control sequencing and confirmation."""

import asyncio
import logging
import socket
import time
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from custom_components.nad_c338 import client as client_module
from custom_components.nad_c338.client import (
    NadClient,
    NadCommandError,
    NadConnectionError,
)
from custom_components.nad_c338.const import SOURCES

LOCALHOST = socket.gethostbyname("localhost")


class ControlDevice:
    """A simulated amplifier with configurable command and query behavior."""

    def __init__(self) -> None:
        self.values = {
            "Model": "NADC338",
            "Power": "Off",
            "Volume": "-64.0",
            "Source": "Wireless",
            "Mute": "Off",
            "Version": "1.76",
        }
        self.requests: list[str] = []
        self.ignore_queries: set[str] = set()
        self.ignore_writes: set[str] = set()
        self.ignore_echoes: set[str] = set()
        self.check_setter_barrier = False
        self.early_data = False
        self.echo_sent = asyncio.Event()
        self.power_observations: list[str] = []
        self.woken = False
        self.source_query_count = 0
        self.restore_volume_on_source: tuple[int, str] | None = None
        self.block_query: str | None = None
        self.query_started = asyncio.Event()
        self.release_query = asyncio.Event()
        self.writers: set[asyncio.StreamWriter] = set()
        self.wake_volume: str | None = None
        self.wake_silence = 0.0
        self.woke_at = 0.0
        self.drop_wake_queries: set[int] = set()
        self.wake_query_count = 0

    async def read_command(self, reader: asyncio.StreamReader) -> str:
        """Parse complete known commands without waiting for a later query."""
        assert await reader.readexactly(5) == b"Main."
        field = ""
        while (char := (await reader.readexactly(1)).decode("ascii")) not in "?=":
            field += char
        if char == "?":
            return f"Main.{field}?"
        if field in ("Power", "Mute"):
            value = (await reader.readexactly(2)).decode("ascii")
            if value == "Of":
                value += (await reader.readexactly(1)).decode("ascii")
        elif field == "Source":
            value = ""
            while value not in SOURCES:
                value += (await reader.readexactly(1)).decode("ascii")
        elif field == "Volume":
            value = (await reader.readuntil(b".")).decode("ascii")
            value += (await reader.readexactly(1)).decode("ascii")
        else:
            raise ValueError(f"Unexpected write field: {field}")
        return f"Main.{field}={value}"

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Respond to each setter independently of any subsequent query."""
        self.writers.add(writer)
        try:
            while True:
                command = await self.read_command(reader)
                self.requests.append(command)
                part = command.removeprefix("Main.")
                if part.endswith("?"):
                    field = part[:-1]
                    if field == self.block_query:
                        self.query_started.set()
                        await self.release_query.wait()
                    silent = (
                        self.woken
                        and asyncio.get_running_loop().time()
                        < self.woke_at + self.wake_silence
                    )
                    if self.woken:
                        self.wake_query_count += 1
                        silent = (
                            silent or self.wake_query_count in self.drop_wake_queries
                        )
                    if field not in self.ignore_queries and not silent:
                        value = self.values[field]
                        if field == "Power" and self.woken and self.power_observations:
                            value = self.power_observations.pop(0)
                        if field == "Source" and self.woken:
                            self.source_query_count += 1
                            if (
                                self.restore_volume_on_source is not None
                                and self.source_query_count
                                == self.restore_volume_on_source[0]
                            ):
                                self.values["Volume"] = self.restore_volume_on_source[1]
                        writer.write(f"Main.{field}={value}\r\n".encode())
                else:
                    field, value = part.split("=", 1)
                    if field not in self.ignore_writes:
                        self.values[field] = value
                        if field == "Power" and value == "On":
                            self.woken = True
                            self.woke_at = asyncio.get_running_loop().time()
                            if self.wake_volume is not None:
                                self.values["Volume"] = self.wake_volume
                            self.values["Mute"] = "Off"
                    if self.check_setter_barrier:
                        try:
                            self.early_data = bool(
                                await asyncio.wait_for(reader.read(1), timeout=0.05)
                            )
                        except TimeoutError:
                            pass
                        if self.early_data:
                            return
                    if field not in self.ignore_echoes:
                        # Echo even when a write did not take effect.
                        writer.write(f"Main.{field}={value}\r\n".encode())
                        await writer.drain()
                        self.echo_sent.set()
                await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            await writer.wait_closed()


@pytest_asyncio.fixture
async def control_device() -> AsyncIterator[tuple[ControlDevice, NadClient]]:
    """Connect a client to an ephemeral local simulated device."""
    device = ControlDevice()
    server = await asyncio.start_server(device.serve, host=LOCALHOST, port=0)
    port = server.sockets[0].getsockname()[1]
    client = NadClient(LOCALHOST, port)
    try:
        await client.connect()
        device.requests.clear()
        yield device, client
    finally:
        device.release_query.set()
        await client.close()
        server.close()
        await server.wait_closed()
        for writer in tuple(device.writers):
            writer.close()
            await writer.wait_closed()


def writes(device: ControlDevice) -> list[str]:
    """Return recorded write commands."""
    return [request for request in device.requests if "=" in request]


async def test_volume_commands_do_not_wake_standby(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    await client.async_set_volume(-20, None)
    await client.async_step_volume(2, None)
    assert writes(device) == []
    assert device.requests == [
        "Main.Power?",
        "Main.Volume?",
        "Main.Power?",
        "Main.Volume?",
    ]


@pytest.mark.parametrize(
    ("volume", "delta", "maximum", "expected"),
    [
        (-20.0, 0.26, None, "-19.5"),
        (-20.0, -0.26, None, "-20.5"),
        (-10.0, 2.0, -20.0, "-20.0"),
        (-79.5, -4.0, None, "-80.0"),
        (11.5, 5.0, None, "12.0"),
        (-21.0, 1.0, -20.2, "-20.5"),
    ],
)
async def test_step_is_clamped_absolute_target(
    control_device: tuple[ControlDevice, NadClient],
    volume: float,
    delta: float,
    maximum: float | None,
    expected: str,
) -> None:
    device, client = control_device
    device.values.update(Power="On", Volume=str(volume))
    await client.async_step_volume(delta, maximum)
    assert writes(device) == [f"Main.Volume={expected}"]
    assert client.state.volume == float(expected)


async def test_source_wake_clamps_before_selection(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.wake_volume = "-10.0"
    await client.async_select_source("TV", -25.0)
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0", "Main.Source=TV"]
    source_index = device.requests.index("Main.Source=TV")
    clamp_index = device.requests.index("Main.Volume=-25.0")
    assert "Main.Volume?" in device.requests[clamp_index + 1 : source_index]
    assert client.state.source == "TV"


@pytest.mark.parametrize(
    ("volume", "maximum", "expected"),
    [(1e308, None, "12.0"), (-1e308, None, "-80.0"), (-20.25, -20.2, "-20.5")],
)
async def test_absolute_volume_rounding_and_bounds(
    control_device: tuple[ControlDevice, NadClient],
    volume: float,
    maximum: float | None,
    expected: str,
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    await client.async_set_volume(volume, maximum)
    assert writes(device) == [f"Main.Volume={expected}"]


async def test_existing_power_on_does_not_send_wake(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values.update(Power="On", Volume="-10")
    await client.async_turn_on(-20)
    assert writes(device) == []
    assert device.values["Volume"] == "-10"


async def test_source_on_does_not_clamp_existing_volume(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values.update(Power="On", Volume="-10")
    await client.async_select_source("TV", -20)
    assert writes(device) == ["Main.Source=TV"]
    assert device.values["Volume"] == "-10"


async def test_wait_for_setter_reply_before_sending_query(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.check_setter_barrier = True
    await client.async_select_source("TV", None)
    assert device.echo_sent.is_set()
    assert not device.early_data
    write_index = device.requests.index("Main.Source=TV")
    assert device.requests[write_index + 1 :] == ["Main.Power?", "Main.Source?"]


async def test_missing_setter_reply_sends_no_followup_query(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.ignore_echoes.add("Source")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    with pytest.raises(NadConnectionError):
        await client.async_select_source("TV", None)
    assert device.requests == ["Main.Power?", "Main.Source=TV"]


async def test_wake_requires_consecutive_ready_observations(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.power_observations = ["On", "Off", "On", "On"]
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0)
    await client.async_select_source("TV", None)
    before_source = device.requests[: device.requests.index("Main.Source=TV")]
    assert before_source.count("Main.Power?") == 5
    assert before_source.count("Main.Source?") == 3


async def test_intermittent_readiness_does_not_select_source(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.power_observations = ["On", "Off", "On", "Off", "On"]
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0)
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", None)
    assert writes(device) == ["Main.Power=On"]


async def test_failed_wake_echo_never_authorizes_source(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.ignore_writes.add("Power")
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0)
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On"]


async def test_readiness_query_timeout_never_authorizes_source(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.ignore_queries.add("Source")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0.1)
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On"]


async def test_wake_clamps_before_unresponsive_source_query(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.wake_volume = "-10"
    device.ignore_queries.add("Source")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0.1)
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0"]
    assert device.requests.index("Main.Volume=-25.0") < device.requests.index(
        "Main.Source?"
    )
    assert device.values["Volume"] == "-25.0"


@pytest.mark.parametrize("restore_query", [1, 2])
async def test_wake_never_sends_second_clamp_after_above_limit_recheck(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    restore_query: int,
) -> None:
    device, client = control_device
    device.wake_volume = "-10"
    device.restore_volume_on_source = (restore_query, "-5")
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0)
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0"]


async def test_missing_wake_echo_warns_that_limit_is_unverified(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    device, client = control_device
    device.ignore_echoes.add("Power")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    with caplog.at_level(logging.WARNING), pytest.raises(NadConnectionError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On"]
    assert "may have been sent" in caplog.text


async def test_wake_tolerates_query_silence_on_same_connection(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.wake_silence = 0.3
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.01)
    await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On", "Main.Source=TV"]
    assert client.state.available
    assert client.state.source == "TV"
    assert device.requests.index("Main.Power=On") + 1 < len(device.requests)
    assert device.requests[device.requests.index("Main.Power=On") + 1] == "Main.Volume?"


async def test_clamp_tolerates_silence_after_readiness(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.wake_volume = "-10"
    device.drop_wake_queries = {3}
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.01)
    await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0", "Main.Source=TV"]
    assert float(device.values["Volume"]) <= -25


async def test_wake_deadline_bounds_silent_queries_without_writes(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    device, client = control_device
    device.ignore_queries.add("Volume")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.2)
    monkeypatch.setattr(client_module, "_WAKE_TIMEOUT", 0.25)
    monkeypatch.setattr(client_module, "_READY_INTERVAL", 0.01)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING), pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert time.monotonic() - started < 0.4
    assert writes(device) == ["Main.Power=On"]
    assert "volume limit was not verified" in caplog.text


async def test_failed_clamp_never_authorizes_source(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.wake_volume = "-10"
    device.ignore_writes.add("Volume")
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", -25)
    assert writes(device) == ["Main.Power=On", "Main.Volume=-25.0"]


async def test_timeout_does_not_retry_write(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.ignore_queries.add("Source")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    observed: list[tuple[bool, str | None]] = []
    client.subscribe(
        lambda: observed.append((client.state.available, client.state.source))
    )
    with pytest.raises(NadConnectionError):
        await client.async_select_source("TV", None)
    assert device.echo_sent.is_set()
    assert writes(device) == ["Main.Source=TV"]
    assert not client.state.available
    assert client.state.source is None
    assert client._writer is not None and client._writer.is_closing()
    assert observed[-1] == (False, None)


@pytest.mark.parametrize(("field", "action"), [("Power", "off"), ("Mute", "mute")])
async def test_echo_is_not_confirmation(
    control_device: tuple[ControlDevice, NadClient], field: str, action: str
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.ignore_writes.add(field)
    with pytest.raises(NadCommandError):
        if action == "off":
            await client.async_turn_off()
        else:
            await client.async_set_mute(True)
    assert len(writes(device)) == 1
    assert not client.state.available
    assert client.state.power is None
    assert client.state.mute is None


async def test_mute_and_standby_confirmation(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    await client.async_set_mute(True)
    assert client.state.mute is True
    await client.async_turn_on(None)
    assert device.values["Mute"] == "Off"
    await client.async_turn_off()
    assert client.state.power is False


async def test_command_and_poll_query_share_lock(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.block_query = "Volume"
    command = asyncio.create_task(client.async_set_volume(-20, None))
    await asyncio.wait_for(device.query_started.wait(), 1)
    poll = asyncio.create_task(client._query("Source"))
    await asyncio.sleep(0)
    assert not poll.done()
    device.block_query = None
    device.release_query.set()
    await asyncio.gather(command, poll)
    assert device.requests.index("Main.Source?") > device.requests.index(
        "Main.Volume=-20.0"
    )


async def test_cancellation_prevents_followup_source_write(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.block_query = "Volume"
    task = asyncio.create_task(client.async_select_source("TV", -25))
    await asyncio.wait_for(device.query_started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.state.available
    assert client.state.power is None
    device.release_query.set()
    await asyncio.sleep(0)
    assert writes(device) == ["Main.Power=On"]


async def test_cancelled_confirmation_invalidates_echo(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.block_query = "Source"
    task = asyncio.create_task(client.async_select_source("TV", None))
    await asyncio.wait_for(device.query_started.wait(), 1)
    assert client.state.available and client.state.source == "TV"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.state.available
    assert client.state.source is None
    assert client._writer is not None and client._writer.is_closing()
    assert writes(device) == ["Main.Source=TV"]


async def test_cancel_while_waiting_for_lock_keeps_connection(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    device.values["Power"] = "On"
    device.block_query = "Volume"
    first = asyncio.create_task(client.async_set_volume(-20, None))
    await asyncio.wait_for(device.query_started.wait(), 1)
    waiting = asyncio.create_task(client.async_turn_off())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert client.state.available
    device.block_query = None
    device.release_query.set()
    await first
    assert client.state.available
    assert writes(device) == ["Main.Volume=-20.0"]


async def test_supervisor_refreshes_after_unconfirmed_command(
    control_device: tuple[ControlDevice, NadClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device, client = control_device
    await client.close()
    await client.start()
    device.requests.clear()
    device.values["Power"] = "On"
    device.ignore_writes.add("Source")
    device.ignore_queries.add("Source")
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.05)
    with pytest.raises(NadConnectionError):
        await client.async_select_source("TV", None)
    assert not client.state.available
    device.ignore_queries.clear()
    async with asyncio.timeout(3):
        while not client.state.available:
            await asyncio.sleep(0.01)
    assert client.state.source == "Wireless"
    assert writes(device) == ["Main.Source=TV"]


async def test_invalid_inputs_and_disconnected_state(
    control_device: tuple[ControlDevice, NadClient],
) -> None:
    device, client = control_device
    assert SOURCES == (
        "Stream",
        "Wireless",
        "TV",
        "Phono",
        "Coax1",
        "Coax2",
        "Opt1",
        "Opt2",
    )
    with pytest.raises(NadCommandError):
        await client.async_select_source("Invalid", None)
    with pytest.raises(NadCommandError):
        await client.async_set_volume(float("nan"), None)
    with pytest.raises(NadCommandError):
        await client.async_set_volume(-20, float("inf"))
    with pytest.raises(NadCommandError):
        await client.async_turn_on(float("inf"))
    with pytest.raises(NadCommandError):
        await client.async_select_source("TV", float("inf"))
    assert not device.requests
    assert client.state.available
    await client.close()
    with pytest.raises(NadConnectionError):
        await client.async_set_volume(-20, None)

"""Tests for the read-only NAD protocol client."""

import asyncio
import logging
from collections.abc import Callable

import pytest
from conftest import LOCALHOST, FakeAmplifier

from custom_components.nad_c338 import client as client_module
from custom_components.nad_c338.client import (
    NadClient,
    NadConnectionError,
    NadModelError,
)


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Wait for an observable asynchronous condition."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_read_status_and_unsolicited_updates(
    amplifier: tuple[FakeAmplifier, int],
    caplog: pytest.LogCaptureFixture,
) -> None:
    device, port = amplifier
    caplog.set_level(logging.DEBUG, logger=client_module.__name__)
    client = NadClient(LOCALHOST, port)
    try:
        await client.start()
        assert client.state.available
        assert client.state.power is False
        assert client.state.volume == -64.0
        assert client.state.source == "Wireless"
        assert client.state.mute is False
        assert client.state.version == "1.76"
        await device.broadcast(
            b"Main.Power=On\r\nMain.Volume=-33.5\r\nMain.Source=UnknownInput\r\n"
        )
        await wait_for(lambda: client.state.source == "UnknownInput")
        assert client.state.power is True
        assert client.state.volume == -33.5
        await device.broadcast(b"Main.Volume=nan\r\nMain.Power=maybe\r\n")
        await asyncio.sleep(0.02)
        assert client.state.volume == -33.5
        assert client.state.power is True
        await device.broadcast(b"invalid\r\n")
        await wait_for(
            lambda: any("malformed" in r.getMessage() for r in caplog.records)
        )
        assert all(
            r.getMessage().startswith("[NAD-C338]")
            for r in caplog.records
            if r.name == client_module.__name__
        )
        assert all(
            request.endswith(b"?") and b"=" not in request
            for request in device.requests
        )
    finally:
        await client.close()


async def test_wrong_model_and_refused_connection(
    amplifier: tuple[FakeAmplifier, int],
) -> None:
    device, port = amplifier
    device.values["Model"] = "OTHER"
    client = NadClient(LOCALHOST, port)
    with pytest.raises(NadModelError):
        await client.connect()
    assert not client.state.available

    server = await asyncio.start_server(device.serve, host=LOCALHOST, port=0)
    closed_port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(NadConnectionError):
        await NadClient(LOCALHOST, closed_port).connect()


async def test_stalled_connection_is_unavailable_then_recovers(
    amplifier: tuple[FakeAmplifier, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    device, port = amplifier
    monkeypatch.setattr(client_module, "_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.1)
    client = NadClient(LOCALHOST, port)
    observed: list[bool] = []
    client.subscribe(lambda: observed.append(client.state.available))
    try:
        await client.start()
        device.ignore.add("Power")
        await wait_for(lambda: not client.state.available)
        assert client.state.power is None
        device.ignore.clear()
        device.values["Volume"] = "-35.0"
        await wait_for(lambda: client.state.available and device.connections >= 2)
        assert client.state.volume == -35.0
        assert False in observed
    finally:
        await client.close()


async def test_missing_firmware_and_close_during_reconnect(
    amplifier: tuple[FakeAmplifier, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Firmware is optional, and cancelling a reconnect closes its reader."""
    device, port = amplifier
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.1)
    monkeypatch.setattr(client_module, "_POLL_INTERVAL", 0.05)
    device.ignore.add("Version")
    client = NadClient(LOCALHOST, port)
    try:
        await client.start()
        assert client.state.available
        assert client.state.version is None
        await device.broadcast(b"Main.Version=1.76\r\n")
        await wait_for(lambda: client.state.version == "1.76")
        device.ignore.add("Model")
        for writer in tuple(device.writers):
            writer.close()
        await wait_for(lambda: device.connections >= 2)
    finally:
        await client.close()
    assert not client.state.available
    await wait_for(lambda: not device.writers)


async def test_close_after_cancelled_close_waiter(
    amplifier: tuple[FakeAmplifier, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancelled transport close waiter does not abort subsequent cleanup."""
    _, port = amplifier
    client = NadClient(LOCALHOST, port)
    await client.connect()
    assert client._writer is not None
    waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def wait_closed() -> None:
        await waiter

    monkeypatch.setattr(client._writer, "wait_closed", wait_closed)
    closing = asyncio.create_task(client._close_connection())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await client.close()
    assert not client.state.available


async def test_outage_logging_is_rate_limited_until_stable(
    amplifier: tuple[FakeAmplifier, int],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log one outage across failed and flapping reconnects, then report recovery."""
    device, port = amplifier
    monkeypatch.setattr(client_module, "_POLL_INTERVAL", 0.05)
    monkeypatch.setattr(client_module, "_TIMEOUT", 0.1)
    monkeypatch.setattr(client_module, "_MAX_BACKOFF", 0.02)
    caplog.set_level(logging.INFO, logger=client_module.__name__)
    client = NadClient(LOCALHOST, port)
    original_connect = client.connect
    flaps = 0
    flap_connection = True

    async def connect_then_drop() -> None:
        nonlocal flaps
        await original_connect()
        if flap_connection:
            assert client._writer is not None
            client._writer.close()
            flaps += 1

    def messages(level: int) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == client_module.__name__ and record.levelno == level
        ]

    try:
        await client.start()
        monkeypatch.setattr(client, "connect", connect_then_drop)
        device.ignore.add("Power")
        await wait_for(lambda: len(messages(logging.WARNING)) == 1)
        await wait_for(lambda: device.connections >= 3, timeout=3.0)
        assert messages(logging.WARNING) == [
            "[NAD-C338] Amplifier unavailable; reconnecting"
        ]
        assert messages(logging.INFO) == []

        device.ignore.clear()
        await wait_for(lambda: flaps >= 2, timeout=5.0)
        assert len(messages(logging.WARNING)) == 1
        assert messages(logging.INFO) == []

        flap_connection = False
        await wait_for(lambda: len(messages(logging.INFO)) == 1, timeout=3.0)
        assert messages(logging.INFO) == ["[NAD-C338] Amplifier connection restored"]
        device.ignore.add("Power")
        await wait_for(lambda: len(messages(logging.WARNING)) == 2)
    finally:
        await client.close()
    assert len(messages(logging.WARNING)) == 2
    assert len(messages(logging.INFO)) == 1

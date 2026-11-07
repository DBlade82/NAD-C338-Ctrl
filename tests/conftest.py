"""Shared simulated amplifier for protocol and Home Assistant tests."""

import asyncio
import socket
from collections.abc import AsyncIterator

import pytest_asyncio

LOCALHOST = socket.gethostbyname("localhost")


class FakeAmplifier:
    """Provide a minimal asynchronous NAD status server."""

    def __init__(self) -> None:
        self.values = {
            "Model": "NADC338",
            "Power": "Off",
            "Volume": "-64.0",
            "Source": "Wireless",
            "Mute": "Off",
            "Version": "1.76",
        }
        self.requests: list[bytes] = []
        self.writers: set[asyncio.StreamWriter] = set()
        self.ignore: set[str] = set()
        self.connections = 0

    async def serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        self.writers.add(writer)
        try:
            while command := await reader.readuntil(b"?"):
                self.requests.append(command)
                field = command.decode().removeprefix("Main.").removesuffix("?")
                if field in self.ignore:
                    continue
                if field in self.values:
                    response = f"Main.{field}={self.values[field]}\r\n".encode()
                    writer.write(b"\x00" + response[:-1])
                    await writer.drain()
                    writer.write(response[-1:])
                    await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            self.writers.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def broadcast(self, data: bytes) -> None:
        for writer in tuple(self.writers):
            writer.write(data)
            await writer.drain()


@pytest_asyncio.fixture
async def amplifier() -> AsyncIterator[tuple[FakeAmplifier, int]]:
    """Start a simulated amplifier on an ephemeral local port."""
    device = FakeAmplifier()
    server = await asyncio.start_server(device.serve, host=LOCALHOST, port=0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield device, port
    finally:
        server.close()
        await server.wait_closed()
        for writer in tuple(device.writers):
            writer.close()
            await writer.wait_closed()

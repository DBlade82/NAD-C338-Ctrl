"""Asynchronous client for the NAD C 338 protocol."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .const import LOG_PREFIX, MAX_VOLUME, MIN_VOLUME, MODEL, PORT, SOURCES

_LOGGER = logging.getLogger(__name__)
_TIMEOUT = 5.0
_POLL_INTERVAL = 30.0
_MAX_BACKOFF = 60.0
_STATUS_FIELDS = ("Power", "Volume", "Source", "Mute")
_WAKE_TIMEOUT = 20.0
_READY_INTERVAL = 0.2
_BRIGHTNESS_PROBE_TIMEOUT = 1.0


class NadConnectionError(Exception):
    """The amplifier could not be contacted or did not respond."""


class NadCommandError(Exception):
    """A command has invalid input or its outcome was not confirmed."""


class NadModelError(Exception):
    """The connected device is not a supported NAD C 338."""


@dataclass
class NadState:
    """The latest observed device state."""

    available: bool = False
    power: bool | None = None
    volume: float | None = None
    source: str | None = None
    mute: bool | None = None
    model: str | None = None
    version: str | None = None
    brightness: int | None = None


class NadClient:
    """Own one TCP connection and its independent status reader."""

    def __init__(
        self,
        host: str,
        port: int = PORT,
        *,
        task_factory: Callable[[Coroutine[Any, Any, None], str], asyncio.Task[None]]
        | None = None,
    ) -> None:
        """Initialize the client without opening a connection."""
        self.host = host
        self.port = port
        self.state = NadState()
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[None]] = {}
        self._lock = asyncio.Lock()
        self._brightness_echo_pending = False
        self._wake_clamp_written = False
        self._listeners: set[Callable[[], None]] = set()
        self._task_factory = task_factory or (
            lambda coro, name: asyncio.create_task(coro, name=name)
        )

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a state listener and return its unsubscribe function."""
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            callback()

    async def connect(self) -> None:
        """Connect and validate the model and initial status.

        Raises:
            NadConnectionError: The connection or status query failed.
            NadModelError: The device reports an unsupported model.
        """
        try:
            async with asyncio.timeout(_TIMEOUT):
                reader, self._writer = await asyncio.open_connection(
                    self.host, self.port, limit=4096
                )
            self.state = NadState()
            self._reader_task = self._task_factory(
                self._read_loop(reader), "NAD C 338 reader"
            )
            await self._query("Model")
            if self.state.model != MODEL:
                raise NadModelError("The device is not a NAD C 338")
            for field in _STATUS_FIELDS:
                await self._query(field)
            await self._query_optional_brightness()
            try:
                await self._query("Version")
            except NadConnectionError:
                if self._reader_task.done():
                    raise
                _LOGGER.debug("%s Firmware version not reported", LOG_PREFIX)
            self.state.available = True
            self._notify()
        except (OSError, TimeoutError) as err:
            await self._close_connection()
            raise NadConnectionError("Connection failed") from err
        except NadConnectionError, NadModelError:
            await self._close_connection()
            raise
        except asyncio.CancelledError:
            await self._close_connection()
            raise

    async def start(self) -> None:
        """Connect and start automatic monitoring and reconnection."""
        await self.connect()
        self._supervisor_task = self._task_factory(
            self._supervise(), "NAD C 338 supervisor"
        )

    async def close(self) -> None:
        """Stop all background tasks and close the connection."""
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            await asyncio.gather(self._supervisor_task, return_exceptions=True)
            self._supervisor_task = None
        await self._close_connection()

    async def _close_connection(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                async with asyncio.timeout(_TIMEOUT):
                    await self._writer.wait_closed()
            except asyncio.CancelledError:
                # A previous cancelled close can cancel the shared transport waiter.
                task = asyncio.current_task()
                if task is None or task.cancelling():
                    raise
            except OSError, TimeoutError:
                _LOGGER.debug("%s Connection did not close cleanly", LOG_PREFIX)
            self._writer = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        self.state = NadState()
        self._notify()

    async def _query(self, field: str) -> None:
        async with self._lock:
            await self._query_locked(field)

    async def _query_optional_brightness(self) -> None:
        try:
            async with self._lock:
                await self._query_locked("Brightness", _BRIGHTNESS_PROBE_TIMEOUT)
        except NadConnectionError:
            if self._reader_task is None or self._reader_task.done():
                raise
            self._connected_writer()
            _LOGGER.debug("%s Brightness not reported", LOG_PREFIX)

    @asynccontextmanager
    async def _command(self) -> AsyncIterator[None]:
        async with self._lock:
            try:
                yield
            except NadConnectionError, NadCommandError, asyncio.CancelledError:
                self.state = NadState()
                try:
                    try:
                        # The supervisor owns asynchronous cleanup and reconnect.
                        if self._reader_task is not None:
                            self._reader_task.cancel()
                        if self._writer is not None:
                            self._writer.close()
                    finally:
                        self._notify()
                except Exception:
                    _LOGGER.exception(
                        "%s Failed to publish connection invalidation", LOG_PREFIX
                    )
                raise

    def _connected_writer(self) -> asyncio.StreamWriter:
        if (
            self._writer is None
            or self._writer.is_closing()
            or self._reader_task is None
            or self._reader_task.done()
        ):
            raise NadConnectionError("Not connected")
        return self._writer

    async def _query_locked(self, field: str, timeout: float | None = None) -> None:
        writer = self._connected_writer()
        future = asyncio.get_running_loop().create_future()
        self._pending[field] = future
        try:
            writer.write(f"Main.{field}?".encode("ascii"))
            async with asyncio.timeout(_TIMEOUT if timeout is None else timeout):
                await writer.drain()
                await future
        except (OSError, TimeoutError) as err:
            raise NadConnectionError(f"Status query failed: {field}") from err
        finally:
            self._pending.pop(field, None)

    async def _write_locked(
        self, field: str, value: str, *, barrier: bool = True
    ) -> None:
        writer = self._connected_writer()
        future = asyncio.get_running_loop().create_future()
        self._pending[field] = future
        try:
            writer.write(f"Main.{field}={value}".encode("ascii"))
            async with asyncio.timeout(_TIMEOUT):
                await writer.drain()
                await future
        except (OSError, TimeoutError) as err:
            raise NadConnectionError(f"Command failed: {field}") from err
        finally:
            self._pending.pop(field, None)
        if not barrier:
            return
        # A different-field query separates the write echo from confirmation.
        # With no IDs, a delayed or unsolicited same-field message can still race.
        await self._query_locked("Volume" if field == "Power" else "Power")

    @staticmethod
    def _maximum(max_volume: float | None) -> float:
        if max_volume is None:
            return MAX_VOLUME
        if not math.isfinite(max_volume) or not MIN_VOLUME <= max_volume <= MAX_VOLUME:
            raise NadCommandError("Invalid maximum volume")
        return max_volume

    @staticmethod
    def _target(volume: float, maximum: float) -> float:
        if not math.isfinite(volume):
            raise NadCommandError("Invalid volume")
        ceiling = math.floor(maximum * 2) / 2
        # Round ties toward the louder step, then enforce the ceiling.
        bounded = min(ceiling, max(MIN_VOLUME, volume))
        return min(ceiling, math.floor(bounded * 2 + 0.5) / 2)

    async def _wake_query_locked(self, field: str, deadline: float) -> bool:
        # The amplifier answers no queries for several seconds while waking.
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        try:
            await self._query_locked(field, min(_TIMEOUT, remaining))
        except NadConnectionError:
            if self._reader_task is None or self._reader_task.done():
                raise
            self._connected_writer()
            _LOGGER.debug("%s No %s reply while waking", LOG_PREFIX, field)
            return False
        return True

    async def _power_volume_locked(self) -> tuple[bool, float]:
        await self._query_locked("Power")
        await self._query_locked("Volume")
        power = self.state.power
        volume = self.state.volume
        if power is None or volume is None or not MIN_VOLUME <= volume <= MAX_VOLUME:
            raise NadCommandError("Unknown power or volume")
        return power, volume

    async def _set_volume_locked(
        self, value: float, maximum: float, *, relative: bool = False
    ) -> None:
        power, volume = await self._power_volume_locked()
        if not power:
            return
        target = self._target(volume + value if relative else value, maximum)
        await self._write_locked("Volume", f"{target:.1f}")
        await self._query_locked("Volume")
        if self.state.volume != target:
            raise NadCommandError("Volume change not confirmed")

    async def _enforce_maximum_locked(self, maximum: float, deadline: float) -> None:
        """Clamp a woken amplifier, writing at most once per wake."""
        loop = asyncio.get_running_loop()
        while True:
            if await self._wake_query_locked(
                "Power", deadline
            ) and await self._wake_query_locked("Volume", deadline):
                power = self.state.power
                volume = self.state.volume
                if not power:
                    raise NadCommandError("Amplifier is not on")
                if volume is not None and MIN_VOLUME <= volume <= MAX_VOLUME:
                    if volume <= maximum:
                        return
                    if self._wake_clamp_written:
                        raise NadCommandError("Volume limit not confirmed")
                    self._wake_clamp_written = True
                    target = self._target(maximum, maximum)
                    await self._write_locked("Volume", f"{target:.1f}", barrier=False)
                    continue
            if loop.time() >= deadline:
                raise NadCommandError("Volume limit not confirmed")
            await asyncio.sleep(_READY_INTERVAL)

    async def _turn_on_locked(self, max_volume: float | None, maximum: float) -> None:
        await self._query_locked("Power")
        if self.state.power is None:
            raise NadCommandError("Unknown power state")
        if self.state.power:
            return
        self._wake_clamp_written = False
        try:
            await self._write_locked("Power", "On", barrier=False)
            await self._confirm_wake_locked(max_volume, maximum)
        except NadConnectionError, NadCommandError, asyncio.CancelledError:
            if max_volume is not None:
                _LOGGER.warning(
                    "%s Wake may have been sent but the volume limit was not verified; "
                    "the amplifier may be above the limit",
                    LOG_PREFIX,
                )
            raise

    async def _confirm_wake_locked(
        self, max_volume: float | None, maximum: float
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _WAKE_TIMEOUT
        consecutive = 0
        while True:
            ready = (
                await self._wake_query_locked("Volume", deadline)
                and await self._wake_query_locked("Power", deadline)
                and self.state.power is True
                and self.state.volume is not None
                and MIN_VOLUME <= self.state.volume <= MAX_VOLUME
            )
            if ready:
                # Restored audio may briefly exceed the limit before this check.
                if max_volume is not None:
                    await self._enforce_maximum_locked(maximum, deadline)
                ready = (
                    await self._wake_query_locked("Source", deadline)
                    and self.state.power is True
                    and self.state.volume is not None
                    and MIN_VOLUME <= self.state.volume <= MAX_VOLUME
                    and self.state.source is not None
                )
            consecutive = consecutive + 1 if ready else 0
            if consecutive >= 2:
                break
            if loop.time() >= deadline:
                raise NadCommandError("Wake readiness not confirmed")
            await asyncio.sleep(_READY_INTERVAL)
        if max_volume is not None:
            await self._enforce_maximum_locked(maximum, deadline)

    async def async_turn_on(self, max_volume: float | None) -> None:
        """Wake the device and enforce the optional post-wake volume limit."""
        maximum = self._maximum(max_volume)
        async with self._command():
            await self._turn_on_locked(max_volume, maximum)

    async def async_turn_off(self) -> None:
        """Put the device in standby and confirm its power state."""
        async with self._command():
            await self._write_locked("Power", "Off")
            await self._query_locked("Power")
            if self.state.power is not False:
                raise NadCommandError("Standby not confirmed")

    async def async_set_volume(
        self, volume_db: float, max_volume: float | None
    ) -> None:
        """Set a clamped absolute volume, ignoring requests in standby."""
        maximum = self._maximum(max_volume)
        target = self._target(volume_db, maximum)
        async with self._command():
            await self._set_volume_locked(target, maximum)

    async def async_step_volume(
        self, delta_db: float, max_volume: float | None
    ) -> None:
        """Adjust volume from a freshly queried value, without waking."""
        maximum = self._maximum(max_volume)
        if not math.isfinite(delta_db):
            raise NadCommandError("Invalid volume step")
        async with self._command():
            await self._set_volume_locked(delta_db, maximum, relative=True)

    async def async_select_source(self, source: str, max_volume: float | None) -> None:
        """Wake if necessary, enforce the post-wake limit, and select a source."""
        if source not in SOURCES:
            raise NadCommandError("Invalid source")
        maximum = self._maximum(max_volume)
        async with self._command():
            await self._turn_on_locked(max_volume, maximum)
            await self._write_locked("Source", source)
            await self._query_locked("Source")
            if self.state.source != source:
                raise NadCommandError("Source change not confirmed")

    async def async_set_mute(self, muted: bool) -> None:
        """Set and confirm mute; standby mute does not persist across wake."""
        if not isinstance(muted, bool):
            raise NadCommandError("Invalid mute state")
        async with self._command():
            await self._write_locked("Mute", "On" if muted else "Off")
            await self._query_locked("Mute")
            if self.state.mute is not muted:
                raise NadCommandError("Mute change not confirmed")

    async def _brightness_wake_locked(
        self, max_volume: float | None, maximum: float
    ) -> None:
        if max_volume is None:
            _LOGGER.warning(
                "%s Brightness command unexpectedly woke the amplifier; "
                "audio may resume and no volume limit is configured",
                LOG_PREFIX,
            )
        else:
            _LOGGER.warning(
                "%s Brightness command unexpectedly woke the amplifier; "
                "audio may exceed the configured limit before it can be checked",
                LOG_PREFIX,
            )
        self._wake_clamp_written = False
        await self._confirm_wake_locked(max_volume, maximum)

    async def _brightness_power_locked(
        self, max_volume: float | None, maximum: float
    ) -> bool:
        """Check standby after a write, allowing time for an unexpected wake."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _WAKE_TIMEOUT
        while loop.time() < deadline:
            if await self._wake_query_locked("Power", deadline):
                if self.state.power is True:
                    await self._brightness_wake_locked(max_volume, maximum)
                    return True
                if self.state.power is False:
                    return False
            await asyncio.sleep(min(_READY_INTERVAL, max(0, deadline - loop.time())))
        raise NadConnectionError("Power not verified after brightness command")

    async def async_set_brightness(
        self, brightness: int, max_volume: float | None
    ) -> None:
        """Set brightness directly, including in standby, and verify its state."""
        if type(brightness) is not int or brightness not in range(4):
            raise NadCommandError("Invalid brightness")
        maximum = self._maximum(max_volume)
        async with self._command():
            await self._query_locked("Power")
            if self.state.power is None:
                raise NadCommandError("Unknown power state")
            standby = not self.state.power
            standby_requested = standby
            try:
                self._brightness_echo_pending = True
                try:
                    await self._write_locked(
                        "Brightness", str(brightness), barrier=False
                    )
                except NadConnectionError:
                    if standby:
                        try:
                            await self._brightness_power_locked(max_volume, maximum)
                        except NadConnectionError, NadCommandError:
                            _LOGGER.warning(
                                "%s Could not verify power or volume after the "
                                "unconfirmed brightness command",
                                LOG_PREFIX,
                            )
                    raise
                finally:
                    self._brightness_echo_pending = False
                if standby:
                    standby = not await self._brightness_power_locked(
                        max_volume, maximum
                    )
                else:
                    await self._query_locked("Power")
                try:
                    await self._query_locked("Brightness")
                except NadConnectionError:
                    if not standby:
                        raise
                    standby = not await self._brightness_power_locked(
                        max_volume, maximum
                    )
                    if standby:
                        raise
                    await self._query_locked("Brightness")
                if standby:
                    await self._brightness_power_locked(max_volume, maximum)
                if self.state.brightness != brightness:
                    raise NadCommandError("Brightness change not confirmed")
            except NadConnectionError, NadCommandError, asyncio.CancelledError:
                if standby_requested:
                    _LOGGER.warning(
                        "%s Brightness command is unverified and may have woken "
                        "the amplifier; check power and volume",
                        LOG_PREFIX,
                    )
                raise

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                self._handle_line(line)
        except OSError, ValueError:
            _LOGGER.debug("%s Amplifier connection closed or invalid", LOG_PREFIX)
        finally:
            self.state = NadState()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(NadConnectionError("Connection closed"))
            self._notify()

    def _handle_line(self, line: bytes) -> None:
        try:
            text = line.replace(b"\x00", b"").decode("ascii").strip()
            key, value = text.split("=", 1)
            if not key.startswith("Main."):
                return
            field = key.removeprefix("Main.")
            if field == "Brightness":
                if self._brightness_echo_pending:
                    self._brightness_echo_pending = False
                else:
                    self.state.brightness = (
                        int(value) if value in ("0", "1", "2", "3") else None
                    )
            elif not value:
                return
            elif field in ("Power", "Mute"):
                if value not in ("On", "Off"):
                    return
                setattr(self.state, field.lower(), value == "On")
            elif field == "Volume":
                volume = float(value)
                if not math.isfinite(volume):
                    return
                self.state.volume = volume
            elif field in ("Model", "Source", "Version"):
                setattr(self.state, field.lower(), value)
            else:
                return
        except UnicodeDecodeError, ValueError:
            _LOGGER.debug("%s Ignoring malformed amplifier status", LOG_PREFIX)
            return
        future = self._pending.get(field)
        if future is not None and not future.done():
            future.set_result(None)
        self._notify()

    async def _supervise(self) -> None:
        backoff = 1.0
        outage_reported = False
        while True:
            try:
                if self._reader_task is None:
                    await self.connect()
                    backoff = 1.0
                assert self._reader_task is not None
                done, _ = await asyncio.wait(
                    {self._reader_task}, timeout=_POLL_INTERVAL
                )
                if done:
                    raise NadConnectionError("Connection closed")
                for field in _STATUS_FIELDS:
                    await self._query(field)
                if outage_reported:
                    _LOGGER.info("%s Amplifier connection restored", LOG_PREFIX)
                    outage_reported = False
            except NadConnectionError, NadModelError:
                if not outage_reported:
                    _LOGGER.warning(
                        "%s Amplifier unavailable; reconnecting", LOG_PREFIX
                    )
                    outage_reported = True
                await self._close_connection()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

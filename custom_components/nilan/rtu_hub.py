"""Modbus RTU hub over a serialx stream.

Drop-in for the subset of homeassistant.components.modbus.ModbusHub that
device.py uses: async_setup(), event_connected, async_pb_call(), async_close().

Intended for serial ports that only serialx can open, e.g. URLs handed out by
the Home Assistant serial port selector for network-attached serial proxies.
This module does not import Home Assistant, so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import struct
import time

_LOGGER = logging.getLogger(__name__)

FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_MULTIPLE = 0x10

CALL_TO_FC = {
    "holding": FC_READ_HOLDING,
    "input": FC_READ_INPUT,
    "write_registers": FC_WRITE_MULTIPLE,
}


class RtuError(Exception):
    """Raised for framing, CRC or exception responses."""


@dataclass
class RegisterResult:
    """Minimal stand-in for a pymodbus response; device.py only reads .registers."""

    registers: list[int]


def crc16(data: bytes) -> int:
    """Modbus CRC-16 (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def with_crc(frame: bytes) -> bytes:
    """Append CRC, low byte first."""
    return frame + struct.pack("<H", crc16(frame))


def build_request(unit: int, address: int, value, use_call: str) -> tuple[bytes, int]:
    """Return (request frame, expected length of a normal response)."""
    fc = CALL_TO_FC[use_call]
    if fc in (FC_READ_HOLDING, FC_READ_INPUT):
        count = int(value)
        if not 1 <= count <= 125:
            raise ValueError(f"register count out of range: {count}")
        pdu = struct.pack(">BBHH", unit, fc, address, count)
        return with_crc(pdu), 5 + 2 * count
    values = [int(v) & 0xFFFF for v in value]
    if not 1 <= len(values) <= 123:
        raise ValueError(f"write count out of range: {len(values)}")
    pdu = struct.pack(">BBHHB", unit, fc, address, len(values), 2 * len(values))
    pdu += struct.pack(f">{len(values)}H", *values)
    return with_crc(pdu), 8


def parse_response(unit: int, fc: int, frame: bytes) -> RegisterResult:
    """Validate a complete response frame and decode it."""
    if len(frame) < 5:
        raise RtuError(f"short frame: {frame.hex()}")
    if crc16(frame[:-2]) != struct.unpack("<H", frame[-2:])[0]:
        raise RtuError(f"crc mismatch: {frame.hex()}")
    if frame[0] != unit:
        raise RtuError(f"unexpected unit {frame[0]} (want {unit})")
    if frame[1] == fc | 0x80:
        raise RtuError(f"exception code {frame[2]:#04x}")
    if frame[1] != fc:
        raise RtuError(f"unexpected function {frame[1]:#04x} (want {fc:#04x})")
    if fc == FC_WRITE_MULTIPLE:
        return RegisterResult(registers=[])
    byte_count = frame[2]
    data = frame[3 : 3 + byte_count]
    return RegisterResult(registers=list(struct.unpack(f">{byte_count // 2}H", data)))


class ConnectionLost(Exception):
    """The stream reported end of file."""


# Reopen delays after a lost connection: 5 s, doubled per failed attempt, at most 60 s.
REOPEN_DELAY_MIN = 5.0
REOPEN_DELAY_MAX = 60.0
OPEN_TIMEOUT = 10.0


class RtuHub:
    """One serial connection, one request at a time.

    async_pb_call never raises. A request that times out or gets a bad frame returns
    None and keeps the connection. Any other error (the stream closed, the proxy went
    away, a stored exception on the reader) closes the connection; later calls return
    None without I/O until the reopen delay has passed, then the port is opened again.
    A dead serialx transport cannot recover on its own, so reopening is the only way back.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = 19200,
        parity: str = "E",
        stopbits: int = 1,
        bytesize: int = 8,
        timeout: float = 1.0,
        opener=None,
        clock=time.monotonic,
    ) -> None:
        """Store parameters; opener and clock are injectable for tests."""
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._bytesize = bytesize
        self._timeout = timeout
        self._opener = opener or self._open_serialx
        self._clock = clock
        self._reader: asyncio.StreamReader | None = None
        self._writer = None
        self._lock = asyncio.Lock()
        # 3.5 character times between frames (11 bits per char with parity)
        self._frame_gap = max(3.5 * 11 / baudrate, 0.00175)
        self.event_connected = asyncio.Event()
        self._closed = False
        self._reopen_delay = REOPEN_DELAY_MIN
        self._next_open = 0.0
        self._lost_at: float | None = None

    @property
    def connected(self) -> bool:
        """True while a stream is open."""
        return self._writer is not None

    async def _open_serialx(self):
        """Open the port via serialx; URL handlers (e.g. esphome-hass://) resolve here."""
        import serialx  # noqa: PLC0415  (only needed at runtime, keeps tests import-free)

        # checked against serialx 1.11.0 (HA 2026.10) and 1.15.1 (HA 2026.11): enum names
        # match, and baudrate/parity/stopbits/byte_size reach the ESPHome proxy configure call
        parity = {"N": serialx.Parity.NONE, "E": serialx.Parity.EVEN, "O": serialx.Parity.ODD}[
            self._parity
        ]
        return await serialx.open_serial_connection(
            self._port,
            baudrate=self._baudrate,
            parity=parity,
            stopbits=serialx.StopBits.ONE,
            byte_size=self._bytesize,
        )

    async def _open(self) -> None:
        """Open the stream, bounded so a hanging proxy cannot block the caller."""
        self._reader, self._writer = await asyncio.wait_for(self._opener(), OPEN_TIMEOUT)
        self.event_connected.set()

    async def async_setup(self) -> bool:
        """Open the connection; mirror ModbusHub by returning a bool."""
        self._closed = False
        try:
            await self._open()
        except Exception as err:  # noqa: BLE001  (any open failure means "not ready")
            _LOGGER.error("Opening %s failed: %s", self._port, _describe(err))
            return False
        self._reopen_delay = REOPEN_DELAY_MIN
        return True

    async def _drop_stream(self) -> None:
        """Forget the stream; closing errors do not matter any more."""
        self.event_connected.clear()
        writer, self._reader, self._writer = self._writer, None, None
        if writer is None:
            return
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), 1.0)
        except Exception:  # noqa: BLE001  (a broken stream may fail to close)
            pass

    async def async_close(self) -> None:
        """Close the connection for good (entry unload); no reopen afterwards."""
        self._closed = True
        await self._drop_stream()

    async def _connection_lost(self, err: BaseException) -> None:
        """Close the broken stream and schedule the first reopen."""
        await self._drop_stream()
        self._lost_at = self._clock()
        self._reopen_delay = REOPEN_DELAY_MIN
        self._next_open = self._lost_at + self._reopen_delay
        _LOGGER.warning(
            "Connection to %s lost (%s), reopening in %.0f s",
            self._port,
            _describe(err),
            self._reopen_delay,
        )

    async def _try_reopen(self) -> bool:
        """Reopen after the delay; on failure double the delay up to the maximum."""
        if self._clock() < self._next_open:
            return False
        try:
            await self._open()
        except Exception as err:  # noqa: BLE001  (the proxy may still be gone)
            self._reopen_delay = min(self._reopen_delay * 2, REOPEN_DELAY_MAX)
            self._next_open = self._clock() + self._reopen_delay
            _LOGGER.debug(
                "Reopening %s failed (%s), next try in %.0f s",
                self._port,
                _describe(err),
                self._reopen_delay,
            )
            return False
        down = self._clock() - self._lost_at if self._lost_at is not None else 0.0
        _LOGGER.info("Connection to %s restored after %.0f s", self._port, down)
        self._lost_at = None
        self._reopen_delay = REOPEN_DELAY_MIN
        return True

    async def _drain_stale(self) -> None:
        """Drop bytes left over from a timed-out exchange."""
        while True:
            try:
                stale = await asyncio.wait_for(self._reader.read(256), self._frame_gap)
            except TimeoutError:
                return
            if not stale:
                if self._reader.at_eof():
                    raise ConnectionLost("end of stream")
                return
            _LOGGER.debug("Dropped %d stale bytes", len(stale))

    async def _exchange(self, unit: int, address: int, value, use_call: str) -> RegisterResult:
        request, expected = build_request(unit, address, value, use_call)
        fc = request[1]
        await self._drain_stale()
        self._writer.write(request)
        await self._writer.drain()
        # Read the 2-byte header first: an exception response is only 5 bytes long.
        head = await asyncio.wait_for(self._reader.readexactly(2), self._timeout)
        remaining = 3 if head[1] == fc | 0x80 else expected - 2
        rest = await asyncio.wait_for(self._reader.readexactly(remaining), self._timeout)
        await asyncio.sleep(self._frame_gap)
        return parse_response(unit, fc, head + rest)

    async def async_pb_call(self, unit, address, value, use_call):
        """Same contract as ModbusHub.async_pb_call: result or None, never an exception."""
        if self._closed:
            return None
        async with self._lock:
            if self._writer is None and not await self._try_reopen():
                return None
            try:
                return await self._exchange(int(unit), int(address), value, use_call)
            except (TimeoutError, RtuError) as err:
                # the device did not answer or answered badly; the link itself is fine
                _LOGGER.debug("RTU %s @%s failed: %s", use_call, address, err)
                return None
            except ValueError as err:
                # a request this hub cannot build (caller error), not a link problem
                _LOGGER.debug("RTU %s @%s rejected: %s", use_call, address, err)
                return None
            except Exception as err:  # noqa: BLE001
                # EOF, OSError from a dead proxy, a stored reader exception: the stream is
                # gone. Never let it escape: the reader raises the SAME exception object on
                # every read, and re-raising it grows its traceback without bound (#2370).
                await self._connection_lost(err)
                return None


def _describe(err: BaseException) -> str:
    """One-line description without traceback."""
    text = str(err)
    return f"{type(err).__name__}: {text}" if text else type(err).__name__

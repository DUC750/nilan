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


class RtuHub:
    """One serial connection, one request at a time."""

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
    ) -> None:
        """Store parameters; opener is injectable for tests."""
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._bytesize = bytesize
        self._timeout = timeout
        self._opener = opener or self._open_serialx
        self._reader: asyncio.StreamReader | None = None
        self._writer = None
        self._lock = asyncio.Lock()
        # 3.5 character times between frames (11 bits per char with parity)
        self._frame_gap = max(3.5 * 11 / baudrate, 0.00175)
        self.event_connected = asyncio.Event()

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

    async def async_setup(self) -> bool:
        """Open the connection; mirror ModbusHub by returning a bool."""
        try:
            self._reader, self._writer = await self._opener()
        except Exception as err:  # noqa: BLE001  (any open failure means "not ready")
            _LOGGER.error("Opening %s failed: %s", self._port, err)
            return False
        self.event_connected.set()
        return True

    async def async_close(self) -> None:
        """Close the connection."""
        self.event_connected.clear()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
        self._reader = self._writer = None

    async def _drain_stale(self) -> None:
        """Drop bytes left over from a timed-out exchange."""
        while True:
            try:
                stale = await asyncio.wait_for(self._reader.read(256), self._frame_gap)
            except TimeoutError:
                return
            if not stale:
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
        """Same contract as ModbusHub.async_pb_call: result or None on failure."""
        if self._writer is None:
            return None
        async with self._lock:
            try:
                return await self._exchange(int(unit), int(address), value, use_call)
            except (TimeoutError, asyncio.IncompleteReadError, RtuError) as err:
                _LOGGER.debug("RTU %s @%s failed: %s", use_call, address, err)
                return None

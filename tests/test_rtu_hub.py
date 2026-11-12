"""Tests for the RTU hub, without Home Assistant and without hardware.

Run: python -m pytest tests/test_rtu_hub.py   (or: python tests/test_rtu_hub.py)
"""

import asyncio
import importlib.util
from pathlib import Path
import struct
import sys

_SPEC = importlib.util.spec_from_file_location(
    "rtu_hub", Path(__file__).parent.parent / "custom_components" / "nilan" / "rtu_hub.py"
)
rtu = importlib.util.module_from_spec(_SPEC)
sys.modules["rtu_hub"] = rtu  # dataclasses resolves annotations through sys.modules
_SPEC.loader.exec_module(rtu)


class FakeWriter:
    """Records writes and answers each request through a responder."""

    def __init__(self, reader, responder):
        self.reader = reader
        self.responder = responder
        self.sent = []

    def write(self, data):
        self.sent.append(bytes(data))
        reply = self.responder(bytes(data))
        if reply:
            self.reader.feed_data(reply)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


def make_hub(responder, timeout=0.2):
    async def opener():
        reader = asyncio.StreamReader()
        return reader, FakeWriter(reader, responder)

    return rtu.RtuHub("esphome-hass://esphome/test?port_name=X", timeout=timeout, opener=opener)


def test_crc_known_vector():
    # Classic example: 01 03 00 00 00 0A -> CRC C5 CD
    assert rtu.with_crc(bytes.fromhex("01030000000A")).hex() == "01030000000ac5cd"


def test_read_holding_control_type():
    # Unit 30, holding 1000 (control_type), device answers 11
    def responder(req):
        assert req == rtu.with_crc(struct.pack(">BBHH", 30, 3, 1000, 1))
        return rtu.with_crc(bytes([30, 3, 2, 0, 11]))

    async def run():
        hub = make_hub(responder)
        assert await hub.async_setup()
        assert hub.event_connected.is_set()
        result = await hub.async_pb_call(30, 1000, 1, "holding")
        await hub.async_close()
        return result

    assert asyncio.run(run()).registers == [11]


def test_write_registers_roundtrip():
    def responder(req):
        assert req[1] == 0x10
        return rtu.with_crc(req[:6])

    async def run():
        hub = make_hub(responder)
        await hub.async_setup()
        return await hub.async_pb_call(30, 1700, [35], "write_registers")

    assert asyncio.run(run()).registers == []


def test_exception_response_returns_none():
    # Illegal data address (0x02), as for an unknown register
    def responder(req):
        return rtu.with_crc(bytes([30, 0x84, 0x02]))

    async def run():
        hub = make_hub(responder)
        await hub.async_setup()
        return await hub.async_pb_call(30, 9999, 1, "input")

    assert asyncio.run(run()) is None


def test_bad_crc_returns_none():
    def responder(req):
        good = rtu.with_crc(bytes([30, 4, 2, 0x07, 0x8C]))
        return good[:-1] + bytes([good[-1] ^ 0xFF])

    async def run():
        hub = make_hub(responder)
        await hub.async_setup()
        return await hub.async_pb_call(30, 201, 1, "input")

    assert asyncio.run(run()) is None


def test_timeout_then_recovery():
    # First request unanswered, second answered: a stale gap must not poison the next call
    calls = {"n": 0}

    def responder(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return b""
        return rtu.with_crc(bytes([30, 4, 2, 0x08, 0x02]))

    async def run():
        hub = make_hub(responder, timeout=0.05)
        await hub.async_setup()
        first = await hub.async_pb_call(30, 202, 1, "input")
        second = await hub.async_pb_call(30, 202, 1, "input")
        return first, second

    first, second = asyncio.run(run())
    assert first is None
    assert second.registers == [0x0802]


def test_open_failure_returns_false():
    async def opener():
        raise OSError("entry not loaded")

    async def run():
        hub = rtu.RtuHub("esphome-hass://esphome/x", opener=opener)
        ok = await hub.async_setup()
        return ok, await hub.async_pb_call(30, 0, 1, "input")

    assert asyncio.run(run()) == (False, None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

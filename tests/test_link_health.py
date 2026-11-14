"""Tests for LinkHealth, without Home Assistant.

Run: python -m pytest tests/test_link_health.py   (or: python tests/test_link_health.py)
"""

import asyncio
import importlib.util
import logging
from pathlib import Path
import sys

_SPEC = importlib.util.spec_from_file_location(
    "link_health",
    Path(__file__).parent.parent / "custom_components" / "nilan" / "link_health.py",
)
lh = importlib.util.module_from_spec(_SPEC)
sys.modules["link_health"] = lh
_SPEC.loader.exec_module(lh)


class FakeClock:
    def __init__(self):
        self.now = 500.0

    def __call__(self):
        return self.now


class FakeHub:
    """Answers with a scripted list; an Exception instance in the list is raised."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.event_connected = asyncio.Event()

    async def async_setup(self):
        self.event_connected.set()
        return True

    async def async_close(self):
        self.event_connected.clear()

    async def async_pb_call(self, unit, address, value, use_call):
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_hub_exception_never_escapes():
    hub = FakeHub([OSError("dead"), RuntimeError("anything")])
    health = lh.LinkHealth(hub, "Nilan", clock=FakeClock())

    async def run():
        return [await health.async_pb_call(30, 1, 1, "input") for _ in range(2)]

    assert asyncio.run(run()) == [None, None]


def test_unavailable_after_timeout_and_back():
    clock = FakeClock()
    hub = FakeHub([None] * 10 + ["ok"])
    health = lh.LinkHealth(hub, "Nilan", clock=clock)
    capture = Capture()
    logging.getLogger("link_health").addHandler(capture)

    async def run():
        await health.async_setup()
        states = []
        for _ in range(10):  # ten failed polls, 10 s apart
            clock.now += 10
            await health.async_pb_call(30, 1, 1, "input")
            states.append(health.available)
        clock.now += 10
        await health.async_pb_call(30, 1, 1, "input")
        states.append(health.available)
        return states

    try:
        states = asyncio.run(run())
    finally:
        logging.getLogger("link_health").removeHandler(capture)
    # 90 s offline threshold: available through 80 s, unavailable from 90 s, back on success
    assert states == [True] * 8 + [False, False, True]
    warnings = [r.getMessage() for r in capture.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, warnings  # one "not answered", one "answers again"
    assert "has not answered for 90 s" in warnings[0]
    assert "answers again after 110 s" in warnings[1]


def test_quiet_after_failures_in_row_but_not_for_single_errors():
    hub = FakeHub([None, "ok"] + [None] * 5)
    health = lh.LinkHealth(hub, "Nilan", clock=FakeClock())

    async def run():
        quiet = []
        for _ in range(7):
            await health.async_pb_call(30, 1, 1, "input")
            quiet.append(health.quiet)
        return quiet

    assert asyncio.run(run()) == [False, False, False, False, False, False, True]


def test_setup_counts_as_sign_of_life():
    clock = FakeClock()
    health = lh.LinkHealth(FakeHub([]), "Nilan", clock=clock)
    clock.now += 1000

    async def run():
        return await health.async_setup()

    assert asyncio.run(run()) is True
    assert health.available
    assert health.event_connected.is_set()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)

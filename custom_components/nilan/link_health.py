"""Link health for the Modbus transport, the same for TCP and the serial proxy.

LinkHealth wraps the hub that device.py talks to (HA's ModbusHub or RtuHub) and
offers the same four members: async_setup(), event_connected, async_pb_call(),
async_close(). On top it records when the last call succeeded, so that

- entities can report unavailable instead of stale or unknown values,
- read failures are logged once per outage instead of once per register and poll,
- the integration can reload its config entry when the link stays dead.

async_pb_call never raises, whatever the hub underneath does (#2370).
This module does not import Home Assistant, so it can be tested on its own.
"""

from __future__ import annotations

import logging
import time

_LOGGER = logging.getLogger(__name__)

# No successful call for this long: entities go unavailable (3 poll cycles of 30 s).
OFFLINE_AFTER = 90.0
# Read failures in a row before single-register errors are no longer worth an ERROR line.
QUIET_AFTER_FAILURES = 5


class LinkHealth:
    """Hub wrapper that tracks whether the device still answers."""

    def __init__(self, hub, name: str, *, clock=time.monotonic) -> None:
        """Wrap hub; name is used in log lines."""
        self._hub = hub
        self._name = name
        self._clock = clock
        self._last_success = clock()
        self._failures_in_row = 0
        self._offline_reported = False

    @property
    def event_connected(self):
        """Pass through the hub's connected event."""
        return self._hub.event_connected

    @property
    def available(self) -> bool:
        """True while the device answered within OFFLINE_AFTER seconds."""
        return self.seconds_since_success < OFFLINE_AFTER

    @property
    def seconds_since_success(self) -> float:
        """Seconds since the last successful call (or since setup)."""
        return self._clock() - self._last_success

    @property
    def quiet(self) -> bool:
        """True when single read errors are part of an outage, not worth an ERROR each."""
        return self._failures_in_row >= QUIET_AFTER_FAILURES or not self.available

    async def async_setup(self) -> bool:
        """Open the hub; a successful open counts as a sign of life."""
        ok = await self._hub.async_setup()
        if ok:
            self._record_success()
        return ok

    async def async_close(self) -> None:
        """Close the hub."""
        await self._hub.async_close()

    async def async_pb_call(self, unit, address, value, use_call):
        """Forward to the hub; result or None, never an exception."""
        try:
            result = await self._hub.async_pb_call(unit, address, value, use_call)
        except Exception as err:  # noqa: BLE001  (last line of defence for any hub)
            _LOGGER.debug("%s: hub call %s @%s raised %s", self._name, use_call, address, err)
            result = None
        if result is None:
            self._record_failure()
        else:
            self._record_success()
        return result

    def _record_success(self) -> None:
        if self._offline_reported:
            _LOGGER.warning(
                "%s answers again after %.0f s without a response",
                self._name,
                self.seconds_since_success,
            )
            self._offline_reported = False
        self._last_success = self._clock()
        self._failures_in_row = 0

    def _record_failure(self) -> None:
        self._failures_in_row += 1
        if not self.available and not self._offline_reported:
            self._offline_reported = True
            _LOGGER.warning(
                "%s has not answered for %.0f s, entities are unavailable until it does",
                self._name,
                self.seconds_since_success,
            )

# This file has been modified from the original veista/nilan integration.
# Original work Copyright veista (Taneli Veistinen), Apache License 2.0.
# See README.md for the list of changes.
"""The Nilan integration."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN
from .device import Device

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "number",
    "select",
    "sensor",
    "switch",
    "water_heater",
]

_LOGGER = logging.getLogger(__name__)

# Reload the entry when the device has not answered for this long (#2370).
RELOAD_AFTER = 300.0
LINK_CHECK_INTERVAL = timedelta(seconds=60)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nilan CTS602 Modbus TCP from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry.data

    name = entry.data["name"]
    host_port = entry.data["host_port"]
    unit_id = entry.data["unit_id"]
    com_type = entry.data["com_type"]
    host_ip = entry.data["host_ip"]
    # board_type = entry.data["board_type"]

    device = Device(hass, name, com_type, host_ip, host_port, unit_id)
    try:
        await device.setup()
    except ValueError as ex:
        raise ConfigEntryNotReady(f"Timeout while connecting {host_ip}") from ex
    hass.data[DOMAIN][entry.entry_id] = device

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # entry.async_on_unload(entry.async_on_state_change())

    @callback
    def _reload_if_link_dead(_now) -> None:
        """Reload once when the device stays silent; HA's setup retry takes it from there.

        RtuHub reopens its own stream, HA's ModbusHub does not reliably reconnect after
        the gateway was away (#2370). A reload rebuilds the hub for both transports; if
        the device is still silent, setup raises ConfigEntryNotReady and HA retries.
        """
        if device.seconds_since_success < RELOAD_AFTER:
            return
        _LOGGER.warning(
            "%s has not answered for %.0f s, reloading the integration",
            name,
            device.seconds_since_success,
        )
        cancel()
        hass.config_entries.async_schedule_reload(entry.entry_id)

    cancel = async_track_time_interval(hass, _reload_if_link_dead, LINK_CHECK_INTERVAL)
    entry.async_on_unload(cancel)
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        new = {**config_entry.data}
        new.update({"com_type": "tcp"})
        new.update({"board_type": "CTS602"})
        config_entry.version = 2
        hass.config_entries.async_update_entry(config_entry, data=new)

    elif config_entry.version == 2:
        new = {**config_entry.data}
        new.update({"board_type": "CTS602"})
        config_entry.version = 3
        hass.config_entries.async_update_entry(config_entry, data=new)

    _LOGGER.info("Migration to version %s successful", config_entry.version)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    await hass.data[DOMAIN][entry.entry_id].async_close()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class NilanEntity(Entity):
    """Nilan Entity."""

    def __init__(self, device: Device) -> None:
        """Initialize the instance."""
        self._device = device

    @property
    def available(self) -> bool:
        """Unavailable while the device does not answer, instead of stale values."""
        return self._device.available

    @property
    def device_info(self):
        """Device Info."""
        unique_id = self._device.get_device_name + self._device.get_device_type

        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, unique_id),
            },
            "name": self._device.get_device_name,
            "manufacturer": "Nilan",
            "model": self._device.get_device_type,
            "sw_version": self._device.get_device_sw_version,
            "hw_version": str(self._device.get_device_hw_version),
            "suggested_area": "Boiler Room",
        }
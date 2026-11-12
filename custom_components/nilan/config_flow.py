"""Config flow for Nilan integration."""

from __future__ import annotations

import logging
from typing import Any, Optional

from pymodbus.client import AsyncModbusSerialClient, AsyncModbusTcpClient
from pymodbus.exceptions import ModbusException
import voluptuous as vol

from homeassistant import config_entries

from .const import DOMAIN
from .device import CTS602_DEVICE_TYPES
from .registers import CTS602HoldingRegisters
from .rtu_hub import RtuHub

try:  # HA 2026.10+: selector config optional, value is a str
    from homeassistant.helpers.selector import SerialPortSelector

    _PORT_FIELD = SerialPortSelector()
except ImportError:  # older HA: accept the port URL as plain text
    _PORT_FIELD = str

STEP_TCP_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_ip"): str,
        vol.Required("host_port", default="502"): str,
        vol.Required("unit_id", default=30): int,
    }
)

STEP_SERIAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_port"): str,
        vol.Required("unit_id", default=30): int,
    }
)

STEP_SERIAL_PROXY_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_port"): _PORT_FIELD,
        vol.Required("unit_id", default=30): int,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_validate_serial_proxy(port: str, unit_id) -> None:
    """Validate device model over a serialx-only port."""
    hub = RtuHub(port)
    if not await hub.async_setup():
        raise ValueError("cannot_connect")
    try:
        result = await hub.async_pb_call(
            int(unit_id), CTS602HoldingRegisters.control_type, 1, "holding"
        )
    finally:
        await hub.async_close()
    if result is None or len(result.registers) == 0:
        raise ValueError("invalid_response")
    if result.registers[0] not in CTS602_DEVICE_TYPES:
        _LOGGER.debug("Device Type %s not found in supported devices list", result.registers[0])
        raise ValueError("unsupported_device")

async def async_validate_device(com_type, port, unit_id, address: str | None) -> None:
    """Validate device model."""
    if com_type == "tcp":
        client = AsyncModbusTcpClient(
            address,
            port=port,
        )
    else:
        client = AsyncModbusSerialClient(
            port=port,
            stopbits=1,
            bytesize=8,
            parity="E",
            baudrate=19200,
            timeout=1,
        )
    try:
        await client.connect()
        result = await client.read_holding_registers(
            CTS602HoldingRegisters.control_type, count=1, device_id=int(unit_id)
        )
    except ModbusException as value_error:
        client.close()
        raise ValueError("cannot_connect") from value_error
    if hasattr(result, "message"):
        client.close()
        raise ValueError("invalid_response")
    if len(result.registers) == 0:
        client.close()
        raise ValueError("invalid_response")
    value_output = int.from_bytes(
        result.registers[0].to_bytes(2, "little", signed=False),
        "little",
        signed=False,
    )
    if value_output not in CTS602_DEVICE_TYPES:
        _LOGGER.debug(
            "Device Type %s not found in supported devices list",
            str(value_output),
        )
        raise ValueError("unsupported_device")
    client.close()


class NilanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nilan CTS602 Modbus TCP."""

    VERSION = 3

    data: Optional[dict(str, Any)]

    async def async_step_user(self, user_input: Optional[dict(str, Any)] = None):
        """Invoke when a user initiates a flow via the user interface."""
        return await self.async_step_menu(user_input)

    async def async_step_menu(self, user_input: Optional[dict(str, Any)] = None):
        """Show Communications Selection."""
        return self.async_show_menu(
            step_id="menu",
            menu_options=["tcp", "serial", "serial_proxy"],
            # description_placeholders={
            #     "model": "Example model",
            # },
        )

    async def async_step_tcp(self, user_input: Optional[dict(str, Any)] = None):
        """Configure ModBus TCP entry."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_device(
                    "tcp",
                    user_input["host_port"],
                    user_input["unit_id"],
                    user_input["host_ip"],
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                # Input is valid, set data.
                self.data = user_input
                self.data.update({"com_type": "tcp"})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="tcp", data_schema=STEP_TCP_DATA_SCHEMA, errors=errors
        )

    async def async_step_serial(self, user_input: Optional[dict(str, Any)] = None):
        """Configure ModBus Serial RTU entry."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_device(
                    "serial", user_input["host_port"], user_input["unit_id"], None
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                # Input is valid, set data.
                self.data = user_input
                self.data.update({"com_type": "serial"})
                self.data.update({"host_ip": None})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="serial", data_schema=STEP_SERIAL_DATA_SCHEMA, errors=errors
        )

    async def async_step_serial_proxy(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Configure Modbus RTU over a serial port selected in Home Assistant."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_serial_proxy(
                    user_input["host_port"], user_input["unit_id"]
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                self.data = user_input
                self.data.update({"com_type": "serial_proxy"})
                self.data.update({"host_ip": None})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="serial_proxy",
            data_schema=STEP_SERIAL_PROXY_DATA_SCHEMA,
            errors=errors,
        )

    # --- Reconfigure: switch the transport of an existing entry in place -------
    # Entity unique_ids do not depend on the entry, but a second entry would
    # collide with them. Changing the transport on the existing entry keeps all
    # entity_ids, statistics and automations untouched. Rollback = reconfigure
    # back to TCP.

    async def async_step_reconfigure(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Choose the new transport for an existing entry."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_tcp", "reconfigure_serial_proxy"],
        )

    async def async_step_reconfigure_tcp(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Switch an existing entry to Modbus TCP."""
        entry = self._get_reconfigure_entry()
        errors: dict(str, str) = {}
        if user_input is not None:
            try:
                await async_validate_device(
                    "tcp",
                    user_input["host_port"],
                    user_input["unit_id"],
                    user_input["host_ip"],
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "com_type": "tcp",
                        "host_ip": user_input["host_ip"],
                        "host_port": user_input["host_port"],
                        "unit_id": user_input["unit_id"],
                    },
                )
        current = entry.data
        schema = vol.Schema(
            {
                vol.Required("host_ip", default=current.get("host_ip") or ""): str,
                vol.Required(
                    "host_port",
                    default=current.get("host_port")
                    if current.get("com_type") == "tcp"
                    else "502",
                ): str,
                vol.Required("unit_id", default=current.get("unit_id", 30)): int,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_tcp", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure_serial_proxy(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Switch an existing entry to Modbus RTU over a serial proxy port."""
        entry = self._get_reconfigure_entry()
        errors: dict(str, str) = {}
        if user_input is not None:
            try:
                await async_validate_serial_proxy(
                    user_input["host_port"], user_input["unit_id"]
                )
            except ValueError as error:
                errors["base"] = str(error)
            except Exception:  # noqa: BLE001  serialx raises its own types
                _LOGGER.exception("Serial proxy validation failed")
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "com_type": "serial_proxy",
                        "host_ip": None,
                        "host_port": user_input["host_port"],
                        "unit_id": user_input["unit_id"],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required("host_port"): _PORT_FIELD,
                vol.Required("unit_id", default=entry.data.get("unit_id", 30)): int,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_serial_proxy", data_schema=schema, errors=errors
        )

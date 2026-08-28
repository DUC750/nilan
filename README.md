[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

> ## ⚠️ This is a modified fork
>
> This repository is a fork of **[veista/nilan](https://github.com/veista/nilan)** by
> **Taneli Veistinen**, licensed under the Apache License 2.0. The integration is his work —
> the changes below are additions on top of it.
>
> **Changes compared to upstream:**
>
> | Area | Change |
> | --- | --- |
> | `device_map.py` | Entity mapping for device type **11** (VP 18comp) — submitted upstream as [PR #234](https://github.com/veista/nilan/pull/234) |
> | `device.py`, `binary_sensor.py`, `device_map.py` | Pressure switch inputs **P_HI / P_LO** exposed as `binary_sensor` |
> | `device.py` | `hw_version` returned as `str` (same fix as upstream [#225](https://github.com/veista/nilan/pull/225)) |
> | `translations/de.json`, `en.json` | Names for the pressure switches; German name for **T5 corrected to condenser** (it is the component, not condensate) |
>
> **Please report problems with this fork here, not in `veista/nilan`.** If you can reproduce the
> problem with the upstream integration, report it there instead — that keeps unrelated reports
> off his tracker.
>
> If you find this integration useful, support goes to the original author:
> [github.com/sponsors/veista](https://github.com/sponsors/veista)

# Nilan
Nilan integration for Home Assistant

CTS602 supported devices (as typed in HMI menu):

- Comfort light
- Comfort Polar
- VPL 15c
- CompactS
- VP18cCom
- COMFORT
- VP 18c
- VP 18ek
- VP 18cek
- VPL 25c
- VPM/28EC
- VP18cCoB
- COMPACTn
- COMFORTn
- COMBI 300 N
- COMBI 302
- COMBI 302 T
- VGU180 ek
- VENTEC
- CompactP (AIR/GEO)

Majority of functions are supported. If some critical is missing please leave an issue.

If you have CTS700 or another device you will have to help me with that.

## Installation
### Hardware
You must have one interface type installed on your Nilan device for this Integration to work 

#### Supported Interface Types
- ModBus RTU to Modbus TCP Bridge 
- USB to RS485 adaptor

#### Tested Known-to-work Bridge Devices
* USR-TCP232-410S
* Waveshare RS485 TO ETH (B)
* https://github.com/veista/modbus_bridge

### Software
#### Manually
- Copy the nilan folder into your custom_components folder
- Restart HA
- Add Nilan from Integrations

#### HACS
- This integration is available from HACS
- Add Nilan from Integrations

## Issues
1. Before submitting an issue, read the previous <a href="https://github.com/veista/nilan/issues?q=">issues</a>, <a href="https://github.com/veista/nilan/wiki">wiki</a>, <a href="https://github.com/veista/nilan/discussions">discussions</a> and <a href="https://github.com/veista/nilan/releases">release notes</a>.
2. If you have a CTS700 device, it is a long road to get it supported by this integration, open an issue and we will go forward there
3. If you have a CTS602 device and you get a device not supported error during installation:
  - Turn on debug logging for the integration and try installing the integration again. Take note of the debug log and submit it with the issue.
  - Take a picture of the device type plate and submit it with the issue.
  - If you have HMI350T - Touch screen HMI - installed on your device, take a picture of the device info page and submit it with the issue.
  - If you have CTS602 HMI, take a picture of "SHOW DATA" -> "TYPE" and submit it with the issue.
4. On other Issues:
  - Submit the following: Logs, Modbus Version, Device Type - as Shown in the Integration, Device Version - as Shown in the Integration

## Support
If you like the integration, please leave a star and concider donating or becoming a sponsor.


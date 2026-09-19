# altimeter — demo board

SRAD Altimeter by [Waterloo Rocketry](https://github.com/waterloo-rocketry/canhw/tree/main/altimeter), a flown rocket avionics board. License: GPL-3.0. Source commit `88f184d`.

4 copper layers, 71 × 71 mm, 133 components: STM32H750 MCU, two pyro channels on a 12.6 V rail (TLP3543A relays, 5 Ω / 35 W limit resistors), CAN transceiver, buck + LDO regulators, SD card on a 4-bit SDMMC bus.

| File | Origin |
| --- | --- |
| `fab/*.gbr`, `fab/*-PTH.drl`, `fab/stackup.json`, `netlist.d356`, `bom.txt` | Exported from the KiCad project with `../../export_kicad.sh` |
| `datasheets/*.pdf` | Manufacturer datasheets (not committed). URLs are in the `Datasheet` column of `bom.txt`; MCP2562 is Microchip DS20005167 |
| `limits.json` | Read from the datasheets by `../../extract_limits.py`. `defaults` holds assumed limits for parts without a part number |
| `conditions.json` | Working voltages and currents. Demo assumptions: 3S LiPo at 12.6 V, 1 Ω e-match |
| `simulation.json`, `fab/*-pos.csv` | Simulation setup: the SD card clock net `/mcu/SDMMC1_CK`, ports at U4 pin 80 and J7 pin 5 |

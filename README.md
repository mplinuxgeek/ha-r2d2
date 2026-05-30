# Home Assistant — Sphero R2D2 / Q5 Droid Integration

Custom integration for controlling Sphero R2-D2 and R2-Q5 droids via Bluetooth LE.

## Features

| Platform | Entities |
|---|---|
| **Button** | Init, Power Off, Tripod, Bipod, Waddle On/Off, Stop, Reset Yaw |
| **Light** | Front LED (RGB), Back LED (RGB), Holo Projector (brightness), Logic Display (brightness) |
| **Number** | Dome Rotation (−160° to +180°) |
| **Select** | Animation picker, Audio clip picker |
| **Sensor** | Battery %, IMU pitch/roll/yaw, Accelerometer X/Y/Z, Head angle, Gyroscope X/Y/Z |

## Installation

### HACS (recommended)

1. Add this repository as a custom HACS repository (Integration type)
2. Install "Sphero R2D2 / Q5 Droid"
3. Restart Home Assistant

### Manual

Copy `custom_components/r2d2/` into your HA config's `custom_components/` directory and restart.

## Setup

### Automatic discovery

If your droid is powered on and in range, HA will automatically discover it and prompt you to add it via a notification.

### Manual

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "R2D2"
3. Enter the droid's MAC address (e.g. `E4:B4:0A:71:79:B9`)

## Requirements

- Home Assistant 2024.1+
- Bluetooth integration enabled in HA
- Direct Bluetooth adapter **or** ESPHome Bluetooth proxy on the same network

## Droid naming

Droids advertise as `D2-XXXX` (R2-D2) or `R2-XXXX` (R2-Q5) where `XXXX` is the last two octets of the MAC address. The integration discovers both patterns.

## Notes

- **Sensors** are enabled automatically on connection. IMU, accelerometer, head angle, and gyroscope data stream at ~150ms intervals.
- **Dome rotation** has a firmware dead zone around +68° to +78° (cable routing limit).
- The droid auto-sleeps after ~10 minutes of inactivity. The integration sends an init command before any operation if idle too long.
- **R2-Q5** support is untested but uses the same Sphero protocol — all commands should work.

## Architecture

The integration vendors the BLE protocol layer from [droid_controller](https://github.com/bircoe/droid_controller) under `droid/`. The integration itself uses HA's Bluetooth stack, which means it works through ESPHome Bluetooth proxies in addition to direct connections.

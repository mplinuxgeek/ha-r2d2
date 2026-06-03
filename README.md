# Home Assistant — Sphero R2D2 / Q5 Droid Integration

Custom integration for controlling Sphero R2-D2 and R2-Q5 droids via Bluetooth LE.

## Features

| Platform | Entities |
|---|---|
| **Button** | Power Off, Tripod, Bipod, Waddle On/Off, Stop Driving, Reset Yaw, Stop Animation, Reconnect |
| **Light** | Front LED (RGB), Back LED (RGB), Holo Projector (brightness), Logic Display (brightness) |
| **Number** | Dome Rotation (−160° to +180°), Dome Speed, Volume |
| **Select** | Animation picker (model-specific), Audio clip picker |
| **Switch** | All Lights, Keep Awake, Idle Animations |
| **Binary sensor** | Connected, Charging, Battery Low |
| **Event** | Collision (physical bump, with accel/axis/power/speed attributes) |
| **Sensor** | Battery %, IMU pitch/roll/yaw, Accelerometer X/Y/Z, Head angle, Gyroscope X/Y/Z, Signal strength, Firmware Version, Bootloader Version, MAC Address, SKU |
| **Services** | `r2d2.drive`, `r2d2.stop` (domain-level, target any/all droids) |

Some diagnostic sensors (Signal strength, Bootloader Version, MAC Address, SKU)
are disabled by default — enable them from the entity list if you want them.

### Model-aware behaviour (R2-D2 vs R2-Q5)

Animation IDs mean different things on the two droids (the emotes especially).
The integration auto-detects the model from the droid's Bluetooth name and
exposes the correct animation set and labels for each. The detected model is
stored with the config entry and shown on the device page.

### Collision, battery & sleep

- **Collision** — the droid reports physical bumps; the `Collision` event entity
  fires with acceleration, which axis, impact power and speed, ready for
  automations.
- **Charging / Battery Low** — push-driven binary sensors from the droid's own
  battery-state notifications (not just the periodic battery-percentage poll).
- **Sleep** — the droid's will-sleep/did-sleep notifications drive the connection
  state directly; with **Keep Awake** on, an imminent sleep is deferred.

### Device info

Firmware/bootloader version, MAC address and SKU are read once on connect and
shown both as sensors and on the device page (Firmware → software version,
board revision → hardware version).

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

Droids advertise as `D2-XXXX` (R2-D2) or `Q5-XXXX` (R2-Q5), where `XXXX` is the
last two octets of the MAC address. The integration auto-discovers both and uses
the prefix to select the correct per-model animation set.

## Notes

- **Sensors** are enabled automatically on connection. IMU, accelerometer, head
  angle and gyroscope stream at a few Hz; values are pushed to HA at ~1 Hz (the
  latest reading is never lost).
- **Dome rotation** has a firmware dead zone around +68° to +78° (cable routing limit).
- The droid auto-sleeps after ~5 minutes of inactivity. The integration re-inits
  and re-arms the sensor stream automatically before a command if it has been
  idle that long, and reacts to the droid's own sleep notifications. There is no
  Bluetooth-settable inactivity timeout on these droids — **Keep Awake** holds
  the link by periodic activity instead.
- **R2-D2** and **R2-Q5** are both supported with model-specific animations.

## Architecture

The integration vendors the BLE protocol layer from [droid_controller](https://github.com/bircoe/droid_controller) under `droid/`. The integration itself uses HA's Bluetooth stack, which means it works through ESPHome Bluetooth proxies in addition to direct connections.

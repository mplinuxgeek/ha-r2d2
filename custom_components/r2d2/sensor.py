"""Sensor entities for the R2D2 integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, DEGREE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ATTR_ACCEL_X,
    ATTR_ACCEL_Y,
    ATTR_ACCEL_Z,
    ATTR_IMU_PITCH,
    ATTR_IMU_ROLL,
    ATTR_IMU_YAW,
    ATTR_HEAD_ANGLE,
    ATTR_GYRO_X,
    ATTR_GYRO_Y,
    ATTR_GYRO_Z,
    ATTR_RSSI,
)
from .coordinator import R2D2Coordinator

# Unit for acceleration in g
_UNIT_G = "g"
# Unit for angular velocity in degrees/s
_UNIT_DEG_S = "°/s"


@dataclass(frozen=True, kw_only=True)
class R2D2SensorEntityDescription(SensorEntityDescription):
    """Describe an R2D2 sensor entity with its data key."""
    data_key: str = ""


SENSOR_DESCRIPTIONS: tuple[R2D2SensorEntityDescription, ...] = (
    R2D2SensorEntityDescription(
        key="battery",
        translation_key="battery",
        data_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_IMU_PITCH,
        translation_key="imu_pitch",
        data_key=ATTR_IMU_PITCH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_IMU_ROLL,
        translation_key="imu_roll",
        data_key=ATTR_IMU_ROLL,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_IMU_YAW,
        translation_key="imu_yaw",
        data_key=ATTR_IMU_YAW,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_ACCEL_X,
        translation_key="accel_x",
        data_key=ATTR_ACCEL_X,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_G,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_ACCEL_Y,
        translation_key="accel_y",
        data_key=ATTR_ACCEL_Y,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_G,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_ACCEL_Z,
        translation_key="accel_z",
        data_key=ATTR_ACCEL_Z,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_G,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_HEAD_ANGLE,
        translation_key="head_angle",
        data_key=ATTR_HEAD_ANGLE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=DEGREE,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_GYRO_X,
        translation_key="gyro_x",
        data_key=ATTR_GYRO_X,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_DEG_S,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_GYRO_Y,
        translation_key="gyro_y",
        data_key=ATTR_GYRO_Y,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_DEG_S,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_GYRO_Z,
        translation_key="gyro_z",
        data_key=ATTR_GYRO_Z,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=_UNIT_DEG_S,
    ),
    R2D2SensorEntityDescription(
        key=ATTR_RSSI,
        translation_key="rssi",
        data_key=ATTR_RSSI,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up R2D2 sensor entities."""
    coordinator: R2D2Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        R2D2Sensor(coordinator, entry, description)
        for description in SENSOR_DESCRIPTIONS
    )


class R2D2Sensor(CoordinatorEntity[R2D2Coordinator], SensorEntity):
    """A sensor entity reading from the R2D2 coordinator data."""

    entity_description: R2D2SensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: R2D2Coordinator,
        entry: ConfigEntry,
        description: R2D2SensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name=coordinator.droid_name,
            manufacturer="Sphero",
            model="R2-D2 / Q5",
        )

    @property
    def available(self) -> bool:
        """Return True if connected and data is present."""
        if not (self.coordinator.droid is not None and self.coordinator.droid.connected):
            return False
        # Battery is available as soon as connected; sensor keys available after first push
        return True

    @property
    def native_value(self) -> Any:
        """Return the sensor value from coordinator data."""
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self.entity_description.data_key)
        # HA rejects non-finite floats for measurement sensors — return None
        # (unavailable) rather than letting them crash async_write_ha_state.
        if isinstance(val, float) and not (val == val and val != float("inf") and val != float("-inf")):
            return None
        return val

"""Sensor platform for AmpliPi integration."""
import os
import yaml
from homeassistant.helpers.entity import Entity

def load_sensors():
    """Load sensors from sensors.yaml."""
    sensor_file = os.path.join(os.path.dirname(__file__), "sensors.yaml")
    if os.path.exists(sensor_file):
        with open(sensor_file, "r") as file:
            return yaml.safe_load(file)
    return {}

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up sensors from the config entry."""
    sensors = load_sensors().get("sensor", [])
    entities = [AmpliPiSensor(sensor) for sensor in sensors]
    async_add_entities(entities, True)

class AmpliPiSensor(Entity):
    """Representation of an AmpliPi sensor."""

    def __init__(self, sensor_data):
        """Initialize the sensor."""
        self._attr_name = sensor_data.get("name")
        self._attr_unique_id = f"amplipi_{sensor_data.get('name')}"
        self._state = sensor_data.get("state", "unknown")

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._attr_name

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

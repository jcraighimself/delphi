"""Indoor sensors: BME688 (temp, humidity, pressure, gas) and SCD-41 (CO2).

Both sit on I2C bus 1. Readings fail soft: if a sensor is missing or glitches,
its fields come back as None and we try to reconnect on the next read.
"""

import logging
import time

import bme680
from smbus2 import SMBus, i2c_msg

log = logging.getLogger(__name__)

I2C_BUS = 1
BME688_ADDRESS = 0x76
SCD41_ADDRESS = 0x62

# Every reading has all of these keys; a value is None if it couldn't be read.
FIELDS = (
    "temperature_c",       # BME688 (primary thermometer)
    "humidity_pct",        # BME688
    "pressure_hpa",        # BME688
    "gas_ohms",            # BME688; None until the gas heater has warmed up
    "co2_ppm",             # SCD-41
    "scd41_temperature_c", # SCD-41 reads warm; kept only for comparison
    "scd41_humidity_pct",  # SCD-41
)


def _crc8(hi, lo):
    """Checksum the SCD-41 sends after every 2-byte word (Sensirion CRC-8)."""
    crc = 0xFF
    for byte in (hi, lo):
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) if crc & 0x80 else (crc << 1)
            crc &= 0xFF
    return crc


class SCD41:
    """Minimal SCD-41 driver: just the four commands we need, from the Sensirion datasheet."""

    START_PERIODIC = 0x21B1
    STOP_PERIODIC = 0x3F86
    DATA_READY = 0xE4B8
    READ_MEASUREMENT = 0xEC05

    def __init__(self, bus, address=SCD41_ADDRESS):
        self.bus = bus
        self.address = address
        # A previous run may have left it measuring, and it ignores most
        # commands until stopped. Stopping takes 500 ms.
        self._send(self.STOP_PERIODIC)
        time.sleep(0.5)
        # From here on it takes a new measurement every 5 seconds by itself.
        self._send(self.START_PERIODIC)

    def _send(self, command):
        self.bus.i2c_rdwr(i2c_msg.write(self.address, [command >> 8, command & 0xFF]))

    def _read_words(self, command, count):
        """Send a command, then read back `count` 16-bit words (each followed by a CRC byte)."""
        self._send(command)
        time.sleep(0.005)
        msg = i2c_msg.read(self.address, count * 3)
        self.bus.i2c_rdwr(msg)
        data = list(msg)
        words = []
        for i in range(0, len(data), 3):
            hi, lo, crc = data[i:i + 3]
            if _crc8(hi, lo) != crc:
                raise IOError("SCD-41 checksum mismatch (check the cable)")
            words.append((hi << 8) | lo)
        return words

    def data_ready(self):
        (status,) = self._read_words(self.DATA_READY, 1)
        return (status & 0x07FF) != 0

    def read(self, timeout=10.0):
        """Wait for the next measurement, then return (co2_ppm, temperature_c, humidity_pct)."""
        deadline = time.monotonic() + timeout
        while not self.data_ready():
            if time.monotonic() > deadline:
                raise TimeoutError("SCD-41 had no measurement ready")
            time.sleep(0.5)
        co2, raw_temp, raw_humidity = self._read_words(self.READ_MEASUREMENT, 3)
        temperature = -45 + 175 * raw_temp / 65535
        humidity = 100 * raw_humidity / 65535
        return co2, temperature, humidity

    def stop(self):
        self._send(self.STOP_PERIODIC)
        time.sleep(0.5)


def _setup_bme688():
    sensor = bme680.BME680(BME688_ADDRESS)
    # Oversampling and filter settings from Pimoroni's examples.
    sensor.set_humidity_oversample(bme680.OS_2X)
    sensor.set_pressure_oversample(bme680.OS_4X)
    sensor.set_temperature_oversample(bme680.OS_8X)
    sensor.set_filter(bme680.FILTER_SIZE_3)
    # The gas sensor works by heating a tiny plate and measuring its resistance.
    sensor.set_gas_status(bme680.ENABLE_GAS_MEAS)
    sensor.set_gas_heater_temperature(320)
    sensor.set_gas_heater_duration(150)
    sensor.select_gas_heater_profile(0)
    return sensor


class Sensors:
    """Keeps both sensors open between reads. Call read() as often as you like."""

    def __init__(self):
        self._bus = None
        self._bme = None
        self._scd = None

    def read(self):
        reading = dict.fromkeys(FIELDS)
        reading.update(self._read_bme688())
        reading.update(self._read_scd41())
        return reading

    def close(self):
        if self._scd is not None:
            try:
                self._scd.stop()
            except OSError:
                pass
        if self._bus is not None:
            self._bus.close()

    def _read_bme688(self):
        try:
            if self._bme is None:
                self._bme = _setup_bme688()
            # Each attempt triggers a measurement; give the heater time to finish.
            for _ in range(5):
                if self._bme.get_sensor_data():
                    break
                time.sleep(0.2)
            else:
                raise IOError("BME688 returned no data")
            data = self._bme.data
            return {
                "temperature_c": round(data.temperature, 1),
                "humidity_pct": round(data.humidity, 1),
                "pressure_hpa": round(data.pressure, 1),
                "gas_ohms": round(data.gas_resistance) if data.heat_stable else None,
            }
        except Exception as error:
            log.warning("BME688 read failed: %s", error)
            self._bme = None  # reconnect next time
            return {}

    def _read_scd41(self):
        try:
            if self._scd is None:
                if self._bus is None:
                    self._bus = SMBus(I2C_BUS)
                self._scd = SCD41(self._bus)
            co2, temperature, humidity = self._scd.read()
            return {
                "co2_ppm": co2,
                "scd41_temperature_c": round(temperature, 1),
                "scd41_humidity_pct": round(humidity, 1),
            }
        except Exception as error:
            log.warning("SCD-41 read failed: %s", error)
            self._scd = None  # reconnect next time
            return {}

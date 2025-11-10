# MIT license; Copyright (c) 2025 Planet Innovation
# ICM-40627 6-axis accelerometer/gyroscope driver for MicroPython.

"""
ICM-40627 6-axis accelerometer/gyroscope driver for MicroPython.

The ICM-40627 is a 6-axis MEMS Motion Tracking device that combines a
3-axis accelerometer and 3-axis gyroscope on a single chip.
"""

# Register addresses (ICM-40627 register map)
_REG_WHO_AM_I = 0x75
_REG_PWR_MGMT_1 = 0x4E
_REG_PWR_MGMT_2 = 0x4F
_REG_ACCEL_CONFIG0 = 0x50
_REG_GYRO_CONFIG0 = 0x4F
_REG_TEMP_CONFIG0 = 0x4D
_REG_ACCEL_X_H = 0x1F
_REG_GYRO_X_H = 0x25
_REG_TEMP_H = 0x1D

# WHO_AM_I expected value
_WHOAMI_VALUE = 0x4E

# Accelerometer scale factors (sensitivity in LSB/g)
# ±2g: 16384 LSB/g, ±4g: 8192 LSB/g, ±8g: 4096 LSB/g, ±16g: 2048 LSB/g
_ACCEL_SCALE_2G = 16384
_ACCEL_SCALE_4G = 8192
_ACCEL_SCALE_8G = 4096
_ACCEL_SCALE_16G = 2048

# Gyroscope scale factors (sensitivity in LSB/°/s)
# ±2000°/s: 16.4 LSB/°/s
_GYRO_SCALE = 16.4

# Temperature scale factor: 1/333.87 °C per LSB, offset 21 °C at 0 LSB
_TEMP_SCALE = 1.0 / 333.87
_TEMP_OFFSET = 21.0


class ICM40627:
    """
    Driver for the ICM-40627 6-axis accelerometer/gyroscope.

    Example usage:
        from machine import I2C
        from icm40627 import ICM40627

        i2c = I2C(1)
        imu = ICM40627(i2c, addr=0x6B)

        ax, ay, az = imu.acceleration
        gx, gy, gz = imu.gyro
        temp = imu.temperature
    """

    def __init__(self, i2c, addr=0x6B, accel_range=8):
        """
        Initialize the ICM-40627.

        Args:
            i2c: machine.I2C object for communication
            addr: I2C address (default 0x6B)
            accel_range: Accelerometer range in g (2, 4, 8, or 16; default 8)
        """
        self.i2c = i2c
        self.addr = addr

        # Validate device presence
        self._device_id = self._read_register(_REG_WHO_AM_I)
        if self._device_id != _WHOAMI_VALUE:
            raise RuntimeError(
                f"ICM-40627 not found at address 0x{addr:02X}. "
                f"Got ID 0x{self._device_id:02X}, expected 0x{_WHOAMI_VALUE:02X}"
            )

        # Set accelerometer scale
        if accel_range == 2:
            self._accel_scale = _ACCEL_SCALE_2G
            self._accel_config = 0x00  # ±2g
        elif accel_range == 4:
            self._accel_scale = _ACCEL_SCALE_4G
            self._accel_config = 0x01  # ±4g
        elif accel_range == 8:
            self._accel_scale = _ACCEL_SCALE_8G
            self._accel_config = 0x02  # ±8g
        elif accel_range == 16:
            self._accel_scale = _ACCEL_SCALE_16G
            self._accel_config = 0x03  # ±16g
        else:
            raise ValueError("accel_range must be 2, 4, 8, or 16")

        # Initialize the device
        self._initialize()

    def _initialize(self):
        """Initialize ICM-40627 with default configuration."""
        # Exit sleep mode
        self._write_register(_REG_PWR_MGMT_1, 0x00)

        # Set accelerometer configuration
        self._write_register(_REG_ACCEL_CONFIG0, self._accel_config << 5)

        # Set gyroscope configuration to ±2000°/s
        self._write_register(_REG_GYRO_CONFIG0, 0x00 << 5)

    def _read_register(self, reg):
        """Read a single register value."""
        data = self.i2c.readfrom_mem(self.addr, reg, 1)
        return data[0]

    def _read_registers(self, reg, count):
        """Read multiple consecutive registers."""
        return self.i2c.readfrom_mem(self.addr, reg, count)

    def _write_register(self, reg, value):
        """Write a single register value."""
        self.i2c.writeto_mem(self.addr, reg, bytes([value]))

    def _read_int16(self, reg):
        """Read a 16-bit signed integer from two consecutive registers."""
        data = self._read_registers(reg, 2)
        value = (data[0] << 8) | data[1]
        # Convert to signed
        if value & 0x8000:
            value -= 0x10000
        return value

    @property
    def acceleration(self):
        """
        Read accelerometer data.

        Returns:
            Tuple of (x, y, z) acceleration in g units
        """
        x = self._read_int16(_REG_ACCEL_X_H) / self._accel_scale
        y = self._read_int16(_REG_ACCEL_X_H + 2) / self._accel_scale
        z = self._read_int16(_REG_ACCEL_X_H + 4) / self._accel_scale
        return (x, y, z)

    @property
    def gyro(self):
        """
        Read gyroscope data.

        Returns:
            Tuple of (x, y, z) angular velocity in degrees per second
        """
        x = self._read_int16(_REG_GYRO_X_H) / _GYRO_SCALE
        y = self._read_int16(_REG_GYRO_X_H + 2) / _GYRO_SCALE
        z = self._read_int16(_REG_GYRO_X_H + 4) / _GYRO_SCALE
        return (x, y, z)

    @property
    def temperature(self):
        """
        Read temperature sensor data.

        Returns:
            Temperature in degrees Celsius
        """
        raw = self._read_int16(_REG_TEMP_H)
        return (raw * _TEMP_SCALE) + _TEMP_OFFSET

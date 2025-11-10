# MIT license; Copyright (c) 2025 Planet Innovation
# ICM-40627 basic example

import time
from icm40627 import ICM40627
from machine import I2C

# Initialize I2C and IMU
i2c = I2C(1)
imu = ICM40627(i2c, addr=0x6B, accel_range=8)

# Read and display sensor data
while True:
    print("Accelerometer: x:{:>8.3f} y:{:>8.3f} z:{:>8.3f}".format(*imu.acceleration))
    print("Gyroscope:     x:{:>8.3f} y:{:>8.3f} z:{:>8.3f}".format(*imu.gyro))
    print("Temperature:   {:>8.2f}°C".format(imu.temperature))
    print("")
    time.sleep_ms(100)

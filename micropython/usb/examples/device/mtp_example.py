# MicroPython USB MTP example
#
# This example demonstrates creating a USB MTP device to expose the MicroPython
# filesystem to a host computer.
#
# To run this example:
#
# 1. Make sure `usb-device-mtp` is installed via: mpremote mip install usb-device-mtp
#
# 2. Run the example via: mpremote run mtp_example.py
#
# 3. mpremote will exit with an error after the previous step, because when the
#    example runs the existing USB device disconnects and then re-enumerates with
#    the MTP interface present. At this point, the example is running.
#
# 4. To see output from the example, reconnect: mpremote connect PORTNAME
#
# 5. On your host computer, you should see a new storage device appear that
#    allows browsing the MicroPython's filesystem.
#
# MIT license; Copyright (c) 2024 MicroPython Developers
import usb.device
from usb.device.mtp import MTPInterface
import time

# Create an MTP interface that exposes the root directory
mtp = MTPInterface(storage_path="/")

# Initialize the USB device with the MTP interface
# Keep builtin_driver=True to maintain the serial REPL alongside MTP
usb.device.get().init(mtp, builtin_driver=True)

print("Waiting for USB host to configure the MTP interface...")

# Wait for the host to configure the interface
while not mtp.is_open():
    time.sleep_ms(100)

print("MTP interface is now available to the host.")
print("Your MicroPython filesystem should be accessible on the host computer.")
print("Press Ctrl+C to exit.")

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("MTP example terminated by user.")
# SPDX-License-Identifier: MIT
# Copyright (c) 2023 MicroPython Team

"""
Example showing how to implement a USB MTP device to expose the MicroPython filesystem.

This example uses the MicroPython USB MTP driver to expose the filesystem
to a host computer as a Media Transfer Protocol (MTP) device. This allows
the host to browse, download, upload, and manipulate files on the MicroPython device.

Usage:
1. Connect to your MicroPython board's REPL
2. Run this example
3. Connect the device to a host computer via USB
4. The device should appear as an MTP device (like a camera or media player)
5. Files can be accessed through the host's file browser
"""

import os
import time
import machine
from usb.device.mtp import MTPInterface

def main():
    """Main function to run the MTP example."""
    # Print startup message
    print("MicroPython USB MTP Example")
    print("===========================")
    
    # Create MTP interface to expose the filesystem
    # The root_dir parameter sets which directory to expose via MTP
    # Here we use '/' to expose the entire filesystem
    mtp = MTPInterface(root_dir="/")
    
    # Create a USB device using machine.USBDevice
    usb_dev = machine.USBDevice(0xF055, 0x9802, "MicroPython", "MTP Device", "123456789")
    
    # Add the MTP interface to the USB device
    usb_dev.add_interface(mtp)
    
    # Enable the USB device
    usb_dev.enable()
    
    # Wait for USB to be configured
    print("Waiting for USB connection...")
    while not usb_dev.configured():
        time.sleep(0.1)
    
    print("USB MTP device connected!")
    print("You can now access files via the host computer")
    print("Press Ctrl+C to exit")
    
    try:
        # Keep the program running to maintain the USB connection
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        # Clean up on exit
        print("Exiting MTP example")
        usb_dev.disable()

if __name__ == "__main__":
    main()
# MicroPython PyUSB

A MicroPython-compatible subset of PyUSB for USB device access via libusb-1.0.

## Overview

This package provides a pyusb-compatible API for MicroPython, enabling USB device
enumeration and control transfers. It uses MicroPython's `ffi` module to call
libusb-1.0 functions directly.

Originally developed for Unix, this package also supports Windows when built with
FFI support (`MICROPY_PY_FFI=1`).

## Supported API

### usb.core

- `find(idVendor=, idProduct=, find_all=, custom_match=)` - Find USB devices
- `Device.set_configuration()` - Set device configuration
- `Device.ctrl_transfer(bmRequestType, bRequest, wValue, wIndex, data_or_wLength, timeout)` - Control transfer
- `Device.configurations()` - Iterate device configurations
- `Device.bus`, `Device.address` - Bus/address properties
- `Device.idVendor`, `Device.idProduct` - Device identifiers
- `Configuration.interfaces()` - Iterate interfaces
- `Configuration[(intf_num, alt_setting)]` - Get interface by number/altsetting
- `Interface.bInterfaceNumber`, `.bAlternateSetting`, `.bInterfaceClass`, `.bInterfaceSubClass`, `.bInterfaceProtocol`, `.iInterface`, `.extra_descriptors`

### usb.util

- `claim_interface(device, interface)` - Claim interface
- `get_string(device, index)` - Get string descriptor
- `dispose_resources(device)` - Release device resources

### usb.control

- `get_descriptor(dev, desc_size, desc_type, desc_index, wIndex)` - Get descriptor

## Usage Example

```python
import usb.core
import usb.util

# Find device by VID/PID
dev = usb.core.find(idVendor=0x0483, idProduct=0xdf11)
if dev is None:
    raise ValueError("Device not found")

# Set configuration and claim interface
dev.set_configuration()
usb.util.claim_interface(dev, 0)

# Control transfer
dev.ctrl_transfer(0x21, 0x03, 0, 0, None, 1000)

# Cleanup
usb.util.dispose_resources(dev)
```

## Bundled libusb-1.0.dll (Windows)

The `usb/libusb-1.0.dll` file is bundled for Windows support.

**Source:** Extracted from PyPI package `libusb-package` version 1.0.26.3
- PyPI: https://pypi.org/project/libusb-package/
- Source: https://github.com/pyocd/libusb-package
- libusb upstream: https://github.com/libusb/libusb

**License:**
- libusb-package Python code: Apache 2.0
- libusb library: LGPL-2.1 (https://github.com/libusb/libusb/blob/master/COPYING)

## License

MicroPython pyusb code: MIT license, Copyright (c) 2021-2024 Damien P. George

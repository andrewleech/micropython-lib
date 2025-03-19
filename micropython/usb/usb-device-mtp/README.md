# USB MTP Device Driver for MicroPython

This package provides a USB Media Transfer Protocol (MTP) interface for MicroPython, enabling filesystem access from host computers via a standard protocol.

## Features

- Implements the Media Transfer Protocol (MTP) based on the PTP/PIMA 15740 Still Image standard
- Compatible with Windows, macOS, and Linux hosts without requiring special drivers
- Exposes MicroPython's filesystem to the host computer
- Supports basic file operations:
  - Browsing directories
  - Uploading files to the device
  - Downloading files from the device
  - Deleting files and directories
- Optional debug logging to assist with troubleshooting

## Installation

Install the package using MicroPython's package manager:

```
mpremote mip install usb-device-mtp
```

## Usage

The basic usage pattern is:

```python
import usb.device
from usb.device.mtp import MTPInterface

# Create an MTP interface that exposes a specific path as storage
mtp = MTPInterface(storage_path="/")

# Initialize the USB device with the MTP interface
# Keep builtin_driver=True to maintain the serial REPL alongside MTP
usb.device.get().init(mtp, builtin_driver=True)

# At this point, the MTP interface is available to the host computer
# Your MicroPython filesystem will be accessible via the standard file manager
```

See [mtp_example.py](/examples/device/mtp_example.py) for a complete example program.

## Configuration Options

The `MTPInterface` constructor accepts the following parameters:

- `storage_path`: Root directory to expose via MTP (default: "/")
- `rx_size`: Size of the receive buffer in bytes (default: 4096)
- `tx_size`: Size of the transmit buffer in bytes (default: 4096)
- `debug`: Enable detailed debug logging (default: False)

## Runtime USB Considerations

When running the MTP interface:

1. The device may disconnect temporarily from the host while USB reconfiguration occurs
2. The MicroPython REPL serial interface will remain accessible if you use `builtin_driver=True`
3. If you add the MTP initialization code to `boot.py`, remember to include `builtin_driver=True` to maintain access to the device

## Limitations

- Performance may vary depending on the MicroPython board's USB hardware
- Large file transfers might be slower than with dedicated file transfer tools
- Limited support for MTP events and advanced features
- Only one storage (the specified path) is exposed

## Implementation Details

This driver implements the USB MTP protocol by extending the `Interface` class from `usb.device.core`. It uses the Still Image class (0x06) with the PIMA 15740 protocol (0x01) specification to provide compatibility with standard MTP implementations on host operating systems.

The driver requires three endpoints:
- Bulk OUT endpoint for receiving commands and data
- Bulk IN endpoint for sending data and responses
- Interrupt IN endpoint for sending events (currently unused)

## Debugging

To enable verbose debug logging:

```python
mtp = MTPInterface(storage_path="/", debug=True)
```

This will output detailed information about USB transfers, MTP commands, and filesystem operations, which can be helpful when troubleshooting connection issues.
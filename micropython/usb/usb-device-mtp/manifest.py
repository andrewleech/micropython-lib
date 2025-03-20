# MicroPython package manifest for usb-device-mtp
package(
    "usb-device-mtp",
    (
        "__init__.py",
        "usb/device/mtp.py",
    ),
    base_path="..",
    version="1.0.0",
    description="USB MTP device driver for MicroPython",
    url="https://github.com/micropython/micropython-lib",
    license="MIT",
    author="MicroPython Developers",
    author_email="team@micropython.org",
)

requires("usb-device")
requires("shutil")
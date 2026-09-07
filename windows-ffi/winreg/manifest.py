metadata(version="0.1.0", description="Subset of CPython's winreg module")

add_library("windows-ffi", "$(MPY_LIB_DIR)/windows-ffi")
require("_wstr", library="windows-ffi")

module("winreg.py")

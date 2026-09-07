metadata(version="0.1.0", description="Windows-only os additions (CPython os module parity)")

add_library("windows-ffi", "$(MPY_LIB_DIR)/windows-ffi")
require("_wstr", library="windows-ffi")

# stat_result is shared with python-stdlib/os rather than forked.
package("os", files=["_stat.py"], base_path="../../python-stdlib/os")
package("os")

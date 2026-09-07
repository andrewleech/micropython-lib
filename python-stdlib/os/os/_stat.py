# Shared by the platform "os" variants (e.g. windows-ffi/os), which pull this
# file in by name via a manifest.py base_path reference back to this package.
from uos import stat as _uos_stat
from collections import namedtuple

# https://docs.python.org/3/library/os.html#os.stat_result
stat_result = namedtuple(
    "stat_result",
    (
        "st_mode",
        "st_ino",
        "st_dev",
        "st_nlink",
        "st_uid",
        "st_gid",
        "st_size",
        "st_atime",
        "st_mtime",
        "st_ctime",
    ),
)


def stat(path):
    return stat_result(*_uos_stat(path))

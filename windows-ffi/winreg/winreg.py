"""A subset of CPython's winreg module, over raw advapi32.dll FFI calls.

Covers create/open/close, get/set of a value, and delete of a key/value.
Not covered: EnumKey/EnumValue/FlushKey, REG_DWORD_BIG_ENDIAN/REG_QWORD,
and CPython's mapping of Win32 error codes onto exception subclasses
(e.g. ERROR_FILE_NOT_FOUND -> FileNotFoundError); errors here are plain
OSError(win32_error_code).
"""

import array
import ffi
import struct

from _wstr import wstr, from_wstr_bytes, wstr_multi, from_wstr_multi

try:
    _advapi32 = ffi.open("advapi32.dll")
except OSError as e:
    # ImportError so a missing DLL fails like any other missing dependency.
    raise ImportError("failed to open advapi32.dll: {}".format(e))

# LSTATUS RegCreateKeyExW(HKEY hKey, LPCWSTR lpSubKey, DWORD Reserved,
#   LPWSTR lpClass, DWORD dwOptions, REGSAM samDesired,
#   const LPSECURITY_ATTRIBUTES lpSecurityAttributes, PHKEY phkResult,
#   LPDWORD lpdwDisposition);
_RegCreateKeyExW = _advapi32.func("i", "RegCreateKeyExW", "ppIpIIppp")
# LSTATUS RegOpenKeyExW(HKEY hKey, LPCWSTR lpSubKey, DWORD ulOptions,
#   REGSAM samDesired, PHKEY phkResult);
_RegOpenKeyExW = _advapi32.func("i", "RegOpenKeyExW", "ppIIp")
# LSTATUS RegSetValueExW(HKEY hKey, LPCWSTR lpValueName, DWORD Reserved,
#   DWORD dwType, const BYTE *lpData, DWORD cbData);
_RegSetValueExW = _advapi32.func("i", "RegSetValueExW", "ppIIpI")
# LSTATUS RegQueryValueExW(HKEY hKey, LPCWSTR lpValueName, LPDWORD lpReserved,
#   LPDWORD lpType, LPBYTE lpData, LPDWORD lpcbData);
_RegQueryValueExW = _advapi32.func("i", "RegQueryValueExW", "pppppp")
# LSTATUS RegDeleteKeyW(HKEY hKey, LPCWSTR lpSubKey);
_RegDeleteKeyW = _advapi32.func("i", "RegDeleteKeyW", "pp")
# LSTATUS RegDeleteValueW(HKEY hKey, LPCWSTR lpValueName);
_RegDeleteValueW = _advapi32.func("i", "RegDeleteValueW", "pp")
# LSTATUS RegCloseKey(HKEY hKey);
_RegCloseKey = _advapi32.func("i", "RegCloseKey", "p")

# HKEY_* root pseudo-handles (never closed).
HKEY_CLASSES_ROOT = 0x80000000
HKEY_CURRENT_USER = 0x80000001
HKEY_LOCAL_MACHINE = 0x80000002
HKEY_USERS = 0x80000003
HKEY_PERFORMANCE_DATA = 0x80000004
HKEY_CURRENT_CONFIG = 0x80000005
HKEY_DYN_DATA = 0x80000006

# KEY_* access rights.
KEY_QUERY_VALUE = 0x0001
KEY_SET_VALUE = 0x0002
KEY_CREATE_SUB_KEY = 0x0004
KEY_ENUMERATE_SUB_KEYS = 0x0008
KEY_NOTIFY = 0x0010
KEY_CREATE_LINK = 0x0020
KEY_WOW64_32KEY = 0x0200
KEY_WOW64_64KEY = 0x0100
KEY_READ = 0x20019
KEY_WRITE = 0x20006
KEY_EXECUTE = 0x20019
KEY_ALL_ACCESS = 0xF003F

# REG_* value types.
REG_NONE = 0
REG_SZ = 1
REG_EXPAND_SZ = 2
REG_BINARY = 3
REG_DWORD = 4
REG_DWORD_LITTLE_ENDIAN = 4
REG_DWORD_BIG_ENDIAN = 5
REG_MULTI_SZ = 7
REG_QWORD = 11
REG_QWORD_LITTLE_ENDIAN = 11

_ERROR_MORE_DATA = 234
_MAX_VALUE_SIZE = 1 << 20


class HKEYType:
    def __init__(self, handle):
        self._handle = handle
        self._closed = False

    def __int__(self):
        return self._handle

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.Close()

    def Close(self):
        if not self._closed:
            _RegCloseKey(self._handle)
            self._closed = True


def CloseKey(key):
    if isinstance(key, HKEYType):
        key.Close()
    else:
        _RegCloseKey(int(key))


def CreateKeyEx(key, sub_key, reserved=0, access=KEY_WRITE):
    hkey_buf = array.array("P", [0])
    disp_buf = array.array("I", [0])
    sub_key_buf = wstr(sub_key)
    rc = _RegCreateKeyExW(int(key), sub_key_buf, reserved, 0, 0, access, 0, hkey_buf, disp_buf)
    if rc != 0:
        raise OSError(rc)
    return HKEYType(hkey_buf[0])


def CreateKey(key, sub_key):
    return CreateKeyEx(key, sub_key, 0, KEY_WRITE)


def OpenKeyEx(key, sub_key, reserved=0, access=KEY_READ):
    hkey_buf = array.array("P", [0])
    sub_key_buf = wstr(sub_key)
    rc = _RegOpenKeyExW(int(key), sub_key_buf, reserved, access, hkey_buf)
    if rc != 0:
        raise OSError(rc)
    return HKEYType(hkey_buf[0])


OpenKey = OpenKeyEx


def DeleteKey(key, sub_key):
    rc = _RegDeleteKeyW(int(key), wstr(sub_key))
    if rc != 0:
        raise OSError(rc)


def DeleteValue(key, value):
    name_buf = wstr(value) if value else None
    rc = _RegDeleteValueW(int(key), name_buf if name_buf else 0)
    if rc != 0:
        raise OSError(rc)


def _pack_value(type, value):
    if type in (REG_SZ, REG_EXPAND_SZ):
        return wstr(value)
    if type == REG_DWORD:
        return struct.pack("<I", value)
    if type == REG_MULTI_SZ:
        return wstr_multi(value)
    if type in (REG_BINARY, REG_NONE):
        return value
    raise NotImplementedError("registry type {} not supported".format(type))


def _unpack_value(type, buf, nbytes):
    if type in (REG_SZ, REG_EXPAND_SZ):
        return from_wstr_bytes(buf, nbytes)
    if type == REG_DWORD:
        return struct.unpack("<I", bytes(buf[:4]))[0]
    if type == REG_MULTI_SZ:
        return from_wstr_multi(buf, nbytes)
    return bytes(buf[:nbytes])


def SetValueEx(key, value_name, reserved, type, value):
    data = _pack_value(type, value)
    name_buf = wstr(value_name) if value_name else None
    rc = _RegSetValueExW(int(key), name_buf if name_buf else 0, 0, type, data, len(data))
    if rc != 0:
        raise OSError(rc)


def QueryValueEx(key, value_name):
    name_buf = wstr(value_name) if value_name else None
    name_arg = name_buf if name_buf else 0
    type_buf = array.array("I", [0])
    size = 256
    while True:
        data_buf = bytearray(size)
        size_buf = array.array("I", [size])
        rc = _RegQueryValueExW(int(key), name_arg, 0, type_buf, data_buf, size_buf)
        if rc == _ERROR_MORE_DATA and size < _MAX_VALUE_SIZE:
            size *= 2
            continue
        if rc != 0:
            raise OSError(rc)
        return _unpack_value(type_buf[0], data_buf, size_buf[0]), type_buf[0]

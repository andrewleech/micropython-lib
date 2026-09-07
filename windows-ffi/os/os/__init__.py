"""CPython's Windows-only os functions, on top of the built-in uos.

Like unix-ffi/os, this replaces the "os" package rather than layering on
python-stdlib/os: both install to "os/__init__.py", so install one or the
other, not both. Packages that only add to "os", such as os-path and
pathlib, still layer on top.

spawnve/spawnle are not implemented.
"""

from uos import *

try:
    from . import path
except ImportError:
    pass

from ._stat import stat_result, stat

import array
import ffi
import uctypes

from _wstr import wstr

try:
    _ucrtbase = ffi.open("ucrtbase.dll")
except OSError:
    # Only the functions below need the CRT; the uos re-exports stay usable.
    _ucrtbase = None

if _ucrtbase:
    # intptr_t _wspawnv(int mode, const wchar_t *cmdname, const wchar_t *const *argv);
    # Return "p", not "l": long is 32-bit on Windows (LLP64) and would
    # truncate the intptr_t handle.
    _wspawnv = _ucrtbase.func("p", "_wspawnv", "ipp")
    # intptr_t _cwait(int *termstat, intptr_t procHandle, int action);
    # `action` is ignored on Win32; the call closes the handle it waits on.
    _cwait = _ucrtbase.func("p", "_cwait", "ppi")
    # errno_t _get_errno(int *pValue). Not the built-in os.errno(): that
    # reads the errno of MicroPython's own CRT (msvcrt.dll on MinGW), a
    # different location to ucrtbase's.
    _get_errno = _ucrtbase.func("i", "_get_errno", "p")
    # int _waccess(const wchar_t *path, int mode);
    _waccess = _ucrtbase.func("i", "_waccess", "pi")
    # int _getpid(void);
    _getpid = _ucrtbase.func("i", "_getpid", "")
    # int _pipe(int *pfds, unsigned int psize, int textmode);
    _pipe = _ucrtbase.func("i", "_pipe", "pIi")
    # int _dup(int fd);
    _dup = _ucrtbase.func("i", "_dup", "i")
    # int _close(int fd);
    _close = _ucrtbase.func("i", "_close", "i")
    # intptr_t _get_osfhandle(int fd);
    _get_osfhandle = _ucrtbase.func("p", "_get_osfhandle", "i")
    # int _read(int fd, void *buffer, unsigned int count);
    _read = _ucrtbase.func("i", "_read", "ipI")
    # int _write(int fd, const void *buffer, unsigned int count);
    _write = _ucrtbase.func("i", "_write", "ipI")

try:
    _shell32 = ffi.open("shell32.dll")
except OSError:
    _shell32 = None

if _shell32:
    # HINSTANCE ShellExecuteW(HWND hwnd, LPCWSTR lpOperation, LPCWSTR lpFile,
    #   LPCWSTR lpParameters, LPCWSTR lpDirectory, INT nShowCmd);
    # Returns a handle-sized value that is not a real HINSTANCE; only its
    # numeric value matters (<=32 is an error).
    _ShellExecuteW = _shell32.func("p", "ShellExecuteW", "pppppi")

try:
    _kernel32 = ffi.open("kernel32.dll")
except OSError:
    _kernel32 = None

if _kernel32:
    # BOOL SetHandleInformation(HANDLE hObject, DWORD dwMask, DWORD dwFlags);
    _SetHandleInformation = _kernel32.func("i", "SetHandleInformation", "pII")

# CPython os.P_* values.
P_WAIT = 0
P_NOWAIT = 1
P_OVERLAY = 2
P_NOWAITO = 3
P_DETACH = 4

# CPython os.*_OK values. Windows has no executable bit, so access() treats
# X_OK as F_OK, as CPython does there.
F_OK = 0
R_OK = 4
W_OK = 2
X_OK = 1

_SW_SHOWNORMAL = 1
_O_BINARY = 0x8000
_HANDLE_FLAG_INHERIT = 0x1

_errno_buf = array.array("i", [0])


def get_errno():
    _get_errno(uctypes.addressof(_errno_buf))
    return _errno_buf[0]


def check_error(ret):
    # Every CRT call bound above returns -1 and sets errno on failure.
    if ret == -1:
        raise OSError(get_errno())
    return ret


def _argv(args):
    if not args:
        raise ValueError("spawnv() arg 2 must not be empty")
    if not args[0]:
        raise ValueError("spawnv() arg 2 first element cannot be empty")
    _bufs = [wstr(a) for a in args]  # must outlive the spawn call below
    ptrs = array.array("P", [uctypes.addressof(b) for b in _bufs] + [0])
    return _bufs, ptrs


def spawnv(mode, path, args):
    # P_WAIT returns the child's exit code; the other modes return a live
    # process handle, which must be passed to waitpid() to be closed.
    _bufs, argv = _argv(args)
    path_buf = wstr(path)
    r = _wspawnv(mode, path_buf, argv)
    return check_error(r)


def spawnl(mode, path, *args):
    return spawnv(mode, path, args)


def waitpid(pid, options=0):
    # `pid` is the handle a non-blocking spawn returned, not a real pid, as
    # on CPython's Windows build. _cwait always blocks, so there's no
    # WNOHANG to honour.
    if options:
        raise ValueError("waitpid() options must be 0")
    status = array.array("i", [0])
    r = check_error(_cwait(uctypes.addressof(status), pid, 0))
    # CPython puts the exit code in the POSIX wait-status high byte.
    return r, status[0] << 8


def access(path, mode):
    return _waccess(wstr(path), mode & (R_OK | W_OK)) == 0


def getpid():
    return _getpid()


def startfile(path, operation="open"):
    r = _ShellExecuteW(0, wstr(operation), wstr(path), 0, 0, _SW_SHOWNORMAL)
    if r <= 32:
        raise OSError(r)


def pipe():
    fds = array.array("i", [0, 0])
    check_error(_pipe(uctypes.addressof(fds), 4096, _O_BINARY))
    return fds[0], fds[1]


def dup(fd):
    return check_error(_dup(fd))


def close(fd):
    return check_error(_close(fd))


def read(fd, n):
    buf = bytearray(n)
    r = check_error(_read(fd, buf, n))
    return bytes(buf[:r])


def write(fd, buf):
    return check_error(_write(fd, buf, len(buf)))


class _PopenFile:
    # A Windows CRT fd belongs to the CRT DLL that created it (ucrtbase
    # here), so builtins.open(), backed by MicroPython's own CRT, can't be
    # handed one. Every operation stays on the ucrtbase calls above.
    def __init__(self, fd, mode, pid):
        self._fd = fd
        self._binary = "b" in mode
        self._pid = pid

    def read(self, n=-1):
        if n is not None and n >= 0:
            data = read(self._fd, n)
        else:
            chunks = []
            while True:
                chunk = read(self._fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        return data if self._binary else data.decode()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode()
        return write(self._fd, data)

    def close(self):
        if self._pid is None:
            return None
        # Our end first: a "w" child won't see EOF, so won't exit, while we
        # still hold the write end.
        close(self._fd)
        pid, self._pid = self._pid, None
        code = waitpid(pid, 0)[1] >> 8  # undo waitpid()'s POSIX shift
        # Matches CPython: None if the child exited cleanly, else its code.
        return code if code else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def popen(cmd, mode="r"):
    # No fork() to redirect inside, so rewire *this* process's std handle,
    # spawn, then restore it: CreateProcess duplicates inheritable handles
    # into the child before it returns.
    i, o = pipe()
    if mode[0] == "w":
        i, o = o, i
    std_fd = 1 if mode[0] == "r" else 0

    # Windows inherits every inheritable handle open at spawn time, not just
    # the one at std_fd, and pipe() fds are inheritable by default. Without
    # this the child holds our end open too and nobody ever sees EOF.
    _SetHandleInformation(_get_osfhandle(i), _HANDLE_FLAG_INHERIT, 0)

    comspec = getenv("ComSpec") or (getenv("SystemRoot") + "\\System32\\cmd.exe")
    saved = dup(std_fd)
    close(std_fd)
    dup(o)
    close(o)
    pid = spawnl(P_NOWAIT, comspec, comspec, "/c", cmd)
    close(std_fd)
    dup(saved)
    close(saved)

    return _PopenFile(i, mode, pid)

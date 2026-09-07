## Windows-specific packages

These are packages that will only run on the Windows port of MicroPython.

**Note:** These packages require a `ports/windows` build with `MICROPY_PY_FFI`
enabled. Upstream MicroPython does not enable `ffi` (libffi-based DLL calls)
for `ports/windows` by default; it is currently only enabled for the Unix
port. A `ports/windows` build must add its own `LoadLibraryW`/`GetProcAddress`
backed `ffi` implementation (and link libffi) before these packages are
usable; without that, importing them raises `ImportError`.

### Background

The packages in this directory provide additional CPython compatibility using
the host operating system's native libraries, by calling raw Win32 and CRT
DLL exports via `ffi`.

### Usage

To use a windows-specific library, a manifest file must add the `windows-ffi`
library to the library search path using `add_library()`:

```py
add_library("windows-ffi", "$(MPY_LIB_DIR)/windows-ffi", prepend=True)
```

Prepending the `windows-ffi` library to the path will make it so that the
`windows-ffi` version of a package will be preferred if that package appears
in both `windows-ffi` and another library (eg `python-stdlib`). Note that
`windows-ffi/os` fully replaces the `os` package (like `unix-ffi/os` does for
`python-stdlib/os`) rather than layering on top of it; install one or the
other, not both.

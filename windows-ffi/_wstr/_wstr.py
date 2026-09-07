"""UTF-16LE pack/unpack for the Win32 wide-string calls in windows-ffi.

MicroPython's str.encode()/bytes.decode() ignore their encoding argument and
always use UTF-8 (str_encode() in py/objstr.c), so asking for "utf-16-le"
silently returns the wrong bytes rather than raising. Every wide-string
buffer here is built and read directly instead.
"""


def wstr(s):
    """Encode `s` as a NUL-terminated UTF-16LE bytearray.

    Non-BMP characters raise: truncating one to 16 bits would silently give
    a different, well-formed string, such as a wrong path.
    """
    out = bytearray(2 * len(s) + 2)
    for i, ch in enumerate(s):
        code = ord(ch)
        if code > 0xFFFF:
            raise ValueError("wstr() does not support non-BMP character {!r}".format(ch))
        out[2 * i] = code & 0xFF
        out[2 * i + 1] = (code >> 8) & 0xFF
    return out


def from_wstr_bytes(buf, nbytes, strip_nul=True):
    """Decode a byte-counted UTF-16LE run starting at buf[0].

    Strips one trailing NUL code unit if present: registry string values are
    not guaranteed to be stored NUL-terminated.
    """
    nchars = nbytes // 2
    if strip_nul and nchars and buf[2 * (nchars - 1)] == 0 and buf[2 * (nchars - 1) + 1] == 0:
        nchars -= 1
    return "".join(chr(buf[2 * i] | (buf[2 * i + 1] << 8)) for i in range(nchars))


def wstr_multi(strings):
    """Encode a list of str as a REG_MULTI_SZ blob."""
    out = bytearray()
    for s in strings:
        out += wstr(s)  # each entry NUL-terminated by wstr() itself
    out += b"\x00\x00"  # extra NUL terminates the whole list
    return out


def from_wstr_multi(buf, nbytes):
    """Decode a REG_MULTI_SZ blob back into a list of str."""
    nchars = nbytes // 2
    codes = [buf[2 * i] | (buf[2 * i + 1] << 8) for i in range(nchars)]
    strings = []
    cur = []
    for code in codes:
        if code == 0:
            if not cur:
                break
            strings.append("".join(chr(c) for c in cur))
            cur = []
        else:
            cur.append(code)
    return strings

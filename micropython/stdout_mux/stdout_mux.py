import os
import io
import uasyncio as asyncio
from micropython import ringbuffer

_DEFAULT_BUFFER_SIZE = const(256)
DEFAULT_BUFFER_TIMEOUT_MS = const(500)


class StdOutMux(io.IOBase):
    def __init__(self) -> None:
        super().__init__()
        self._dupterm = []
        self._packet_buf = io.BytesIO(258)
        self._channels = [
            asyncio.StreamReader(
                ringbuffer(_DEFAULT_BUFFER_SIZE, DEFAULT_BUFFER_TIMEOUT_MS)
            )
        ]

    async def new_channel(self, buff_size=None, timeout=None, meta={}):
        buff_size = buff_size or _DEFAULT_BUFFER_SIZE
        timeout = timeout if timeout is not None else DEFAULT_BUFFER_TIMEOUT_MS
        rb_in = ringbuffer(buff_size, timeout)
        # rb_out = ringbuffer(buff_size, timeout)
        srb_in = asyncio.StreamWriter(rb_in)
        # srb_out = asyncio.StreamReader(rb_out)

        self._channels.append(srb_in)
        new_channel = len(self._channels)
        return (
            new_channel,
            asyncio.StreamReader(rb_in, meta),
            MuxStreamWriter(self, new_channel),
        )

    async def enable(self):
        # Intercept all existing dupterm endpoints
        for slot in range(1, 10):
            try:
                existing = os.dupterm(None, slot)
                self._dupterm.append(existing)
                if existing:
                    se = asyncio.StreamReader(existing)
                    asyncio.create_task(self._read_task(se))

            except ValueError:
                break

        # Register this as the sole dupterm handler
        os.dupterm(self, 0)

    async def _read_task(self, stream):
        partial = None
        while True:
            data = await stream.read()
            if data:
                chunks = data.split(b'\x00')
                for i, chunk in enumerate(chunks):
                    if partial:
                        chunk = partial + chunk
                        partial = None
                    mchunk = memoryview(chunk)
                    channel = chunk[0]
                    length = chunk[1]
                    clen = len(chunk)
                    if length == clen-2:
                        self._packet_buf.seek(0)
                        cl = cobs_decode(mchunk[2:], self._packet_buf)
                        self._channels[channel].write(self._cobs_buf[:cl])
                    else:
                        if i == len(chunks) - 1:
                            # final, incomplete
                            partial = chunk
                        else:
                            self.faults.append(chunk)

    def _write_channel(self, channel, buf):
        l = len(buf)
        while l > 0:
            c = min(l, 255)
            self._write_buf[0] = 0
            self._write_buf[1] = channel
            self._write_buf[2] = c
            cobs_encode(buf, self._cobs_buf)
            for sout in self._dupterm:
                sout.write(self._write_buf)
            l -= c

    def write(self, buf):
        return self._write_channel(0, buf)

    def readinto(self, buf):
        read = 0
        for slot in self._dupterm:
            if slot is None:
                continue
            if slot.any():
                read = slot.readinto(buf)
                break
        return read

class MuxStreamWriter:
    def __init__(self, mux: StdOutMux, channel: int):
        self.mux = mux
        self.channel = channel

    def write(self, buf):
        self.mux._write_channel(self.channel, buf)


# Consistent Overhead Byte Stuffing (COBS)

class DecodeError(Exception):
    pass


def _get_buffer_view(in_bytes):
    mv = memoryview(in_bytes)
    if mv.ndim > 1 or mv.itemsize > 1:
        raise BufferError('object must be a single-dimension buffer of bytes.')
    try:
        mv = mv.cast('c')
    except AttributeError:
        pass
    return mv


def cobs_encode(in_bytes, out_bytes):
    """Encode a string using Consistent Overhead Byte Stuffing (COBS).

    Input is any byte string. Output is also a byte string.

    Encoding guarantees no zero bytes in the output. The output
    string will be expanded slightly, by a predictable amount.

    An empty string is encoded to '\\x01'"""
    if isinstance(in_bytes, str):
        raise TypeError('Unicode-objects must be encoded as bytes first')
    in_bytes_mv = _get_buffer_view(in_bytes)
    final_zero = True
    oidx = 0
    idx = 0
    search_start_idx = 0
    for in_char in in_bytes_mv:
        if in_char == b'\x00':
            final_zero = True
            slen = 1 + idx - search_start_idx
            out_bytes[oidx] = idx - search_start_idx + 1
            out_bytes[oidx + 1: oidx + slen] = in_bytes_mv[search_start_idx:idx]
            search_start_idx = idx + 1
            oidx += slen
        else:
            if idx - search_start_idx == 0xFD:
                final_zero = False
                slen = 2 + idx - search_start_idx
                out_bytes[oidx] = 0xFF
                out_bytes[oidx + 1: oidx + slen] += in_bytes_mv[search_start_idx:idx+1]
                search_start_idx = idx + 1
                oidx += slen
        idx += 1
    if idx != search_start_idx or final_zero:
        slen = 1 + idx - search_start_idx
        out_bytes[oidx] = idx - search_start_idx + 1
        out_bytes[oidx + 1: oidx + slen] = in_bytes_mv[search_start_idx:idx]
        oidx += slen
    return out_bytes


def cobs_decode(in_bytes, out_bytes):
    """Decode a string using Consistent Overhead Byte Stuffing (COBS).

    Input should be a byte string that has been COBS encoded. Output
    is also a byte string.

    A cobs.DecodeError exception will be raised if the encoded data
    is invalid."""
    if isinstance(in_bytes, str):
        raise TypeError('Unicode-objects are not supported; byte buffer objects only')
    in_bytes_mv = _get_buffer_view(in_bytes)
    idx = 0

    if len(in_bytes_mv) > 0:
        while True:
            length = ord(in_bytes_mv[idx])
            if length == 0:
                raise DecodeError("zero byte found in input")
            idx += 1
            end = idx + length - 1
            copy_mv = in_bytes_mv[idx:end]
            if b'\x00' in copy_mv:
                raise DecodeError("zero byte found in input")
            out_bytes += copy_mv
            idx = end
            if idx > len(in_bytes_mv):
                raise DecodeError("not enough input bytes for length code")
            if idx < len(in_bytes_mv):
                if length < 0xFF:
                    out_bytes.append(0)
            else:
                break
    return bytes(out_bytes)

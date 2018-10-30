import os
import ubinascii


# int is an arg name in cpython UUID, as such we need to save off a reference
# to the ral int here beforehand for use in __init__
_int = int


class UUID:
    def __init__(self, hex=None, bytes=None, bytes_le=None, fields=None, int=None):
        if hex:
            b = ubinascii.unhexlify(hex.strip('{}').replace('-', ''))
            if len(b) != 16:
                raise ValueError('string args must be in long uuid format')
            self._bytes = b

        elif bytes:
            if len(bytes) != 16:
                raise ValueError('bytes arg must be 16 bytes long')
            self._bytes = bytes

        elif bytes_le:
            if len(bytes_le) != 16:
                raise ValueError('bytes_le must be 16 bytes')
            self._bytes = b''.join([
                _int.from_bytes(bytes_le[:4], 'little').to_bytes(4, 'big'),
                _int.from_bytes(bytes_le[4:6], 'little').to_bytes(2, 'big'),
                _int.from_bytes(bytes_le[6:8], 'little').to_bytes(2, 'big'),
                bytes_le[8:]]
            )

        elif fields:
            if len(fields) != 6:
                raise ValueError('fields must be the six integer fields of the UUID')
            self._bytes = b''.join((
                fields[0].to_bytes(4, 'big'),
                fields[1].to_bytes(2, 'big'),
                fields[2].to_bytes(2, 'big'),
                fields[3].to_bytes(1, 'big'),
                fields[4].to_bytes(1, 'big'),
                fields[5].to_bytes(6, 'big'),
            ))

        elif int:
            self._bytes = int.to_bytes(16, 'big')

        else:
            raise ValueError("No valid argument passed")

    @property
    def hex(self):
        return ubinascii.hexlify(self._bytes).decode()

    def __str__(self):
        h = self.hex
        return '-'.join((h[0:8], h[8:12], h[12:16], h[16:20], h[20:32]))

    def __repr__(self):
        return "<UUID: %s>" % str(self)

    def __bytes__(self):
        return self._bytes

    def __int__(self):
        return int.from_bytes(self._bytes, 'big')

    def __hash__(self):
        return hash(self._bytes)

    def __eq__(self, other):
        if isinstance(other, UUID):
            return other.__bytes__() == self._bytes


def uuid4():
    """ Generates a random UUID compliant to RFC 4122 pg.14 """
    random = bytearray(os.urandom(16))
    random[6] = (random[6] & 0x0F) | 0x40
    random[8] = (random[8] & 0x3F) | 0x80
    return UUID(bytes=random)

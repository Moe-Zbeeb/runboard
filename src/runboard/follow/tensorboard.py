import os
import struct
import sys
from pathlib import Path

from ..storage import clean_value
from . import Record

MAX_RECORD = 1024 * 1024
MAX_RECORDS_PER_POLL = 100000
DT_FLOAT = 1
DT_DOUBLE = 2

_TABLE = []
for _index in range(256):
    _crc = _index
    for _ in range(8):
        _crc = (_crc >> 1) ^ 0x82F63B78 if _crc & 1 else _crc >> 1
    _TABLE.append(_crc)


def crc32c(data):
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def masked_crc32c(data):
    crc = crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _varint(buf, pos):
    result = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _fields(buf):
    pos = 0
    while pos < len(buf):
        key, pos = _varint(buf, pos)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos : pos + 8], pos + 8
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value, pos = buf[pos : pos + size], pos + size
        elif wire == 5:
            value, pos = buf[pos : pos + 4], pos + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        if pos > len(buf):
            raise ValueError("truncated protobuf field")
        yield number, wire, value


def _tensor_scalar(buf):
    dtype, content, values = DT_FLOAT, b"", []
    for number, wire, value in _fields(buf):
        if number == 1 and wire == 0:
            dtype = value
        elif number == 4 and wire == 2:
            content = value
        elif number == 5 and wire in (2, 5):
            values.extend(item for (item,) in struct.iter_unpack("<f", value))
        elif number == 6 and wire in (1, 2):
            values.extend(item for (item,) in struct.iter_unpack("<d", value))
    if dtype not in (DT_FLOAT, DT_DOUBLE):
        return None
    if content:
        fmt = "<f" if dtype == DT_FLOAT else "<d"
        if len(content) != struct.calcsize(fmt):
            return None
        values = [struct.unpack(fmt, content)[0]]
    return values[0] if len(values) == 1 else None


def _scalars(summary):
    out = {}
    for number, wire, value in _fields(summary):
        if number != 1 or wire != 2:
            continue
        tag = scalar = None
        for inner, inner_wire, inner_value in _fields(value):
            if inner == 1 and inner_wire == 2:
                tag = inner_value.decode("utf-8", "replace")
            elif inner == 2 and inner_wire == 5:
                scalar = struct.unpack("<f", inner_value)[0]
            elif inner == 8 and inner_wire == 2:
                scalar = _tensor_scalar(inner_value)
        if tag is not None and scalar is not None:
            cleaned = clean_value(scalar)
            if cleaned is not ...:
                out[tag] = cleaned
    return out


def parse_event(payload):
    wall, step = None, 0
    metrics = {}
    for number, wire, value in _fields(payload):
        if number == 1 and wire == 1:
            wall = struct.unpack("<d", value)[0]
        elif number == 2 and wire == 0:
            step = value - (1 << 64) if value >= 1 << 63 else value
        elif number == 5 and wire == 2:
            metrics.update(_scalars(value))
    return Record(step, wall, metrics) if metrics else None


class TensorBoardFollower:
    kind = "tensorboard"

    def __init__(self, path, state=None):
        self.path = Path(path)
        self.offset = int((state or {}).get("offset", 0))
        self.broken = False
        self.warned = False

    def state(self):
        return {"offset": self.offset}

    def _warn(self, message):
        if not self.warned:
            print(f"[runboard] {self.path}: {message}", file=sys.stderr, flush=True)
            self.warned = True

    def poll(self):
        if self.broken:
            return []
        records = []
        try:
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if size < self.offset:
                    self.offset = 0
                pos = self.offset
                for _ in range(MAX_RECORDS_PER_POLL):
                    if pos + 12 > size:
                        break
                    f.seek(pos)
                    head = f.read(12)
                    if len(head) < 12:
                        break
                    length, length_crc = struct.unpack("<QI", head)
                    if masked_crc32c(head[:8]) != length_crc:
                        self._warn("corrupt record framing; stopped reading this file")
                        self.broken = True
                        break
                    end = pos + 12 + length + 4
                    if end > size:
                        break
                    if length > MAX_RECORD:
                        pos = end
                        continue
                    payload = f.read(length)
                    tail = f.read(4)
                    pos = end
                    if len(tail) < 4 or masked_crc32c(payload) != struct.unpack("<I", tail)[0]:
                        self._warn("skipped a record with a bad checksum")
                        continue
                    try:
                        record = parse_event(payload)
                    except (ValueError, IndexError, struct.error):
                        continue
                    if record:
                        records.append(record)
                self.offset = pos
        except OSError:
            return records
        return records

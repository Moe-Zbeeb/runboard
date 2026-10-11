import struct
import time
import urllib.request

from runboard.follow.tensorboard import masked_crc32c


def http_get(srv, path, token="secret"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read()


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def _varint(n):
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | 0x80 if n else byte)
        if not n:
            return bytes(out)


def _field(number, wire, payload):
    key = _varint(number << 3 | wire)
    return key + _varint(len(payload)) + payload if wire == 2 else key + payload


def tf_version_event():
    return _field(1, 1, struct.pack("<d", 0.0)) + _field(3, 2, b"brain.Event:2")


def tf_event(step, wall_time, scalars=(), tensors=()):
    values = b"".join(_field(1, 2, _field(1, 2, tag.encode()) + _field(2, 5, struct.pack("<f", value))) for tag, value in scalars)
    values += b"".join(
        _field(1, 2, _field(1, 2, tag.encode()) + _field(8, 2, _field(1, 0, _varint(2)) + _field(6, 2, struct.pack("<d", value))))
        for tag, value in tensors
    )
    body = _field(1, 1, struct.pack("<d", wall_time)) + (_field(2, 0, _varint(step)) if step else b"")
    return body + _field(5, 2, values) if values else body


def tf_record(payload):
    head = struct.pack("<Q", len(payload))
    return head + struct.pack("<I", masked_crc32c(head)) + payload + struct.pack("<I", masked_crc32c(payload))

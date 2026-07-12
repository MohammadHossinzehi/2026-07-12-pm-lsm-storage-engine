"""Write-Ahead Log (WAL).

Every mutation is appended here, fsync'd, and only then applied to the
in-memory memtable. If the process crashes before the memtable is flushed
to an SSTable, restarting the engine replays the WAL to reconstruct the
memtable exactly as it was. This is what makes an in-memory-first design
durable without giving up the write speed of an append-only log.

Record format (all integers little-endian):
    op        : 1 byte    (1 = PUT, 2 = DELETE)
    key_len   : 4 bytes
    key       : key_len bytes (utf-8)
    value_len : 4 bytes   (0 for DELETE)
    value     : value_len bytes (utf-8)
    crc       : 4 bytes   (zlib.crc32 over everything above, for torn-write
                           detection at the tail of the file)
"""

from __future__ import annotations

import os
import struct
import zlib
from typing import Iterator, Optional, Tuple

OP_PUT = 1
OP_DELETE = 2


class WAL:
    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "ab", buffering=0)

    def _write_record(self, op: int, key: str, value: Optional[str]) -> None:
        kb = key.encode("utf-8")
        vb = value.encode("utf-8") if value is not None else b""
        body = struct.pack("<BI", op, len(kb)) + kb + struct.pack("<I", len(vb)) + vb
        crc = zlib.crc32(body) & 0xFFFFFFFF
        record = body + struct.pack("<I", crc)
        self._fh.write(record)
        os.fsync(self._fh.fileno())

    def log_put(self, key: str, value: str) -> None:
        self._write_record(OP_PUT, key, value)

    def log_delete(self, key: str) -> None:
        self._write_record(OP_DELETE, key, None)

    def close(self) -> None:
        self._fh.close()

    def truncate(self) -> None:
        """Called after a successful memtable flush: everything in the WAL
        is now durable inside an SSTable, so the log can start fresh."""
        self._fh.close()
        self._fh = open(self.path, "wb", buffering=0)
        self._fh.close()
        self._fh = open(self.path, "ab", buffering=0)

    @staticmethod
    def replay(path: str) -> Iterator[Tuple[int, str, Optional[str]]]:
        """Yield (op, key, value) tuples in log order. Stops cleanly (rather
        than raising) at a truncated/corrupt tail record, which is the
        expected shape of a crash that happened mid-write."""
        if not os.path.exists(path):
            return
        with open(path, "rb") as f:
            data = f.read()
        offset = 0
        n = len(data)
        while offset < n:
            if offset + 5 > n:
                break
            op, key_len = struct.unpack_from("<BI", data, offset)
            pos = offset + 5
            if pos + key_len + 4 > n:
                break
            key = data[pos:pos + key_len].decode("utf-8")
            pos += key_len
            (value_len,) = struct.unpack_from("<I", data, pos)
            pos += 4
            if pos + value_len + 4 > n:
                break
            value = data[pos:pos + value_len].decode("utf-8") if value_len else None
            pos += value_len
            (stored_crc,) = struct.unpack_from("<I", data, pos)
            pos += 4
            body = data[offset:pos - 4]
            if (zlib.crc32(body) & 0xFFFFFFFF) != stored_crc:
                break
            yield op, key, value
            offset = pos

"""SSTable (Sorted String Table): the immutable on-disk file format an LSM
tree flushes its memtable into.

File layout (all integers little-endian, written in this order):

    [ data block ]     sequence of entries, each:
                           flag(1B: 0=value, 1=tombstone)
                           key_len(4B) key_bytes
                           value_len(4B) value_bytes   (0 bytes if tombstone)
    [ sparse index ]   every SPARSE_STRIDE-th key: key_len(4B) key_bytes
                       offset(8B) -- lets a lookup jump close to a key
                       without loading the whole table into memory
    [ bloom filter ]   serialized BloomFilter bytes, used to skip this whole
                       file when a key is definitely absent
    [ footer, 32B ]    index_offset(8B) index_len(8B)
                       bloom_offset(8B) bloom_len(8B)

Keeping the index and bloom filter small and separate from the data block
is what lets a table stay mostly on disk: opening a table only reads the
footer + index + bloom filter into memory, not the (potentially huge) data
block itself.
"""

from __future__ import annotations

import struct
import os
from typing import Iterator, List, Optional, Tuple

from .bloom import BloomFilter

FOOTER_FORMAT = "<QQQQ"
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)
SPARSE_STRIDE = 16  # index every 16th key
TOMBSTONE = object()  # sentinel meaning "this key was deleted"


def _encode_entry(key: str, value: Optional[str]) -> bytes:
    kb = key.encode("utf-8")
    if value is TOMBSTONE or value is None:
        return struct.pack("<BI", 1, len(kb)) + kb + struct.pack("<I", 0)
    vb = value.encode("utf-8")
    return struct.pack("<BI", 0, len(kb)) + kb + struct.pack("<I", len(vb)) + vb


def write_sstable(path: str, sorted_items: List[Tuple[str, Optional[str]]]) -> None:
    """sorted_items must already be sorted by key ascending, one entry per
    key (the caller is responsible for de-duplication / picking the newest
    version -- see engine.py flush() and compaction())."""
    index_entries: List[Tuple[str, int]] = []
    bloom = BloomFilter(expected_items=max(1, len(sorted_items)))

    with open(path, "wb") as f:
        offset = 0
        for i, (key, value) in enumerate(sorted_items):
            if i % SPARSE_STRIDE == 0:
                index_entries.append((key, offset))
            bloom.add(key)
            chunk = _encode_entry(key, value)
            f.write(chunk)
            offset += len(chunk)

        index_start = offset
        index_bytes = bytearray()
        for key, off in index_entries:
            kb = key.encode("utf-8")
            index_bytes += struct.pack("<I", len(kb)) + kb + struct.pack("<Q", off)
        f.write(index_bytes)

        bloom_start = index_start + len(index_bytes)
        bloom_bytes = bloom.to_bytes()
        f.write(bloom_bytes)

        footer = struct.pack(
            FOOTER_FORMAT, index_start, len(index_bytes), bloom_start, len(bloom_bytes)
        )
        f.write(footer)


class SSTableReader:
    def __init__(self, path: str):
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(self.size - FOOTER_SIZE)
            footer = f.read(FOOTER_SIZE)
            self.index_offset, self.index_len, self.bloom_offset, self.bloom_len = (
                struct.unpack(FOOTER_FORMAT, footer)
            )
            f.seek(self.index_offset)
            index_bytes = f.read(self.index_len)
            f.seek(self.bloom_offset)
            bloom_bytes = f.read(self.bloom_len)

        self.bloom = BloomFilter.from_bytes(bloom_bytes)
        self.index: List[Tuple[str, int]] = []
        pos = 0
        while pos < len(index_bytes):
            (klen,) = struct.unpack_from("<I", index_bytes, pos)
            pos += 4
            key = index_bytes[pos:pos + klen].decode("utf-8")
            pos += klen
            (off,) = struct.unpack_from("<Q", index_bytes, pos)
            pos += 8
            self.index.append((key, off))

    def _floor_offset(self, key: str) -> int:
        """Largest indexed offset for a key <= target (0 if none)."""
        lo, hi = 0, len(self.index) - 1
        best = 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.index[mid][0] <= key:
                best = self.index[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def get(self, key: str) -> Tuple[bool, Optional[str]]:
        """Returns (found, value). value is None if the key was deleted
        (tombstone) -- callers must distinguish "not found here, keep
        checking older tables" from "found: it was deleted"."""
        if not self.bloom.might_contain(key):
            return False, None

        start = self._floor_offset(key)
        with open(self.path, "rb") as f:
            f.seek(start)
            limit = self.index_offset
            pos = start
            while pos < limit:
                flag, klen = struct.unpack("<BI", f.read(5))
                k = f.read(klen).decode("utf-8")
                (vlen,) = struct.unpack("<I", f.read(4))
                v = f.read(vlen).decode("utf-8") if vlen else None
                consumed = 5 + klen + 4 + vlen
                pos += consumed
                if k == key:
                    return True, (None if flag == 1 else v)
                if k > key:
                    return False, None
        return False, None

    def scan(self, start_key: Optional[str], end_key: Optional[str]) -> Iterator[Tuple[str, Optional[str]]]:
        offset = self._floor_offset(start_key) if start_key is not None else 0
        with open(self.path, "rb") as f:
            f.seek(offset)
            limit = self.index_offset
            pos = offset
            while pos < limit:
                flag, klen = struct.unpack("<BI", f.read(5))
                k = f.read(klen).decode("utf-8")
                (vlen,) = struct.unpack("<I", f.read(4))
                v = f.read(vlen).decode("utf-8") if vlen else None
                pos += 5 + klen + 4 + vlen
                if start_key is not None and k < start_key:
                    continue
                if end_key is not None and k > end_key:
                    return
                yield k, (None if flag == 1 else v)

    def all_items(self) -> Iterator[Tuple[str, Optional[str]]]:
        yield from self.scan(None, None)

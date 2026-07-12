"""A small Bloom filter used by each on-disk SSTable to answer "definitely not
present" for a key without touching disk. This is infrastructure for the
storage engine (SSTables in an LSM-tree skip disk reads via a filter), not
the project itself.
"""

from __future__ import annotations

import hashlib
import math
import struct


class BloomFilter:
    """A classic Bloom filter backed by a bytearray bit set.

    Two independent hashes (via hashlib) are combined with the standard
    double-hashing trick (Kirsch-Mitzenmacher) to simulate k independent
    hash functions cheaply.
    """

    def __init__(self, expected_items: int, false_positive_rate: float = 0.01):
        expected_items = max(1, expected_items)
        self.size = self._optimal_size(expected_items, false_positive_rate)
        self.num_hashes = self._optimal_num_hashes(self.size, expected_items)
        self.bits = bytearray((self.size + 7) // 8)
        self.count = 0

    @staticmethod
    def _optimal_size(n: int, p: float) -> int:
        m = -(n * math.log(p)) / (math.log(2) ** 2)
        return max(8, int(math.ceil(m)))

    @staticmethod
    def _optimal_num_hashes(m: int, n: int) -> int:
        k = (m / n) * math.log(2)
        return max(1, int(round(k)))

    def _hashes(self, key: bytes):
        h1 = int.from_bytes(hashlib.md5(key).digest()[:8], "little")
        h2 = int.from_bytes(hashlib.sha1(key).digest()[:8], "little")
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % self.size

    def add(self, key: str) -> None:
        kb = key.encode("utf-8")
        for bit in self._hashes(kb):
            self.bits[bit // 8] |= 1 << (bit % 8)
        self.count += 1

    def might_contain(self, key: str) -> bool:
        kb = key.encode("utf-8")
        return all(self.bits[bit // 8] & (1 << (bit % 8)) for bit in self._hashes(kb))

    def to_bytes(self) -> bytes:
        header = struct.pack("<QQ", self.size, self.num_hashes)
        return header + bytes(self.bits)

    @classmethod
    def from_bytes(cls, data: bytes) -> "BloomFilter":
        size, num_hashes = struct.unpack("<QQ", data[:16])
        obj = cls.__new__(cls)
        obj.size = size
        obj.num_hashes = num_hashes
        obj.bits = bytearray(data[16:])
        obj.count = 0
        return obj

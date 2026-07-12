"""A from-scratch probabilistic skip list.

This is the data structure backing the memtable: it keeps keys in sorted
order with O(log n) expected insert/search/delete, which a plain dict
cannot do (a dict has no cheap ordered iteration, which the engine needs
for range scans and for building sorted SSTable files on flush).
"""

from __future__ import annotations

import random
from typing import Any, Iterator, Optional, Tuple

MAX_LEVEL = 16
P = 0.5


class _Node:
    __slots__ = ("key", "value", "forward")

    def __init__(self, key: Optional[str], value: Any, level: int):
        self.key = key
        self.value = value
        self.forward = [None] * (level + 1)


class SkipList:
    """Sorted map from str key -> arbitrary value, supporting delete."""

    def __init__(self):
        self.head = _Node(None, None, MAX_LEVEL)
        self.level = 0
        self.size = 0

    def _random_level(self) -> int:
        lvl = 0
        while random.random() < P and lvl < MAX_LEVEL:
            lvl += 1
        return lvl

    def insert(self, key: str, value: Any) -> None:
        update = [None] * (MAX_LEVEL + 1)
        node = self.head
        for i in range(self.level, -1, -1):
            while node.forward[i] is not None and node.forward[i].key < key:
                node = node.forward[i]
            update[i] = node
        node = node.forward[0]

        if node is not None and node.key == key:
            node.value = value
            return

        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level + 1, new_level + 1):
                update[i] = self.head
            self.level = new_level

        new_node = _Node(key, value, new_level)
        for i in range(new_level + 1):
            new_node.forward[i] = update[i].forward[i]
            update[i].forward[i] = new_node
        self.size += 1

    def get(self, key: str) -> Tuple[bool, Any]:
        node = self.head
        for i in range(self.level, -1, -1):
            while node.forward[i] is not None and node.forward[i].key < key:
                node = node.forward[i]
        node = node.forward[0]
        if node is not None and node.key == key:
            return True, node.value
        return False, None

    def delete(self, key: str) -> bool:
        update = [None] * (MAX_LEVEL + 1)
        node = self.head
        for i in range(self.level, -1, -1):
            while node.forward[i] is not None and node.forward[i].key < key:
                node = node.forward[i]
            update[i] = node
        node = node.forward[0]
        if node is None or node.key != key:
            return False
        for i in range(self.level + 1):
            if update[i].forward[i] is not node:
                continue
            update[i].forward[i] = node.forward[i]
        while self.level > 0 and self.head.forward[self.level] is None:
            self.level -= 1
        self.size -= 1
        return True

    def __len__(self) -> int:
        return self.size

    def items(self) -> Iterator[Tuple[str, Any]]:
        """In-order (key, value) iteration -- required for range scans and
        for writing sorted SSTable segments during flush."""
        node = self.head.forward[0]
        while node is not None:
            yield node.key, node.value
            node = node.forward[0]

    def items_range(self, start: Optional[str], end: Optional[str]) -> Iterator[Tuple[str, Any]]:
        if start is None:
            node = self.head.forward[0]
        else:
            node = self.head
            for i in range(self.level, -1, -1):
                while node.forward[i] is not None and node.forward[i].key < start:
                    node = node.forward[i]
            node = node.forward[0]
        while node is not None and (end is None or node.key <= end):
            yield node.key, node.value
            node = node.forward[0]

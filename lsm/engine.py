"""LSMTree: the public storage engine API tying together the WAL, the
in-memory memtable (skip list), and on-disk SSTables with compaction.

Write path:      put/delete -> WAL (durable) -> memtable (fast, in RAM)
                  memtable grows -> flush() -> new immutable SSTable on disk
Read path:       memtable (newest) -> SSTables newest-to-oldest, each one
                  first checked against its Bloom filter to skip disk reads
                  for keys it definitely doesn't hold
Recovery:        on startup, replay the WAL into a fresh memtable so data
                  written since the last flush isn't lost on crash/restart

This mirrors the design real engines (LevelDB, RocksDB, Cassandra's storage
layer) use, simplified to be readable in one sitting.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Dict, Iterator, List, Optional, Tuple

from .skiplist import SkipList
from .sstable import SSTableReader, TOMBSTONE, write_sstable
from .wal import WAL, OP_DELETE, OP_PUT

MANIFEST_NAME = "MANIFEST.json"
WAL_NAME = "wal.log"


class LSMTree:
    def __init__(
        self,
        data_dir: str,
        memtable_max_entries: int = 1000,
        compaction_threshold: int = 4,
    ):
        self.data_dir = data_dir
        self.memtable_max_entries = memtable_max_entries
        self.compaction_threshold = compaction_threshold
        os.makedirs(data_dir, exist_ok=True)

        self._manifest_path = os.path.join(data_dir, MANIFEST_NAME)
        self._wal_path = os.path.join(data_dir, WAL_NAME)

        self.memtable = SkipList()
        self.sstables: List[SSTableReader] = []  # oldest first, newest last
        self._next_gen = 0

        self._load_manifest()
        self.wal = WAL(self._wal_path)
        self._replay_wal()

    # -- persistence bookkeeping -------------------------------------------------

    def _load_manifest(self) -> None:
        if not os.path.exists(self._manifest_path):
            self._save_manifest()
            return
        with open(self._manifest_path, "r") as f:
            manifest = json.load(f)
        self._next_gen = manifest.get("next_gen", 0)
        for name in manifest.get("sstables", []):
            path = os.path.join(self.data_dir, name)
            if os.path.exists(path):
                self.sstables.append(SSTableReader(path))

    def _save_manifest(self) -> None:
        manifest = {
            "next_gen": self._next_gen,
            "sstables": [os.path.basename(t.path) for t in self.sstables],
        }
        tmp = self._manifest_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(manifest, f)
        os.replace(tmp, self._manifest_path)

    def _replay_wal(self) -> None:
        for op, key, value in WAL.replay(self._wal_path):
            if op == OP_PUT:
                self.memtable.insert(key, value)
            elif op == OP_DELETE:
                self.memtable.insert(key, TOMBSTONE)

    # -- write path ---------------------------------------------------------------

    def put(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("keys and values must be str")
        self.wal.log_put(key, value)
        self.memtable.insert(key, value)
        if len(self.memtable) >= self.memtable_max_entries:
            self.flush()

    def delete(self, key: str) -> None:
        self.wal.log_delete(key)
        self.memtable.insert(key, TOMBSTONE)
        if len(self.memtable) >= self.memtable_max_entries:
            self.flush()

    def flush(self) -> Optional[str]:
        """Persist the current memtable to a new immutable SSTable and start
        a fresh, empty memtable. Returns the new file's path, or None if
        the memtable was empty."""
        if len(self.memtable) == 0:
            return None

        items = list(self.memtable.items())
        name = f"sstable_{self._next_gen:06d}.sst"
        self._next_gen += 1
        path = os.path.join(self.data_dir, name)
        write_sstable(path, items)

        self.sstables.append(SSTableReader(path))
        self.memtable = SkipList()
        self.wal.truncate()
        self._save_manifest()

        if len(self.sstables) >= self.compaction_threshold:
            self.compact()
        return path

    # -- read path ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        found, value = self.memtable.get(key)
        if found:
            return None if value is TOMBSTONE else value

        for table in reversed(self.sstables):
            found, value = table.get(key)
            if found:
                return value  # None already means tombstone here
        return None

    def contains(self, key: str) -> bool:
        found, value = self.memtable.get(key)
        if found:
            return value is not TOMBSTONE
        for table in reversed(self.sstables):
            found, value = table.get(key)
            if found:
                return value is not None
        return False

    def range(self, start: Optional[str] = None, end: Optional[str] = None) -> Iterator[Tuple[str, str]]:
        """Merged, de-duplicated, tombstone-filtered ascending scan over
        [start, end] (inclusive), newest version of each key wins."""
        merged: Dict[str, object] = {}

        for table in self.sstables:  # oldest first, so newer overwrite below
            for k, v in table.scan(start, end):
                merged[k] = TOMBSTONE if v is None else v

        for k, v in self.memtable.items_range(start, end):
            merged[k] = v

        for key in sorted(merged.keys()):
            value = merged[key]
            if value is not TOMBSTONE:
                yield key, value

    # -- compaction -----------------------------------------------------------------

    def compact(self) -> Optional[str]:
        """Full compaction: merge every existing SSTable into a single new
        one, newest value per key wins, tombstones are dropped (safe here
        because this merge covers *all* on-disk data -- there is no older
        table left for a tombstone to still be shadowing)."""
        if len(self.sstables) < 2:
            return None

        merged: Dict[str, object] = {}
        for table in self.sstables:  # oldest -> newest, later overwrites
            for k, v in table.all_items():
                merged[k] = TOMBSTONE if v is None else v

        sorted_items = [(k, v) for k, v in sorted(merged.items()) if v is not TOMBSTONE]

        old_paths = [t.path for t in self.sstables]
        name = f"sstable_{self._next_gen:06d}.sst"
        self._next_gen += 1
        new_path = os.path.join(self.data_dir, name)
        write_sstable(new_path, sorted_items)

        self.sstables = [SSTableReader(new_path)]
        for p in old_paths:
            os.remove(p)
        self._save_manifest()
        return new_path

    # -- lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        self.wal.close()

    def stats(self) -> dict:
        return {
            "memtable_entries": len(self.memtable),
            "sstable_count": len(self.sstables),
            "sstable_files": [os.path.basename(t.path) for t in self.sstables],
        }

    @staticmethod
    def destroy(data_dir: str) -> None:
        """Delete an engine's data directory entirely. Used by tests."""
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)

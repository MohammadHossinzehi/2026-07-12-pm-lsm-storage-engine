# lsm-tree-storage-engine

A log-structured merge-tree (LSM-tree) key-value storage engine, built from
scratch in pure Python (standard library only). This is the write path
design used inside real databases like LevelDB, RocksDB, and Cassandra's
storage layer, implemented here at a scale you can read end to end in one
sitting.

## Why this exists

A hash map gives you O(1) get/put but no ordering, no durability, and no
sane story for data bigger than RAM. A B-tree gives you ordering and
durability but pays for it with random disk writes on every update. The
LSM-tree trades a bit of read complexity for something databases care
about a lot: every write is a sequential append, never a random disk seek,
which is what makes engines like RocksDB fast enough to back things like
Kafka's local state stores.

This project implements that trade-off directly:

- **Writes never block on disk seeks.** Every `put`/`delete` is appended to
  a write-ahead log (WAL) and applied to an in-memory sorted structure
  (a skip list). Only once that memtable fills up does it get flushed to
  disk as a single sequential write.
- **Reads degrade gracefully.** A `get` checks the memtable first, then
  walks on-disk SSTables from newest to oldest. Each SSTable carries a
  Bloom filter so the engine can skip a disk read entirely for a table
  that definitely doesn't contain the key, and a sparse index so it never
  has to load a whole table into memory to find one key.
- **Crashes don't lose data.** If the process dies with data sitting only
  in the memtable, the WAL replays on restart and reconstructs it exactly.
- **Old data gets reclaimed.** Compaction merges SSTables together,
  resolves multiple versions of the same key (newest wins), and drops
  tombstones once it's safe to do so.

## What's in the box

```
lsm/
  skiplist.py   in-memory sorted map backing the memtable (from-scratch skip list)
  wal.py        write-ahead log: append, fsync, crash-safe replay
  bloom.py      Bloom filter used per-SSTable to skip unnecessary disk reads
  sstable.py    on-disk sorted string table format: data block + sparse index + bloom filter + footer
  engine.py     LSMTree: put/get/delete/range, flush, compaction, recovery
cli.py          interactive REPL over the engine
tests/          unit + integration tests (pytest)
```

## How to run it

Requires Python 3.8+, no third-party dependencies for the engine itself.

```bash
# interactive REPL
python cli.py ./mydata

lsm> put user:1 alice
OK
lsm> put user:2 bob
OK
lsm> get user:1
alice
lsm> range user: user:9
  user:1 = alice
  user:2 = bob
(2 entries)
lsm> del user:1
OK
lsm> get user:1
(nil)
lsm> flush
flushed to ./mydata/sstable_000000.sst
lsm> compact
nothing to compact
lsm> exit
```

Or use it as a library:

```python
from lsm.engine import LSMTree

db = LSMTree("./mydata", memtable_max_entries=1000)
db.put("k1", "v1")
db.delete("k2")
print(db.get("k1"))          # "v1"
print(list(db.range("k0", "k9")))
db.close()
```

Restarting `LSMTree` against the same directory recovers automatically:
flushed data comes back from SSTables, unflushed data comes back by
replaying the WAL.

### Tests

```bash
pip install pytest
pytest -q
```

30 tests cover the skip list (including a 3000-op randomized workload
checked against a plain dict), the Bloom filter's false-positive behavior,
WAL replay including a simulated torn write at the tail, SSTable read/write
including range scans, and engine-level integration tests: auto-flush at
the memtable threshold, tombstone shadowing across flush boundaries,
compaction correctness, and a 400-op randomized workload that restarts the
engine mid-run (to exercise WAL + SSTable recovery together) and checks
every key and a full range scan against a reference dict at the end.

## Design decisions and trade-offs

**Compaction strategy is intentionally simple.** Real engines use tiered
or leveled compaction that merges a handful of files at a time to bound
write amplification. This engine merges *all* existing SSTables into one
whenever the count crosses `compaction_threshold`. That's the only
strategy where dropping a tombstone is trivially safe (there's no older
data left anywhere that the tombstone could still need to shadow), which
keeps the correctness argument simple at the cost of doing more I/O per
compaction than a production engine would. The trade-off is called out
here rather than hidden, since it's the main place this diverges from a
real system.

**Skip list over a balanced tree for the memtable.** A skip list gives
O(log n) expected insert/search/delete with far less implementation
complexity than a red-black or AVL tree, and — critically for this
use case — trivial in-order iteration, which is what both range queries
and "dump the memtable to a sorted SSTable on flush" need.

**Bloom filter sized per-table, not globally.** Each SSTable gets its own
filter sized from `-n ln(p) / (ln 2)^2`, computed from the number of keys
actually in that table, rather than one filter shared across the whole
engine. This keeps the false-positive rate stable as the engine
accumulates tables of very different sizes over time.

**Values are `str`, not arbitrary bytes.** This keeps the on-disk format
and the CLI simple to read and demo. The framing (length-prefixed records
with a 1-byte tombstone flag) generalizes to arbitrary bytes with no
structural change if that's ever needed.

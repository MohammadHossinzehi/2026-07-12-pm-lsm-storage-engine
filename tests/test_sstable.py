from lsm.sstable import SSTableReader, TOMBSTONE, write_sstable


def test_write_and_get(tmp_path):
    path = str(tmp_path / "table.sst")
    items = [(f"k{i:03d}", f"v{i}") for i in range(100)]
    write_sstable(path, items)

    reader = SSTableReader(path)
    for k, v in items:
        found, value = reader.get(k)
        assert found is True
        assert value == v

    found, _ = reader.get("does-not-exist")
    assert found is False


def test_tombstone_round_trip(tmp_path):
    path = str(tmp_path / "table.sst")
    items = [("a", "1"), ("b", TOMBSTONE), ("c", "3")]
    write_sstable(path, items)

    reader = SSTableReader(path)
    found, value = reader.get("b")
    assert found is True
    assert value is None  # tombstone decodes to None, distinct from "not found"


def test_scan_range(tmp_path):
    path = str(tmp_path / "table.sst")
    items = [(f"k{i:03d}", f"v{i}") for i in range(50)]
    write_sstable(path, items)

    reader = SSTableReader(path)
    got = list(reader.scan("k010", "k015"))
    assert [k for k, _ in got] == [f"k{i:03d}" for i in range(10, 16)]


def test_bloom_filter_skips_absent_keys(tmp_path):
    path = str(tmp_path / "table.sst")
    items = [(f"k{i:03d}", f"v{i}") for i in range(200)]
    write_sstable(path, items)

    reader = SSTableReader(path)
    # A key that was never inserted should either be rejected by the bloom
    # filter or, on the rare false-positive, correctly reported not-found
    # by the linear scan -- either way get() must return False.
    found, _ = reader.get("definitely-absent-key")
    assert found is False

import os

from lsm.wal import WAL, OP_DELETE, OP_PUT


def test_log_and_replay(tmp_path):
    path = str(tmp_path / "wal.log")
    wal = WAL(path)
    wal.log_put("a", "1")
    wal.log_put("b", "2")
    wal.log_delete("a")
    wal.close()

    records = list(WAL.replay(path))
    assert records == [
        (OP_PUT, "a", "1"),
        (OP_PUT, "b", "2"),
        (OP_DELETE, "a", None),
    ]


def test_replay_missing_file_returns_nothing(tmp_path):
    path = str(tmp_path / "does_not_exist.log")
    assert list(WAL.replay(path)) == []


def test_truncate_clears_log(tmp_path):
    path = str(tmp_path / "wal.log")
    wal = WAL(path)
    wal.log_put("a", "1")
    wal.truncate()
    assert list(WAL.replay(path)) == []
    wal.log_put("b", "2")
    wal.close()
    assert list(WAL.replay(path)) == [(OP_PUT, "b", "2")]


def test_replay_stops_cleanly_at_corrupt_tail(tmp_path):
    path = str(tmp_path / "wal.log")
    wal = WAL(path)
    wal.log_put("a", "1")
    wal.log_put("b", "2")
    wal.close()

    # Simulate a crash mid-write: append a few garbage bytes that look like
    # the start of a record but are incomplete/corrupt.
    with open(path, "ab") as f:
        f.write(b"\x01\x05\x00\x00\x00garbage-not-a-full-record")

    records = list(WAL.replay(path))
    assert records == [(OP_PUT, "a", "1"), (OP_PUT, "b", "2")]

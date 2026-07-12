import random

from lsm.skiplist import SkipList


def test_insert_and_get():
    sl = SkipList()
    sl.insert("b", 2)
    sl.insert("a", 1)
    sl.insert("c", 3)
    assert sl.get("a") == (True, 1)
    assert sl.get("b") == (True, 2)
    assert sl.get("c") == (True, 3)
    assert sl.get("z") == (False, None)


def test_overwrite_updates_value_not_size():
    sl = SkipList()
    sl.insert("a", 1)
    sl.insert("a", 2)
    assert len(sl) == 1
    assert sl.get("a") == (True, 2)


def test_delete():
    sl = SkipList()
    sl.insert("a", 1)
    sl.insert("b", 2)
    assert sl.delete("a") is True
    assert sl.get("a") == (False, None)
    assert sl.delete("a") is False
    assert len(sl) == 1


def test_items_are_sorted():
    sl = SkipList()
    keys = [f"key{i:04d}" for i in range(200)]
    shuffled = keys[:]
    random.shuffle(shuffled)
    for k in shuffled:
        sl.insert(k, k.upper())
    assert [k for k, _ in sl.items()] == keys


def test_items_range():
    sl = SkipList()
    for i in range(10):
        sl.insert(f"k{i}", i)
    got = [k for k, _ in sl.items_range("k3", "k6")]
    assert got == ["k3", "k4", "k5", "k6"]

    got_open_start = [k for k, _ in sl.items_range(None, "k2")]
    assert got_open_start == ["k0", "k1", "k2"]

    got_open_end = [k for k, _ in sl.items_range("k8", None)]
    assert got_open_end == ["k8", "k9"]


def test_large_random_workload_matches_dict():
    sl = SkipList()
    reference = {}
    random.seed(42)
    keys = [f"k{i}" for i in range(500)]
    for _ in range(3000):
        k = random.choice(keys)
        if random.random() < 0.2 and k in reference:
            sl.delete(k)
            del reference[k]
        else:
            v = random.randint(0, 1_000_000)
            sl.insert(k, v)
            reference[k] = v

    assert len(sl) == len(reference)
    for k, v in reference.items():
        assert sl.get(k) == (True, v)
    assert [k for k, _ in sl.items()] == sorted(reference.keys())

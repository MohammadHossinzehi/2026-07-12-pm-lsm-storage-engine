from lsm.bloom import BloomFilter


def test_no_false_negatives():
    bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
    keys = [f"key-{i}" for i in range(1000)]
    for k in keys:
        bf.add(k)
    for k in keys:
        assert bf.might_contain(k) is True


def test_false_positive_rate_is_roughly_bounded():
    bf = BloomFilter(expected_items=1000, false_positive_rate=0.01)
    keys = [f"key-{i}" for i in range(1000)]
    for k in keys:
        bf.add(k)

    absent = [f"absent-{i}" for i in range(5000)]
    false_positives = sum(1 for k in absent if bf.might_contain(k))
    rate = false_positives / len(absent)
    # Generous bound: real rate should be near 1%, allow up to 5% for a
    # small/randomized test to stay non-flaky.
    assert rate < 0.05


def test_serialization_round_trip():
    bf = BloomFilter(expected_items=100)
    for i in range(100):
        bf.add(f"item-{i}")
    data = bf.to_bytes()
    restored = BloomFilter.from_bytes(data)
    for i in range(100):
        assert restored.might_contain(f"item-{i}") is True

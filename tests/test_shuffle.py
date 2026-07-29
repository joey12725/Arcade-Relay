#!/usr/bin/env python3
"""Unit tests for the screensaver shuffle policies.

These assert the DISTRIBUTIONAL claims each policy makes, not just that it
returns the right number of ids — an implementation that ignored its policy
entirely would still pass a length check.
"""
import collections
import random
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import shuffle  # noqa: E402


def make(series_sizes, movies=0):
    """series_sizes: {name: episode_count}"""
    entries, si = [], 0
    for name, count in series_sizes.items():
        for i in range(count):
            entries.append({"id": f"{name}-e{i}", "source_id": name,
                            "source_type": "Series", "index": i,
                            "source_index": si})
        si += 1
    for m in range(movies):
        entries.append({"id": f"movie-{m}", "source_id": f"movie-{m}",
                        "source_type": "Movie", "index": 0,
                        "source_index": si})
        si += 1
    return entries


def series_of(vid):
    return vid.rsplit("-e", 1)[0] if "-e" in vid else vid


def test_in_order():
    e = make({"A": 3, "B": 2})
    got = shuffle.order(e, "in_order")
    assert got == ["A-e0", "A-e1", "A-e2", "B-e0", "B-e1"], got


def test_random_episode_favors_big_series():
    """Uniform over episodes: a 100-ep series should dominate a 4-ep one."""
    e = make({"Big": 100, "Small": 4})
    rng = random.Random(7)
    got = shuffle.order(e, "random_episode", rng=rng, limit=400)
    c = collections.Counter(series_of(v) for v in got)
    assert c["Big"] > c["Small"] * 5, c


def test_even_series_equalizes_series():
    """Equal weight per SERIES despite very different episode counts."""
    e = make({"Big": 100, "Small": 4})
    rng = random.Random(7)
    got = shuffle.order(e, "even_series", rng=rng, limit=1000)
    c = collections.Counter(series_of(v) for v in got)
    ratio = c["Big"] / c["Small"]
    assert 0.8 < ratio < 1.25, f"not equalized: {c} ratio={ratio}"


def test_round_robin_no_repeat_until_all_played():
    e = make({"A": 5, "B": 5, "C": 5})
    rng = random.Random(3)
    got = shuffle.order(e, "round_robin", rng=rng, limit=30)
    for i in range(0, 30, 3):
        window = [series_of(v) for v in got[i:i + 3]]
        assert sorted(window) == ["A", "B", "C"], f"window {i}: {window}"


def test_decay_avoids_recently_played():
    """A just-played series should be rarer immediately after."""
    e = make({"A": 20, "B": 20, "C": 20})
    rng = random.Random(11)
    got = [series_of(v) for v in
           shuffle.order(e, "decay", rng=rng, limit=600)]
    immediate_repeats = sum(1 for i in range(1, len(got)) if got[i] == got[i-1])
    # uniform-random would repeat ~1/3 of the time; decay must do better
    assert immediate_repeats < len(got) * 0.2, \
        f"decay did not avoid repeats: {immediate_repeats}/{len(got)}"


def test_movies_pooled_vs_individual():
    """movies_as_series=False pools all movies into ONE bucket, so movies
    collectively get one series' worth of slots instead of ten."""
    e = make({"Show": 10}, movies=10)
    rng = random.Random(5)
    pooled = [v for v in shuffle.order(e, "even_series", rng=random.Random(5),
                                       movies_as_series=False, limit=600)]
    individual = [v for v in shuffle.order(e, "even_series", rng=random.Random(5),
                                           movies_as_series=True, limit=600)]
    pooled_movies = sum(1 for v in pooled if v.startswith("movie-"))
    indiv_movies = sum(1 for v in individual if v.startswith("movie-"))
    # 1 show + 10 movies: pooled gives movies 1 of 2 buckets (~50% of slots),
    # individual gives them 10 of 11 (~91%) — so ~1.8x, not 2x.
    assert pooled_movies < 600 * 0.60, f"pooled movies too high: {pooled_movies}"
    assert indiv_movies > 600 * 0.80, f"individual movies too low: {indiv_movies}"
    assert indiv_movies > pooled_movies * 1.5, \
        f"pooling had no effect: pooled={pooled_movies} indiv={indiv_movies}"


def test_empty_and_unknown():
    assert shuffle.order([], "even_series") == []
    try:
        shuffle.order(make({"A": 1}), "nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown policy must raise")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError as ex:
                fails += 1
                print(f"  FAIL {name}: {ex}")
    print("SHUFFLE-TESTS", "PASS" if not fails else f"FAIL ({fails})")
    sys.exit(1 if fails else 0)

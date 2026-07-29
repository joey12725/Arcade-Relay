#!/usr/bin/env python3
"""Screensaver playlist ordering policies (spec §5.5 `mode`, extended).

The server resolves enabled sources into a flat list of playable video ids
and then orders it. Ordering lives here, server-side, so the policy is
config-as-code and the cabinet stays a dumb player.

Policies
--------
in_order          every source in library order, episodes in episode order
random_episode    uniform over ALL episodes (a 200-episode series dominates
                  a 6-episode one — that is the point of the alternatives)
even_series       pick a series uniformly, then an episode within it
                  uniformly, repeatedly — each SERIES gets equal weight
round_robin       random series order, but once a series plays an episode it
                  waits until every other series has played one
decay             weighted random; a series' weight is halved each time it
                  plays and recovers toward 1.0 as others play, so recently
                  seen series are gently avoided rather than banned

Movies
------
`movies_as_series` decides whether each movie is its own "series" (so a
lone movie competes with a 60-episode show for a slot) or whether ALL
movies share one pooled series. Only affects the series-aware policies.
"""
import random

POLICIES = ("in_order", "random_episode", "even_series", "round_robin",
            "decay")
DEFAULT_POLICY = "even_series"
MOVIES_KEY = "\x00movies"     # pooled-movies bucket id; cannot collide


def _group(entries, movies_as_series):
    """entries: [{id, source_id, source_type, index}] -> {group: [ids]}"""
    groups = {}
    for e in entries:
        if e["source_type"] == "Movie" and not movies_as_series:
            key = MOVIES_KEY
        else:
            key = e["source_id"]
        groups.setdefault(key, []).append(e)
    for g in groups.values():
        g.sort(key=lambda e: e["index"])
    return groups


def order(entries, policy=DEFAULT_POLICY, movies_as_series=True, rng=None,
          limit=None):
    """Returns an ordered list of video ids.

    entries is the flat resolved list; each item carries which source it
    came from so the series-aware policies can group. `limit` caps the
    generated queue (the queue loops, so it need not cover the library).
    """
    rng = rng or random
    if not entries:
        return []
    if policy == "in_order":
        ordered = sorted(entries, key=lambda e: (e["source_index"], e["index"]))
        return [e["id"] for e in ordered][:limit] if limit else \
            [e["id"] for e in ordered]

    if policy == "random_episode":
        pool = [e["id"] for e in entries]
        rng.shuffle(pool)
        return pool[:limit] if limit else pool

    groups = _group(entries, movies_as_series)
    keys = sorted(groups)
    n = limit or len(entries)
    out = []

    if policy == "even_series":
        # uniform over series, then uniform within — no memory
        cursors = {k: list(range(len(groups[k]))) for k in keys}
        for k in keys:
            rng.shuffle(cursors[k])
        pos = {k: 0 for k in keys}
        for _ in range(n):
            k = rng.choice(keys)
            g, c = groups[k], cursors[k]
            if pos[k] >= len(c):          # exhausted: reshuffle that series
                rng.shuffle(c)
                pos[k] = 0
            out.append(g[c[pos[k]]]["id"])
            pos[k] += 1
        return out

    if policy == "round_robin":
        # every series plays once before any repeats
        pos = {k: 0 for k in keys}
        for k in keys:
            rng.shuffle(groups[k])
        while len(out) < n:
            round_keys = keys[:]
            rng.shuffle(round_keys)       # random order WITHIN each round
            for k in round_keys:
                if len(out) >= n:
                    break
                g = groups[k]
                if pos[k] >= len(g):
                    rng.shuffle(g)
                    pos[k] = 0
                out.append(g[pos[k]]["id"])
                pos[k] += 1
        return out

    if policy == "decay":
        # weight halves on play, recovers 15% of the gap per other-pick
        weights = {k: 1.0 for k in keys}
        pos = {k: 0 for k in keys}
        for k in keys:
            rng.shuffle(groups[k])
        for _ in range(n):
            total = sum(weights[k] for k in keys)
            r = rng.uniform(0, total)
            acc = 0.0
            chosen = keys[-1]
            for k in keys:
                acc += weights[k]
                if r <= acc:
                    chosen = k
                    break
            g = groups[chosen]
            if pos[chosen] >= len(g):
                rng.shuffle(g)
                pos[chosen] = 0
            out.append(g[pos[chosen]]["id"])
            pos[chosen] += 1
            for k in keys:
                if k == chosen:
                    weights[k] *= 0.5
                else:
                    weights[k] += (1.0 - weights[k]) * 0.15
        return out

    raise ValueError(f"unknown shuffle policy {policy!r}")

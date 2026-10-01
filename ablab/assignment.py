"""Variant assignment.

Deterministic hashing, not a coin flip. Given the same visitor id and the same
experiment salt, this function returns the same variant forever -- which is what
makes assignment sticky without storing an assignment table anywhere. It also
means assignment can be recomputed during analysis to audit what the server did.
"""
from __future__ import annotations

import hashlib

BUCKETS = 100


def bucket(visitor_id: str, salt: str) -> int:
    """Map a visitor onto 0..99. Uniform, stable, and salted per experiment.

    The salt is what keeps a visitor from landing in the same relative position
    in every experiment -- without it, experiment 002 would reuse experiment
    001's split and the two would be correlated.
    """
    digest = hashlib.sha256(f"{visitor_id}:{salt}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % BUCKETS


def assign(visitor_id: str, salt: str, split: int = 50) -> str:
    """Return 'A' (control) or 'B' (treatment).

    `split` is the percentage of traffic sent to A, so a 90/10 holdout is just
    split=90. Fixed 50/50 would have been three characters shorter and would
    have made every future experiment a rewrite.
    """
    if not 0 <= split <= 100:
        raise ValueError("split must be a percentage between 0 and 100")
    return "A" if bucket(visitor_id, salt) < split else "B"

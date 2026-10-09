"""Deterministic randomness.  Nothing in reel uses global random state or Python's `hash()`.

Every random choice is a pure function of (spec seed, a stable string/int path), so the
same spec renders to the same pixels regardless of worker count or frame order.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np


def stable_int(*parts: Any, bits: int = 63) -> int:
    """Hash arbitrary parts to a stable integer (sha256-based, process independent)."""
    h = hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") & ((1 << bits) - 1)


def derive_rng(seed: int, *path: Any) -> np.random.Generator:
    """A generator that depends only on ``seed`` and ``path``."""
    return np.random.default_rng(stable_int(seed, *path))


def jitter(seed: int, *path: Any) -> float:
    """A single stable float in [0, 1)."""
    return stable_int(seed, *path, bits=53) / float(1 << 53)

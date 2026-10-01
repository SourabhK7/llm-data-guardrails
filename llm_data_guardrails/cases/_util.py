from __future__ import annotations

from typing import Sequence, TypeVar

import numpy as np

T = TypeVar("T")


def pick(rng: np.random.Generator, seq: Sequence[T]) -> T:
    return seq[int(rng.integers(len(seq)))]


def pick_n(rng: np.random.Generator, seq: Sequence[T], k: int) -> list[T]:
    return [seq[int(i)] for i in rng.choice(len(seq), size=k, replace=False)]


def jitter_shares(
    rng: np.random.Generator, shares: Sequence[float], sd: float = 0.02, floor: float = 0.03
) -> list[float]:
    arr = np.clip(np.asarray(shares, dtype=float) + rng.normal(0, sd, len(shares)), floor, None)
    return (arr / arr.sum()).tolist()


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def signed_pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%"


def sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def fmt_p(p: float) -> str:
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"

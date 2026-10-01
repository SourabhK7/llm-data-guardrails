"""Small, dependency-free statistical helpers shared by the trap generators and the guardrail."""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

Z95 = 1.959963984540054


def two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


def wilson_interval(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n <= 0:
        raise ValueError("n must be positive")
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def two_proportion_test(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """Pooled two-proportion z-test. Returns (rate2 - rate1, two-sided p)."""
    if n1 <= 0 or n2 <= 0:
        raise ValueError("sample sizes must be positive")
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return p2 - p1, 1.0
    return p2 - p1, two_sided_p((p2 - p1) / se)


def welch_test(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """Difference in means (y - x) with a normal-approximation Welch test.

    Intended for the large samples typical of product experiments, where the t and
    normal critical values are indistinguishable.
    """
    xa, ya = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    se = math.sqrt(xa.var(ddof=1) / len(xa) + ya.var(ddof=1) / len(ya))
    diff = float(ya.mean() - xa.mean())
    if se == 0:
        return diff, 1.0
    return diff, two_sided_p(diff / se)


def _upper_regularized_gamma(a: float, x: float) -> float:
    if x < 0 or a <= 0:
        raise ValueError("invalid arguments")
    if x == 0:
        return 1.0
    log_prefactor = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1:
        term = total = 1.0 / a
        ap = a
        for _ in range(10_000):
            ap += 1
            term *= x / ap
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefactor))
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, 10_000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = d if abs(d) > tiny else tiny
        c = b + an / c
        c = c if abs(c) > tiny else tiny
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return math.exp(log_prefactor) * h


def chi2_sf(x: float, df: int) -> float:
    return _upper_regularized_gamma(df / 2.0, x / 2.0)


def srm_p_value(observed: Sequence[int], expected_shares: Sequence[float]) -> float:
    """Chi-square goodness-of-fit p-value for a sample ratio mismatch check."""
    obs = np.asarray(observed, dtype=float)
    shares = np.asarray(expected_shares, dtype=float)
    if len(obs) != len(shares) or len(obs) < 2:
        raise ValueError("need matching observed counts and expected shares for 2+ arms")
    expected = obs.sum() * shares / shares.sum()
    stat = float(((obs - expected) ** 2 / expected).sum())
    return chi2_sf(stat, len(obs) - 1)

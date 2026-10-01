import math

import numpy as np
import pytest

from llm_data_guardrails.stats import (
    chi2_sf,
    srm_p_value,
    two_proportion_test,
    welch_test,
    wilson_interval,
)


def test_wilson_matches_reference_values():
    lo, hi = wilson_interval(50, 100)
    assert lo == pytest.approx(0.4038, abs=1e-4)
    assert hi == pytest.approx(0.5962, abs=1e-4)
    assert wilson_interval(0, 10)[0] == 0.0


def test_two_proportion_reference_value():
    diff, p = two_proportion_test(100, 1000, 150, 1000)
    assert diff == pytest.approx(0.05)
    assert p == pytest.approx(0.00072, abs=5e-5)


def test_two_proportion_identical_rates():
    diff, p = two_proportion_test(10, 100, 10, 100)
    assert diff == 0 and p == pytest.approx(1.0)


@pytest.mark.parametrize("x, df, expected", [(3.841459, 1, 0.05), (5.991465, 2, 0.05), (0.0, 1, 1.0), (20.0, 3, 0.00016974)])
def test_chi2_sf(x, df, expected):
    assert chi2_sf(x, df) == pytest.approx(expected, rel=1e-3, abs=1e-9)


def test_srm_detects_mismatch():
    assert srm_p_value([50_000, 50_000], [0.5, 0.5]) == pytest.approx(1.0)
    assert srm_p_value([52_000, 48_000], [0.5, 0.5]) < 1e-30


def test_welch_null_and_shift():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 5000)
    _, p_same = welch_test(x, x)
    assert p_same == pytest.approx(1.0)
    diff, p_shift = welch_test(x, x + 0.2)
    assert diff == pytest.approx(0.2) and p_shift < 1e-10
    assert not math.isnan(p_shift)

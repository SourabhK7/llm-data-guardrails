import copy

import pandas as pd
import pytest

from llm_data_guardrails.cases import TRAPS, Claim, build_case, build_suite
from llm_data_guardrails.cases.model import GroundTruth, TableSpec
from llm_data_guardrails.guardrail import (
    CHECKS,
    CLAIM_KINDS,
    Guardrail,
    claim_json_schema,
    validate_claim,
)
from llm_data_guardrails.guardrail.evaluate import evaluate_guardrail, render_markdown
from llm_data_guardrails.stats import bh_adjust, log_ratio_test, stratified_difference

TRAP_IDS = sorted(TRAPS)


@pytest.fixture(scope="module")
def suite():
    return build_suite(n_per_trap=6)


def test_every_generated_claim_is_valid(suite):
    for case in suite:
        assert validate_claim(case.claim, case.tables) == [], case.case_id


def test_every_claim_matches_its_schema_branch(suite):
    branches = {b["properties"]["kind"]["const"]: b for b in claim_json_schema()["anyOf"]}
    assert set(branches) == set(CLAIM_KINDS)
    for case in suite:
        params_schema = branches[case.claim.kind]["properties"]["params"]
        assert set(params_schema["required"]) <= set(case.claim.params), case.case_id
        assert set(case.claim.params) <= set(params_schema["properties"]), case.case_id


def test_guardrail_ignores_ground_truth_and_generation_metadata():
    case = build_case("simpsons_paradox", 0, True)
    blinded = copy.deepcopy(case)
    blinded.ground_truth = GroundTruth(trap_present=False, acceptable_verdicts=frozenset())
    blinded.generation = {}
    blinded.trap_id = "unknown"
    g = Guardrail()
    assert g.evaluate_case(case).to_dict() == g.evaluate_case(blinded).to_dict()


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_in_distribution_separation(trap_id):
    """Regression gate: every check keeps firing on its trap and not on matched controls.

    This is a construction check, not a performance estimate. See README.
    """
    cases = [build_case(trap_id, i, planted) for i in range(6) for planted in (True, False)]
    stats = evaluate_guardrail(cases)[trap_id]
    assert stats.caught / stats.n_trap >= 5 / 6
    assert stats.false_alarms / stats.n_control <= 1 / 6


def test_every_check_is_exercised(suite):
    fired = set()
    for s in evaluate_guardrail(suite).values():
        fired |= set(s.fired_on_traps)
    assert fired == set(CHECKS)


def test_invalid_claim_is_unverified():
    df = pd.DataFrame({"a": [1]})
    claim = Claim(text="x", kind="period_change", table="t", params={"period_col": "missing"})
    report = Guardrail().evaluate(claim, {"t": df}, {"t": TableSpec("t", {"a": "a"})})
    assert report.decision == "unverified" and report.errors


def test_render_markdown_has_totals(suite):
    assert "**all**" in render_markdown(evaluate_guardrail(suite))


def test_bh_adjust_reference():
    assert bh_adjust([0.01, 0.04, 0.03, 0.005]) == pytest.approx([0.02, 0.04, 0.04, 0.02])


def test_stratified_difference_single_stratum_matches_simple_difference():
    diff, se, p = stratified_difference([(100, 1000, 150, 1000)])
    assert diff == pytest.approx(0.05)
    assert 0 < p < 0.001


def test_log_ratio_test_null_and_shift():
    ratio, p = log_ratio_test(1000, 10, 1000, 10)
    assert ratio == pytest.approx(1.0) and p == pytest.approx(1.0)
    ratio, p = log_ratio_test(1000, 10, 1300, 10)
    assert ratio == pytest.approx(1.3) and p < 1e-6


def test_rate_mix_effects_sum_to_total():
    case = build_case("mix_shift", 0, True)
    report = Guardrail(checks=["rate_mix_decomposition"]).evaluate_case(case)
    ev = report.results[0].evidence
    assert ev["rate_effect"] + ev["mix_effect"] == pytest.approx(ev["total_change"], abs=1e-12)

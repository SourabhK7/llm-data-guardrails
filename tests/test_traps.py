"""Every trap must be correct by construction, deterministic, and serializable."""

import re

import pytest

from llm_data_guardrails.cases import TRAPS, Case, Verdict, build_case, render_case

N_PAIRS = 8
TRAP_IDS = sorted(TRAPS)
UNFILLED = re.compile(r"\{[a-z_0-9]+\}")


@pytest.mark.parametrize("trap_id", TRAP_IDS)
@pytest.mark.parametrize("index", range(N_PAIRS))
@pytest.mark.parametrize("planted", [True, False])
def test_invariants_hold_on_emitted_data(trap_id, index, planted):
    case = build_case(trap_id, index, planted)
    assert TRAPS[trap_id].check_case(case) == []


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_verdict_sets_are_consistent(trap_id):
    trap, control = build_case(trap_id, 0, True), build_case(trap_id, 0, False)
    assert Verdict.SUPPORTED not in trap.ground_truth.acceptable_verdicts
    assert Verdict.SUPPORTED in control.ground_truth.acceptable_verdicts
    assert trap.ground_truth.trap_present and not control.ground_truth.trap_present
    assert trap.ground_truth.mechanism and control.ground_truth.mechanism is None


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_matched_pair_shares_scenario(trap_id):
    trap, control = build_case(trap_id, 3, True), build_case(trap_id, 3, False)
    assert trap.seed == control.seed
    assert trap.domain == control.domain
    assert set(trap.tables) == set(control.tables)


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_text_is_fully_rendered(trap_id):
    for planted in (True, False):
        case = build_case(trap_id, 1, planted)
        assert not UNFILLED.search(case.claim.text), case.claim.text
        assert not UNFILLED.search(case.ground_truth.mechanism or ""), case.ground_truth.mechanism
        assert "ground truth" not in render_case(case).lower()


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_data_dictionary_matches_tables(trap_id):
    case = build_case(trap_id, 2, True)
    assert case.claim.table in case.tables
    for name, df in case.tables.items():
        assert set(case.table_specs[name].columns) == set(df.columns), name


@pytest.mark.parametrize("trap_id", TRAP_IDS)
def test_deterministic_and_round_trips(trap_id):
    a = build_case(trap_id, 4, True)
    b = build_case(trap_id, 4, True)
    assert a.to_json() == b.to_json()
    assert Case.from_dict(a.to_dict()).to_json() == a.to_json()

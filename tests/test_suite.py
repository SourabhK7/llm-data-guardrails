import pytest

from llm_data_guardrails.cases import TRAPS, build_suite, read_jsonl, write_jsonl
from llm_data_guardrails.cases.suite import case_seed


def test_suite_size_and_unique_ids():
    cases = build_suite(n_per_trap=2)
    assert len(cases) == len(TRAPS) * 2 * 2
    assert len({c.case_id for c in cases}) == len(cases)
    assert sum(c.planted for c in cases) == len(cases) // 2


def test_heldout_requires_a_secret(monkeypatch):
    monkeypatch.delenv("LDG_HELDOUT_SECRET", raising=False)
    with pytest.raises(ValueError):
        build_suite(split="heldout", n_per_trap=1)


def test_heldout_is_secret_dependent_and_disjoint_from_dev():
    dev = case_seed("mix_shift", 0, "dev", "v")
    a = case_seed("mix_shift", 0, "heldout", "v", "secret-a")
    b = case_seed("mix_shift", 0, "heldout", "v", "secret-b")
    assert len({dev, a, b}) == 3
    assert a == case_seed("mix_shift", 0, "heldout", "v", "secret-a")


def test_version_bump_changes_cases():
    assert case_seed("peeking", 0, "dev", "2026.10") != case_seed("peeking", 0, "dev", "2026.11")


def test_jsonl_round_trip(tmp_path):
    cases = build_suite(n_per_trap=1, trap_ids=["simpsons_paradox", "outlier_driven_mean"])
    path = tmp_path / "suite.jsonl"
    assert write_jsonl(cases, path) == 4
    loaded = read_jsonl(path)
    assert [c.to_json() for c in loaded] == [c.to_json() for c in cases]

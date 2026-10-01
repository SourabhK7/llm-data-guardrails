"""The structured-claim contract.

An assistant answering a data question emits its prose answer plus one structured claim
per conclusion. The guardrail checks that claim against the data it names. Keeping the
claim structured is what makes the guardrail targeted: it runs the checks that apply to
this kind of claim instead of scanning the data for anything that looks odd.

`claim_json_schema()` returns a JSON Schema suitable for constrained decoding
(structured outputs), so the assistant can't emit a claim the guardrail can't read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..cases.model import Claim

DIRECTIONS = ("increase", "decrease", "higher", "lower")


@dataclass(frozen=True)
class ClaimKind:
    description: str
    required: tuple[str, ...]
    column_params: tuple[str, ...] = ()  # params that must name a column of the claim table
    optional: tuple[str, ...] = ()


CLAIM_KINDS: dict[str, ClaimKind] = {
    "group_comparison": ClaimKind(
        "One group has a higher rate than another.",
        ("group_col", "winner", "loser", "numerator", "denominator"),
        ("group_col", "numerator", "denominator"),
    ),
    "period_change": ClaimKind(
        "A rate changed between two periods.",
        ("period_col", "before", "after", "direction", "numerator", "denominator"),
        ("period_col", "numerator", "denominator"),
        ("asserts", "aggregation"),
    ),
    "cohort_comparison": ClaimKind(
        "One cohort performs differently from the others.",
        ("cohort_col", "target", "direction", "numerator", "denominator"),
        ("cohort_col", "numerator", "denominator"),
    ),
    "observational_effect": ClaimKind(
        "Users who did X have a different outcome than those who didn't.",
        ("treatment_col", "treated", "untreated", "numerator", "denominator"),
        ("treatment_col", "numerator", "denominator"),
        ("asserts",),
    ),
    "segment_outperformance": ClaimKind(
        "One segment's rate stands out from the rest.",
        ("segment_col", "segment", "direction", "numerator", "denominator"),
        ("segment_col", "numerator", "denominator"),
    ),
    "intervention_effect": ClaimKind(
        "Units that received an intervention changed between a before and after measurement.",
        ("treatment_col", "treated", "before", "after", "direction"),
        ("treatment_col", "before", "after"),
        ("selection",),
    ),
    "experiment_effect": ClaimKind(
        "A randomized test shows the treatment moved a metric.",
        ("metric", "direction"),
        (),
        ("relative_lift", "asserts", "stopped_day", "planned_days"),
    ),
    "segment_effect": ClaimKind(
        "A randomized test moved a metric within one segment.",
        ("segment_col", "segment", "direction"),
        ("segment_col",),
        ("segments_tested",),
    ),
    "metric_change": ClaimKind(
        "A metric changed starting on a date, attributed to something that happened then.",
        ("time_col", "change_date", "direction"),
        ("time_col",),
        ("numerator", "denominator", "value_col", "segment_col", "segment", "attributed_to", "asserts"),
    ),
}


def direction_sign(direction: str | None) -> int:
    return -1 if direction in ("decrease", "lower") else 1


def validate_claim(claim: Claim, tables: dict[str, pd.DataFrame]) -> list[str]:
    errors = []
    kind = CLAIM_KINDS.get(claim.kind)
    if kind is None:
        return [f"unknown claim kind {claim.kind!r}; expected one of {sorted(CLAIM_KINDS)}"]
    if claim.table not in tables:
        return [f"claim names table {claim.table!r}, which was not provided"]
    columns = set(tables[claim.table].columns)
    for name in kind.required:
        if name not in claim.params:
            errors.append(f"{claim.kind} claim is missing required param {name!r}")
    declared = kind.required + kind.optional
    column_params = set(kind.column_params) | {
        p for p in ("numerator", "denominator", "value_col") if p in declared
    }
    for name in sorted(column_params):
        value = claim.params.get(name)
        if value is not None and value not in columns:
            errors.append(f"param {name}={value!r} is not a column of {claim.table}")
    direction = claim.params.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        errors.append(f"direction must be one of {DIRECTIONS}")
    return errors


def claim_json_schema() -> dict[str, Any]:
    """JSON Schema for a structured claim, one branch per claim kind."""
    branches = []
    for name, kind in CLAIM_KINDS.items():
        props = {p: {"type": ["string", "number", "integer"]} for p in kind.required + kind.optional}
        if "direction" in props:
            props["direction"] = {"type": "string", "enum": list(DIRECTIONS)}
        branches.append({
            "type": "object",
            "description": kind.description,
            "properties": {
                "kind": {"const": name},
                "table": {"type": "string"},
                "params": {
                    "type": "object",
                    "properties": props,
                    "required": list(kind.required),
                    "additionalProperties": False,
                },
            },
            "required": ["kind", "table", "params"],
            "additionalProperties": False,
        })
    return {"title": "StructuredClaim", "anyOf": branches}

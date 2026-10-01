"""Plain-text rendering of a case.

`render_case(case, include_truth=False)` is exactly what a system under test sees: the
claim, any context, and each table with its data dictionary. Ground truth is only included
for human inspection.
"""

from __future__ import annotations

from .model import Case


def render_case(case: Case, include_truth: bool = False) -> str:
    out = ["## Claim", case.claim.text, ""]
    if case.context:
        out += ["## Context", *[f"- {line}" for line in case.context], ""]
    for name, df in case.tables.items():
        spec = case.table_specs[name]
        out += [f"## Table: {name}", spec.description, "", "Columns:"]
        out += [f"- {col}: {desc}" for col, desc in spec.columns.items()]
        out += ["", "```csv", df.to_csv(index=False).strip(), "```", ""]
    if include_truth:
        gt = case.ground_truth
        out += [
            "## Ground truth",
            f"- case: {case.case_id} ({case.variant}, domain: {case.domain})",
            f"- acceptable verdicts: {', '.join(sorted(v.value for v in gt.acceptable_verdicts))}",
        ]
        if gt.mechanism:
            out.append(f"- mechanism: {gt.mechanism}")
    return "\n".join(out).rstrip() + "\n"

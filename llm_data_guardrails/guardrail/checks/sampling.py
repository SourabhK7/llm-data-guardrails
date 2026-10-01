"""Checks for whether there is enough (and complete enough) data behind a claim."""

from __future__ import annotations

from ...stats import bh_adjust, two_proportion_test, wilson_interval
from ..core import WARN, Check, CheckResult, register
from ._common import fmt_p, pct, pp


@register
class CohortCompleteness(Check):
    id = "cohort_completeness"
    title = "Has the cohort finished its measurement window?"
    kinds = ("cohort_comparison",)
    description = (
        "Compares the measured denominator of the target cohort to its full population. Warns "
        "when less than 95% of the cohort has reached the measurement day while the comparison "
        "cohorts are complete; otherwise tests the difference against the other cohorts."
    )
    min_coverage = 0.95

    def applies(self, ctx):
        return super().applies(ctx) and ctx.role("population") is not None

    def run(self, ctx):
        p = ctx.params
        df, pop = ctx.table, ctx.role("population")
        num, den, col = p["numerator"], p["denominator"], p["cohort_col"]
        is_target = df[col].astype(str) == str(p["target"])
        target, others = df[is_target], df[~is_target]
        coverage = target[den].sum() / target[pop].sum()
        other_coverage = others[den].sum() / others[pop].sum()
        evidence = {"target_coverage": coverage, "comparison_coverage": other_coverage}
        if coverage < self.min_coverage <= other_coverage:
            return [CheckResult(
                self.id, True, WARN,
                f"Only {pct(coverage, 0)} of the {p['target']} cohort has reached the measurement "
                f"window, versus {pct(other_coverage, 0)} of the comparison cohorts. The rate is measured "
                f"on its earliest members only, so the comparison is premature.",
                evidence=evidence,
            )]
        diff, pval = two_proportion_test(int(others[num].sum()), int(others[den].sum()),
                                         int(target[num].sum()), int(target[den].sum()))
        evidence.update(difference=diff, p=pval)
        if ctx.sign * diff <= 0 or pval >= ctx.alpha:
            return [CheckResult(
                self.id, True, WARN,
                f"{p['target']} differs from the other cohorts by {pp(diff)}, which is not significant "
                f"(p={fmt_p(pval)}).",
                basis="evidence_test", p_value=pval, evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"Cohort is complete and differs by {pp(diff)} (p={fmt_p(pval)}).", evidence=evidence)]


@register
class SegmentInterval(Check):
    id = "segment_interval"
    title = "Is the standout segment big enough to stand out?"
    kinds = ("segment_outperformance",)
    description = (
        "Tests the claimed segment against the rest of the population and reports its Wilson "
        "95% interval. Warns when the difference is not significant in the claimed direction."
    )

    def run(self, ctx):
        p = ctx.params
        df, col, num, den = ctx.table, p["segment_col"], p["numerator"], p["denominator"]
        is_seg = df[col].astype(str) == str(p["segment"])
        k, n = int(df.loc[is_seg, num].sum()), int(df.loc[is_seg, den].sum())
        kr, nr = int(df.loc[~is_seg, num].sum()), int(df.loc[~is_seg, den].sum())
        diff, pval = two_proportion_test(kr, nr, k, n)
        lo, hi = wilson_interval(k, n)
        evidence = {"n": n, "rate": k / n, "ci": [lo, hi], "rest_rate": kr / nr, "p": pval}
        if ctx.sign * diff <= 0 or pval >= ctx.alpha:
            return [CheckResult(
                self.id, True, WARN,
                f"{p['segment']} has only {n:,} {den}; its {pct(k / n)} rate has a 95% interval of "
                f"{pct(lo)} to {pct(hi)}, against {pct(kr / nr)} for everyone else (p={fmt_p(pval)}).",
                basis="evidence_test", p_value=pval, evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"{p['segment']} stands out with n={n:,} (95% CI {pct(lo)} to {pct(hi)}).",
                            evidence=evidence)]


@register
class Multiplicity(Check):
    id = "multiplicity"
    title = "Does a segment-level result survive correction for the number of segments tested?"
    kinds = ("segment_effect",)
    description = (
        "Recomputes the treatment-vs-control test in every segment, then applies a "
        "Benjamini-Hochberg correction across segments. Warns when the claimed segment is not "
        "significant after correction."
    )

    def applies(self, ctx):
        return super().applies(ctx) and isinstance(ctx.role("arms"), dict)

    def run(self, ctx):
        p = ctx.params
        df, col, arms = ctx.table, p["segment_col"], ctx.role("arms")
        (cu, cc), (tu, tc) = arms["control"], arms["treatment"]
        tests = [two_proportion_test(int(r[cc]), int(r[cu]), int(r[tc]), int(r[tu])) for _, r in df.iterrows()]
        pvals = [t[1] for t in tests]
        adjusted = bh_adjust(pvals)
        idx = list(df[col].astype(str)).index(str(p["segment"]))
        diff, raw, adj = tests[idx][0], pvals[idx], adjusted[idx]
        m = len(df)
        evidence = {"segments_tested": m, "p_raw": raw, "p_bh": adj,
                    "segments_significant_raw": sum(x < ctx.alpha for x in pvals)}
        if ctx.sign * diff <= 0 or adj >= ctx.alpha:
            return [CheckResult(
                self.id, True, WARN,
                f"{p['segment']} is 1 of {m} segments tested. Its p={fmt_p(raw)} becomes "
                f"{fmt_p(adj)} after Benjamini-Hochberg correction; {evidence['segments_significant_raw']} "
                f"of {m} segments cross 0.05 uncorrected, about what chance produces.",
                basis="evidence_test", p_value=adj, evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"{p['segment']} remains significant after correcting for {m} segments "
                            f"(adjusted p={fmt_p(adj)}).", evidence=evidence)]

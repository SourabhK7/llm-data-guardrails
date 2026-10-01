"""Checks for claims about experiment and intervention effects."""

from __future__ import annotations

import math

from ...stats import Z95, srm_p_value
from ..core import BLOCK, WARN, Check, CheckResult, register
from ._common import fmt_p, signed, split_arms


@register
class SampleRatioMismatch(Check):
    id = "sample_ratio_mismatch"
    title = "Did assignment match the configured split?"
    kinds = ("experiment_effect", "segment_effect")
    description = (
        "Chi-square goodness-of-fit of assigned units against the configured split. Uses the "
        "conventional stricter threshold (p < 0.001) because it runs on every test."
    )

    def applies(self, ctx):
        return super().applies(ctx) and isinstance(ctx.role("expected_split"), dict)

    def run(self, ctx):
        df, arm_col, den = ctx.table, ctx.role("arm"), ctx.role("denominator")
        expected = ctx.role("expected_split")
        counts = df.groupby(arm_col)[den].sum()
        labels = [a for a in expected if a in counts.index]
        observed = [int(counts[a]) for a in labels]
        pval = srm_p_value(observed, [expected[a] for a in labels])
        shares = {a: observed[i] / sum(observed) for i, a in enumerate(labels)}
        evidence = {"observed": dict(zip(labels, observed)), "expected": expected, "p": pval}
        split = " / ".join(f"{shares[a]:.1%}" for a in labels)
        target = " / ".join(f"{expected[a]:.0%}" for a in labels)
        if pval < ctx.srm_alpha:
            return [CheckResult(
                self.id, True, BLOCK,
                f"Sample ratio mismatch: assigned {split} against a configured {target} "
                f"(chi-square p={fmt_p(pval)}). Assignment or logging is broken, so the comparison "
                f"can't be trusted until the cause is found.",
                basis="rule", p_value=pval, evidence=evidence,
            )]
        return [CheckResult(self.id, False, BLOCK, f"Split {split} matches {target} (p={fmt_p(pval)}).",
                            evidence=evidence)]


@register
class SequentialBoundary(Check):
    id = "sequential_boundary"
    title = "Was the test stopped before its planned length?"
    kinds = ("experiment_effect",)
    description = (
        "When a test ends before its planned length, compares the final z-statistic to an "
        "approximate O'Brien-Fleming boundary (z_alpha/2 / sqrt(information fraction)), which "
        "keeps the overall false-positive rate near alpha under repeated looks."
    )

    def applies(self, ctx):
        return super().applies(ctx) and bool(ctx.role("cumulative")) and isinstance(ctx.role("arms"), dict)

    def run(self, ctx):
        planned = ctx.params.get("planned_days") or ctx.context_number(r"planned test length:\s*(\d+)")
        if not planned:
            return []
        df, arms = ctx.table, ctx.role("arms")
        (cu, cc), (tu, tc) = arms["control"], arms["treatment"]
        last = df.iloc[-1]
        n_c, k_c, n_t, k_t = int(last[cu]), int(last[cc]), int(last[tu]), int(last[tc])
        p1, p2 = k_c / n_c, k_t / n_t
        pooled = (k_c + k_t) / (n_c + n_t)
        z = (p2 - p1) / math.sqrt(pooled * (1 - pooled) * (1 / n_c + 1 / n_t))
        fraction = min(1.0, len(df) / float(planned))
        boundary = Z95 / math.sqrt(fraction)
        evidence = {"looks": len(df), "planned": planned, "information_fraction": fraction,
                    "z": z, "boundary": boundary}
        if fraction < 1 and abs(z) < boundary:
            return [CheckResult(
                self.id, True, WARN,
                f"The test stopped at day {len(df)} of {int(planned)} ({fraction:.0%} of planned data). "
                f"With repeated looks, an early stop needs |z| >= {boundary:.2f}; observed z={z:.2f}. "
                f"The nominal p-value overstates the evidence.",
                evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"z={z:.2f} clears the boundary of {boundary:.2f} at {fraction:.0%} information.",
                            evidence=evidence)]


@register
class EffectStability(Check):
    id = "effect_stability"
    title = "Is the effect stable over the test, or decaying?"
    kinds = ("experiment_effect",)
    description = (
        "Compares the treatment effect in the first and last third of a daily (non-cumulative) "
        "test. Flags when the difference is significant and the late effect is under half the "
        "early one, the signature of a novelty effect."
    )

    def applies(self, ctx):
        return (super().applies(ctx) and isinstance(ctx.role("arms"), dict)
                and ctx.role("time") is not None and not ctx.role("cumulative"))

    def run(self, ctx):
        df, arms = ctx.table.sort_values(ctx.role("time")), ctx.role("arms")
        (cu, cc), (tu, tc) = arms["control"], arms["treatment"]
        k = len(df) // 3
        if k < 3:
            return []

        def window(sub):
            n_c, k_c, n_t, k_t = (int(sub[c].sum()) for c in (cu, cc, tu, tc))
            p1, p2 = k_c / n_c, k_t / n_t
            return p2 - p1, p1 * (1 - p1) / n_c + p2 * (1 - p2) / n_t, p2 / p1 - 1

        d_e, v_e, rel_e = window(df.iloc[:k])
        d_l, v_l, rel_l = window(df.iloc[-k:])
        z = (d_e - d_l) / math.sqrt(v_e + v_l)
        pval = math.erfc(abs(z) / math.sqrt(2))
        decaying = ctx.sign * d_e > 0 and abs(d_l) < 0.5 * abs(d_e)
        evidence = {"early_relative_lift": rel_e, "late_relative_lift": rel_l, "p": pval, "window_days": k}
        severity = BLOCK if ctx.params.get("asserts") == "durable_effect" else WARN
        message = (f"{signed(rel_e)} relative lift in the first {k} days vs {signed(rel_l)} in the last "
                   f"{k} (difference p={fmt_p(pval)}).")
        if decaying:
            message += " If the decay is real, the full-period average overstates the lasting effect."
        return [CheckResult(self.id, decaying, severity, message,
                            basis="problem_test", p_value=pval, evidence=evidence)]


@register
class OutlierInfluence(Check):
    id = "outlier_influence"
    title = "Is a mean-based lift carried by a handful of observations?"
    kinds = ("experiment_effect",)
    description = (
        "For mean metrics with a table of the largest individual observations, recomputes the "
        "lift after removing those observations. Warns when less than a quarter of the headline "
        "lift survives."
    )
    surviving_share = 0.25

    def applies(self, ctx):
        return (super().applies(ctx) and ctx.role("value") is not None and ctx.role("arm") is not None
                and bool(ctx.other_tables_with_roles("arm", "value")))

    def run(self, ctx):
        df = ctx.table
        arm_col, users_col, value_col = ctx.role("arm"), ctx.role("denominator"), ctx.role("value")
        control, treatment = split_arms([str(a) for a in df[arm_col]])
        summary = df.set_index(df[arm_col].astype(str))
        _, obs, spec = ctx.other_tables_with_roles("arm", "value")[0]
        o_arm, o_val = spec.roles["arm"], spec.roles["value"]

        def per_user(arm, trimmed):
            total = float(summary.loc[arm, value_col])
            if trimmed:
                total -= float(obs.loc[obs[o_arm].astype(str) == arm, o_val].sum())
            return total / float(summary.loc[arm, users_col])

        headline = per_user(treatment, False) / per_user(control, False) - 1
        trimmed = per_user(treatment, True) / per_user(control, True) - 1
        evidence = {"headline_lift": headline, "trimmed_lift": trimmed, "observations_removed": len(obs)}
        if ctx.sign * headline > 0 and ctx.sign * trimmed < self.surviving_share * abs(headline):
            return [CheckResult(
                self.id, True, WARN,
                f"Excluding the {len(obs)} largest observations, the lift falls from {signed(headline)} to "
                f"{signed(trimmed)}. The headline result is carried by a few outliers.",
                evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"Lift survives trimming the {len(obs)} largest observations ({signed(trimmed)}).",
                            evidence=evidence)]


@register
class SelectionRegression(Check):
    id = "selection_regression"
    title = "Are units chosen for being extreme just regressing to the mean?"
    kinds = ("intervention_effect",)
    description = (
        "When treated units were selected on their before-score, compares their change to the "
        "change of similarly selected units without treatment: a historical baseline table if "
        "one is provided, otherwise the next-most-extreme untreated units. Warns when the excess "
        "change is not significant."
    )

    def applies(self, ctx):
        return super().applies(ctx) and bool(ctx.params.get("selection") or ctx.role("selection"))

    def run(self, ctx):
        p = ctx.params
        df, before, after = ctx.table, p["before"], p["after"]
        treated = df[p["treatment_col"]].astype(str) == str(p["treated"])
        change = df[after] - df[before]
        gain = change[treated]
        gain_mean, gain_var = float(gain.mean()), float(gain.var(ddof=1) / len(gain))
        history = ctx.other_tables_with_roles("value")
        if history:
            _, hist_df, spec = history[0]
            comparison = hist_df[spec.roles["value"]].astype(float)
            source = "historical baseline"
        else:
            pool = df[~treated].sort_values(before).head(int(treated.sum()))
            comparison = pool[after] - pool[before]
            source = "next-most-extreme untreated units"
        baseline = float(comparison.mean())
        base_var = float(comparison.var(ddof=1) / len(comparison)) if len(comparison) > 1 else 0.0
        z = (gain_mean - baseline) / math.sqrt(gain_var + base_var) * ctx.sign
        evidence = {"treated_change": gain_mean, "baseline_change": baseline, "baseline_source": source, "z": z}
        if z < Z95:
            return [CheckResult(
                self.id, True, WARN,
                f"Treated units were selected for extreme {before} values, and similarly selected units "
                f"move by {baseline:+.1f} on their own ({source}). The treated change of {gain_mean:+.1f} is "
                f"not significantly larger (z={z:.2f}).",
                evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"Treated change {gain_mean:+.1f} exceeds the {source} ({baseline:+.1f}), z={z:.2f}.",
                            evidence=evidence)]

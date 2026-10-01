"""Checks for claims that compare aggregates whose composition can differ."""

from __future__ import annotations

from ...stats import stratified_difference, two_proportion_test
from ..core import BLOCK, INFO, WARN, Check, CheckResult, ClaimContext, register
from ._common import fmt_p, pct, pp, sums


@register
class SegmentReversal(Check):
    id = "segment_reversal"
    title = "Does the group difference hold inside segments?"
    kinds = ("group_comparison",)
    description = (
        "Re-estimates the winner-vs-loser difference within each segment (inverse-variance "
        "stratified difference). Blocks when the within-segment difference has the opposite sign "
        "(Simpson's paradox); warns when it is no longer significant."
    )

    def applies(self, ctx: ClaimContext) -> bool:
        return super().applies(ctx) and bool(ctx.role("segments"))

    def run(self, ctx):
        p = ctx.params
        df, g, num, den = ctx.table, p["group_col"], p["numerator"], p["denominator"]
        winner, loser = p["winner"], p["loser"]
        out = []
        for dim in ctx.role("segments"):
            strata, rows = [], {}
            for value, sub in df.groupby(dim):
                kw, nw = sums(sub[sub[g] == winner], num, den)
                kl, nl = sums(sub[sub[g] == loser], num, den)
                if nw and nl:
                    strata.append((kl, nl, kw, nw))
                    rows[str(value)] = {"winner_rate": kw / nw, "loser_rate": kl / nl}
            if not strata:
                continue
            diff, _, pval = stratified_difference(strata)
            evidence = {"dimension": dim, "stratified_difference": diff, "segments": rows}
            if diff < 0:
                out.append(CheckResult(
                    self.id, True, BLOCK,
                    f"Within {dim}, {winner} converts lower than {loser} (stratified difference "
                    f"{pp(diff)}, p={fmt_p(pval)}). Its overall lead comes from a different {dim} mix.",
                    basis="problem_test", p_value=pval, evidence=evidence,
                ))
            elif pval >= ctx.alpha:
                out.append(CheckResult(
                    self.id, True, WARN,
                    f"After adjusting for {dim}, {winner}'s advantage ({pp(diff)}) is not significant "
                    f"(p={fmt_p(pval)}).",
                    basis="evidence_test", p_value=pval, evidence=evidence,
                ))
            else:
                out.append(CheckResult(
                    self.id, False, BLOCK, f"{winner}'s advantage holds within {dim} ({pp(diff)}).",
                    evidence=evidence,
                ))
        return out


def _period_frames(ctx: ClaimContext):
    p = ctx.params
    df = ctx.table
    return df[df[p["period_col"]].astype(str) == str(p["before"])], df[df[p["period_col"]].astype(str) == str(p["after"])]


@register
class RateMixDecomposition(Check):
    id = "rate_mix_decomposition"
    title = "Is a change in the overall rate a change in behavior or in mix?"
    kinds = ("period_change",)
    description = (
        "Splits the change in the aggregate rate into a rate effect (segments converting "
        "differently) and a mix effect (segment shares moving), using midpoint weights so the "
        "two sum exactly to the total. Flags when mix explains at least half the change and "
        "the within-segment change is not significant in the claimed direction."
    )

    def applies(self, ctx):
        return super().applies(ctx) and bool(ctx.role("segments"))

    def run(self, ctx):
        p = ctx.params
        num, den = p["numerator"], p["denominator"]
        before, after = _period_frames(ctx)
        out = []
        for dim in ctx.role("segments"):
            b = before.groupby(dim)[[num, den]].sum()
            a = after.groupby(dim)[[num, den]].sum()
            idx = b.index.union(a.index)
            b, a = b.reindex(idx, fill_value=0), a.reindex(idx, fill_value=0)
            s0, s1 = b[den] / b[den].sum(), a[den] / a[den].sum()
            r0 = (b[num] / b[den]).fillna(0)
            r1 = (a[num] / a[den]).fillna(0)
            total = float((s1 * r1).sum() - (s0 * r0).sum())
            rate_effect = float(((r1 - r0) * (s0 + s1) / 2).sum())
            mix_effect = float(((s1 - s0) * (r0 + r1) / 2).sum())
            strata = [(int(b.loc[i, num]), int(b.loc[i, den]), int(a.loc[i, num]), int(a.loc[i, den])) for i in idx]
            within, _, pval = stratified_difference(strata)
            mover = (s1 - s0).abs().idxmax()
            evidence = {"dimension": dim, "total_change": total, "rate_effect": rate_effect,
                        "mix_effect": mix_effect, "within_segment_change": within, "within_p": pval}
            within_supports = ctx.sign * within > 0 and pval < ctx.alpha
            mostly_mix = total != 0 and abs(mix_effect) >= 0.5 * abs(total)
            if ctx.sign * total > 0 and mostly_mix and not within_supports:
                severity = BLOCK if p.get("asserts") == "within_segment_behavior" else WARN
                out.append(CheckResult(
                    self.id, True, severity,
                    f"{mix_effect / total:.0%} of the {pp(total)} change is a mix effect: {mover} went "
                    f"from {pct(s0[mover], 0)} to {pct(s1[mover], 0)} of {den}. Within {dim}, the rate "
                    f"changed by {pp(within)} (p={fmt_p(pval)}).",
                    basis="evidence_test", p_value=pval, evidence=evidence,
                ))
            else:
                out.append(CheckResult(
                    self.id, False, WARN,
                    f"Rate effect {pp(rate_effect)}, mix effect {pp(mix_effect)} across {dim}.",
                    evidence=evidence,
                ))
        return out


@register
class PooledRate(Check):
    id = "pooled_rate"
    title = "Does the volume-weighted rate move the way the claim says?"
    kinds = ("period_change",)
    description = (
        "Recomputes the period-over-period change as a pooled (volume-weighted) rate. Blocks if "
        "it moved significantly the other way, which happens when a claim averages per-unit "
        "rates; warns if it isn't significant."
    )

    def run(self, ctx):
        p = ctx.params
        num, den = p["numerator"], p["denominator"]
        before, after = _period_frames(ctx)
        k0, n0 = sums(before, num, den)
        k1, n1 = sums(after, num, den)
        diff, pval = two_proportion_test(k0, n0, k1, n1)
        evidence = {"pooled_before": k0 / n0, "pooled_after": k1 / n1, "p": pval}
        note = " The claim averages per-unit rates, which weights small units like large ones." if p.get(
            "aggregation") == "mean_of_rates" else ""
        if ctx.sign * diff < 0 and pval < ctx.alpha:
            return [CheckResult(
                self.id, True, BLOCK,
                f"Weighted by volume, the rate moved the other way: {pct(k0 / n0, 2)} to {pct(k1 / n1, 2)} "
                f"(p={fmt_p(pval)}).{note}",
                basis="problem_test", p_value=pval, evidence=evidence,
            )]
        if ctx.sign * diff <= 0 or pval >= ctx.alpha:
            return [CheckResult(
                self.id, True, WARN,
                f"The volume-weighted change ({pp(diff)}) is not significant (p={fmt_p(pval)}).{note}",
                basis="evidence_test", p_value=pval, evidence=evidence,
            )]
        return [CheckResult(self.id, False, BLOCK,
                            f"Volume-weighted rate moved {pp(diff)} (p={fmt_p(pval)}).", evidence=evidence)]


@register
class StratifiedEffect(Check):
    id = "stratified_effect"
    title = "Does an observational gap survive adjusting for who self-selects?"
    kinds = ("observational_effect",)
    description = (
        "Compares treated and untreated units within each segment of a pre-treatment variable. "
        "Blocks when the within-segment gap is not significant or flips sign, meaning the crude "
        "gap is explained by who chose the treatment."
    )

    def applies(self, ctx):
        return super().applies(ctx) and bool(ctx.role("segments"))

    def run(self, ctx):
        p = ctx.params
        df, t, num, den = ctx.table, p["treatment_col"], p["numerator"], p["denominator"]
        kt, nt = sums(df[df[t].astype(str) == str(p["treated"])], num, den)
        ku, nu = sums(df[df[t].astype(str) == str(p["untreated"])], num, den)
        crude = kt / nt - ku / nu
        out = []
        for dim in ctx.role("segments"):
            strata = []
            for _, sub in df.groupby(dim):
                a, b = sub[sub[t].astype(str) == str(p["untreated"])], sub[sub[t].astype(str) == str(p["treated"])]
                strata.append((*sums(a, num, den), *sums(b, num, den)))
            within, _, pval = stratified_difference(strata)
            evidence = {"dimension": dim, "crude_difference": crude, "stratified_difference": within}
            if within * crude <= 0 or pval >= ctx.alpha:
                out.append(CheckResult(
                    self.id, True, BLOCK,
                    f"Within {dim}, treated and untreated differ by only {pp(within)} (p={fmt_p(pval)}), "
                    f"versus {pp(crude)} overall. The gap reflects who adopted, not the treatment.",
                    basis="evidence_test", p_value=pval, evidence=evidence,
                ))
            else:
                out.append(CheckResult(
                    self.id, False, BLOCK,
                    f"Gap holds within {dim}: {pp(within)} vs {pp(crude)} crude "
                    f"({1 - within / crude:.0%} of the crude gap is explained by {dim}).",
                    evidence=evidence,
                ))
        if p.get("asserts") == "causal":
            out.append(CheckResult(
                self.id, False, INFO,
                "Causal claim from observational data: adjusting for measured variables cannot rule "
                "out unmeasured ones. Treat as a hypothesis for a randomized test.",
            ))
        return out

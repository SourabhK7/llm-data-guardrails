"""Checks for claims that a metric changed starting on a date."""

from __future__ import annotations

import math
from datetime import timedelta

import pandas as pd

from ...stats import welch_test
from ..core import BLOCK, WARN, Check, CheckResult, register
from ._common import fmt_p, signed


def _dates(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_datetime(df[col]).dt.normalize()


@register
class SeasonalBaseline(Check):
    id = "seasonal_baseline"
    title = "Did the same calendar window move the same way last year?"
    kinds = ("metric_change",)
    description = (
        "Compares the week after the change date with the week before, this year and in the "
        "same weekday-aligned window 364 days earlier. Warns when last year moved in the same "
        "direction by at least half as much, so the change can't be attributed to the event."
    )
    explained_share = 0.5

    def applies(self, ctx):
        return super().applies(ctx) and ctx.params.get("value_col") is not None

    def run(self, ctx):
        p = ctx.params
        df = ctx.table
        dates, values = _dates(df, p["time_col"]), df[p["value_col"]].astype(float)
        change = pd.Timestamp(p["change_date"]).normalize()

        def wow(start: pd.Timestamp) -> float | None:
            before = values[(dates >= start - timedelta(days=7)) & (dates < start)]
            after = values[(dates >= start) & (dates < start + timedelta(days=7))]
            if len(before) < 7 or len(after) < 7:
                return None
            return float(after.sum() / before.sum() - 1)

        this_year, last_year = wow(change), wow(change - timedelta(days=364))
        if this_year is None or last_year is None:
            return []
        evidence = {"this_year_change": this_year, "last_year_change": last_year}
        seasonal = (ctx.sign * this_year > 0 and ctx.sign * last_year > 0
                    and abs(last_year) >= self.explained_share * abs(this_year))
        if seasonal:
            return [CheckResult(
                self.id, True, WARN,
                f"The same weekday-aligned window last year moved {signed(last_year, 0)}, versus "
                f"{signed(this_year, 0)} this year. The change is largely seasonal and can't be "
                f"attributed to {p.get('attributed_to', 'the event')}.",
                evidence=evidence,
            )]
        return [CheckResult(self.id, False, WARN,
                            f"Last year's window moved {signed(last_year, 0)} vs {signed(this_year, 0)} now.",
                            evidence=evidence)]


def _pre_post(ctx, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = _dates(df, ctx.params["time_col"])
    change = pd.Timestamp(ctx.params["change_date"]).normalize()
    return df[dates < change], df[dates >= change]


@register
class TrackingDivergence(Check):
    id = "tracking_divergence"
    title = "Did a client-side event move without the server-side outcome?"
    kinds = ("metric_change",)
    description = (
        "When a table declares both a client-side event and a server-side outcome, compares "
        "their per-unit rates before and after the change date. Blocks when the client event "
        "fell by half or more while the server outcome stayed within 15%, the signature of a "
        "tracking break rather than a behavior change."
    )

    def applies(self, ctx):
        return super().applies(ctx) and ctx.role("client_event") and ctx.role("server_outcome")

    def run(self, ctx):
        p = ctx.params
        df = ctx.table
        if p.get("segment_col"):
            df = df[df[p["segment_col"]].astype(str) == str(p["segment"])]
        client, server, den = ctx.role("client_event"), ctx.role("server_outcome"), ctx.role("denominator")
        pre, post = _pre_post(ctx, df)

        def ratio(col):
            return float((post[col].sum() / post[den].sum()) / (pre[col].sum() / pre[den].sum()))

        c, s = ratio(client), ratio(server)
        evidence = {"client_rate_ratio": c, "server_rate_ratio": s}
        if c <= 0.5 and 0.85 <= s <= 1.15:
            return [CheckResult(
                self.id, True, BLOCK,
                f"Client-side {client} per {den} changed {signed(c - 1, 0)}, but server-side {server} "
                f"changed only {signed(s - 1, 0)}. Users are still completing the funnel: this looks "
                f"like a tracking break, not a behavior change.",
                evidence=evidence,
            )]
        return [CheckResult(self.id, False, BLOCK,
                            f"Client event {signed(c - 1, 0)} and server outcome {signed(s - 1, 0)} move together.",
                            evidence=evidence)]


@register
class DenominatorShift(Check):
    id = "denominator_shift"
    title = "Did the rate move because the numerator changed, or the denominator?"
    kinds = ("metric_change",)
    description = (
        "Splits the log change in the rate into its numerator and denominator parts "
        "(log rate ratio = log numerator ratio - log denominator ratio). Blocks when the rate "
        "moved in the claimed direction but the numerator explains less than half of that move, "
        "or its change is not significant on a day-level Welch test, and lists any same-day "
        "changes from a changelog table."
    )
    min_numerator_share = 0.5

    def applies(self, ctx):
        return super().applies(ctx) and ctx.params.get("numerator") and ctx.params.get("denominator")

    def run(self, ctx):
        p = ctx.params
        df = ctx.table
        if p.get("segment_col"):
            df = df[df[p["segment_col"]].astype(str) == str(p["segment"])]
        num, den = p["numerator"], p["denominator"]
        pre, post = _pre_post(ctx, df)
        rate_ratio = float((post[num].sum() / post[den].sum()) / (pre[num].sum() / pre[den].sum()))
        num_ratio = float(post[num].mean() / pre[num].mean())
        den_ratio = float(post[den].mean() / pre[den].mean())
        _, pval = welch_test(pre[num].astype(float), post[num].astype(float))
        share = math.log(num_ratio) / math.log(rate_ratio) if rate_ratio != 1 else 0.0
        same_day = []
        for _, log, spec in ctx.other_tables_with_roles("time", "event"):
            day = _dates(log, spec.roles["time"]) == pd.Timestamp(p["change_date"]).normalize()
            same_day += log.loc[day, spec.roles["event"]].astype(str).tolist()
        evidence = {"rate_ratio": rate_ratio, "numerator_ratio": num_ratio, "numerator_p": pval,
                    "denominator_ratio": den_ratio, "numerator_share": share, "same_day_changes": same_day}
        rate_moved = ctx.sign * (rate_ratio - 1) > 0
        numerator_moved = (ctx.sign * (num_ratio - 1) > 0 and pval < ctx.alpha
                           and share >= self.min_numerator_share)
        if rate_moved and not numerator_moved:
            changes = f" Same-day changes: {'; '.join(same_day)}." if same_day else ""
            return [CheckResult(
                self.id, True, BLOCK,
                f"The rate moved {signed(rate_ratio - 1)}, but {num} changed only {signed(num_ratio - 1)} "
                f"per day (p={fmt_p(pval)}, {max(share, 0):.0%} of the move) while {den} changed "
                f"{signed(den_ratio - 1)}. The jump comes from the denominator.{changes}",
                basis="evidence_test", p_value=pval, evidence=evidence,
            )]
        return [CheckResult(self.id, False, BLOCK,
                            f"{num} changed {signed(num_ratio - 1)} per day (p={fmt_p(pval)}); "
                            f"{den} changed {signed(den_ratio - 1)}.", evidence=evidence)]

"""Aggregation traps: the total tells a different story than its parts."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ...stats import two_proportion_test
from .._util import jitter_shares, pct, pick, pick_n
from ..base import Draft, Trap, register
from ..domains import DOMAINS
from ..model import Claim, TableSpec, Verdict

S, NS, INC = Verdict.SUPPORTED, Verdict.NOT_SUPPORTED, Verdict.INCONCLUSIVE

GROUPINGS = (
    ("onboarding_flow", "onboarding flow", ("Flow A", "Flow B")),
    ("landing_page", "landing page", ("Page A", "Page B")),
    ("signup_flow", "signup flow", ("Signup v1", "Signup v2")),
)


@register
class SimpsonsParadox(Trap):
    id = "simpsons_paradox"
    family = "aggregation"
    title = "Simpson's paradox"
    summary = (
        "The group that wins overall loses inside every segment, because the two groups "
        "draw very different segment mixes."
    )
    mechanism = (
        "The overall comparison reverses inside every {dim_label} segment: {claimed} converts worse "
        "than {other} in each one and only wins overall because most of its traffic comes from the "
        "highest-converting segment."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S})
    templates = (
        "Quick readout on the {noun} comparison: {a} converts at {ra} vs {rb} for {b} over the last "
        "30 days. {a} is clearly the better {noun}, so let's standardize on it.",
        "{a} is beating {b} on {conv}: {ra} vs {rb}. Proposing we move everyone to {a}.",
        "Pulled {conv} by {noun}. {a}: {ra}. {b}: {rb}. {a} wins and should become the default.",
    )

    def eligible_domains(self):
        return [d for d in DOMAINS if any(len(v) >= 3 for v in d.dims.values())]

    def scenario(self, rng, domain):
        dim = pick(rng, sorted(k for k, v in domain.dims.items() if len(v) >= 3))
        col, noun, labels = pick(rng, GROUPINGS)
        claimed, other = labels if rng.random() < 0.5 else labels[::-1]
        base = sorted(
            (rng.uniform(0.24, 0.34), rng.uniform(0.11, 0.17), rng.uniform(0.035, 0.065)),
            reverse=True,
        )
        return {
            "domain": domain,
            "dim": dim,
            "dim_label": dim.replace("_", " "),
            "segments": pick_n(rng, domain.dims[dim], 3),
            "group_col": col,
            "noun": noun,
            "claimed": claimed,
            "other": other,
            "base": base,
            "gap": float(rng.uniform(0.02, 0.035)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        claimed, other = s["claimed"], s["other"]
        lifted = [b + s["gap"] for b in s["base"]]
        if planted:
            alloc = {claimed: jitter_shares(rng, [0.6, 0.3, 0.1]), other: jitter_shares(rng, [0.1, 0.3, 0.6])}
            rates = {claimed: s["base"], other: lifted}
        else:
            alloc = {g: jitter_shares(rng, [0.34, 0.33, 0.33]) for g in (claimed, other)}
            rates = {claimed: lifted, other: s["base"]}
        rows = []
        for g in (claimed, other):
            total = int(rng.integers(12_000, 20_001))
            for seg, share, r in zip(s["segments"], alloc[g], rates[g]):
                n = int(round(total * share))
                rows.append({s["group_col"]: g, s["dim"]: seg, d.unit: n, d.success: int(rng.binomial(n, r))})
        df = pd.DataFrame(rows)
        agg = df.groupby(s["group_col"])[[d.unit, d.success]].sum()
        ra = agg.loc[claimed, d.success] / agg.loc[claimed, d.unit]
        rb = agg.loc[other, d.success] / agg.loc[other, d.unit]
        text = self.templates[s["template"]].format(
            a=claimed, b=other, ra=pct(ra), rb=pct(rb), conv=d.conversion, noun=s["noun"]
        )
        table = "conversion_by_segment"
        spec = TableSpec(
            description=f"Last 30 days of {d.unit} and {d.success}, by {s['noun']} and {s['dim_label']}.",
            columns={
                s["group_col"]: f"Which {s['noun']} the {d.users} saw",
                s["dim"]: f"{s['dim_label'].capitalize()} of the {d.users}",
                d.unit: f"Number of {d.unit.replace('_', ' ')}",
                d.success: f"Number of {d.success.replace('_', ' ')}",
            },
            roles={"group": s["group_col"], "segments": [s["dim"]], "denominator": d.unit, "numerator": d.success},
        )
        params = {
            "group_col": s["group_col"],
            "dim": s["dim"],
            "claimed": claimed,
            "other": other,
            "unit": d.unit,
            "success": d.success,
        }
        claim = Claim(
            text=text,
            kind="group_comparison",
            table=table,
            params={"group_col": s["group_col"], "winner": claimed, "loser": other,
                    "numerator": d.success, "denominator": d.unit},
        )
        context = [f"The two {s['noun']}s were rolled out to different traffic. This was not a randomized test."]
        return Draft({table: df}, {table: spec}, claim, context, params)

    def check(self, tables, p, planted):
        df = tables["conversion_by_segment"]
        g, dim, u, k = p["group_col"], p["dim"], p["unit"], p["success"]
        problems = []
        if df[u].min() < 300:
            problems.append("a cell has fewer than 300 units")
        agg = df.groupby(g)[[u, k]].sum()
        agg_rate = agg[k] / agg[u]
        if agg_rate[p["claimed"]] - agg_rate[p["other"]] < 0.01:
            problems.append("claimed group does not win overall by at least 1pp")
        seg = df.set_index([g, dim])
        seg_rate = seg[k] / seg[u]
        for value in df[dim].unique():
            gap = seg_rate[(p["claimed"], value)] - seg_rate[(p["other"], value)]
            if planted and gap > -0.005:
                problems.append(f"claimed group is not worse inside segment {value}")
            if not planted and gap < 0.005:
                problems.append(f"claimed group is not better inside segment {value}")
        return problems


def _monday(rng: np.random.Generator) -> date:
    start = date(2025, 2, 3) + timedelta(weeks=int(rng.integers(0, 40)))
    return start - timedelta(days=start.weekday())


@register
class MixShift(Trap):
    id = "mix_shift"
    family = "aggregation"
    title = "Mix shift read as a behavior change"
    summary = (
        "The overall rate fell only because traffic moved toward a low-converting channel. "
        "Conversion inside every channel is unchanged."
    )
    mechanism = (
        "The drop is a composition effect: {low_channel} grew from {share0} to {share1} of "
        "{unit_label} and converts far below the other channels, while conversion inside every "
        "channel is unchanged."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S})
    templates = (
        "{conv} dropped from {r0} to {r1} week over week. People are converting worse than they "
        "were, so something in the funnel broke. Can we find it?",
        "Heads up: {conv} fell from {r0} to {r1} last week. Visitors to the {product} are just converting "
        "less now. We need a funnel deep-dive.",
    )

    def scenario(self, rng, domain):
        channels = pick_n(rng, domain.dims["channel"], 4)
        rates = [
            float(rng.uniform(0.05, 0.07)),
            float(rng.uniform(0.035, 0.05)),
            float(rng.uniform(0.025, 0.035)),
            float(rng.uniform(0.008, 0.014)),
        ]
        return {
            "domain": domain,
            "channels": channels,
            "rates": rates,
            "low_channel": channels[3],
            "unit_label": domain.unit.replace("_", " "),
            "shares0": jitter_shares(rng, [0.36, 0.30, 0.24, 0.10], sd=0.015),
            "total": int(rng.integers(150_000, 250_001)),
            "week1": _monday(rng),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        shares0 = np.asarray(s["shares0"])
        rates0 = np.asarray(s["rates"])
        if planted:
            bump = rng.uniform(0.18, 0.24)
            shares1 = shares0 * (1 - shares0[3] - bump) / (1 - shares0[3])
            shares1[3] = shares0[3] + bump
            rates1 = rates0
        else:
            shares1 = np.asarray(jitter_shares(rng, shares0, sd=0.004, floor=0.01))
            rates1 = rates0 * rng.uniform(0.78, 0.85)
        weeks = [s["week1"], s["week1"] + timedelta(weeks=1)]
        rows = []
        for week, shares, rates in ((weeks[0], shares0, rates0), (weeks[1], shares1, rates1)):
            total = int(s["total"] * rng.uniform(0.97, 1.03))
            for ch, share, r in zip(s["channels"], shares, rates):
                n = int(round(total * share))
                rows.append({"week_starting": week.isoformat(), "channel": ch, d.unit: n,
                             d.success: int(rng.binomial(n, r))})
        df = pd.DataFrame(rows)
        agg = df.groupby("week_starting")[[d.unit, d.success]].sum()
        r0, r1 = (agg[d.success] / agg[d.unit]).tolist()
        low = df[df["channel"] == s["low_channel"]].set_index("week_starting")[d.unit] / agg[d.unit]
        text = self.templates[s["template"]].format(
            conv=d.conversion.capitalize(), r0=pct(r0), r1=pct(r1), product=d.product
        )
        table = "weekly_conversion_by_channel"
        spec = TableSpec(
            description=f"Weekly {d.unit.replace('_', ' ')} and {d.success.replace('_', ' ')} by acquisition channel.",
            columns={
                "week_starting": "Monday of the week (ISO date)",
                "channel": "Acquisition channel",
                d.unit: f"Number of {d.unit.replace('_', ' ')}",
                d.success: f"Number of {d.success.replace('_', ' ')}",
            },
            roles={"time": "week_starting", "segments": ["channel"], "denominator": d.unit, "numerator": d.success},
        )
        params = {
            "unit": d.unit,
            "success": d.success,
            "low": s["low_channel"],
            "share0": pct(float(low.iloc[0]), 0),
            "share1": pct(float(low.iloc[1]), 0),
        }
        claim = Claim(
            text=text,
            kind="period_change",
            table=table,
            params={"period_col": "week_starting", "before": weeks[0].isoformat(),
                    "after": weeks[1].isoformat(), "direction": "decrease",
                    "numerator": d.success, "denominator": d.unit, "asserts": "within_segment_behavior"},
        )
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["weekly_conversion_by_channel"]
        u, k = p["unit"], p["success"]
        weeks = sorted(df["week_starting"].unique())
        problems = []
        agg = df.groupby("week_starting")[[u, k]].sum()
        diff, pval = two_proportion_test(agg.loc[weeks[0], k], agg.loc[weeks[0], u],
                                         agg.loc[weeks[1], k], agg.loc[weeks[1], u])
        if diff > -0.004 or pval > 0.01:
            problems.append("overall rate does not drop clearly")
        shares = df.pivot(index="channel", columns="week_starting", values=u)
        shares = shares / shares.sum()
        for ch, grp in df.groupby("channel"):
            g = grp.set_index("week_starting")
            cdiff, cp = two_proportion_test(g.loc[weeks[0], k], g.loc[weeks[0], u],
                                            g.loc[weeks[1], k], g.loc[weeks[1], u])
            base = g.loc[weeks[0], k] / g.loc[weeks[0], u]
            if planted and cp < 0.05:
                problems.append(f"channel {ch} rate changed significantly")
            if not planted and (cdiff / base > -0.10 or cp > 0.05):
                problems.append(f"channel {ch} rate did not drop clearly")
        share_change = (shares[weeks[1]] - shares[weeks[0]]).abs()
        if planted and share_change[p["low"]] < 0.12:
            problems.append("low-converting channel share did not grow enough")
        if not planted and share_change.max() > 0.02:
            problems.append("channel mix moved in the control")
        return problems


CAMPAIGNS = (
    "Spring Sale", "Brand Search", "Retargeting", "Lookalike Audiences", "Newsletter Promo",
    "Partner Launch", "Back to School", "Holiday Teaser", "Creator Collab", "Win-back",
    "Free Trial Push", "Webinar Series",
)
MONTH_PAIRS = (("July", "August"), ("August", "September"), ("April", "May"), ("October", "November"))


@register
class AverageOfRatios(Trap):
    id = "average_of_ratios"
    family = "aggregation"
    title = "Average of ratios vs ratio of totals"
    summary = (
        "The unweighted average of per-campaign conversion rates rose, but the pooled rate fell, "
        "because tiny campaigns improved while the large ones declined."
    )
    mechanism = (
        "The claim averages per-campaign rates, which weights a campaign with a few hundred clicks "
        "the same as one with tens of thousands. The small campaigns improved, but the large ones "
        "that drive most clicks got worse, so pooled conversion actually fell from {pooled0} to {pooled1}."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S})
    templates = (
        "Average campaign conversion rate went from {m0} in {p0} to {m1} in {p1}. Campaign "
        "performance improved across the program this month.",
        "Our campaigns converted better in {p1}: the average conversion rate across campaigns was "
        "{m1}, up from {m0} in {p0}. Overall campaign performance is up.",
    )

    def scenario(self, rng, domain):
        names = pick_n(rng, CAMPAIGNS, 10)
        return {
            "domain": domain,
            "big": names[:3],
            "small": names[3:],
            "months": pick(rng, MONTH_PAIRS),
            "big_clicks": [int(rng.integers(25_000, 60_001)) for _ in range(3)],
            "small_clicks": [int(rng.integers(400, 1_501)) for _ in range(7)],
            "big_rates": [float(rng.uniform(0.025, 0.045)) for _ in range(3)],
            "small_rates": [float(rng.uniform(0.02, 0.06)) for _ in range(7)],
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        m0, m1 = s["months"]
        rows = []
        for names, clicks, rates, is_big in (
            (s["big"], s["big_clicks"], s["big_rates"], True),
            (s["small"], s["small_clicks"], s["small_rates"], False),
        ):
            for name, n0, r0 in zip(names, clicks, rates):
                if planted:
                    r1 = r0 * rng.uniform(0.72, 0.82) if is_big else r0 + rng.uniform(0.025, 0.05)
                else:
                    r1 = r0 + rng.uniform(0.004, 0.008) if is_big else r0 + rng.uniform(0.008, 0.02)
                n1 = int(n0 * rng.uniform(0.95, 1.05))
                rows.append({"campaign": name, "month": m0, "clicks": n0, "conversions": int(rng.binomial(n0, r0))})
                rows.append({"campaign": name, "month": m1, "clicks": n1, "conversions": int(rng.binomial(n1, r1))})
        df = pd.DataFrame(rows)
        mean0, mean1, pooled0, pooled1 = _campaign_rates(df, m0, m1)
        text = self.templates[s["template"]].format(m0=pct(mean0), m1=pct(mean1), p0=m0, p1=m1)
        table = "campaign_performance"
        spec = TableSpec(
            description="Monthly clicks and conversions per marketing campaign.",
            columns={
                "campaign": "Campaign name",
                "month": "Calendar month",
                "clicks": "Ad clicks",
                "conversions": "Conversions attributed to the campaign",
            },
            roles={"time": "month", "unit_id": "campaign", "denominator": "clicks", "numerator": "conversions"},
        )
        params = {"months": [m0, m1], "pooled0": pct(pooled0), "pooled1": pct(pooled1)}
        claim = Claim(
            text=text,
            kind="period_change",
            table=table,
            params={"period_col": "month", "before": m0, "after": m1, "direction": "increase",
                    "numerator": "conversions", "denominator": "clicks", "aggregation": "mean_of_rates"},
        )
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["campaign_performance"]
        m0, m1 = p["months"]
        mean0, mean1, pooled0, pooled1 = _campaign_rates(df, m0, m1)
        t = df.groupby("month")[["clicks", "conversions"]].sum()
        _, pval = two_proportion_test(t.loc[m0, "conversions"], t.loc[m0, "clicks"],
                                      t.loc[m1, "conversions"], t.loc[m1, "clicks"])
        problems = []
        if planted:
            if mean1 - mean0 < 0.008:
                problems.append("average of rates did not rise enough")
            if pooled1 - pooled0 > -0.002 or pval > 0.05:
                problems.append("pooled rate did not fall clearly")
        else:
            if mean1 - mean0 < 0.005:
                problems.append("average of rates did not rise enough")
            if pooled1 - pooled0 < 0.002 or pval > 0.05:
                problems.append("pooled rate did not rise clearly")
        return problems


def _campaign_rates(df: pd.DataFrame, m0: str, m1: str) -> tuple[float, float, float, float]:
    rate = df["conversions"] / df["clicks"]
    mean0 = float(rate[df["month"] == m0].mean())
    mean1 = float(rate[df["month"] == m1].mean())
    t = df.groupby("month")[["clicks", "conversions"]].sum()
    pooled0 = float(t.loc[m0, "conversions"] / t.loc[m0, "clicks"])
    pooled1 = float(t.loc[m1, "conversions"] / t.loc[m1, "clicks"])
    return mean0, mean1, pooled0, pooled1

"""Selection traps: who ends up in the numbers decides what the numbers say."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from ...stats import two_proportion_test, wilson_interval
from .._util import pct, pick, pick_n
from ..base import Draft, Trap, register
from ..model import Claim, TableSpec, Verdict

S, NS, INC = Verdict.SUPPORTED, Verdict.NOT_SUPPORTED, Verdict.INCONCLUSIVE


@register
class IncompleteCohort(Trap):
    id = "incomplete_cohort"
    family = "selection"
    title = "Comparing a cohort that hasn't finished its window"
    summary = (
        "The newest cohort's day-30 retention is measured only on the users who have had 30 days "
        "since signup, so it isn't comparable to fully observed cohorts yet."
    )
    mechanism = (
        "Only {coverage} of the {latest} cohort has reached 30 days since signup, so its day-30 "
        "retention is measured on its earliest signups alone. Comparing it with fully observed "
        "cohorts is premature."
    )
    trap_verdicts = frozenset({INC, NS})
    control_verdicts = frozenset({S})
    templates = (
        "The {latest} cohort is retaining much better: {r_latest} day-30 retention vs about {r_prev} "
        "for the five cohorts before it. Best cohort we've had.",
        "Day-30 retention for {latest} signups is {r_latest}, up from roughly {r_prev} for earlier "
        "cohorts. Our newest {users} are sticking around a lot more.",
    )

    def scenario(self, rng, domain):
        last = int(rng.integers(6, 11))  # latest cohort month in 2025 (June..October)
        months = [date(2025, m, 1) for m in range(last - 5, last + 1)]
        return {
            "domain": domain,
            "months": months,
            "latest": months[-1].strftime("%Y-%m"),
            "base": float(rng.uniform(0.21, 0.26)),
            "latest_rate": float(rng.uniform(0.30, 0.34)),
            "coverage_target": float(rng.uniform(0.30, 0.50)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        rows = []
        for i, month in enumerate(s["months"]):
            signups = int(rng.integers(3_000, 6_001))
            is_latest = i == len(s["months"]) - 1
            if is_latest:
                elapsed = int(round(signups * s["coverage_target"])) if planted else signups
                retained = int(rng.binomial(elapsed, s["latest_rate"]))
            else:
                elapsed = signups
                retained = int(rng.binomial(signups, s["base"] + rng.uniform(-0.01, 0.01)))
            rows.append({
                "signup_month": month.strftime("%Y-%m"),
                "signups": signups,
                "users_with_30_days_elapsed": elapsed,
                "retained_day_30": retained,
                "day_30_retention": round(retained / elapsed, 4),
            })
        df = pd.DataFrame(rows)
        latest_start = s["months"][-1]
        days_into = int(round(s["coverage_target"] * 30)) if planted else 40
        pull = latest_start + timedelta(days=30 + days_into)
        prev = df.iloc[:-1]
        r_prev = prev["retained_day_30"].sum() / prev["users_with_30_days_elapsed"].sum()
        r_latest = df.iloc[-1]["retained_day_30"] / df.iloc[-1]["users_with_30_days_elapsed"]
        text = self.templates[s["template"]].format(
            latest=s["latest"], r_latest=pct(r_latest), r_prev=pct(r_prev), users=d.users
        )
        table = "cohort_retention"
        spec = TableSpec(
            description=f"Day-30 retention by signup month for the {d.product}.",
            columns={
                "signup_month": "Month the user signed up",
                "signups": "Users who signed up that month",
                "users_with_30_days_elapsed": "Users whose signup was at least 30 days before the data pull",
                "retained_day_30": "Of those, users active on day 30 after signup",
                "day_30_retention": "retained_day_30 / users_with_30_days_elapsed",
            },
            roles={"cohort": "signup_month", "population": "signups",
                   "denominator": "users_with_30_days_elapsed", "numerator": "retained_day_30"},
        )
        params = {"latest": s["latest"], "coverage": pct(df.iloc[-1]["users_with_30_days_elapsed"] / df.iloc[-1]["signups"], 0)}
        claim = Claim(
            text=text,
            kind="cohort_comparison",
            table=table,
            params={"cohort_col": "signup_month", "target": s["latest"], "direction": "higher",
                    "numerator": "retained_day_30", "denominator": "users_with_30_days_elapsed"},
        )
        context = [f"Data pulled on {pull.isoformat()}."]
        return Draft({table: df}, {table: spec}, claim, context, params)

    def check(self, tables, p, planted):
        df = tables["cohort_retention"]
        coverage = df["users_with_30_days_elapsed"] / df["signups"]
        prev, latest = df.iloc[:-1], df.iloc[-1]
        diff, pval = two_proportion_test(
            prev["retained_day_30"].sum(), prev["users_with_30_days_elapsed"].sum(),
            latest["retained_day_30"], latest["users_with_30_days_elapsed"],
        )
        problems = []
        if (coverage.iloc[:-1] < 1).any():
            problems.append("an older cohort is not fully observed")
        if diff < 0.04:
            problems.append("latest cohort does not look clearly better")
        if planted and coverage.iloc[-1] > 0.55:
            problems.append("latest cohort is too complete to be a trap")
        if not planted and (coverage.iloc[-1] < 1 or pval > 0.01):
            problems.append("control latest cohort is incomplete or not significantly better")
        return problems


@register
class SelfSelection(Trap):
    id = "self_selection"
    family = "selection"
    title = "Feature adopters were already your best users"
    summary = (
        "Adopters retain far better than non-adopters, but only because already-engaged users "
        "adopt the feature. Within each engagement tier there is no difference."
    )
    mechanism = (
        "Adoption of {feature} is concentrated among users who were already highly engaged before "
        "launch. Within each prior-engagement tier, adopters and non-adopters retain at about the "
        "same rate, so the gap reflects who adopts the feature, not what it does."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S, INC})
    templates = (
        "Users who adopted the {feature} retain at {ra} vs {rn} for non-adopters (90-day). That's "
        "{ratio}x. Rolling the {feature} out to everyone should lift retention a lot.",
        "The {feature} is a retention driver: 90-day retention is {ra} for adopters and {rn} for "
        "everyone else. Let's push adoption hard.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "tier_users": [int(rng.integers(25_000, 35_001)), int(rng.integers(15_000, 22_001)),
                           int(rng.integers(7_000, 11_001))],
            "adoption": [float(rng.uniform(0.04, 0.07)), float(rng.uniform(0.15, 0.25)),
                         float(rng.uniform(0.45, 0.60))],
            "retention": [float(rng.uniform(0.12, 0.18)), float(rng.uniform(0.30, 0.38)),
                          float(rng.uniform(0.60, 0.70))],
            "effect": float(rng.uniform(0.10, 0.14)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        rows = []
        for tier, n, adopt, ret in zip(("Low", "Medium", "High"), s["tier_users"], s["adoption"], s["retention"]):
            n_adopt = int(rng.binomial(n, adopt))
            n_non = n - n_adopt
            ret_adopt = ret + (0 if planted else s["effect"])
            rows.append({"prior_engagement": tier, "used_feature": "yes", "users": n_adopt,
                         "retained_90d": int(rng.binomial(n_adopt, ret_adopt))})
            rows.append({"prior_engagement": tier, "used_feature": "no", "users": n_non,
                         "retained_90d": int(rng.binomial(n_non, ret))})
        df = pd.DataFrame(rows)
        agg = df.groupby("used_feature")[["users", "retained_90d"]].sum()
        ra = agg.loc["yes", "retained_90d"] / agg.loc["yes", "users"]
        rn = agg.loc["no", "retained_90d"] / agg.loc["no", "users"]
        text = self.templates[s["template"]].format(
            feature=s["feature"], ra=pct(ra), rn=pct(rn), ratio=f"{ra / rn:.1f}"
        )
        table = "retention_by_feature_use"
        spec = TableSpec(
            description=f"90-day retention of users active at the {s['feature']} launch, by feature use.",
            columns={
                "prior_engagement": f"Engagement tier in the 30 days before {s['feature']} launched",
                "used_feature": f"Whether the user used {s['feature']} at least once",
                "users": "Users in this group",
                "retained_90d": "Users still active 90 days after launch",
            },
            roles={"treatment": "used_feature", "segments": ["prior_engagement"],
                   "denominator": "users", "numerator": "retained_90d"},
        )
        claim = Claim(
            text=text,
            kind="observational_effect",
            table=table,
            params={"treatment_col": "used_feature", "treated": "yes", "untreated": "no",
                    "numerator": "retained_90d", "denominator": "users", "asserts": "causal"},
        )
        return Draft({table: df}, {table: spec}, claim, [], {"feature": s["feature"]})

    def check(self, tables, p, planted):
        df = tables["retention_by_feature_use"]
        agg = df.groupby("used_feature")[["users", "retained_90d"]].sum()
        ratio = (agg.loc["yes", "retained_90d"] / agg.loc["yes", "users"]) / (
            agg.loc["no", "retained_90d"] / agg.loc["no", "users"]
        )
        problems = []
        if ratio < 1.6:
            problems.append("adopters do not look much better overall")
        for tier, grp in df.groupby("prior_engagement"):
            g = grp.set_index("used_feature")
            gap = g.loc["yes", "retained_90d"] / g.loc["yes", "users"] - g.loc["no", "retained_90d"] / g.loc["no", "users"]
            if planted and abs(gap) > 0.025:
                problems.append(f"tier {tier} shows a real gap in the trap")
            if not planted and gap < 0.06:
                problems.append(f"tier {tier} shows no gap in the control")
        return problems


PARTNERS = ("Partner: Northwind", "Partner: Bluebird", "Partner: Lumen", "Partner: Harbor", "Partner: Kestrel")


@register
class SmallSample(Trap):
    id = "small_sample"
    family = "selection"
    title = "A dramatic rate from a handful of users"
    summary = (
        "One source shows a conversion rate several times the average, but it has so few users "
        "that the interval around it includes the average."
    )
    mechanism = (
        "{target} has only {n} {unit_label}, so its {rate} rate has a 95% interval of roughly {lo} to "
        "{hi}, which includes the {avg} average. The sample is too small to justify moving budget."
    )
    trap_verdicts = frozenset({INC, NS})
    control_verdicts = frozenset({S})
    templates = (
        "{target} converts at {rate}, {ratio}x our {avg} average. We should move budget toward {target}.",
        "Big find: {target} is converting at {rate} against a {avg} average across sources. "
        "Let's double down there.",
    )

    def scenario(self, rng, domain):
        channels = pick_n(rng, domain.dims["channel"], 4)
        partners = pick_n(rng, PARTNERS, 3)
        return {
            "domain": domain,
            "sources": channels + partners,
            "target": partners[0],
            "unit_label": domain.unit.replace("_", " "),
            "overall": float(rng.uniform(0.04, 0.07)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        rows = []
        for src in s["sources"]:
            if src == s["target"]:
                n = int(rng.integers(12, 27)) if planted else int(rng.integers(1_500, 3_001))
                k = int(rng.binomial(n, 3.3 * s["overall"]))
            else:
                n = int(rng.integers(2_000, 15_001))
                k = int(rng.binomial(n, s["overall"] + rng.uniform(-0.01, 0.01)))
            rows.append({"source": src, d.unit: n, d.success: k})
        df = pd.DataFrame(rows)
        avg = df[d.success].sum() / df[d.unit].sum()
        t = df.set_index("source").loc[s["target"]]
        rate = t[d.success] / t[d.unit]
        lo, hi = wilson_interval(int(t[d.success]), int(t[d.unit]))
        text = self.templates[s["template"]].format(
            target=s["target"], rate=pct(rate), avg=pct(avg), ratio=f"{rate / avg:.1f}"
        )
        table = "conversion_by_source"
        spec = TableSpec(
            description=f"Last 30 days of {d.unit.replace('_', ' ')} and {d.success.replace('_', ' ')} by acquisition source.",
            columns={
                "source": "Acquisition channel or partner",
                d.unit: f"Number of {d.unit.replace('_', ' ')}",
                d.success: f"Number of {d.success.replace('_', ' ')}",
            },
            roles={"segments": ["source"], "denominator": d.unit, "numerator": d.success},
        )
        params = {
            "unit": d.unit, "success": d.success, "target": s["target"], "n": int(t[d.unit]),
            "rate": pct(rate), "avg": pct(avg), "lo": pct(lo), "hi": pct(hi),
        }
        claim = Claim(
            text=text,
            kind="segment_outperformance",
            table=table,
            params={"segment_col": "source", "segment": s["target"], "direction": "higher",
                    "numerator": d.success, "denominator": d.unit},
        )
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["conversion_by_source"].set_index("source")
        u, k = p["unit"], p["success"]
        avg = df[k].sum() / df[u].sum()
        n, kk = int(df.loc[p["target"], u]), int(df.loc[p["target"], k])
        lo, _ = wilson_interval(kk, n)
        problems = []
        if kk / n < 2.5 * avg:
            problems.append("target rate is not dramatic enough")
        if planted and (n > 30 or lo >= avg):
            problems.append("trap sample is not small enough to be inconclusive")
        if not planted and lo < 2 * avg:
            problems.append("control sample does not clearly support the claim")
        return problems

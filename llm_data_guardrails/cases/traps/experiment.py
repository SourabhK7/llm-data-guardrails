"""Experiment traps: a valid-looking test result that the test design can't support."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ...stats import srm_p_value, two_proportion_test, welch_test
from .._util import fmt_p, pct, pick, sentence, signed_pct
from ..base import Draft, Trap, register
from ..model import Claim, TableSpec, Verdict

S, NS, INC = Verdict.SUPPORTED, Verdict.NOT_SUPPORTED, Verdict.INCONCLUSIVE

ARM_COLUMNS = {
    "arm": "Experiment arm",
    "users": "Users assigned to the arm",
    "conversions": "Users who converted",
}


@register
class SampleRatioMismatch(Trap):
    id = "sample_ratio_mismatch"
    family = "experiment"
    title = "Sample ratio mismatch"
    summary = (
        "The test was configured 50/50 but the arms came out visibly unequal, which means "
        "assignment or logging is broken and the result can't be trusted."
    )
    mechanism = (
        "The test was configured for a 50/50 split but assigned {control_n} vs {treatment_n} users "
        "(chi-square p = {srm_p}). A sample ratio mismatch this large means assignment or logging "
        "is broken, so the lift can't be trusted until the cause is found."
    )
    trap_verdicts = frozenset({NS, INC})
    control_verdicts = frozenset({S})
    templates = (
        "{feature} test is done: treatment {rt} vs control {rc} ({lift} relative, {p}). Shipping it.",
        "Results for {feature}: {rt} conversion in treatment vs {rc} in control, a {lift} relative "
        "lift ({p}). Clear win.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "total": int(rng.integers(80_000, 150_001)),
            "base": float(rng.uniform(0.04, 0.10)),
            "lift": float(rng.uniform(0.06, 0.10)),
            "share": float(rng.uniform(0.52, 0.535)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        nt = int(rng.binomial(s["total"], s["share"] if planted else 0.5))
        nc = s["total"] - nt
        kc = int(rng.binomial(nc, s["base"]))
        kt = int(rng.binomial(nt, s["base"] * (1 + s["lift"])))
        df = pd.DataFrame([
            {"arm": "control", "users": nc, "conversions": kc},
            {"arm": "treatment", "users": nt, "conversions": kt},
        ])
        _, pval = two_proportion_test(kc, nc, kt, nt)
        rc, rt = kc / nc, kt / nt
        text = sentence(self.templates[s["template"]].format(
            feature=s["feature"], rt=pct(rt, 2), rc=pct(rc, 2), lift=signed_pct(rt / rc - 1), p=fmt_p(pval)
        ))
        table = "experiment_results"
        spec = TableSpec(
            description=f"Final results of the {s['feature']} A/B test (14 days).",
            columns=ARM_COLUMNS,
            roles={"arm": "arm", "denominator": "users", "numerator": "conversions",
                   "expected_split": {"control": 0.5, "treatment": 0.5}},
        )
        claim = Claim(
            text=text,
            kind="experiment_effect",
            table=table,
            params={"metric": "conversion", "direction": "increase", "relative_lift": round(rt / rc - 1, 4)},
        )
        context = ["Configured traffic split: 50% control, 50% treatment."]
        params = {"control_n": f"{nc:,}", "treatment_n": f"{nt:,}",
                  "srm_p": f"{srm_p_value([nc, nt], [0.5, 0.5]):.1e}"}
        return Draft({table: df}, {table: spec}, claim, context, params)

    def check(self, tables, p, planted):
        df = tables["experiment_results"].set_index("arm")
        nc, nt = int(df.loc["control", "users"]), int(df.loc["treatment", "users"])
        srm = srm_p_value([nc, nt], [0.5, 0.5])
        diff, pval = two_proportion_test(int(df.loc["control", "conversions"]), nc,
                                         int(df.loc["treatment", "conversions"]), nt)
        problems = []
        if diff <= 0 or pval > 0.01:
            problems.append("treatment does not win clearly")
        if planted and srm > 1e-4:
            problems.append("split is not mismatched enough")
        if not planted and srm < 0.1:
            problems.append("control split is mismatched")
        return problems


@register
class Peeking(Trap):
    id = "peeking"
    family = "experiment"
    title = "Stopping a test the first time it looks significant"
    summary = (
        "The test was checked daily and stopped the first day p fell under 0.05. Repeated looks "
        "inflate false positives, and in this case there is no real effect."
    )
    mechanism = (
        "The team checked the p-value daily and stopped at the first value under 0.05, on day "
        "{stopped_day} of a planned 28. Repeated peeking inflates the false-positive rate far above "
        "5%, so this p-value is not valid evidence. The data was generated with no true effect."
    )
    trap_verdicts = frozenset({INC, NS})
    control_verdicts = frozenset({S})
    trap_templates = (
        "We called the {feature} test on day {day} as soon as it hit significance ({p}). Treatment "
        "wins: {rt} vs {rc}.",
        "{feature} reached stat sig on day {day} ({p}), so we stopped early to save time. "
        "Treatment {rt} vs control {rc}. Shipping.",
    )
    control_templates = (
        "The {feature} test ran its full 28 days. Treatment wins: {rt} vs {rc} ({p}).",
        "{feature} test finished its planned 28 days: treatment {rt} vs control {rc} ({p}). Shipping.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "base": float(rng.uniform(0.05, 0.10)),
            "lift": float(rng.uniform(0.08, 0.12)),
            "template": int(rng.integers(2)),
        }

    def draw(self, rng, s, planted):
        days = 14 if planted else 28
        true_lift = 0.0 if planted else s["lift"]
        rows, cum = [], np.zeros(4, dtype=np.int64)
        stopped = None
        for day in range(1, days + 1):
            nc, nt = int(rng.integers(1_500, 3_001)), int(rng.integers(1_500, 3_001))
            kc = int(rng.binomial(nc, s["base"]))
            kt = int(rng.binomial(nt, s["base"] * (1 + true_lift)))
            cum += np.array([nc, kc, nt, kt])
            diff, pval = two_proportion_test(int(cum[1]), int(cum[0]), int(cum[3]), int(cum[2]))
            rows.append({"day": day, "control_users": int(cum[0]), "control_conversions": int(cum[1]),
                         "treatment_users": int(cum[2]), "treatment_conversions": int(cum[3]),
                         "p_value": round(pval, 4)})
            if planted and day >= 3 and pval < 0.05 and diff > 0:
                stopped = day
                break
        df = pd.DataFrame(rows)
        last = df.iloc[-1]
        rc = last["control_conversions"] / last["control_users"]
        rt = last["treatment_conversions"] / last["treatment_users"]
        templates = self.trap_templates if planted else self.control_templates
        text = sentence(templates[s["template"]].format(
            feature=s["feature"], day=len(df), p=fmt_p(float(last["p_value"])), rt=pct(rt, 2), rc=pct(rc, 2)
        ))
        table = "cumulative_results"
        spec = TableSpec(
            description=f"Cumulative results of the {s['feature']} A/B test, by day (50/50 split).",
            columns={
                "day": "Day of the test",
                "control_users": "Cumulative control users",
                "control_conversions": "Cumulative control conversions",
                "treatment_users": "Cumulative treatment users",
                "treatment_conversions": "Cumulative treatment conversions",
                "p_value": "Two-sided p-value of the cumulative comparison as of that day",
            },
            roles={"time": "day", "cumulative": True, "p_value": "p_value",
                   "arms": {"control": ["control_users", "control_conversions"],
                            "treatment": ["treatment_users", "treatment_conversions"]}},
        )
        context = ["Planned test length: 28 days."]
        if not planted:
            context.append("Results were analyzed once, at the end of the test.")
        claim = Claim(
            text=text,
            kind="experiment_effect",
            table=table,
            params={"metric": "conversion", "direction": "increase", "stopped_day": len(df),
                    "planned_days": 28},
        )
        params = {"true_lift": true_lift, "stopped_day": stopped if planted else 28}
        return Draft({table: df}, {table: spec}, claim, context, params)

    def check(self, tables, p, planted):
        df = tables["cumulative_results"]
        pvals = df["p_value"].to_numpy()
        problems = []
        if planted:
            if p["stopped_day"] is None:
                problems.append("null test never crossed 0.05 in the first 14 days")
            elif (pvals[:-1] < 0.05).any() or pvals[-1] >= 0.05:
                problems.append("stopping day is not the first crossing")
            if p["true_lift"] != 0:
                problems.append("trap must have no true effect")
        else:
            last = df.iloc[-1]
            diff, pval = two_proportion_test(int(last["control_conversions"]), int(last["control_users"]),
                                             int(last["treatment_conversions"]), int(last["treatment_users"]))
            if len(df) != 28 or diff <= 0 or pval > 0.01:
                problems.append("control test did not finish with a clear win")
        return problems


COUNTRIES = (
    "United States", "Canada", "United Kingdom", "Germany", "France", "Spain", "Italy", "Netherlands",
    "Sweden", "Poland", "Brazil", "Mexico", "Argentina", "India", "Japan", "South Korea", "Australia",
    "Singapore", "South Africa", "Turkey",
)


@register
class MultipleComparisons(Trap):
    id = "multiple_comparisons"
    family = "experiment"
    title = "One significant segment out of twenty"
    summary = (
        "A test is flat overall, but one of 20 country breakdowns has p < 0.05. With 20 tests and "
        "no real effect, about one is expected to cross 0.05 by chance."
    )
    mechanism = (
        "{segment} is the only one of 20 countries with p < 0.05, which is what chance produces when "
        "20 segments are tested with no real effect. Its p-value does not survive a "
        "multiple-comparison correction (Bonferroni threshold 0.0025)."
    )
    trap_verdicts = frozenset({NS, INC})
    control_verdicts = frozenset({S, INC})
    templates = (
        "The new {feature} is flat overall, but it's a clear win in {segment}: {lift} relative lift "
        "({p}). Let's launch it in {segment}.",
        "{feature} didn't move the global number, but {segment} shows a {lift} lift ({p}). "
        "Proposing a {segment}-only launch.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "target": pick(rng, COUNTRIES),
            "lift": float(rng.uniform(0.30, 0.40)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        rows = []
        for country in COUNTRIES:
            base = rng.uniform(0.04, 0.08)
            lift = s["lift"] if (not planted and country == s["target"]) else 0.0
            nc, nt = int(rng.integers(2_500, 6_001)), int(rng.integers(2_500, 6_001))
            kc, kt = int(rng.binomial(nc, base)), int(rng.binomial(nt, base * (1 + lift)))
            _, pval = two_proportion_test(kc, nc, kt, nt)
            rows.append({"country": country, "control_users": nc, "control_conversions": kc,
                         "treatment_users": nt, "treatment_conversions": kt,
                         "relative_lift": round((kt / nt) / (kc / nc) - 1, 4), "p_value": round(pval, 4)})
        df = pd.DataFrame(rows)
        sig = df[df["p_value"] < 0.05]
        winner = s["target"] if not planted else (sig.iloc[0]["country"] if len(sig) else s["target"])
        w = df.set_index("country").loc[winner]
        text = sentence(self.templates[s["template"]].format(
            feature=s["feature"], segment=winner, lift=signed_pct(float(w["relative_lift"])),
            p=fmt_p(float(w["p_value"])),
        ))
        table = "results_by_country"
        spec = TableSpec(
            description=f"Results of the {s['feature']} A/B test broken down by country.",
            columns={
                "country": "User country",
                "control_users": "Users in control",
                "control_conversions": "Control users who converted",
                "treatment_users": "Users in treatment",
                "treatment_conversions": "Treatment users who converted",
                "relative_lift": "treatment rate / control rate - 1",
                "p_value": "Two-sided p-value for this country alone",
            },
            roles={"segments": ["country"], "p_value": "p_value",
                   "arms": {"control": ["control_users", "control_conversions"],
                            "treatment": ["treatment_users", "treatment_conversions"]}},
        )
        claim = Claim(
            text=text,
            kind="segment_effect",
            table=table,
            params={"segment_col": "country", "segment": winner, "direction": "increase",
                    "segments_tested": len(df)},
        )
        return Draft({table: df}, {table: spec}, claim, [], {"segment": winner})

    def check(self, tables, p, planted):
        df = tables["results_by_country"]
        tot = df[["control_users", "control_conversions", "treatment_users", "treatment_conversions"]].sum()
        _, overall_p = two_proportion_test(int(tot["control_conversions"]), int(tot["control_users"]),
                                           int(tot["treatment_conversions"]), int(tot["treatment_users"]))
        row = df.set_index("country").loc[p["segment"]]
        others = df[df["country"] != p["segment"]]
        problems = []
        if overall_p < 0.05:
            problems.append("overall result is not flat")
        if row["relative_lift"] <= 0:
            problems.append("claimed segment is not a win")
        if planted:
            if (df["p_value"] < 0.05).sum() != 1 or row["p_value"] >= 0.05 or row["p_value"] < 0.0025:
                problems.append("trap needs exactly one chance-significant segment")
        else:
            if row["p_value"] >= 0.001 or (others["p_value"] < 0.0025).any():
                problems.append("control segment effect is not clearly real")
        return problems


@register
class OutlierDrivenMean(Trap):
    id = "outlier_driven_mean"
    family = "experiment"
    title = "A revenue lift that is really two orders"
    summary = (
        "Mean revenue per user is up, but the whole difference comes from a couple of unusually "
        "large orders. Purchase rates and typical order values didn't change."
    )
    mechanism = (
        "The revenue-per-user lift comes from {n_whales} unusually large treatment orders (largest "
        "{largest}). Excluding the 5 largest orders in each arm, the lift is about {trimmed}, and "
        "purchase rates are the same, so there's no evidence of a broad revenue effect."
    )
    trap_verdicts = frozenset({NS, INC})
    control_verdicts = frozenset({S})
    templates = (
        "{feature} raises revenue per user: ${t} vs ${c} (+{lift}). That's real money. Shipping it.",
        "Revenue readout for {feature}: ${t} per user in treatment vs ${c} in control, up {lift}. "
        "Let's roll it out.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "users": int(rng.integers(40_000, 60_001)),
            "purchase_rate": float(rng.uniform(0.04, 0.06)),
            "mu": float(rng.uniform(3.4, 3.9)),
            "whale_share": float(rng.uniform(0.10, 0.15)),
            "n_whales": int(rng.integers(2, 4)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        n = s["users"]

        def arm_orders(rate, mu):
            buyers = int(rng.binomial(n, rate))
            return rng.lognormal(mu, 0.7, buyers)

        control = arm_orders(s["purchase_rate"], s["mu"])
        if planted:
            treatment = arm_orders(s["purchase_rate"], s["mu"])
            extra = control.sum() * s["whale_share"]
            weights = rng.dirichlet(np.ones(s["n_whales"]))
            treatment[: s["n_whales"]] = extra * weights + 200
        else:
            treatment = arm_orders(s["purchase_rate"] * 1.07, s["mu"] + np.log(1.05))
        control, treatment = np.round(control, 2), np.round(treatment, 2)
        summary_rows, per_user = [], {}
        for arm, orders in (("control", control), ("treatment", treatment)):
            revenue = np.zeros(n)
            revenue[: len(orders)] = orders
            per_user[arm] = revenue
            summary_rows.append({
                "arm": arm, "users": n, "purchasers": len(orders), "revenue": round(float(orders.sum()), 2),
                "revenue_per_user": round(float(orders.sum()) / n, 3),
                "median_order_value": round(float(np.median(orders)), 2),
                "p95_order_value": round(float(np.percentile(orders, 95)), 2),
                "largest_order": round(float(orders.max()), 2),
            })
        summary = pd.DataFrame(summary_rows)
        top = pd.DataFrame(
            [{"arm": "control", "order_value": v} for v in control]
            + [{"arm": "treatment", "order_value": v} for v in treatment]
        ).sort_values("order_value", ascending=False).head(8).reset_index(drop=True)

        def trimmed_rpu(orders):
            return np.sort(orders)[:-5].sum() / n

        trimmed = trimmed_rpu(treatment) / trimmed_rpu(control) - 1
        _, welch_p = welch_test(per_user["control"], per_user["treatment"])
        _, rate_p = two_proportion_test(len(control), n, len(treatment), n)
        c, t = summary["revenue_per_user"].tolist()
        text = sentence(self.templates[s["template"]].format(
            feature=s["feature"], t=f"{t:.2f}", c=f"{c:.2f}", lift=pct(t / c - 1)
        ))
        specs = {
            "revenue_summary": TableSpec(
                description=f"Revenue results of the {s['feature']} A/B test (50/50 split, 21 days).",
                columns={
                    "arm": "Experiment arm",
                    "users": "Users assigned",
                    "purchasers": "Users with at least one order",
                    "revenue": "Total revenue in USD",
                    "revenue_per_user": "revenue / users",
                    "median_order_value": "Median order value in USD",
                    "p95_order_value": "95th percentile order value in USD",
                    "largest_order": "Largest single order in USD",
                },
                roles={"arm": "arm", "denominator": "users", "value": "revenue",
                       "distribution": ["median_order_value", "p95_order_value", "largest_order"]},
            ),
            "largest_orders": TableSpec(
                description="The 8 largest individual orders in the test, across both arms.",
                columns={"arm": "Experiment arm", "order_value": "Order value in USD"},
                roles={"arm": "arm", "value": "order_value"},
            ),
        }
        claim = Claim(
            text=text,
            kind="experiment_effect",
            table="revenue_summary",
            params={"metric": "revenue_per_user", "direction": "increase", "relative_lift": round(t / c - 1, 4)},
        )
        params = {
            "n_whales": s["n_whales"] if planted else 0,
            "largest": f"${treatment.max():,.0f}",
            "trimmed": signed_pct(trimmed),
            "trimmed_lift": round(float(trimmed), 4),
            "welch_p": round(float(welch_p), 6),
            "purchase_rate_p": round(float(rate_p), 4),
        }
        return Draft({"revenue_summary": summary, "largest_orders": top}, specs, claim, [], params)

    def check(self, tables, p, planted):
        summary = tables["revenue_summary"].set_index("arm")
        lift = summary.loc["treatment", "revenue_per_user"] / summary.loc["control", "revenue_per_user"] - 1
        problems = []
        if lift < 0.08:
            problems.append("headline lift is too small")
        if planted:
            if abs(p["trimmed_lift"]) > 0.03 or p["purchase_rate_p"] < 0.05:
                problems.append("trap lift is not explained by a few orders")
        else:
            if p["trimmed_lift"] < 0.06 or p["welch_p"] > 0.01:
                problems.append("control lift is not broad and significant")
        return problems

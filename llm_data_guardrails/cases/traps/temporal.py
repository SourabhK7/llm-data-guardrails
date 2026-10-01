"""Temporal traps: timing creates an effect that isn't there."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from ...stats import srm_p_value, two_proportion_test
from .._util import fmt_p, pct, pick, sentence, signed_pct
from ..base import Draft, Trap, register
from ..model import Claim, TableSpec, Verdict

S, NS, INC = Verdict.SUPPORTED, Verdict.NOT_SUPPORTED, Verdict.INCONCLUSIVE

# (id prefix, plural noun, metric label, mean, true-score sd, monthly noise sd)
UNIT_KINDS = (
    ("SA", "support agents", "CSAT score", 78.0, 3.5, 6.0),
    ("ST", "store locations", "NPS", 42.0, 5.0, 8.0),
    ("AM", "account managers", "customer health score", 70.0, 4.0, 6.0),
)
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


@register
class RegressionToMean(Trap):
    id = "regression_to_mean"
    family = "temporal"
    title = "Regression to the mean after picking the worst performers"
    summary = (
        "The lowest scorers were coached and improved, but the lowest scorers always improve the "
        "next month because extreme scores are partly noise."
    )
    mechanism = (
        "The coached {units} were chosen because they scored lowest, and extreme low scores are "
        "partly noise, so they tend to rise the next month on their own. Before the program "
        "existed, the bottom 10 rose by about {history} points on average, close to the coached "
        "group's gain."
    )
    trap_verdicts = frozenset({NS, INC})
    control_verdicts = frozenset({S, INC})
    templates = (
        "The 10 lowest-scoring {units} in {m1} went through coaching, and their {metric} rose by "
        "{gain} points on average in {m2}. Coaching works. Let's roll it out to everyone.",
        "Coaching results are in: the bottom 10 {units} from {m1} improved their {metric} by "
        "{gain} points in {m2}. Strong evidence the program pays off.",
    )

    def scenario(self, rng, domain):
        prefix, units, metric, mu, sd_true, noise = pick(rng, UNIT_KINDS)
        m = int(rng.integers(3, 12))
        return {
            "domain": domain,
            "prefix": prefix,
            "units": units,
            "metric": metric,
            "mu": mu,
            "sd_true": sd_true,
            "noise": noise,
            "m1": MONTHS[m - 1],
            "m2": MONTHS[m],
            "history_months": [MONTHS[m - 4], MONTHS[m - 3], MONTHS[m - 2]],
            "effect": float(rng.uniform(8.0, 11.0)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        n = 40
        true = rng.normal(s["mu"], s["sd_true"], n)
        m1 = np.round(true + rng.normal(0, s["noise"], n), 1)
        coached = np.zeros(n, dtype=bool)
        coached[np.argsort(m1, kind="stable")[:10]] = True
        effect = 0.0 if planted else s["effect"]
        m2 = np.round(true + rng.normal(0, s["noise"], n) + effect * coached, 1)
        ids = [f"{s['prefix']}-{100 + i}" for i in range(n)]
        col1 = f"score_{s['m1'].lower()}"
        col2 = f"score_{s['m2'].lower()}"
        scores = pd.DataFrame({"id": ids, col1: m1, col2: m2,
                               "coached": np.where(coached, "yes", "no")})
        history_rows = []
        for i, month in enumerate(s["history_months"]):
            a = true + rng.normal(0, s["noise"], n)
            b = true + rng.normal(0, s["noise"], n)
            bottom = np.argsort(a, kind="stable")[:10]
            history_rows.append({
                "period": f"{month} to {MONTHS[(MONTHS.index(month) + 1) % 12]}",
                "bottom_10_avg_change": round(float((b[bottom] - a[bottom]).mean()), 1),
            })
        history = pd.DataFrame(history_rows)
        gain = float((m2[coached] - m1[coached]).mean())
        text = self.templates[s["template"]].format(
            units=s["units"], metric=s["metric"], m1=s["m1"], m2=s["m2"], gain=f"{gain:.1f}"
        )
        specs = {
            "scores": TableSpec(
                description=f"Monthly {s['metric']} for every one of the {n} {s['units']}.",
                columns={
                    "id": "Identifier",
                    col1: f"{s['metric']} in {s['m1']}",
                    col2: f"{s['metric']} in {s['m2']}",
                    "coached": f"Whether they joined the coaching program after {s['m1']}",
                },
                roles={"unit_id": "id", "before": col1, "after": col2, "treatment": "coached",
                       "selection": f"lowest 10 by {col1}"},
            ),
            "bottom_10_history": TableSpec(
                description=(f"Before the coaching program existed: average month-over-month change "
                             f"for the 10 lowest-scoring {s['units']} in each period."),
                columns={"period": "Month pair", "bottom_10_avg_change": "Average change in points"},
                roles={"time": "period", "value": "bottom_10_avg_change"},
            ),
        }
        params = {"col1": col1, "col2": col2,
                  "history": f"{history['bottom_10_avg_change'].mean():.1f}"}
        claim = Claim(
            text=text,
            kind="intervention_effect",
            table="scores",
            params={"treatment_col": "coached", "treated": "yes", "before": col1, "after": col2,
                    "direction": "increase", "selection": "lowest 10 by before-score"},
        )
        return Draft({"scores": scores, "bottom_10_history": history}, specs, claim, [], params)

    def check(self, tables, p, planted):
        scores = tables["scores"]
        hist = float(tables["bottom_10_history"]["bottom_10_avg_change"].mean())
        coached = scores["coached"] == "yes"
        ranked = scores[p["col1"]].rank(method="first")
        problems = []
        if set(scores.index[coached]) != set(scores.index[ranked <= 10]):
            problems.append("coached set is not the bottom 10")
        gain = float((scores.loc[coached, p["col2"]] - scores.loc[coached, p["col1"]]).mean())
        if gain < 3:
            problems.append("coached group did not improve visibly")
        if planted and (gain - hist > 2.5 or hist < 3):
            problems.append("history does not explain the gain")
        if not planted and gain - hist < 5:
            problems.append("control gain is not clearly above the historical bottom-10 rebound")
        return problems


@register
class NoveltyEffect(Trap):
    id = "novelty_effect"
    family = "temporal"
    title = "Novelty effect averaged into a durable lift"
    summary = (
        "A test shows a significant 4-week lift, but the lift decays to nothing by week 3. The "
        "average overstates what launch will deliver."
    )
    mechanism = (
        "The lift is a novelty effect: about {week1} relative in week 1, decaying to {late} over "
        "the last two weeks. The 4-week average overstates any durable effect."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S})
    templates = (
        "Over the 4-week test, {feature} lifted {conv} by {lift} relative ({p}). We should plan on "
        "a durable {lift} lift after launch.",
        "{feature} test wrapped: {lift} relative lift in {conv} over 28 days, {p}. Building the "
        "forecast on a {lift} lift going forward.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "base": float(rng.uniform(0.08, 0.12)),
            "l0": float(rng.uniform(0.16, 0.22)),
            "tau": float(rng.uniform(3.5, 5.0)),
            "flat": float(rng.uniform(0.05, 0.07)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        rows = []
        for day in range(1, 29):
            lift = s["l0"] * np.exp(-(day - 1) / s["tau"]) if planted else s["flat"]
            total = int(rng.integers(18_000, 24_001))
            nt = int(rng.binomial(total, 0.5))
            nc = total - nt
            rows.append({
                "day": day,
                "control_users": nc,
                "control_conversions": int(rng.binomial(nc, s["base"])),
                "treatment_users": nt,
                "treatment_conversions": int(rng.binomial(nt, s["base"] * (1 + lift))),
            })
        df = pd.DataFrame(rows)
        rel, pval = _rel_lift(df)
        w1, _ = _rel_lift(df[df["day"] <= 7])
        late, _ = _rel_lift(df[df["day"] >= 15])
        text = sentence(self.templates[s["template"]].format(
            feature=s["feature"], conv=d.conversion, lift=signed_pct(rel), p=fmt_p(pval),
        ))
        table = "daily_results"
        spec = TableSpec(
            description=f"Daily results from a 28-day randomized test of {s['feature']} (50/50 split).",
            columns={
                "day": "Day of the test (1 = launch day)",
                "control_users": "Users in control that day",
                "control_conversions": "Control users who converted",
                "treatment_users": "Users in treatment that day",
                "treatment_conversions": "Treatment users who converted",
            },
            roles={"time": "day", "arms": {"control": ["control_users", "control_conversions"],
                                           "treatment": ["treatment_users", "treatment_conversions"]}},
        )
        claim = Claim(
            text=text,
            kind="experiment_effect",
            table=table,
            params={"metric": "conversion", "direction": "increase", "asserts": "durable_effect",
                    "relative_lift": round(rel, 4)},
        )
        params = {"feature": s["feature"], "week1": signed_pct(w1), "late": signed_pct(late)}
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["daily_results"]
        rel, pval = _rel_lift(df)
        w1, _ = _rel_lift(df[df["day"] <= 7])
        late, _ = _rel_lift(df[df["day"] >= 15])
        problems = []
        if rel < 0.03 or pval > 0.01:
            problems.append("pooled lift is not clearly significant")
        if srm_p_value([int(df["control_users"].sum()), int(df["treatment_users"].sum())], [0.5, 0.5]) < 0.05:
            problems.append("arms are unbalanced (unintended sample ratio mismatch)")
        if planted and (w1 < 0.09 or not -0.03 <= late <= 0.015):
            problems.append("lift does not decay like a novelty effect")
        if not planted:
            weekly = [_rel_lift(df[(df["day"] > 7 * w) & (df["day"] <= 7 * (w + 1))])[0] for w in range(4)]
            if not all(0.025 <= x <= 0.10 for x in weekly):
                problems.append("control lift is not stable week to week")
        return problems


def _rel_lift(df: pd.DataFrame) -> tuple[float, float]:
    kc, nc = df["control_conversions"].sum(), df["control_users"].sum()
    kt, nt = df["treatment_conversions"].sum(), df["treatment_users"].sum()
    _, pval = two_proportion_test(int(kc), int(nc), int(kt), int(nt))
    return float((kt / nt) / (kc / nc) - 1), pval


# (holiday name, Monday of the holiday week this year, weekday index of the holiday)
HOLIDAY_WEEKS = (
    ("Thanksgiving", date(2025, 11, 24), 3),
    ("Christmas", date(2025, 12, 22), 3),
    ("Independence Day", date(2025, 6, 30), 4),
    ("Memorial Day", date(2025, 5, 26), 0),
    ("Labor Day", date(2025, 9, 1), 0),
)
ORDINARY_WEEKS = (date(2025, 3, 17), date(2025, 6, 9), date(2025, 8, 11), date(2025, 10, 20), date(2025, 4, 28))
WEEKDAY_WEIGHTS = (1.05, 1.08, 1.06, 1.03, 0.97, 0.88, 0.93)


@register
class CalendarEffect(Trap):
    id = "calendar_effect"
    family = "temporal"
    title = "Holiday week blamed on a launch"
    summary = (
        "Signups fell the week a new page shipped, but that week contains a holiday and the same "
        "week last year fell just as much."
    )
    mechanism = (
        "The week of {launch} includes {holiday}. The same week last year dropped by a similar "
        "amount ({last_wow}), so the decline is seasonal and does not point at the new page."
    )
    trap_verdicts = frozenset({NS, INC})
    control_verdicts = frozenset({S, INC})
    templates = (
        "Signups dropped {wow} week over week after the new landing page went live on {launch}. "
        "That's an unusual drop and the timing points straight at the new page.",
        "New landing page shipped {launch} and signups are down {wow} versus the prior week. "
        "I think the page is hurting us. Should we roll back?",
    )

    def scenario(self, rng, domain):
        holiday, monday, offset = pick(rng, HOLIDAY_WEEKS)
        return {
            "domain": domain,
            "holiday": holiday,
            "holiday_monday": monday,
            "holiday_offset": offset,
            "ordinary_monday": pick(rng, ORDINARY_WEEKS),
            "base": float(rng.uniform(2_500, 4_500)),
            "last_year_scale": float(rng.uniform(0.8, 0.9)),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        launch = s["holiday_monday"] if planted else s["ordinary_monday"]
        rows = []
        for year_shift, scale in ((364, s["last_year_scale"]), (0, 1.0)):
            week2 = launch - timedelta(days=year_shift)
            week1 = week2 - timedelta(days=7)
            this_year = year_shift == 0
            if planted:
                dip = rng.uniform(0.78, 0.85)
            else:
                dip = rng.uniform(0.78, 0.85) if this_year else rng.uniform(0.98, 1.03)
            for i in range(14):
                day = week1 + timedelta(days=i)
                mult = 1.0
                if i >= 7:
                    mult = dip
                    if planted and i - 7 == s["holiday_offset"]:
                        mult *= 0.6
                lam = s["base"] * scale * WEEKDAY_WEIGHTS[day.weekday()] * mult
                rows.append({"date": day.isoformat(), "day_of_week": day.strftime("%a"),
                             "signups": int(rng.poisson(lam))})
        df = pd.DataFrame(rows)
        this_wow, last_wow = _wows(df)
        text = self.templates[s["template"]].format(
            wow=pct(-this_wow, 0), launch=f"{launch.strftime('%A, %B')} {launch.day}"
        )
        table = "daily_signups"
        spec = TableSpec(
            description=(f"Daily signups for the {s['domain'].product}: the two weeks around the "
                         f"launch, plus the same weekdays last year."),
            columns={"date": "Calendar date", "day_of_week": "Day of the week",
                     "signups": "New signups that day"},
            roles={"time": "date", "value": "signups"},
        )
        params = {"launch": launch.isoformat(), "holiday": s["holiday"] if planted else None,
                  "last_wow": signed_pct(last_wow, 0)}
        claim = Claim(
            text=text,
            kind="metric_change",
            table=table,
            params={"time_col": "date", "value_col": "signups", "change_date": launch.isoformat(),
                    "direction": "decrease", "attributed_to": "landing page launch"},
        )
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        this_wow, last_wow = _wows(tables["daily_signups"])
        problems = []
        if this_wow > -0.12:
            problems.append("this year's drop is too small")
        if planted and (last_wow > -0.10 or abs(this_wow - last_wow) > 0.07):
            problems.append("last year's same week does not show a matching dip")
        if not planted and abs(last_wow) > 0.04:
            problems.append("last year's same week is not flat in the control")
        return problems


def _wows(df: pd.DataFrame) -> tuple[float, float]:
    """Week-over-week change for this year and last year (rows are last year first)."""
    s = df["signups"].to_numpy()
    last, this = s[:14], s[14:]
    return float(this[7:].sum() / this[:7].sum() - 1), float(last[7:].sum() / last[:7].sum() - 1)

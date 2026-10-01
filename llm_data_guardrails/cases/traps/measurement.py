"""Measurement traps: the metric moved because the measurement changed."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .._util import pct, pick, sentence
from ..base import Draft, Trap, register
from ..domains import DOMAINS
from ..model import Claim, TableSpec, Verdict

S, NS, INC = Verdict.SUPPORTED, Verdict.NOT_SUPPORTED, Verdict.INCONCLUSIVE


def _start_date(rng) -> date:
    return date(2025, 1, 6) + timedelta(days=int(rng.integers(0, 280)))


def _ratio(df: pd.DataFrame, col: str, split: str) -> float:
    pre = df[df["date"] < split][col].mean()
    post = df[df["date"] >= split][col].mean()
    return float(post / pre)


@register
class InstrumentationBreak(Trap):
    id = "instrumentation_break"
    family = "measurement"
    title = "A tracking break read as a behavior change"
    summary = (
        "A client-side funnel event collapsed on one platform, but server-side outcomes on that "
        "platform didn't move. Users didn't change; the tracking did."
    )
    mechanism = (
        "Only the client-side {mid_label} event dropped on {platform}; server-side {success_label} "
        "on {platform} stayed flat. People are still completing the funnel, so this is a tracking "
        "break, not a change in user behavior, and there is no revenue impact."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S})
    templates = (
        "{platform} {mid_label} rate collapsed starting {date}: from {r0} to {r1} of {unit_label}. "
        "{platform} {users} basically stopped {mid_verb}. Something in the {platform} experience "
        "broke and it's costing us revenue.",
        "Alert: since {date}, {mid_label} on {platform} is down from {r0} to {r1} of {unit_label}. "
        "{users} on {platform} are clearly having trouble and we're losing {success_label}.",
    )

    def eligible_domains(self):
        return [d for d in DOMAINS if len(d.dims.get("platform", ())) >= 2]

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "platform": pick(rng, domain.dims["platform"]),
            "start": _start_date(rng),
            "mid_label": domain.mid_label,
            "success_label": domain.success.replace("_", " "),
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        break_day = s["start"] + timedelta(days=7)
        rows = []
        for plat in d.dims["platform"]:
            base = rng.uniform(20_000, 60_000)
            mid_rate, success_rate = rng.uniform(0.08, 0.15), rng.uniform(0.02, 0.04)
            mid_drop = rng.uniform(0.04, 0.10) if planted else rng.uniform(0.04, 0.12)
            success_drop = 1.0 if planted else rng.uniform(0.04, 0.12)
            for i in range(14):
                day = s["start"] + timedelta(days=i)
                broken = plat == s["platform"] and day >= break_day
                sessions = int(base * rng.uniform(0.95, 1.05))
                rows.append({
                    "date": day.isoformat(),
                    "platform": plat,
                    d.unit: sessions,
                    d.mid_event: int(rng.binomial(sessions, mid_rate * (mid_drop if broken else 1))),
                    d.success: int(rng.binomial(sessions, success_rate * (success_drop if broken else 1))),
                })
        df = pd.DataFrame(rows)
        plat_df = df[df["platform"] == s["platform"]]
        split = break_day.isoformat()
        pre, post = plat_df[plat_df["date"] < split], plat_df[plat_df["date"] >= split]
        r0 = pre[d.mid_event].sum() / pre[d.unit].sum()
        r1 = post[d.mid_event].sum() / post[d.unit].sum()
        text = sentence(self.templates[s["template"]].format(
            platform=s["platform"], mid_label=d.mid_label, mid_verb=d.mid_verb, users=d.users,
            date=split, r0=pct(r0), r1=pct(r1), unit_label=d.unit.replace("_", " "),
            success_label=s["success_label"],
        ))
        table = "daily_funnel_by_platform"
        spec = TableSpec(
            description="Daily funnel counts by platform.",
            columns={
                "date": "Calendar date",
                "platform": "Client platform",
                d.unit: f"{d.unit.replace('_', ' ').capitalize()}, from server request logs",
                d.mid_event: f"{d.mid_label.capitalize()} events, from the client-side analytics SDK",
                d.success: f"{d.success.replace('_', ' ').capitalize()}, from the production database",
            },
            roles={"time": "date", "segments": ["platform"], "denominator": d.unit,
                   "client_event": d.mid_event, "server_outcome": d.success},
        )
        params = {"mid": d.mid_event, "success": d.success, "unit": d.unit, "split": split}
        claim = Claim(
            text=text,
            kind="metric_change",
            table=table,
            params={"time_col": "date", "change_date": split, "segment_col": "platform",
                    "segment": s["platform"], "numerator": d.mid_event, "denominator": d.unit,
                    "direction": "decrease", "asserts": "user_behavior_and_revenue"},
        )
        return Draft({table: df}, {table: spec}, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["daily_funnel_by_platform"]
        plats = df["platform"].unique()
        # The broken platform is the one whose client event collapsed.
        ratios = {pl: _ratio(df[df["platform"] == pl], p["mid"], p["split"]) for pl in plats}
        broken = min(ratios, key=ratios.get)
        sub = df[df["platform"] == broken]
        success_ratio = _ratio(sub, p["success"], p["split"])
        problems = []
        if ratios[broken] > 0.15:
            problems.append("client event did not collapse")
        if any(r < 0.9 for pl, r in ratios.items() if pl != broken):
            problems.append("another platform's client event moved")
        if planted and success_ratio < 0.9:
            problems.append("server outcome moved in the trap")
        if not planted and success_ratio > 0.2:
            problems.append("server outcome did not fall in the control")
        return problems


UNRELATED_CHANGES = (
    "Updated email footer links",
    "Rotated API keys for the payments vendor",
    "Added FAQ page",
    "Warehouse maintenance window (no data loss)",
    "Refreshed homepage hero image",
)


@register
class DenominatorChange(Trap):
    id = "denominator_change"
    family = "measurement"
    title = "A rate jump caused by a smaller denominator"
    summary = (
        "Conversion jumped the day a feature shipped, but a pipeline filter went live the same day "
        "and removed junk traffic from the denominator. The numerator didn't change."
    )
    mechanism = (
        "A pipeline change ({filter_change}) went live the same day and cut {unit_label} by about "
        "{drop}. {success_label} did not change, so the higher rate comes from a smaller "
        "denominator, not from {feature}."
    )
    trap_verdicts = frozenset({NS})
    control_verdicts = frozenset({S, INC})
    templates = (
        "{conv} jumped from {r0} to {r1} the day {feature} shipped ({date}). Big win for the "
        "{feature} team.",
        "Since {feature} launched on {date}, {conv} is up from {r0} to {r1}. That's the biggest "
        "single-day improvement we've seen.",
    )

    def scenario(self, rng, domain):
        return {
            "domain": domain,
            "feature": pick(rng, domain.features),
            "filter_change": domain.filter_change,
            "unit_label": domain.unit.replace("_", " "),
            "success_label": sentence(domain.success.replace("_", " ")),
            "start": _start_date(rng),
            "base": float(rng.uniform(50_000, 80_000)),
            "rate": float(rng.uniform(0.02, 0.04)),
            "unrelated": [pick(rng, UNRELATED_CHANGES[:3]), pick(rng, UNRELATED_CHANGES[3:])],
            "template": int(rng.integers(len(self.templates))),
        }

    def draw(self, rng, s, planted):
        d = s["domain"]
        change_day = s["start"] + timedelta(days=10)
        session_mult = rng.uniform(0.70, 0.78) if planted else 1.0
        success_mult = 1.0 if planted else rng.uniform(1.25, 1.35)
        rows = []
        for i in range(20):
            day = s["start"] + timedelta(days=i)
            after = day >= change_day
            real = int(s["base"] * rng.uniform(0.96, 1.04))
            recorded = int(real * (session_mult if after else 1.0))
            successes = int(rng.binomial(real, s["rate"] * (success_mult if after else 1.0)))
            rows.append({"date": day.isoformat(), d.unit: recorded, d.success: successes,
                         "conversion_rate": round(successes / recorded, 4)})
        df = pd.DataFrame(rows)
        log = [
            {"date": (s["start"] + timedelta(days=3)).isoformat(), "change": s["unrelated"][0]},
            {"date": change_day.isoformat(), "change": f"{sentence(s['feature'])} shipped"},
        ]
        if planted:
            log.append({"date": change_day.isoformat(), "change": s["filter_change"]})
        log.append({"date": (s["start"] + timedelta(days=14)).isoformat(), "change": s["unrelated"][1]})
        changelog = pd.DataFrame(log)
        split = change_day.isoformat()
        pre, post = df[df["date"] < split], df[df["date"] >= split]
        r0 = pre[d.success].sum() / pre[d.unit].sum()
        r1 = post[d.success].sum() / post[d.unit].sum()
        text = sentence(self.templates[s["template"]].format(
            conv=d.conversion, r0=pct(r0, 2), r1=pct(r1, 2), feature=s["feature"], date=split
        ))
        specs = {
            "daily_metrics": TableSpec(
                description=f"Daily {d.unit.replace('_', ' ')}, {d.success.replace('_', ' ')} and conversion rate.",
                columns={
                    "date": "Calendar date",
                    d.unit: f"Number of {d.unit.replace('_', ' ')}",
                    d.success: f"Number of {d.success.replace('_', ' ')}",
                    "conversion_rate": f"{d.success} / {d.unit}",
                },
                roles={"time": "date", "denominator": d.unit, "numerator": d.success},
            ),
            "changelog": TableSpec(
                description="Product and data-platform changes deployed in this period.",
                columns={"date": "Deploy date", "change": "What changed"},
                roles={"time": "date", "event": "change"},
            ),
        }
        params = {"unit": d.unit, "success": d.success, "split": split,
                  "drop": pct(1 - _ratio(df, d.unit, split), 0)}
        claim = Claim(
            text=text,
            kind="metric_change",
            table="daily_metrics",
            params={"time_col": "date", "change_date": split, "numerator": d.success,
                    "denominator": d.unit, "direction": "increase", "attributed_to": s["feature"]},
        )
        return Draft({"daily_metrics": df, "changelog": changelog}, specs, claim, [], params)

    def check(self, tables, p, planted):
        df = tables["daily_metrics"]
        unit_ratio = _ratio(df, p["unit"], p["split"])
        success_ratio = _ratio(df, p["success"], p["split"])
        rate_ratio = _ratio(df, "conversion_rate", p["split"])
        problems = []
        if rate_ratio < 1.2:
            problems.append("conversion rate did not jump")
        if planted and (unit_ratio > 0.8 or not 0.95 <= success_ratio <= 1.06):
            problems.append("trap jump is not purely a denominator change")
        if not planted and (not 0.95 <= unit_ratio <= 1.05 or success_ratio < 1.2):
            problems.append("control jump is not a real numerator increase")
        return problems

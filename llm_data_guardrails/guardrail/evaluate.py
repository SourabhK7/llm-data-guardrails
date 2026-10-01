"""Measure the guardrail on the trap suite.

A guardrail that ships without its own error rates is a liability: you can't tell a
useful warning from noise. This module reports, per trap:

- catch rate: share of trap cases the guardrail flags (warn or block)
- false-alarm rate: share of matched control cases it flags, where the claim is true

with Wilson 95% intervals. Every applicable check runs on every case, so a check that
misfires on another trap's control shows up as a false alarm there.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from ..cases.model import Case
from ..stats import wilson_interval
from .core import Guardrail


@dataclass
class TrapStats:
    trap_id: str
    family: str
    n_trap: int = 0
    caught: int = 0
    blocked: int = 0
    n_control: int = 0
    false_alarms: int = 0
    unverified: int = 0
    fired_on_traps: Counter = field(default_factory=Counter)
    fired_on_controls: Counter = field(default_factory=Counter)


def evaluate_guardrail(cases: Iterable[Case], guardrail: Guardrail | None = None) -> dict[str, TrapStats]:
    guardrail = guardrail or Guardrail()
    stats: dict[str, TrapStats] = {}
    for case in cases:
        s = stats.setdefault(case.trap_id, TrapStats(case.trap_id, case.family))
        report = guardrail.evaluate_case(case)
        flagged = report.decision in ("warn", "block")
        fired = {r.check for r in report.flags}
        if report.decision == "unverified":
            s.unverified += 1
        if case.planted:
            s.n_trap += 1
            s.caught += flagged
            s.blocked += report.decision == "block"
            s.fired_on_traps.update(fired)
        else:
            s.n_control += 1
            s.false_alarms += flagged
            s.fired_on_controls.update(fired)
    return stats


def _rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson_interval(k, n)
    return f"{k / n:.0%} ({lo:.0%} to {hi:.0%})"


def render_markdown(stats: dict[str, TrapStats]) -> str:
    rows = sorted(stats.values(), key=lambda s: (s.family, s.trap_id))
    lines = [
        "| Family | Trap | Catch rate (95% CI) | False-alarm rate (95% CI) | Checks that fired on traps |",
        "|---|---|---|---|---|",
    ]
    for s in rows:
        fired = ", ".join(f"`{c}`" for c, _ in s.fired_on_traps.most_common()) or "none"
        lines.append(f"| {s.family} | `{s.trap_id}` | {_rate(s.caught, s.n_trap)} | "
                     f"{_rate(s.false_alarms, s.n_control)} | {fired} |")
    caught = sum(s.caught for s in rows)
    n_trap = sum(s.n_trap for s in rows)
    alarms = sum(s.false_alarms for s in rows)
    n_control = sum(s.n_control for s in rows)
    lines.append(f"| **all** | | **{_rate(caught, n_trap)}** | **{_rate(alarms, n_control)}** | |")
    unverified = sum(s.unverified for s in rows)
    if unverified:
        lines.append(f"\n{unverified} case(s) were unverified (no applicable check or invalid claim).")
    misfires = Counter()
    for s in rows:
        misfires.update(s.fired_on_controls)
    if misfires:
        lines.append("\nFalse alarms by check: " + ", ".join(f"`{c}` x{n}" for c, n in misfires.most_common()))
    return "\n".join(lines)

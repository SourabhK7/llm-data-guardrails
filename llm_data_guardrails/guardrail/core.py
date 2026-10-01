"""Guardrail core: check registry, results, reports, and multiple-testing control."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, Iterable

import pandas as pd

from ..cases.model import Case, Claim, TableSpec
from ..stats import bh_adjust
from .claims import direction_sign, validate_claim

BLOCK, WARN, INFO = "block", "warn", "info"


@dataclass
class CheckResult:
    """Outcome of one check.

    basis:
      "problem_test"  flags when a hypothesis test finds a problem (null = no problem).
                      These are Benjamini-Hochberg adjusted together within one report.
      "evidence_test" flags when the evidence for the claim doesn't clear the bar.
      "rule"          flags on a documented deterministic threshold.
    """

    check: str
    flagged: bool
    severity: str
    message: str
    basis: str = "rule"
    p_value: float | None = None
    p_adjusted: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardrailReport:
    claim_kind: str
    results: list[CheckResult]
    errors: list[str] = field(default_factory=list)

    @property
    def flags(self) -> list[CheckResult]:
        return [r for r in self.results if r.flagged]

    @property
    def decision(self) -> str:
        """pass | warn | block | unverified."""
        if self.errors or not any(r.severity != INFO for r in self.results):
            return "unverified"
        if any(r.severity == BLOCK for r in self.flags):
            return "block"
        if self.flags:
            return "warn"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "claim_kind": self.claim_kind,
            "errors": self.errors,
            "results": [asdict(r) for r in self.results],
        }

    def render(self) -> str:
        lines = [f"Decision: {self.decision.upper()}  (claim kind: {self.claim_kind})"]
        lines += [f"  ! {e}" for e in self.errors]
        for r in sorted(self.results, key=lambda r: (not r.flagged, r.check)):
            if r.severity == INFO:
                mark = "info "
            elif r.flagged:
                mark = "BLOCK" if r.severity == BLOCK else "WARN "
            else:
                mark = "ok   "
            lines.append(f"  [{mark}] {r.check}: {r.message}")
        return "\n".join(lines)


@dataclass
class ClaimContext:
    claim: Claim
    tables: dict[str, pd.DataFrame]
    specs: dict[str, TableSpec]
    context: list[str]
    alpha: float
    srm_alpha: float

    @property
    def table(self) -> pd.DataFrame:
        return self.tables[self.claim.table]

    @property
    def params(self) -> dict[str, Any]:
        return self.claim.params

    @property
    def sign(self) -> int:
        return direction_sign(self.params.get("direction"))

    def role(self, name: str, table: str | None = None) -> Any:
        spec = self.specs.get(table or self.claim.table)
        return spec.roles.get(name) if spec else None

    def other_tables_with_roles(self, *roles: str) -> list[tuple[str, pd.DataFrame, TableSpec]]:
        return [
            (name, self.tables[name], spec)
            for name, spec in self.specs.items()
            if name != self.claim.table and all(r in spec.roles for r in roles)
        ]

    def context_number(self, pattern: str) -> float | None:
        for line in self.context:
            m = re.search(pattern, line, flags=re.IGNORECASE)
            if m:
                return float(m.group(1))
        return None


class Check(ABC):
    id: ClassVar[str]
    title: ClassVar[str]
    kinds: ClassVar[tuple[str, ...]]
    description: ClassVar[str]

    def applies(self, ctx: ClaimContext) -> bool:
        return ctx.claim.kind in self.kinds

    @abstractmethod
    def run(self, ctx: ClaimContext) -> list[CheckResult]:
        ...


CHECKS: dict[str, Check] = {}


def register(cls: type[Check]) -> type[Check]:
    CHECKS[cls.id] = cls()
    return cls


class Guardrail:
    """Run the checks that apply to a structured claim and return a report.

    The guardrail only receives what a production system would have: the claim, the
    tables it refers to, their data dictionaries, and free-text context. It never sees
    ground truth or how a case was generated.
    """

    def __init__(self, alpha: float = 0.05, srm_alpha: float = 0.001, checks: Iterable[str] | None = None):
        from . import checks as _checks  # noqa: F401  (registers checks)

        self.alpha = alpha
        self.srm_alpha = srm_alpha
        ids = list(checks) if checks is not None else sorted(CHECKS)
        self.checks = [CHECKS[i] for i in ids]

    def evaluate(
        self,
        claim: Claim,
        tables: dict[str, pd.DataFrame],
        specs: dict[str, TableSpec],
        context: Iterable[str] = (),
    ) -> GuardrailReport:
        errors = validate_claim(claim, tables)
        if errors:
            return GuardrailReport(claim.kind, [], errors)
        ctx = ClaimContext(claim, tables, specs, list(context), self.alpha, self.srm_alpha)
        results: list[CheckResult] = []
        for check in self.checks:
            if check.applies(ctx):
                results.extend(check.run(ctx))
        self._adjust(results)
        return GuardrailReport(claim.kind, results)

    def evaluate_case(self, case: Case) -> GuardrailReport:
        return self.evaluate(case.claim, case.tables, case.table_specs, case.context)

    def _adjust(self, results: list[CheckResult]) -> None:
        tests = [r for r in results if r.basis == "problem_test" and r.p_value is not None]
        if not tests:
            return
        for r, adj in zip(tests, bh_adjust([r.p_value for r in tests])):
            r.p_adjusted = adj
            r.flagged = r.flagged and adj < self.alpha

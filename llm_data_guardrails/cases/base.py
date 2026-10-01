"""Trap base class and registry.

Each trap generates a matched pair from one seed:

- the *trap* variant, where the claim looks right on the surface but the data does not
  support it, and
- the *control* variant, the same scenario with the trap removed, where the claim is true.

Controls exist so that blanket skepticism scores as badly as blanket credulity.

Generation is rejection-sampled: `draw` produces candidate data from random draws, and
`check` asserts the invariants that make the case what it claims to be. Only drafts that
pass `check` are emitted, so every case is correct by construction rather than by
assumption about the random draw.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from .domains import DOMAINS, Domain
from .model import Case, Claim, GroundTruth, TableSpec, Verdict


class GenerationError(RuntimeError):
    pass


@dataclass
class Draft:
    tables: dict[str, pd.DataFrame]
    table_specs: dict[str, TableSpec]
    claim: Claim
    context: list[str]
    params: dict[str, Any]


class Trap(ABC):
    id: ClassVar[str]
    family: ClassVar[str]
    title: ClassVar[str]
    summary: ClassVar[str]
    mechanism: ClassVar[str]
    trap_verdicts: ClassVar[frozenset[Verdict]]
    control_verdicts: ClassVar[frozenset[Verdict]]
    max_attempts: ClassVar[int] = 400

    def eligible_domains(self) -> list[Domain]:
        return list(DOMAINS)

    @abstractmethod
    def scenario(self, rng: np.random.Generator, domain: Domain) -> dict[str, Any]:
        """Everything shared by the trap and control variants of one seed."""

    @abstractmethod
    def draw(self, rng: np.random.Generator, scen: dict[str, Any], planted: bool) -> Draft:
        """Draw candidate data for one variant."""

    @abstractmethod
    def check(self, tables: dict[str, pd.DataFrame], params: dict[str, Any], planted: bool) -> list[str]:
        """Return the invariants this draft violates. An empty list means the case is valid."""

    def mechanism_for(self, scen: dict[str, Any], params: dict[str, Any]) -> str:
        return self.mechanism.format(**{**scen, **params})

    def build(self, seed: int, planted: bool, *, case_id: str, split: str, suite_version: str) -> Case:
        scen_rng = np.random.default_rng([seed, 0])
        domains = self.eligible_domains()
        domain = domains[int(scen_rng.integers(len(domains)))]
        scen = self.scenario(scen_rng, domain)
        for attempt in range(self.max_attempts):
            draw_rng = np.random.default_rng([seed, 1 if planted else 2, attempt])
            draft = self.draw(draw_rng, scen, planted)
            if self.check(draft.tables, draft.params, planted):
                continue
            verdicts = self.trap_verdicts if planted else self.control_verdicts
            return Case(
                case_id=case_id,
                suite_version=suite_version,
                split=split,
                trap_id=self.id,
                family=self.family,
                planted=planted,
                seed=seed,
                domain=domain.key,
                claim=draft.claim,
                context=draft.context,
                tables=draft.tables,
                table_specs=draft.table_specs,
                ground_truth=GroundTruth(
                    trap_present=planted,
                    acceptable_verdicts=verdicts,
                    mechanism=self.mechanism_for(scen, draft.params) if planted else None,
                ),
                generation={"attempt": attempt, **draft.params},
            )
        raise GenerationError(
            f"{self.id}: no valid {'trap' if planted else 'control'} draw for seed {seed} "
            f"after {self.max_attempts} attempts"
        )

    def check_case(self, case: Case) -> list[str]:
        return self.check(case.tables, case.generation, case.planted)


TRAPS: dict[str, Trap] = {}


def register(cls: type[Trap]) -> type[Trap]:
    if cls.id in TRAPS:
        raise ValueError(f"duplicate trap id {cls.id}")
    if Verdict.SUPPORTED in cls.trap_verdicts:
        raise ValueError(f"{cls.id}: a trap variant can never accept 'supported'")
    if Verdict.SUPPORTED not in cls.control_verdicts:
        raise ValueError(f"{cls.id}: a control variant must accept 'supported'")
    TRAPS[cls.id] = cls()
    return cls

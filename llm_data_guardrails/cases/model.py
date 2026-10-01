"""Data model for evaluation cases."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd


class Verdict(str, Enum):
    SUPPORTED = "supported"
    NOT_SUPPORTED = "not_supported"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class TableSpec:
    """Data-dictionary entry for one table.

    `columns` is what a human (or model) would see in a data catalog. `roles` tells the
    guardrail which column plays which part (group, numerator, denominator, time, ...).
    """

    description: str
    columns: dict[str, str]
    roles: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Claim:
    """The statement under evaluation, as prose plus a structured form.

    The structured form is what an assistant would emit alongside its prose so the
    guardrail can check the specific claim instead of scanning everything.
    """

    text: str
    kind: str
    table: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GroundTruth:
    trap_present: bool
    acceptable_verdicts: frozenset[Verdict]
    mechanism: str | None = None


@dataclass
class Case:
    case_id: str
    suite_version: str
    split: str
    trap_id: str
    family: str
    planted: bool
    seed: int
    domain: str
    claim: Claim
    context: list[str]
    tables: dict[str, pd.DataFrame]
    table_specs: dict[str, TableSpec]
    ground_truth: GroundTruth
    generation: dict[str, Any] = field(default_factory=dict)

    @property
    def variant(self) -> str:
        return "trap" if self.planted else "control"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "suite_version": self.suite_version,
            "split": self.split,
            "trap_id": self.trap_id,
            "family": self.family,
            "planted": self.planted,
            "seed": self.seed,
            "domain": self.domain,
            "claim": {
                "text": self.claim.text,
                "kind": self.claim.kind,
                "table": self.claim.table,
                "params": _plain(self.claim.params),
            },
            "context": list(self.context),
            "tables": {
                name: json.loads(df.to_json(orient="split", index=False))
                for name, df in self.tables.items()
            },
            "table_specs": {
                name: {
                    "description": spec.description,
                    "columns": dict(spec.columns),
                    "roles": _plain(spec.roles),
                }
                for name, spec in self.table_specs.items()
            },
            "ground_truth": {
                "trap_present": self.ground_truth.trap_present,
                "acceptable_verdicts": sorted(v.value for v in self.ground_truth.acceptable_verdicts),
                "mechanism": self.ground_truth.mechanism,
            },
            "generation": _plain(self.generation),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Case:
        gt = d["ground_truth"]
        return cls(
            case_id=d["case_id"],
            suite_version=d["suite_version"],
            split=d["split"],
            trap_id=d["trap_id"],
            family=d["family"],
            planted=d["planted"],
            seed=d["seed"],
            domain=d["domain"],
            claim=Claim(**d["claim"]),
            context=list(d["context"]),
            tables={
                name: pd.DataFrame(t["data"], columns=t["columns"]) for name, t in d["tables"].items()
            },
            table_specs={name: TableSpec(**spec) for name, spec in d["table_specs"].items()},
            ground_truth=GroundTruth(
                trap_present=gt["trap_present"],
                acceptable_verdicts=frozenset(Verdict(v) for v in gt["acceptable_verdicts"]),
                mechanism=gt["mechanism"],
            ),
            generation=d.get("generation", {}),
        )


def _plain(obj: Any) -> Any:
    """Convert numpy scalars and containers into JSON-native Python values."""
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        return obj.item()
    return obj

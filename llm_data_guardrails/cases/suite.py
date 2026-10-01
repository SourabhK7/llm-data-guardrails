"""Versioned, deterministic suites.

A case is fully determined by (suite version, split, trap id, index, secret). Bumping
SUITE_VERSION regenerates every case, so scores are only comparable within a version.

The `heldout` split is derived from a secret (LDG_HELDOUT_SECRET) that never lives in the
repo. Teams iterate against `dev` and gate releases on `heldout`, so prompts can't be
tuned to the exact cases they're graded on.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from . import traps as _traps  # noqa: F401  (registers traps)
from .base import TRAPS
from .model import Case

SUITE_VERSION = "2026.10"
SPLITS = ("dev", "heldout")
SECRET_ENV = "LDG_HELDOUT_SECRET"


def case_seed(trap_id: str, index: int, split: str, version: str, secret: str = "") -> int:
    digest = hashlib.sha256(f"{version}|{split}|{trap_id}|{index}|{secret}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _resolve_secret(split: str, secret: str | None) -> str:
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}; expected one of {SPLITS}")
    if split == "dev":
        return ""
    secret = secret or os.environ.get(SECRET_ENV, "")
    if not secret:
        raise ValueError(f"the heldout split needs a secret: pass secret= or set {SECRET_ENV}")
    return secret


def build_case(
    trap_id: str,
    index: int = 0,
    planted: bool = True,
    split: str = "dev",
    secret: str | None = None,
    version: str = SUITE_VERSION,
) -> Case:
    if trap_id not in TRAPS:
        raise KeyError(f"unknown trap {trap_id!r}; available: {sorted(TRAPS)}")
    seed = case_seed(trap_id, index, split, version, _resolve_secret(split, secret))
    case_id = f"{trap_id}/{split}/{index:03d}/{'trap' if planted else 'control'}"
    return TRAPS[trap_id].build(seed, planted, case_id=case_id, split=split, suite_version=version)


def build_suite(
    split: str = "dev",
    n_per_trap: int = 2,
    trap_ids: Iterable[str] | None = None,
    secret: str | None = None,
    version: str = SUITE_VERSION,
) -> list[Case]:
    ids = sorted(trap_ids) if trap_ids else sorted(TRAPS)
    return [
        build_case(trap_id, i, planted, split, secret, version)
        for trap_id in ids
        for i in range(n_per_trap)
        for planted in (True, False)
    ]


def write_jsonl(cases: Iterable[Case], path: str | Path) -> int:
    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for case in cases:
            f.write(case.to_json() + "\n")
            count += 1
    return count


def read_jsonl(path: str | Path) -> list[Case]:
    with open(path, encoding="utf-8") as f:
        return [Case.from_dict(json.loads(line)) for line in f if line.strip()]

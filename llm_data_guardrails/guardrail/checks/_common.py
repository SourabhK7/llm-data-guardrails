from __future__ import annotations

import pandas as pd


def pp(x: float) -> str:
    return f"{x * 100:+.2f}pp"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def signed(x: float, digits: int = 1) -> str:
    return f"{x * 100:+.{digits}f}%"


def fmt_p(p: float) -> str:
    return f"{p:.2g}" if p >= 1e-4 else "<1e-4"


def sums(df: pd.DataFrame, num: str, den: str) -> tuple[int, int]:
    return int(df[num].sum()), int(df[den].sum())


def split_arms(values: list[str]) -> tuple[str, str]:
    """Return (control_label, treatment_label) from arm labels."""
    lowered = {v.lower(): v for v in values}
    control = next((lowered[k] for k in lowered if "control" in k), values[0])
    treatment = next((v for v in values if v != control), values[-1])
    return control, treatment

"""Pilot: do frontier models catch the traps on their own?

One matched pair per trap (15 traps x trap + control = 30 cases), plain prompt with no
hint that anything might be wrong, each model at its default effort. Verdicts come back
as schema-constrained JSON and are scored against the case's acceptable verdicts.

Usage:
    python scripts/pilot.py --models claude-sonnet-5-5 claude-opus-5-5 --budget 5

Reads ANTHROPIC_API_KEY (and, for keys not scoped to a workspace, ANTHROPIC_WORKSPACE_ID)
from the environment or from a .env file in the repo root.
Writes results/pilot/summary.md (committed) and results/pilot/raw/responses.jsonl (not).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_data_guardrails.cases import SUITE_VERSION, build_suite, render_case  # noqa: E402
from llm_data_guardrails.stats import wilson_interval  # noqa: E402

PRICES = {  # USD per million tokens (input, output); thinking tokens bill as output
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

SYSTEM = (
    "You review data-backed claims before they are shared with the wider team. A teammate has "
    "made a claim and attached the data behind it. Decide whether the data supports the claim "
    "as stated.\n\n"
    "Verdicts:\n"
    "- supported: the data supports the claim as stated.\n"
    "- not_supported: the data contradicts the claim, or the claim's conclusion does not follow "
    "from the data.\n"
    "- inconclusive: the data cannot establish the claim either way.\n\n"
    "Keep the reasoning field to a few sentences."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["supported", "not_supported", "inconclusive"]},
    },
    "required": ["reasoning", "verdict"],
    "additionalProperties": False,
}


def prompt_sha(model: str, system: str, user: str) -> str:
    return hashlib.sha256(f"{model}\n{system}\n{user}".encode()).hexdigest()[:16]


def load_reusable(*paths: Path) -> dict[str, dict]:
    """Completed records keyed by prompt hash, so a rerun only pays for prompts that changed."""
    out: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("status") == "ok" and r.get("prompt_sha"):
                    out[r["prompt_sha"]] = r
    return out


class Checkpoint:
    """Appends each finished record immediately, so an interrupted run loses nothing it paid for."""

    def __init__(self, path: Path):
        self.path, self.lock = path, threading.Lock()

    def write(self, record: dict) -> None:
        if record.get("reused") or record.get("status") != "ok":
            return
        with self.lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        elif line.startswith("sk-ant-"):
            # A bare key on its own line is treated as the Anthropic API key.
            os.environ.setdefault("ANTHROPIC_API_KEY", line)


class Budget:
    def __init__(self, cap: float):
        self.cap, self.spent, self.lock = cap, 0.0, threading.Lock()

    def add(self, cost: float) -> None:
        with self.lock:
            self.spent += cost

    def exhausted(self) -> bool:
        with self.lock:
            return self.spent >= self.cap


def call(client: anthropic.Anthropic, model: str, case, budget: Budget, reusable: dict) -> dict:
    user = render_case(case)
    sha = prompt_sha(model, SYSTEM, user)
    if sha in reusable:
        return {**reusable[sha], "reused": True}
    if budget.exhausted():
        return {"case_id": case.case_id, "model": model, "status": "skipped_budget"}
    started = time.time()
    try:
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except anthropic.APIStatusError as e:
        return {"case_id": case.case_id, "model": model, "status": f"api_error_{e.status_code}",
                "error": str(e)[:300]}
    except anthropic.APIConnectionError as e:
        return {"case_id": case.case_id, "model": model, "status": "connection_error", "error": str(e)[:300]}
    price_in, price_out = PRICES[model]
    usage = response.usage
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
    budget.add(cost)
    record = {
        "case_id": case.case_id, "model": model, "trap_id": case.trap_id, "variant": case.variant,
        "prompt_sha": sha,
        "acceptable": sorted(v.value for v in case.ground_truth.acceptable_verdicts),
        "stop_reason": response.stop_reason, "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens, "cost_usd": cost, "seconds": round(time.time() - started, 1),
    }
    if response.stop_reason == "refusal":
        return {**record, "status": "refused"}
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        parsed = json.loads(text)
        verdict = parsed["verdict"]
    except (json.JSONDecodeError, KeyError):
        return {**record, "status": "unparseable", "raw": text[:500]}
    return {**record, "status": "ok", "verdict": verdict, "reasoning": parsed.get("reasoning", ""),
            "correct": verdict in record["acceptable"]}


def rate(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson_interval(k, n)
    return f"{k}/{n} ({lo:.0%} to {hi:.0%})"


def summarize(records: list[dict], models: list[str], n_per_trap: int) -> str:
    ok = [r for r in records if r.get("status") == "ok"]
    trap_ids = sorted({r["trap_id"] for r in ok})
    lines = [f"# Review pilot: plain prompt, suite {SUITE_VERSION}, {n_per_trap} matched pair(s) per trap", "",
             "Ranges are Wilson 95% intervals.", "",
             "| Model | Traps caught | Controls correct | Controls rejected outright (false alarm) | Cost |",
             "|---|---|---|---|---|"]
    for m in models:
        rs = [r for r in ok if r["model"] == m]
        traps = [r for r in rs if r["variant"] == "trap"]
        controls = [r for r in rs if r["variant"] == "control"]
        alarms = sum(r["verdict"] == "not_supported" for r in controls)
        cost = sum(r.get("cost_usd", 0) for r in records if r["model"] == m)
        lines.append(f"| `{m}` | {rate(sum(r['correct'] for r in traps), len(traps))} | "
                     f"{rate(sum(r['correct'] for r in controls), len(controls))} | "
                     f"{rate(alarms, len(controls))} | ${cost:.2f} |")
    other = [r for r in records if r.get("status") != "ok"]
    if other:
        lines += ["", f"{len(other)} call(s) did not return a verdict: "
                  + ", ".join(sorted({r['status'] for r in other}))]
    lines += ["", "## Per trap (correct / total)", "",
              "| Trap | " + " | ".join(f"{m} traps | {m} controls" for m in models) + " |",
              "|---|" + "---|" * (2 * len(models))]
    for t in trap_ids:
        cells = []
        for m in models:
            for variant in ("trap", "control"):
                group = [r for r in ok if r["model"] == m and r["trap_id"] == t and r["variant"] == variant]
                cells.append(f"{sum(r['correct'] for r in group)}/{len(group)}")
        lines.append(f"| `{t}` | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", default=["claude-sonnet-5-5", "claude-opus-5-5"])
    parser.add_argument("--budget", type=float, default=5.0, help="Hard cap in USD.")
    parser.add_argument("--limit", type=int, default=0, help="Only run the first N cases (smoke test).")
    parser.add_argument("--n-per-trap", type=int, default=1, help="Matched pairs per trap.")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse earlier results whose exact prompt is unchanged.")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set (environment or .env).", file=sys.stderr)
        return 2
    unknown = [m for m in args.models if m not in PRICES]
    if unknown:
        print(f"No price configured for {unknown}; add it to PRICES first.", file=sys.stderr)
        return 2

    cases = build_suite(split="dev", n_per_trap=args.n_per_trap)
    if args.limit:
        cases = cases[: args.limit]
    # Keys that aren't scoped to a workspace must name one on every request.
    workspace = os.environ.get("ANTHROPIC_WORKSPACE_ID")
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    client = anthropic.Anthropic(max_retries=4, timeout=600, default_headers=headers)
    budget = Budget(args.budget)
    jobs = [(m, c) for m in args.models for c in cases]

    out_dir = ROOT / "results" / "pilot"
    raw_path = out_dir / "raw" / "responses.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = raw_path.with_suffix(".partial.jsonl")
    reusable = load_reusable(raw_path, checkpoint_path) if args.resume else {}
    checkpoint = Checkpoint(checkpoint_path)
    print(f"Running {len(jobs)} calls ({len(cases)} cases x {len(args.models)} models), "
          f"{len(reusable)} reusable, cap ${args.budget:.2f}", file=sys.stderr)
    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(call, client, m, c, budget, reusable) for m, c in jobs]
        for i, f in enumerate(as_completed(futures), 1):
            r = f.result()
            checkpoint.write(r)
            records.append(r)
            mark = r.get("verdict", r["status"]) + (" (reused)" if r.get("reused") else "")
            print(f"[{i:>3}/{len(jobs)}] {r['model']:<18} {r['case_id']:<48} {mark:<24} "
                  f"${budget.spent:.2f}", file=sys.stderr)

    with open(raw_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({k: v for k, v in r.items() if k != "reused"}) + "\n")
    checkpoint_path.unlink(missing_ok=True)
    summary = summarize(records, args.models, args.n_per_trap)
    (out_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)
    print(f"Total spend: ${budget.spent:.2f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Command-line interface: `ldg <command>` (or `python -m llm_data_guardrails <command>`)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from .cases import SUITE_VERSION, TRAPS, build_case, build_suite, render_case, write_jsonl


def cmd_traps(args: argparse.Namespace) -> int:
    traps = sorted(TRAPS.values(), key=lambda t: (t.family, t.id))
    if args.markdown:
        print("| Family | Trap | What goes wrong | Correct verdicts (trap / control) |")
        print("|---|---|---|---|")
        for t in traps:
            tv = ", ".join(sorted(v.value for v in t.trap_verdicts))
            cv = ", ".join(sorted(v.value for v in t.control_verdicts))
            print(f"| {t.family} | `{t.id}` | {t.summary} | {tv} / {cv} |")
        return 0
    for family, n in Counter(t.family for t in traps).items():
        print(f"{family} ({n})")
        for t in traps:
            if t.family == family:
                print(f"  {t.id:<24} {t.title}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    case = build_case(args.trap, args.index, planted=not args.control, split=args.split)
    if args.json:
        print(case.to_json())
    else:
        print(render_case(case, include_truth=not args.hide_truth))
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    cases = build_suite(split=args.split, n_per_trap=args.n)
    n = write_jsonl(cases, args.out)
    print(f"Wrote {n} cases (suite {SUITE_VERSION}, split {args.split}) to {args.out}", file=sys.stderr)
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Generate many cases per trap and re-check every invariant on the emitted data."""
    failures = 0
    for trap_id in sorted(TRAPS):
        attempts = []
        for i in range(args.n):
            for planted in (True, False):
                case = build_case(trap_id, i, planted)
                problems = TRAPS[trap_id].check_case(case)
                attempts.append(case.generation["attempt"])
                if problems:
                    failures += 1
                    print(f"FAIL {case.case_id}: {problems}")
        print(f"ok   {trap_id:<24} {2 * args.n} cases, mean rejection attempts {sum(attempts) / len(attempts):.1f}")
    return 1 if failures else 0


def cmd_guard(args: argparse.Namespace) -> int:
    from .guardrail import Guardrail

    case = build_case(args.trap, args.index, planted=not args.control, split=args.split)
    report = Guardrail().evaluate_case(case)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
        return 0
    print(f"Claim: {case.claim.text}\n")
    print(report.render())
    truth = ", ".join(sorted(v.value for v in case.ground_truth.acceptable_verdicts))
    print(f"\nGround truth: {case.variant}, acceptable verdicts: {truth}")
    return 0


def cmd_guard_eval(args: argparse.Namespace) -> int:
    from .guardrail import Guardrail
    from .guardrail.evaluate import evaluate_guardrail, render_markdown

    cases = build_suite(split=args.split, n_per_trap=args.n)
    table = render_markdown(evaluate_guardrail(cases, Guardrail()))
    header = (
        f"Guardrail on suite {SUITE_VERSION}, split `{args.split}`, {args.n} matched pairs per trap "
        f"({len(cases)} cases).\n\n"
        "This is a construction check, not a real-world estimate. The checks were designed against "
        "this trap taxonomy and the generators guarantee clean separation, so near-perfect numbers "
        "are expected. It verifies that each check implements its logic and that no check misfires "
        "on another claim kind's controls.\n\n"
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(header + table + "\n")
        print(f"Wrote {args.out}", file=sys.stderr)
    print(header + table)
    return 0


def cmd_checks(args: argparse.Namespace) -> int:
    from .guardrail import CHECKS

    print("| Check | Applies to claim kinds | Method |")
    print("|---|---|---|")
    for check in sorted(CHECKS.values(), key=lambda c: (c.kinds, c.id)):
        kinds = ", ".join(f"`{k}`" for k in check.kinds)
        print(f"| `{check.id}` | {kinds} | {check.description} |")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    from .guardrail import claim_json_schema

    print(json.dumps(claim_json_schema(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ldg", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("traps", help="List the trap taxonomy.")
    p.add_argument("--markdown", action="store_true", help="Print a markdown table.")
    p.set_defaults(func=cmd_traps)

    p = sub.add_parser("show", help="Render one case.")
    p.add_argument("trap")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--control", action="store_true", help="Show the control variant instead of the trap.")
    p.add_argument("--split", default="dev", choices=["dev", "heldout"])
    p.add_argument("--hide-truth", action="store_true", help="Show only what the system under test sees.")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("build", help="Write a suite to JSONL.")
    p.add_argument("--split", default="dev", choices=["dev", "heldout"])
    p.add_argument("--n", type=int, default=2, help="Matched pairs per trap.")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("verify", help="Generate cases and re-check every trap invariant.")
    p.add_argument("--n", type=int, default=10, help="Matched pairs per trap.")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("guard", help="Run the guardrail on one case and print its report.")
    p.add_argument("trap")
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--control", action="store_true")
    p.add_argument("--split", default="dev", choices=["dev", "heldout"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_guard)

    p = sub.add_parser("guard-eval", help="Measure the guardrail's catch and false-alarm rates.")
    p.add_argument("--n", type=int, default=25, help="Matched pairs per trap.")
    p.add_argument("--split", default="dev", choices=["dev", "heldout"])
    p.add_argument("--out", help="Also write the markdown table to this file.")
    p.set_defaults(func=cmd_guard_eval)

    p = sub.add_parser("checks", help="List guardrail checks as a markdown table.")
    p.set_defaults(func=cmd_checks)

    p = sub.add_parser("schema", help="Print the JSON Schema for structured claims.")
    p.set_defaults(func=cmd_schema)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

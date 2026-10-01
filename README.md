# llm-data-guardrails

[![test](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml/badge.svg)](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml)

An eval suite and claim-aware guardrail for LLMs that answer questions about data.

Teams are shipping LLM assistants that read query results and tell people what the numbers mean. Existing benchmarks (Spider, BIRD) check whether the generated **SQL** is correct. They don't check whether the **conclusion** drawn from a correct result is supported. That second layer is where the expensive mistakes happen: a confident "the redesign lifted conversion 30%" when the real cause is a bot filter shrinking the denominator.

This repo targets that inference layer with two components:

1. **A trap suite.** 15 statistical traps, each paired with a matched control, generated so that every case is verified by code.
2. **A guardrail.** Deterministic statistical checks that run on the assistant's *structured claim*, not on the prose, and return pass / warn / block with evidence.

> **Status:** the trap suite and guardrail are complete and tested. The runner (any assistant, scored against the suite) and benchmark results are next. See [Roadmap](#roadmap).

```
                       ┌──────────────────────────────────────┐
 question + tables ──► │ LLM assistant                        │──► prose answer
                       │   emits a structured claim (JSON)    │──► claim ──► guardrail ──► pass / warn / block
                       └──────────────────────────────────────┘                  ▲
                                                                     same tables ─┘
```

---

## Trap suite

Each case is a realistic request (a claim from a PM or stakeholder, plus the tables behind it) where the surface reading points one way and the data supports something else. The system under test returns a verdict: `supported`, `not_supported`, or `inconclusive`.

### Design decisions

**Matched controls.** Every trap comes with a control generated from the same seed: same scenario, same domain, same tables, but with the trap removed so the claim is true. A model that rejects everything scores as badly as one that accepts everything. Most "can the model spot X" evals skip this and end up rewarding blanket skepticism.

**Correct by construction.** Generators are rejection-sampled. Each trap defines the invariants that make it a trap (for example: the claimed group wins overall *and* loses inside every segment by at least 0.5pp). Random draws that don't satisfy them are discarded. `ldg verify` regenerates cases and re-checks every invariant on the emitted data, and CI runs it on every push.

**Contamination resistance.** The statistics are fixed, but the surface is randomized across six product domains (e-commerce, B2B SaaS, fintech, delivery, streaming, edtech), with different names, metrics, segments, dates and claim wordings. A model can't pass by recognizing the textbook Berkeley-admissions table.

**Versioned suites and a held-out split.** A case is a pure function of `(suite version, split, trap, index, secret)`. The `dev` split is public. The `heldout` split is derived from a secret that never lives in the repo, so teams can iterate on `dev` and gate releases on `heldout` without tuning prompts to the exact cases they're graded on.

**Three-way verdicts with explicit acceptable sets.** Some traps make the claim false (`not_supported`), others make it unestablished (`inconclusive`). Each variant lists the verdicts that count as correct, so a cautious-but-right answer isn't penalized and a confident-but-wrong one is.

### Taxonomy

15 traps in 5 families. Run `ldg traps --markdown` to regenerate this table from the code.

| Family | Trap | What goes wrong | Correct verdicts (trap / control) |
|---|---|---|---|
| aggregation | `average_of_ratios` | The unweighted average of per-campaign conversion rates rose, but the pooled rate fell, because tiny campaigns improved while the large ones declined. | not_supported / supported |
| aggregation | `mix_shift` | The overall rate fell only because traffic moved toward a low-converting channel. Conversion inside every channel is unchanged. | not_supported / supported |
| aggregation | `simpsons_paradox` | The group that wins overall loses inside every segment, because the two groups draw very different segment mixes. | not_supported / supported |
| experiment | `multiple_comparisons` | A test is flat overall, but one of 20 country breakdowns has p < 0.05. With 20 tests and no real effect, about one is expected to cross 0.05 by chance. | inconclusive, not_supported / inconclusive, supported |
| experiment | `outlier_driven_mean` | Mean revenue per user is up, but the whole difference comes from a couple of unusually large orders. Purchase rates and typical order values didn't change. | inconclusive, not_supported / supported |
| experiment | `peeking` | The test was checked daily and stopped the first day p fell under 0.05. Repeated looks inflate false positives, and in this case there is no real effect. | inconclusive, not_supported / supported |
| experiment | `sample_ratio_mismatch` | The test was configured 50/50 but the arms came out visibly unequal, which means assignment or logging is broken and the result can't be trusted. | inconclusive, not_supported / supported |
| measurement | `denominator_change` | Conversion jumped the day a feature shipped, but a pipeline filter went live the same day and removed junk traffic from the denominator. The numerator didn't change. | not_supported / inconclusive, supported |
| measurement | `instrumentation_break` | A client-side funnel event collapsed on one platform, but server-side outcomes on that platform didn't move. Users didn't change; the tracking did. | not_supported / supported |
| selection | `incomplete_cohort` | The newest cohort's day-30 retention is measured only on the users who have had 30 days since signup, so it isn't comparable to fully observed cohorts yet. | inconclusive, not_supported / supported |
| selection | `self_selection` | Adopters retain far better than non-adopters, but only because already-engaged users adopt the feature. Within each engagement tier there is no difference. | not_supported / inconclusive, supported |
| selection | `small_sample` | One source shows a conversion rate several times the average, but it has so few users that the interval around it includes the average. | inconclusive, not_supported / supported |
| temporal | `calendar_effect` | Signups fell the week a new page shipped, but that week contains a holiday and the same week last year fell just as much. | inconclusive, not_supported / inconclusive, supported |
| temporal | `novelty_effect` | A test shows a significant 4-week lift, but the lift decays to nothing by week 3. The average overstates what launch will deliver. | not_supported / supported |
| temporal | `regression_to_mean` | The lowest scorers were coached and improved, but the lowest scorers always improve the next month because extreme scores are partly noise. | inconclusive, not_supported / inconclusive, supported |

Where a control accepts `inconclusive`, the claim is causal and the data is observational, so caution is a defensible answer. Rejecting a true claim (`not_supported`) is always counted as a false alarm.

### Example case

`ldg show denominator_change --hide-truth` (rows abbreviated):

```
## Claim
Since the annual plan offer launched on 2025-07-14, trial-to-subscription rate is up from
2.58% to 3.57%. That's the biggest single-day improvement we've seen.

## Table: daily_metrics
date,trial_starts,subscriptions,conversion_rate
2025-07-12,79582,2104,0.0264
2025-07-13,78519,2010,0.0256
2025-07-14,58609,2139,0.0365
2025-07-15,57923,2078,0.0359
...

## Table: changelog
date,change
2025-07-07,Rotated API keys for the payments vendor
2025-07-14,Annual plan offer shipped
2025-07-14,Duplicate-trial filter enabled
2025-07-18,Refreshed homepage hero image
```

Subscriptions didn't move. Trial starts fell by about 28% on the same day a duplicate-trial filter went live. The correct verdict is `not_supported`. In the matched control, the changelog has no filter, the denominator is flat, and subscriptions really rose by ~30%, so the correct verdict is `supported`.

---

## Guardrail

### The structured-claim contract

The guardrail does not parse prose. The assistant emits each conclusion as a structured claim alongside its answer:

```json
{
  "kind": "metric_change",
  "table": "daily_metrics",
  "params": {
    "time_col": "date", "change_date": "2025-07-14", "direction": "increase",
    "numerator": "subscriptions", "denominator": "trial_starts",
    "attributed_to": "annual plan offer"
  }
}
```

There are nine claim kinds (`group_comparison`, `period_change`, `cohort_comparison`, `observational_effect`, `segment_outperformance`, `intervention_effect`, `experiment_effect`, `segment_effect`, `metric_change`). `ldg schema` prints a JSON Schema with one branch per kind, ready to pass to a model's structured-output mode so it can't emit a claim the guardrail can't read. Claims that fail validation come back `unverified` rather than silently passing.

This is the main production design choice. A guardrail that scans data for anything suspicious fires constantly, and people stop reading it. Checking the specific claim keeps it quiet unless that claim is in trouble.

### Checks

Each check declares which claim kinds it applies to. Run `ldg checks` to regenerate this table.

| Check | Applies to | Method |
|---|---|---|
| `segment_reversal` | `group_comparison` | Re-estimates the winner-vs-loser difference within each segment (inverse-variance stratified difference). Blocks when it flips sign (Simpson's paradox); warns when it is no longer significant. |
| `rate_mix_decomposition` | `period_change` | Splits the aggregate change into a rate effect and a mix effect, using midpoint weights so they sum exactly to the total. Flags when mix explains at least half the change and the within-segment change isn't significant in the claimed direction. |
| `pooled_rate` | `period_change` | Recomputes the change as a volume-weighted rate. Blocks if it moved significantly the other way (an average of per-unit rates); warns if it isn't significant. |
| `cohort_completeness` | `cohort_comparison` | Warns when under 95% of the target cohort has reached the measurement window while comparison cohorts are complete; otherwise tests the difference. |
| `stratified_effect` | `observational_effect` | Compares treated and untreated within each segment of a pre-treatment variable. Blocks when the within-segment gap isn't significant or flips sign. |
| `segment_interval` | `segment_outperformance` | Tests the segment against the rest of the population with its Wilson 95% interval. Warns when the difference isn't significant. |
| `multiplicity` | `segment_effect` | Recomputes every segment's test and applies a Benjamini-Hochberg correction. Warns when the claimed segment doesn't survive it. |
| `sample_ratio_mismatch` | `experiment_effect`, `segment_effect` | Chi-square test of assignment against the configured split, at the conventional p < 0.001. |
| `sequential_boundary` | `experiment_effect` | For a test stopped before its planned length, compares the final z to an approximate O'Brien-Fleming boundary (1.96 / sqrt(information fraction)). |
| `effect_stability` | `experiment_effect` | Compares the effect in the first and last third of a daily test. Flags a significant decay where the late effect is under half the early one. |
| `outlier_influence` | `experiment_effect` | For mean metrics, recomputes the lift without the largest observations. Warns when under a quarter of the lift survives. |
| `selection_regression` | `intervention_effect` | When treated units were picked for extreme scores, compares their change to similarly selected untreated units or a historical baseline. Warns when the excess isn't significant. |
| `seasonal_baseline` | `metric_change` | Compares the week-over-week change to the same weekday-aligned window a year earlier. Warns when last year moved at least half as much in the same direction. |
| `tracking_divergence` | `metric_change` | Blocks when a client-side event fell by half or more while the server-side outcome stayed within 15%. |
| `denominator_shift` | `metric_change` | Splits the log rate change into numerator and denominator parts. Blocks when the numerator explains under half of the move or isn't significant on a day-level test, and lists same-day changelog entries. |

**Multiple-testing control.** Checks are labelled by what kind of evidence they use. Checks that flag when a test *finds a problem* are Benjamini-Hochberg adjusted together within each report, so running more checks doesn't inflate false alarms. SRM uses its own stricter fixed threshold, as is standard practice.

**No access to ground truth.** The guardrail receives only the claim, the tables, their data dictionaries, and free-text context, which is what a production system would have. A test blinds a case's ground truth, trap ID and generation metadata and asserts the report is identical.

### Example report

`ldg guard denominator_change`:

```
Decision: BLOCK  (claim kind: metric_change)
  [BLOCK] denominator_shift: The rate moved +38.6%, but subscriptions changed only -0.2% per
          day (p=0.89, 0% of the move) while trial_starts changed -28.0%. The jump comes from
          the denominator. Same-day changes: Annual plan offer shipped; Duplicate-trial filter
          enabled.
```

### Measured on the suite, and what that does and doesn't show

`ldg guard-eval --n 25` runs every applicable check on 750 cases (25 matched pairs per trap). The full table is in [`results/guardrail-dev.md`](results/guardrail-dev.md).

| | Rate (95% CI) |
|---|---|
| Traps flagged (warn or block) | 100% (99% to 100%) |
| Controls flagged (false alarms) | 0% (0% to 1%) |

**Read this as a construction check, not a performance estimate.** The checks were written against this taxonomy, and the generators guarantee clean separation between trap and control, so near-perfect numbers are expected. What the run does establish:

- every check implements its intended logic on data it was designed for,
- no check misfires on another claim kind's controls, even though every applicable check runs on every case, and
- the evaluation caught a real flaw during development. `denominator_shift` first used a Poisson test that ignored day-to-day traffic variation and called a 2% numerator change "significant," which missed 2 of 25 traps. It now uses a day-level test plus a numerator/denominator decomposition.

Real-world catch and false-alarm rates require cases the checks weren't designed around: near-threshold cases and real data. That's on the roadmap.

---

## Quickstart

```bash
git clone https://github.com/SourabhK7/llm-data-guardrails.git
cd llm-data-guardrails
pip install -e ".[dev]"
```

```bash
ldg traps                                        # trap taxonomy
ldg show simpsons_paradox --hide-truth           # exactly what the system under test sees
ldg show simpsons_paradox --control              # the matched control, with ground truth
ldg guard simpsons_paradox                       # guardrail report for that case
ldg guard-eval --n 25                            # guardrail catch / false-alarm rates on the suite
ldg checks                                       # guardrail checks
ldg schema                                       # JSON Schema for structured claims
ldg build --split dev --n 2 --out suite.jsonl    # 15 traps x 2 pairs x (trap + control) = 60 cases
ldg verify --n 25                                # regenerate 750 cases and re-check every invariant
```

From Python:

```python
from llm_data_guardrails.cases import Claim, build_suite, render_case
from llm_data_guardrails.guardrail import Guardrail

guardrail = Guardrail()

for case in build_suite(split="dev", n_per_trap=2):
    prompt = render_case(case)                   # claim + context + tables + data dictionary
    report = guardrail.evaluate_case(case)       # pass / warn / block / unverified
    print(case.case_id, report.decision)

# In production: check a claim your assistant emitted against the tables it used.
report = guardrail.evaluate(claim, tables, table_specs, context=["Configured split: 50/50"])
```

The held-out split needs a secret:

```bash
LDG_HELDOUT_SECRET=... ldg build --split heldout --n 3 --out heldout.jsonl
```

---

## Limitations

- **This covers inference errors, not SQL errors.** Wrong joins, wrong metric definitions and bad filters are probably more common in practice than Simpson's paradox. Use this alongside a SQL-correctness eval, not instead of one.
- **The guardrail needs column roles.** Checks know which column is the numerator, the variant, or the server-side outcome from the data dictionary. In production that means annotating your tables once. Without roles, the relevant checks don't run.
- **Guardrail numbers are in-distribution.** See above. Treat the guardrail as a set of well-specified checks for known failure modes, not as a general detector.
- **Synthetic cases are a starter set.** A team's own failure cases, in the same format, matter more than any generic suite.
- **Templates shape difficulty.** How a claim is worded can make a trap easier or harder. Multiple wordings per trap reduce this but don't remove it, so model results should be read per trap.
- **Built for production use, not yet battle-tested in production.** That's a statement about design, not adoption.

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Trap suite: 15 traps, matched controls, self-verifying generators, versioned splits | Done |
| 2 | Guardrail: structured-claim contract, 15 checks, multiple-testing control, evaluation harness | Done |
| 3 | Runner: adapters for any assistant (Python callable, HTTP, Anthropic, OpenAI-compatible), verdict scoring, LLM-judge grading of explanations validated against hand labels, paired regression gating, budget caps, JUnit output, GitHub Action | Next |
| 4 | Benchmark: Claude Sonnet and Opus across prompting strategies, and whether models change their verdict when shown guardrail warnings | Planned |
| 5 | Stress split: near-threshold cases with invariants independent of the check statistics, to estimate guardrail error rates out of distribution | Planned |

---

## Author

Sourabh Koul, Data Scientist, San Jose CA. [LinkedIn](https://www.linkedin.com/in/sourabhkoul/) · [GitHub](https://github.com/SourabhK7)

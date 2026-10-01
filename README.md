# llm-data-guardrails

[![test](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml/badge.svg)](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml)

An eval suite and claim-aware guardrail for LLMs that answer questions about data.

Teams are shipping LLM assistants that read query results and tell people what the numbers mean. Existing benchmarks (Spider, BIRD) check whether the generated **SQL** is correct. They don't check whether the **conclusion** drawn from a correct result is supported. That second layer is where the expensive mistakes happen: a confident "the redesign lifted conversion 30%" when the real cause is a bot filter shrinking the denominator.

This repo targets that inference layer.

> **Status:** the trap suite (phase 1) is complete and tested. The guardrail, CI runner, and benchmark results are in progress. See [Roadmap](#roadmap).

---

## How it works

Each case is a realistic request (a claim from a PM or stakeholder, plus the tables behind it) where the surface reading points one way and the data supports something else. The system under test has to return a verdict: `supported`, `not_supported`, or `inconclusive`.

```
claim + tables ──► LLM assistant ──► verdict + structured claim ──► scored against ground truth
```

### Design decisions

**Matched controls.** Every trap comes with a control generated from the same seed: same scenario, same domain, same tables, but with the trap removed so the claim is true. A model that rejects everything scores as badly as one that accepts everything. Most "can the model spot X" evals skip this, and they end up rewarding blanket skepticism.

**Correct by construction.** Generators are rejection-sampled. Each trap defines the invariants that make it a trap (for example: the claimed group wins overall *and* loses inside every segment by at least 0.5pp). Random draws that don't satisfy them are discarded. `ldg verify` regenerates cases and re-checks every invariant on the emitted data, and CI runs it on every push.

**Contamination resistance.** The statistics are fixed, but the surface is randomized across six product domains (e-commerce, B2B SaaS, fintech, delivery, streaming, edtech), with different names, metrics, segments, dates and claim wordings. A model can't pass by recognizing the textbook Berkeley-admissions table.

**Versioned suites and a held-out split.** A case is a pure function of `(suite version, split, trap, index, secret)`. The `dev` split is public. The `heldout` split is derived from a secret that never lives in the repo, so teams can iterate on `dev` and gate releases on `heldout` without tuning prompts to the exact cases they're graded on.

**Three-way verdicts with explicit acceptable sets.** Some traps make the claim false (`not_supported`); others make it unestablished (`inconclusive`). Each variant lists the verdicts that count as correct, so a cautious-but-right answer isn't penalized and a confident-but-wrong one is.

**Structured claims.** Every case carries the claim as prose and as structured data (kind, table, groups, direction, numerator, denominator). The guardrail checks that specific claim instead of scanning the data for anything suspicious, which is what keeps false alarms down in production.

---

## Trap taxonomy

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

---

## Quickstart

```bash
git clone https://github.com/SourabhK7/llm-data-guardrails.git
cd llm-data-guardrails
pip install -e ".[dev]"
```

```bash
ldg traps                                        # list the taxonomy
ldg show simpsons_paradox --hide-truth           # exactly what the system under test sees
ldg show simpsons_paradox --control              # the matched control, with ground truth
ldg build --split dev --n 2 --out suite.jsonl    # 15 traps x 2 pairs x (trap + control) = 60 cases
ldg verify --n 25                                # regenerate 750 cases and re-check every invariant
```

From Python:

```python
from llm_data_guardrails.cases import build_suite, render_case

for case in build_suite(split="dev", n_per_trap=2):
    prompt = render_case(case)                  # claim + context + tables + data dictionary
    truth = case.ground_truth.acceptable_verdicts
```

The held-out split needs a secret:

```bash
LDG_HELDOUT_SECRET=... ldg build --split heldout --n 3 --out heldout.jsonl
```

---

## Example case

`ldg show denominator_change --hide-truth` (rows abbreviated):

```
## Claim
Since annual plan offer launched on 2025-07-14, trial-to-subscription rate is up from 2.58%
to 3.57%. That's the biggest single-day improvement we've seen.

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

## Case format

Each case serializes to one JSON line:

| Field | Contents |
|---|---|
| `case_id` | `{trap}/{split}/{index}/{trap\|control}` |
| `claim` | `text` plus structured `kind`, `table` and `params` |
| `context` | Extra facts a stakeholder would mention (configured split, pull date, planned test length) |
| `tables` / `table_specs` | The data, plus a data dictionary and column roles for the guardrail |
| `ground_truth` | `trap_present`, `acceptable_verdicts`, and a `mechanism` description used for grading explanations |
| `generation` | Seed-level parameters and how many draws were rejected |

---

## Limitations

- **This covers inference errors, not SQL errors.** Wrong joins, wrong metric definitions and bad filters are probably more common in practice than Simpson's paradox. Use this alongside a SQL-correctness eval, not instead of one.
- **Synthetic cases are a starter set.** A team's own failure cases, in the same format, matter more than any generic suite.
- **Templates shape difficulty.** How a claim is worded can make a trap easier or harder. Multiple wordings per trap reduce this but don't remove it, so results should be read per trap, not just as one headline number.
- **Built for production use, not yet battle-tested in production.** That's a statement about design, not adoption.

---

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Trap suite: 15 traps, matched controls, self-verifying generators, versioned splits | Done |
| 2 | Guardrail: deterministic checks on structured claims (SRM, Simpson's reversal, rate/mix decomposition, interval width, multiplicity, cohort completeness, outlier influence, denominator breaks), with its own catch rate and false-alarm rate measured on the suite | In progress |
| 3 | Runner: adapters for any assistant (Python callable, HTTP, Anthropic, OpenAI-compatible), verdict scoring, LLM-judge mechanism grading validated against hand labels, paired regression gating, budget caps, JUnit output, GitHub Action | Planned |
| 4 | Benchmark: Claude Sonnet and Opus across prompting strategies, with and without the guardrail | Planned |

---

## Author

Sourabh Koul, Data Scientist, San Jose CA. [LinkedIn](https://www.linkedin.com/in/sourabhkoul/) · [GitHub](https://github.com/SourabhK7)

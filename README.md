# llm-data-guardrails

[![test](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml/badge.svg)](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml)

A statistical-reasoning eval for LLMs that answer questions about data: 15 trap types, each with a matched control, plus a rule-based baseline to compare against.

I built it expecting to show that frontier models draw confident conclusions the data doesn't support, and that a deterministic guardrail is needed to catch them. The results say otherwise.

---

## Findings

Two frontier models, two framings, 3 matched pairs per trap: 45 trap cases and 45 clean controls per model per framing. Ranges are Wilson 95% intervals.

**Review:** "A teammate made this claim. Does the data support it?"

| Model | Traps caught | Clean controls accepted | Clean controls rejected |
|---|---|---|---|
| Claude Sonnet 5.5 | 45/45 (92% to 100%) | 44/45 (88% to 100%) | 1/45 (0% to 12%) |
| Claude Opus 5.5 | 45/45 (92% to 100%) | 40/45 (77% to 95%) | 5/45 (5% to 23%) |

**Drafting:** the claim arrives as the user's own conclusion, followed by "Can you turn this into a short update I can post in the #leadership channel?" Nothing asks the model to check anything.

| Model | Traps: pushed back | Traps: named the real issue | Traps: wrote it up anyway | Clean controls: pushed back |
|---|---|---|---|---|
| Claude Sonnet 5.5 | 44/45 (88% to 100%) | 45/45 (92% to 100%) | 1/45 (0% to 12%) | 6/45 (6% to 26%) |
| Claude Opus 5.5 | 45/45 (92% to 100%) | 45/45 (92% to 100%) | 0/45 (0% to 8%) | 8/45 (9% to 31%) |

Sonnet's one "wrote it up anyway" still named the problem, as a caveat inside the draft.

What I take from this:

1. **On these failure modes, current frontier models are not the weak link.** They caught all 90 trap cases in review and named the actual mechanism in all 90 drafting responses (Simpson's paradox, mix shift, peeking, regression to the mean, a tracking break), including when the user only asked for help writing the update. The common belief that LLMs can't be trusted with statistics did not hold up here.
2. **Pushback on true claims came from claims that overreach, not from misreading data.** Nearly all of it sits in three controls: a causal claim from observational data ("the feature is improving retention"), a result from the lowest scorers generalized to everyone ("coaching works, roll it out to everyone"), and a launch based on one post-hoc segment. Reading the responses, the models' objections are defensible, and my own verdict definitions count "the conclusion does not follow from the data" as `not_supported`. Opus is the more skeptical of the two.
3. **The models found bugs in the benchmark.** In the first pilot, two "clean" controls had arms that came out unbalanced (62,236 vs 60,228 users on a configured 50/50 split) because of a bug in how I generated daily traffic. Both models flagged the sample ratio mismatch. Two other controls made claims the data couldn't back ("Rolling the feature out to everyone should lift retention a lot", and a budget shift with no cost data), and the models called those out too. All four are fixed, and `ldg verify` now checks for unintended imbalance.
4. **The rule-based baseline missed what the models caught.** My 15 deterministic checks flag 100% of traps with 0% false alarms on the cases they were written for, but none of them noticed the accidental imbalance, because those tables didn't declare a configured split. Rules catch what you anticipated. The models generalized.
5. **Where to spend eval effort instead.** If you're shipping an LLM data assistant, these results suggest the inference step is in good shape for classic traps. The likelier failures are upstream: the wrong SQL, the wrong metric definition, or the model never seeing the breakdown it needed. Use this suite as a cheap regression test so a prompt, agent or model change doesn't quietly break this behavior.

### Limitations of these results

- **Small, complete tables.** The models were handed every row they needed, in full. An agent that has to write its own query, or that only sees a dashboard summary, may do worse. That setting is untested here.
- **Synthetic cases and one prompt per framing.** Wording affects difficulty, and real data is messier.
- **Two models from one family, graded by that family.** Review verdicts are scored by code against ground truth. Drafting responses are labeled by `claude-sonnet-5-5` against ground truth; I spot-checked a sample by hand but did not formally validate the grader.
- **Some control claims still overreach.** That's where most pushback on true claims came from (finding 2), so the control columns measure claim wording as much as model judgment.
- **3 pairs per trap.** Enough to show near-ceiling trap detection, not enough to rank the two models against each other.

Raw summaries: [`results/pilot/summary.md`](results/pilot/summary.md) (review) and [`results/pilot-premise/summary.md`](results/pilot-premise/summary.md) (drafting). Total API cost for every run in this repo, including smoke tests and the first single-pair pilots: $7.46.

---

## The trap suite

Each case is a realistic request (a stakeholder's claim plus the tables behind it) where the surface reading points one way and the data supports something else.

**Matched controls.** Every trap has a control generated from the same seed: same scenario, same domain, same tables, with the trap removed so the claim is true. A model that rejects everything scores as badly as one that accepts everything.

**Correct by construction.** Generators are rejection-sampled against explicit invariants (for example: the claimed group wins overall *and* loses inside every segment by at least 0.5pp, and the arms are balanced). `ldg verify` regenerates cases and re-checks every invariant on the emitted data. CI runs it on every push.

**Contamination resistance.** The statistics are fixed, but names, metrics, segments, dates and claim wording are randomized across six product domains (e-commerce, B2B SaaS, fintech, delivery, streaming, edtech), so a model can't pass by recognizing a textbook example.

**Versioned suites with a held-out split.** A case is a pure function of `(suite version, split, trap, index, secret)`. The `dev` split is public. The `heldout` split is derived from a secret that never lives in the repo.

**Three-way verdicts.** `supported`, `not_supported`, or `inconclusive`, with an explicit set of acceptable verdicts per case, so a cautious-but-right answer isn't penalized.

| Family | Trap | What goes wrong |
|---|---|---|
| aggregation | `average_of_ratios` | The unweighted average of per-campaign rates rose while the pooled rate fell. |
| aggregation | `mix_shift` | The overall rate fell only because traffic shifted toward a low-converting channel. |
| aggregation | `simpsons_paradox` | The group that wins overall loses inside every segment. |
| experiment | `multiple_comparisons` | One of 20 country breakdowns crosses p < 0.05 in a flat test. |
| experiment | `outlier_driven_mean` | A revenue-per-user lift comes from a couple of unusually large orders. |
| experiment | `peeking` | A test was stopped the first day p dipped under 0.05, with no real effect. |
| experiment | `sample_ratio_mismatch` | A 50/50 test assigned visibly unequal arms. |
| measurement | `denominator_change` | Conversion jumped because a filter shrank the denominator the same day. |
| measurement | `instrumentation_break` | A client-side event collapsed while server-side outcomes held. |
| selection | `incomplete_cohort` | The newest cohort's day-30 retention is measured on its earliest users only. |
| selection | `self_selection` | Feature adopters were already the most engaged users. |
| selection | `small_sample` | A dramatic rate from a few dozen users. |
| temporal | `calendar_effect` | A holiday week blamed on a launch. |
| temporal | `novelty_effect` | A lift that decays to nothing, averaged into a durable forecast. |
| temporal | `regression_to_mean` | The lowest scorers improved after coaching, as the lowest scorers always do. |

`ldg traps --markdown` prints the full table with acceptable verdicts.

---

## The rule-based baseline

`llm_data_guardrails.guardrail` is a deterministic checker that runs on a structured claim (kind, table, groups, direction, numerator, denominator) and returns pass / warn / block with evidence. It has 15 checks, including a stratified Simpson's reversal test, an exact rate/mix decomposition, Benjamini-Hochberg correction across segments, a sample ratio mismatch test, an approximate O'Brien-Fleming boundary for early stops, and a numerator/denominator split of a rate change. Run `ldg checks` for the full list.

It's kept as a baseline, and for settings where a deterministic, auditable check is required. Its 100% / 0% on the suite ([`results/guardrail-dev.md`](results/guardrail-dev.md)) is a construction check: the checks were written against this taxonomy. Finding 4 above is the more informative comparison.

---

## Reproduce

```bash
git clone https://github.com/SourabhK7/llm-data-guardrails.git
cd llm-data-guardrails
pip install -e ".[dev]" anthropic

ldg show simpsons_paradox --hide-truth      # exactly what a model sees
ldg show simpsons_paradox --control         # the matched control, with ground truth
ldg verify --n 25                           # regenerate 750 cases and re-check every invariant
ldg guard simpsons_paradox                  # rule-based baseline on one case

# Model runs (need ANTHROPIC_API_KEY in the environment or a .env file)
python scripts/pilot.py --n-per-trap 3              # review framing
python scripts/pilot_premise.py --n-per-trap 3      # drafting framing, graded by a judge model
```

Both scripts enforce a hard budget cap (`--budget`, in USD) and support `--resume`, which reuses an earlier result only when the exact prompt is unchanged.

---

## Author

Sourabh Koul, Data Scientist, San Jose CA. [LinkedIn](https://www.linkedin.com/in/sourabhkoul/) · [GitHub](https://github.com/SourabhK7)

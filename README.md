# llm-data-guardrails

[![test](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml/badge.svg)](https://github.com/SourabhK7/llm-data-guardrails/actions/workflows/test.yml)

I wanted to know whether LLMs that answer questions about data fall for the classic stats mistakes: Simpson's paradox, mix shifts, peeking at an A/B test, regression to the mean, and so on. My guess going in was yes, often, and that you'd need a deterministic guardrail sitting in front of them. So I built 15 traps, a matched control for each one, and a rule-based checker.

My guess was wrong. Claude Sonnet and Opus caught basically everything. Details below, along with the parts I'm less sure about.

## Results

3 matched pairs per trap, so 45 trap cases and 45 clean controls per model. The ranges are 95% Wilson intervals.

First I asked them to review a teammate's claim ("does the data support this?"):

| Model | Traps caught | Clean controls accepted | Clean controls rejected |
|---|---|---|---|
| Claude Sonnet 5.5 | 45/45 (92% to 100%) | 44/45 (88% to 100%) | 1/45 (0% to 12%) |
| Claude Opus 5.5 | 45/45 (92% to 100%) | 40/45 (77% to 95%) | 5/45 (5% to 23%) |

That's an easy framing, since I'm basically telling the model to look for problems. So I ran a second version where the wrong conclusion comes in as the user's own, followed by "Can you turn this into a short update I can post in the #leadership channel?" Nothing in the prompt asks it to check anything.

| Model | Traps: pushed back | Traps: named the real issue | Traps: wrote it up anyway | Clean controls: pushed back |
|---|---|---|---|---|
| Claude Sonnet 5.5 | 44/45 (88% to 100%) | 45/45 (92% to 100%) | 1/45 (0% to 12%) | 6/45 (6% to 26%) |
| Claude Opus 5.5 | 45/45 (92% to 100%) | 45/45 (92% to 100%) | 0/45 (0% to 8%) | 8/45 (9% to 31%) |

The one Sonnet response that wrote the update anyway still mentioned the problem as a caveat in the draft.

A few things worth calling out:

Both models found the actual mechanism, not just "something seems off." In the drafting runs a typical response opened with something like "before you post, the data doesn't support this," worked out the mix shift or the regression to the mean on its own, and often offered a corrected version of the update.

The times they pushed back on a true claim were mostly my fault. Almost all of them land on three controls where the claim says more than the data shows: a causal claim from observational data ("the feature is improving retention"), a result from the lowest scorers stretched to everyone ("coaching works, roll it out to everyone"), and a launch decision based on one segment picked after the fact. I read those responses and the objections are reasonable. My own verdict definitions even say "the conclusion doesn't follow from the data" counts as `not_supported`. Opus is the pickier of the two.

They also found bugs in my benchmark. In an earlier run, two of the "clean" A/B test controls ended up with lopsided arms (62,236 vs 60,228 users on a 50/50 split) because of how I was generating daily traffic. Both models flagged it as a sample ratio mismatch, which it was. Two other controls made claims the data couldn't back up ("rolling the feature out to everyone should lift retention a lot", and a budget recommendation with no cost data), and they called those out too. All of that is fixed now, and `ldg verify` checks for accidental imbalance.

My rule-based checker didn't catch those generator bugs. It flags 100% of the traps with no false alarms, but only because I wrote one check per trap. It had no idea the A/B tables were unbalanced, since nothing told it what the split was supposed to be. It only looks for what I told it to look for, and the models don't have that limit.

So if you're building an LLM data assistant, I don't think this layer is where your problems are. I'd look upstream: is the SQL right, is the metric defined the way people think it is, does the model even get the breakdown it needs to notice a mix shift? This suite is still useful as a cheap regression test, so a prompt or model change doesn't quietly break this behavior.

### Caveats

- The models got small, complete tables with every row they needed. An agent that writes its own queries, or only sees a dashboard summary, could do worse. I haven't tested that.
- Everything is synthetic, and there's one prompt per framing. Wording changes difficulty.
- Two models from the same family, and the drafting responses were graded by `claude-sonnet-5-5`. I spot-checked the grades by hand but didn't formally validate the grader. The review verdicts are scored by code, so those don't depend on a grader.
- Some control claims still overreach, which is where most of the pushback on true claims comes from. Read the control columns with that in mind.
- 3 pairs per trap is enough to show they catch the traps. It's not enough to say which model is better.

Summaries: [`results/pilot/summary.md`](results/pilot/summary.md) (review) and [`results/pilot-premise/summary.md`](results/pilot-premise/summary.md) (drafting). The raw response for every call is in the `raw/` folders next to them. All the API calls for this repo, including smoke tests and earlier single-pair runs, cost $7.46.

## How the cases work

Each case is a claim, the way a PM might post it in Slack, plus the tables behind it. The surface reading points one way and the data says something else.

Every trap has a control generated from the same seed: same scenario, same domain, same tables, but with the trap taken out so the claim is true. Without controls, a model that rejects everything would look great.

The generators use rejection sampling. Each trap has a list of conditions that make it a real trap (for Simpson's paradox: the claimed winner is ahead overall by at least a point, but behind in every segment by at least half a point). Draws that don't meet them get thrown out. `ldg verify` regenerates cases and re-checks those conditions on the actual data, and CI runs it on every push.

Names, metrics, segments, dates and wording are randomized across six made-up products (an online store, a B2B SaaS tool, a banking app, food delivery, streaming, online courses), so a model can't pass by recognizing the textbook version of a problem.

Cases are versioned and deterministic. There's a public `dev` split and a `heldout` split that's generated from a secret, so you can't tune a prompt to the exact cases it gets graded on.

The verdicts are `supported`, `not_supported` or `inconclusive`, and each case lists which ones count as correct. That way a cautious answer like "inconclusive" isn't marked wrong when it's reasonable.

| Family | Trap | What goes wrong |
|---|---|---|
| aggregation | `average_of_ratios` | The average of per-campaign rates went up while the pooled rate went down. |
| aggregation | `mix_shift` | The overall rate dropped only because traffic shifted to a low-converting channel. |
| aggregation | `simpsons_paradox` | The group that wins overall loses inside every segment. |
| experiment | `multiple_comparisons` | One of 20 country breakdowns hits p < 0.05 in a flat test. |
| experiment | `outlier_driven_mean` | A revenue-per-user lift that's really a couple of huge orders. |
| experiment | `peeking` | A test stopped the first day p dipped under 0.05, with no real effect. |
| experiment | `sample_ratio_mismatch` | A 50/50 test with visibly unequal arms. |
| measurement | `denominator_change` | Conversion jumped because a filter shrank the denominator the same day. |
| measurement | `instrumentation_break` | A client-side event collapsed while server-side outcomes didn't move. |
| selection | `incomplete_cohort` | The newest cohort's day-30 retention only counts its earliest users. |
| selection | `self_selection` | Feature adopters were already the most engaged users. |
| selection | `small_sample` | A dramatic rate from a few dozen users. |
| temporal | `calendar_effect` | A holiday week blamed on a launch. |
| temporal | `novelty_effect` | A lift that fades to nothing, averaged into a forecast. |
| temporal | `regression_to_mean` | The lowest scorers improved after coaching, like the lowest scorers always do. |

`ldg traps --markdown` prints this with the accepted verdicts for each.

## The rule-based checker

`llm_data_guardrails.guardrail` takes a structured version of a claim (what kind of claim, which table, which groups, which direction, numerator and denominator) and runs whichever checks apply to it, returning pass, warn or block with the evidence. There are 15 checks. A few examples: a stratified test for Simpson's reversals, a rate/mix decomposition, Benjamini-Hochberg across segments, a sample ratio mismatch test, an approximate O'Brien-Fleming boundary for tests stopped early, and splitting a rate change into its numerator and denominator parts. `ldg checks` lists them all.

I've kept it as a baseline to compare against, and because some teams need a check that's deterministic and auditable. Its 100% score on the suite ([`results/guardrail-dev.md`](results/guardrail-dev.md)) doesn't mean much on its own, since I wrote the checks with these exact traps in mind. Missing the generator bugs is the more useful data point.

## Running it

```bash
git clone https://github.com/SourabhK7/llm-data-guardrails.git
cd llm-data-guardrails
pip install -e ".[dev]" anthropic

ldg show simpsons_paradox --hide-truth      # what the model sees
ldg show simpsons_paradox --control         # the matched control, with the answer
ldg verify --n 25                           # regenerate 750 cases and re-check them
ldg guard simpsons_paradox                  # run the rule-based checker on one case

# model runs, need ANTHROPIC_API_KEY in your environment or a .env file
python scripts/pilot.py --n-per-trap 3
python scripts/pilot_premise.py --n-per-trap 3
```

Both scripts stop at a hard budget (`--budget`, in dollars), save every result as it comes back, and with `--resume` they reuse old results as long as the exact prompt hasn't changed.

Sourabh Koul · [LinkedIn](https://www.linkedin.com/in/sourabhkoul/) · [GitHub](https://github.com/SourabhK7)

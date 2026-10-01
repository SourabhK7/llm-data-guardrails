# Review pilot: plain prompt, suite 2026.10, 3 matched pair(s) per trap

Ranges are Wilson 95% intervals.

| Model | Traps caught | Controls correct | Controls rejected outright (false alarm) | Cost |
|---|---|---|---|---|
| `claude-sonnet-5-5` | 45/45 (92% to 100%) | 44/45 (88% to 100%) | 1/45 (0% to 12%) | $0.69 |
| `claude-opus-5-5` | 45/45 (92% to 100%) | 40/45 (77% to 95%) | 5/45 (5% to 23%) | $1.26 |

## Per trap (correct / total)

| Trap | claude-sonnet-5-5 traps | claude-sonnet-5-5 controls | claude-opus-5-5 traps | claude-opus-5-5 controls |
|---|---|---|---|---|
| `average_of_ratios` | 3/3 | 3/3 | 3/3 | 3/3 |
| `calendar_effect` | 3/3 | 2/3 | 3/3 | 3/3 |
| `denominator_change` | 3/3 | 3/3 | 3/3 | 3/3 |
| `incomplete_cohort` | 3/3 | 3/3 | 3/3 | 3/3 |
| `instrumentation_break` | 3/3 | 3/3 | 3/3 | 3/3 |
| `mix_shift` | 3/3 | 3/3 | 3/3 | 3/3 |
| `multiple_comparisons` | 3/3 | 3/3 | 3/3 | 3/3 |
| `novelty_effect` | 3/3 | 3/3 | 3/3 | 3/3 |
| `outlier_driven_mean` | 3/3 | 3/3 | 3/3 | 3/3 |
| `peeking` | 3/3 | 3/3 | 3/3 | 3/3 |
| `regression_to_mean` | 3/3 | 3/3 | 3/3 | 1/3 |
| `sample_ratio_mismatch` | 3/3 | 3/3 | 3/3 | 3/3 |
| `self_selection` | 3/3 | 3/3 | 3/3 | 0/3 |
| `simpsons_paradox` | 3/3 | 3/3 | 3/3 | 3/3 |
| `small_sample` | 3/3 | 3/3 | 3/3 | 3/3 |

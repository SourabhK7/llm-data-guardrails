Guardrail on suite 2026.10, split `dev`, 25 matched pairs per trap (750 cases).

This is a construction check, not a real-world estimate. The checks were designed against this trap taxonomy and the generators guarantee clean separation, so near-perfect numbers are expected. It verifies that each check implements its logic and that no check misfires on another claim kind's controls.

| Family | Trap | Catch rate (95% CI) | False-alarm rate (95% CI) | Checks that fired on traps |
|---|---|---|---|---|
| aggregation | `average_of_ratios` | 100% (87% to 100%) | 0% (0% to 13%) | `pooled_rate` |
| aggregation | `mix_shift` | 100% (87% to 100%) | 0% (0% to 13%) | `rate_mix_decomposition` |
| aggregation | `simpsons_paradox` | 100% (87% to 100%) | 0% (0% to 13%) | `segment_reversal` |
| experiment | `multiple_comparisons` | 100% (87% to 100%) | 0% (0% to 13%) | `multiplicity` |
| experiment | `outlier_driven_mean` | 100% (87% to 100%) | 0% (0% to 13%) | `outlier_influence` |
| experiment | `peeking` | 100% (87% to 100%) | 0% (0% to 13%) | `sequential_boundary` |
| experiment | `sample_ratio_mismatch` | 100% (87% to 100%) | 0% (0% to 13%) | `sample_ratio_mismatch` |
| measurement | `denominator_change` | 100% (87% to 100%) | 0% (0% to 13%) | `denominator_shift` |
| measurement | `instrumentation_break` | 100% (87% to 100%) | 0% (0% to 13%) | `tracking_divergence` |
| selection | `incomplete_cohort` | 100% (87% to 100%) | 0% (0% to 13%) | `cohort_completeness` |
| selection | `self_selection` | 100% (87% to 100%) | 0% (0% to 13%) | `stratified_effect` |
| selection | `small_sample` | 100% (87% to 100%) | 0% (0% to 13%) | `segment_interval` |
| temporal | `calendar_effect` | 100% (87% to 100%) | 0% (0% to 13%) | `seasonal_baseline` |
| temporal | `novelty_effect` | 100% (87% to 100%) | 0% (0% to 13%) | `effect_stability` |
| temporal | `regression_to_mean` | 100% (87% to 100%) | 0% (0% to 13%) | `selection_regression` |
| **all** | | **100% (99% to 100%)** | **0% (0% to 1%)** | |

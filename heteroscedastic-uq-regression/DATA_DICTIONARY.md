# Data dictionary

The `results/` directory contains analysis outputs rather than raw confidential data.

- `*_seed*.csv`: one row per optimization seed or simulation seed.
- `*_fold_results.csv`: one row per validation fold and model.
- `*_oof_predictions.csv`: out-of-fold observed values, predictions, and interval bounds.
- `*_summary.csv`: aggregate descriptive summaries across seeds, folds, or outer realizations.
- `*_bootstrap_contrasts.csv`: bootstrap contrasts between prespecified model pairs.
- `protocol_b_*`: outer-data-realization and inner-optimization sensitivity outputs.
- `synthetic_region_*`: region-specific in-distribution, left-OOD, and right-OOD metrics.
- `synthetic_ood_utility_*`: OOD discrimination and uncertainty-error association outputs.
- `synthetic_selective_risk_*`: selective prediction risk as observations are retained by uncertainty rank.
- `sbpr_multicollinearity_*`: controlled multicollinearity ablation outputs.
- `posterior_draw_sensitivity_*`: predictive-interval sensitivity to the number of posterior draws.
- `compute_benchmark*`: measured CPU training/inference time and related metadata.
- `software_hardware_environment.json`: software versions and hardware information.
- `pytest_log.txt`: test-suite result.

# Detector SQL (P2)

Rendered and run in order by `python -m services.detector pipeline`. Placeholders:

| Placeholder | Meaning |
|---|---|
| `__P__` | project id (scripts/run_sql.py) |
| `__TRADES__`, `__ACCOUNTS__`, `__MARKETS__` | input tables (live, or a validation seed in `eval_val`) |
| `__OUT__`, `__PFX__` | output dataset and table-name prefix |

Scenario-agnostic by construction: nothing here names an account, device or funding-source
id, and nothing reads `eval.ground_truth` or `eval.decoy_groups`
(`tests/unit/test_detector_no_leakage.py`). Files named `eval_*.sql` are evaluation scripts.

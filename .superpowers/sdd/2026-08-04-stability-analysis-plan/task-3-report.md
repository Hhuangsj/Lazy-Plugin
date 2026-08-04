# Task 3 report

## Changed files

- `skills/science/stability-analysis/scripts/analyze_stability.py`
- `skills/science/stability-analysis/tests/test_analyze_stability.py`

## Verification

- `python3 -m py_compile skills/science/stability-analysis/scripts/analyze_stability.py` — passed.
- `python3 -m unittest discover -s skills/science/stability-analysis/tests -v` — passed, 19 tests.
- `git diff --check` — passed.

## Covered behavior

The tests cover lower/higher activity thresholds, unfiltered activity, exact scope and per-group summary counts, raw stability values, reference-first candidate output, activity sorting, automatic position columns, and CLI validation errors.

# Task 3 report

## Changed files

- `skills/science/stability-analysis/scripts/analyze_stability.py`
- `skills/science/stability-analysis/tests/test_analyze_stability.py`

## Verification

- `python3 -m py_compile skills/science/stability-analysis/scripts/analyze_stability.py` — passed.
- `python3 -m unittest discover -s skills/science/stability-analysis/tests -v` — passed, 21 tests.
- `git diff --check` — passed.

## Covered behavior

The tests cover lower/higher activity thresholds, unfiltered activity, exact scope and per-group summary counts, raw stability values, reference-first candidate output, activity sorting, automatic position columns, and CLI validation errors.

## Review fixes

- Candidate CSV now writes the requested activity source column after selected stability columns and before position columns, retaining its raw string.
- Candidate CSV and summary paths are rejected before reading or writing when they resolve to an input path or, when both paths exist, refer to the same file through a hard link.
- The full test command above includes regression coverage for candidate and summary hard-link outputs and verifies the input contents remain unchanged.

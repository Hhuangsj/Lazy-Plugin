---
name: stability-analysis
description: Analyze assay stability from user-provided CSV or XLSX tables, using an optional reference, scope, activity filter, and stability columns to produce candidate and summary files.
---

# Stability analysis

Use this skill when the user asks to analyze assay or compound stability from a CSV/XLSX table, identify stability candidates, or compare stability measurements under an activity constraint.

## Conversational protocol

Follow this order before running the analysis:

1. Require one or more user-provided CSV/XLSX files. If no input files were provided, ask the user to attach or identify them.
2. Accept an optional reference identifier. If it matches zero rows or multiple rows, show the relevant ambiguity and ask the user to confirm the intended row or identifier before continuing.
3. Accept an optional grouping column or scope, such as `Project` or `Pipeline`. If none is provided, analyze the full input table.
4. Require an activity column, an activity direction (`lower` or `higher`), and an optional threshold before filtering. If the threshold is omitted, report an overview without filtering rather than inventing a cutoff.
5. Ask the user for stability columns when they cannot be identified from the request or the input headers.
6. Run `scripts/analyze_stability.py` with explicit arguments, then report the output CSV path and summary path.

Do not assume that a particular activity name, reference format, grouping field, or stability-column naming convention is universal. Preserve the user's column names exactly when passing them to the script.

## Invocation

Run the script from the repository root (or use the skill's repository root as the working directory). Include only arguments supported by the available inputs and the user's confirmed choices. For example, using current AssayView-style columns:

```bash
python3 scripts/analyze_stability.py \
  --input AssayView.csv \
  --output-csv stability_candidates.csv \
  --summary stability_summary.md \
  --reference SYN-003333 \
  --activity-column 'Reporter_Cell_NPR1/EC50(nM)' \
  --activity-direction lower \
  --activity-threshold 2 \
  --stability-column 'In-vitro stability/T1/2 (h), Mouse Serum' \
  --stability-column 'In-vitro stability/T1/2 (h), SIF' \
  --stability-column 'In-vitro stability/T1/2 (h), SGF'
```

The AssayView-style columns in this example are illustrative, not mandatory. When the threshold is omitted, omit `--activity-threshold` and explain that the result is an unfiltered overview. When a grouping column or scope is confirmed, pass the corresponding explicit script option supported by the implementation.

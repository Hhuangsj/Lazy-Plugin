# Stability Analysis Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal `stability-analysis` skill that reads user-provided CSV/XLSX tables, compares an optional reference molecule, applies user-selected activity rules, summarizes available stability columns, and writes an AssayView-like candidate CSV.

**Architecture:** Add one skill under the existing `skills/science/` directory; the skill instructions handle the conversational questions, while one Python script performs deterministic table loading, filtering, summary calculation, and CSV writing. CSV uses the standard library; XLSX uses an explicit `openpyxl` adapter and reports a clear dependency error when unavailable.

**Tech Stack:** Markdown, Python 3, standard library (`csv`, `argparse`, `statistics`, `unittest`), `openpyxl` for XLSX input.

## Global Constraints

- Accept only user-provided input paths; do not scan the workspace for a default table.
- Support `.csv` and `.xlsx`; preserve source-file provenance when multiple tables are merged.
- Do not hardcode Cys/Pen, a Series definition, or EC50 as the universal activity field.
- Do not modify source tables; write analysis outputs to user-selected paths.
- Preserve raw values such as blanks, `-`, `<`, `>`, `0`, and upper-limit values in exported rows.
- Keep unrelated existing changes in the Lazy-Plugin worktree untouched.

---

### Task 1: Add the skill contract and runtime dependency declaration

**Files:**
- Create: `skills/science/stability-analysis/SKILL.md`
- Create: `skills/science/stability-analysis/requirements.txt`

**Interfaces:**
- `SKILL.md` tells the agent when to invoke the skill, asks for missing input/reference/activity information, and invokes `scripts/analyze_stability.py` with explicit arguments.
- `requirements.txt` declares `openpyxl>=3.1,<4` for XLSX input; no pandas dependency is added.

- [ ] **Step 1: Write the skill instructions**

  Define the conversational protocol in this order:

  1. Require one or more user-provided CSV/XLSX files; ask for them if absent.
  2. Accept an optional reference identifier and ask for confirmation if it matches zero or multiple rows.
  3. Accept an optional grouping column or scope such as Project/Pipeline; if absent, analyze the full input table.
  4. Require an activity column, direction (`lower` or `higher`), and optional threshold before filtering; if the threshold is omitted, report an overview without filtering.
  5. Ask for stability columns when they cannot be identified from the user's request.
  6. Run the script and report the output CSV and summary path.

  Include an example that uses the current AssayView-style columns without making those columns mandatory:

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

- [ ] **Step 2: Declare the XLSX dependency**

  Add exactly:

  ```text
  openpyxl>=3.1,<4
  ```

- [ ] **Step 3: Check the documentation contract**

  Run:

  ```bash
  grep -n -E 'ANCHORS|AA_POS|EC50.*唯一|TODO|TBD' skills/science/stability-analysis/SKILL.md
  ```

  Expected: no structure-anchor implementation rule, no statement that EC50 is universal, and no TODO/TBD placeholders.

### Task 2: Implement table loading, merging, and field resolution

**Files:**
- Create: `skills/science/stability-analysis/scripts/analyze_stability.py`
- Create: `skills/science/stability-analysis/tests/test_analyze_stability.py`
- Create: `skills/science/stability-analysis/tests/fixtures/sample.csv`

**Interfaces:**
- `read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]`
- `merge_tables(paths: list[Path]) -> tuple[list[str], list[dict[str, str]]]`
- `resolve_id_column(headers: list[str], requested: str | None) -> str`
- `find_reference(rows: list[dict[str, str]], identifier: str, id_columns: list[str]) -> dict[str, str]`
- `resolve_column(headers: list[str], requested: str) -> str`

- [ ] **Step 1: Write failing loader tests**

  Add tests that verify:

  ```python
  headers, rows = read_table(Path("tests/fixtures/sample.csv"))
  assert headers[0] == "Customer ID"
  assert rows[0]["Customer ID"] == "REF-001"
  ```

  Also test that `merge_tables` appends rows from two CSV inputs and adds `__source_file` without dropping columns that appear in only one file.

- [ ] **Step 2: Run the focused tests and confirm failure**

  Run:

  ```bash
  python3 -m unittest discover -s skills/science/stability-analysis/tests -p 'test_analyze_stability.py' -v
  ```

  Expected: FAIL because the loader functions do not exist yet.

- [ ] **Step 3: Implement CSV and XLSX readers**

  Implement `read_table` as follows:

  - CSV: open with `utf-8-sig`, `csv.DictReader`, and preserve raw cell strings.
  - XLSX: import `openpyxl` lazily, open the first worksheet in read-only/data-only mode, use the first non-empty row as headers, and convert `None` cells to empty strings.
  - If `openpyxl` is missing for an XLSX input, raise an error that names `openpyxl` and points to `requirements.txt`.
  - Reject unsupported extensions with the exact path and supported extensions in the message.

- [ ] **Step 4: Implement merge and lookup helpers**

  - Merge headers by first-seen order.
  - Add `__source_file` to every merged row.
  - Do not silently deduplicate repeated IDs; preserve all input rows.
  - Resolve an explicit ID column exactly; otherwise accept `Customer ID`, `CompoundID`, `ID`, or `Alias` when uniquely available.
  - Match a reference identifier against exact ID fields and comma/semicolon-separated alias tokens.
  - Raise an ambiguity error when more than one row matches.

- [ ] **Step 5: Run the focused tests and confirm pass**

  Run the same discovery command. Expected: PASS for CSV loading, merging, reference matching, missing-column, and ambiguous-reference cases. Run the XLSX test only when `openpyxl` is installed; otherwise assert the documented dependency error.

### Task 3: Implement activity filtering, stability summaries, and candidate CSV output

**Files:**
- Modify: `skills/science/stability-analysis/scripts/analyze_stability.py`
- Modify: `skills/science/stability-analysis/tests/test_analyze_stability.py`

**Interfaces:**
- `parse_first_number(raw: str) -> float | None`
- `filter_by_scope(rows: list[dict[str, str]], group_by: str | None, group_value: str | None) -> list[dict[str, str]]`
- `filter_by_activity(rows: list[dict[str, str]], column: str | None, direction: str | None, threshold: float | None) -> list[dict[str, str]]`
- `summarize_column(rows: list[dict[str, str]], column: str) -> dict[str, object]`
- `write_candidate_csv(rows: list[dict[str, str]], output: Path, id_column: str, stability_columns: list[str], position_columns: list[str]) -> None`
- CLI entry point: `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write failing activity and summary tests**

  Add tests for:

  ```python
  assert parse_first_number("<0.00381;0.008669") == 0.00381
  assert len(filter_by_activity(rows, "EC50", "lower", 2)) == 2
  assert summarize_column(rows, "SIF")["nonempty"] == 2
  ```

  Add an output test asserting that the reference row is first and the header order is:

  ```text
  Customer ID, selected stability columns, detected position columns
  ```

- [ ] **Step 2: Run focused tests and confirm failure**

  Run:

  ```bash
  python3 -m unittest discover -s skills/science/stability-analysis/tests -p 'test_analyze_stability.py' -v
  ```

  Expected: FAIL because the analysis functions do not exist yet.

- [ ] **Step 3: Implement deterministic activity filtering**

  Apply scope before activity filtering:

  - If neither `--group-by` nor `--group-value` is given, keep all rows.
  - If `--group-by` is given without `--group-value`, keep all rows and report per-group counts in the summary.
  - If both are given, keep only rows whose group value exactly matches the requested value.
  - If `--group-value` is given without `--group-by`, return a clear CLI error.

  - Preserve the raw activity cell in output.
  - Extract the first numeric token for filtering; retain `<`/`>` in the raw value and mention censored values in the summary.
  - `lower` keeps values `<= threshold`; `higher` keeps values `>= threshold`.
  - If any activity argument is missing, keep all rows and record that no activity filter was applied.
  - If a row has no parseable activity value, exclude it only when a filter is active; count it as missing.

- [ ] **Step 4: Implement stability summaries**

  For each user-selected stability column, return:

  - total rows
  - non-empty rows
  - missing rows
  - parseable numeric rows
  - median, minimum, and maximum of the first numeric token
  - raw reference-molecule value when a reference exists

  Keep raw boundary strings in the candidate CSV; do not reinterpret `0`, `100`, `<`, or `>`.

- [ ] **Step 5: Implement candidate CSV field selection**

  - Always write the resolved ID column as `Customer ID` in the output.
  - Write selected stability columns using their source headers.
  - Detect position columns from exact numeric headers (`0`, `1`, ...), `AA0`, or `Sequence_Decomposition/AA0` patterns; preserve their input order and values.
  - When position columns are not present, write only ID and selected stability columns.
  - Put the reference row first, then activity-qualified rows sorted by numeric activity (ascending for `lower`, descending for `higher`), then original input order for ties.
  - Leave missing stability cells empty.

- [ ] **Step 6: Implement Markdown summary output and CLI**

  The summary must state input files, row count, reference match, group/scope, activity rule, stability columns, missingness, reference values, and output path. CLI options must include:

  ```text
  --input PATH [PATH ...]
  --output-csv PATH
  --summary PATH
  --reference VALUE
  --id-column NAME
  --group-by NAME
  --group-value VALUE
  --activity-column NAME
  --activity-direction {lower,higher}
  --activity-threshold NUMBER
  --stability-column NAME   (repeatable)
  --position-column NAME    (repeatable; optional override)
  ```

- [ ] **Step 7: Run the focused tests and confirm pass**

  Run the same discovery command and verify the generated CSV with:

  ```bash
  python3 - <<'PY'
  import csv
  from pathlib import Path
  p = Path("/tmp/stability-candidates.csv")
  with p.open(newline="", encoding="utf-8") as f:
      rows = list(csv.reader(f))
  assert rows[0][0] == "Customer ID"
  assert rows[1][0] == "REF-001"
  PY
  ```

### Task 4: Validate skill discovery and handoff documentation

**Files:**
- Modify: `skills/science/stability-analysis/SKILL.md` only if validation finds an instruction mismatch.

**Interfaces:**
- The existing marketplace entry remains unchanged because it already exposes `./skills/science` recursively.
- `toolenv index skills/science/stability-analysis/scripts` discovers the script from its header metadata.

- [ ] **Step 1: Add script metadata**

  Put these header lines at the top of `analyze_stability.py`:

  ```text
  # @name: analyze_stability
  # @description: Compare user-provided table rows against an optional reference and summarize stability
  # @requires: python3
  # @usage: analyze_stability.py --input <table.csv|table.xlsx> --output-csv <out.csv> --summary <summary.md>
  ```

- [ ] **Step 2: Run all relevant validation**

  Run:

  ```bash
  python3 -m unittest discover -s skills/science/stability-analysis/tests -v
  ./toolenv/toolenv index skills/science/stability-analysis/scripts
  bash toolenv/tests/run_tests.sh
  git diff --check
  ```

  Expected: the new tests pass, the script appears in the tool index, the existing toolenv tests remain green, and no whitespace errors are reported.

- [ ] **Step 3: Inspect the final diff**

  Run:

  ```bash
  git status --short
  git diff -- skills/science/stability-analysis
  ```

  Confirm that only the new skill files are part of the feature change; do not stage or alter the existing MD worktree changes.

# MMGBSA Trajectory Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route normal and decomposed MM/GBSA through the shared strict raw/Align CMS-trajectory selector.

**Architecture:** Extend `trajectory_source.sh` with optional raw overrides, then make `run_mmgbsa.sh` select one complete pair before touching output. Pass the selected trajectory into DECOMP instead of scanning every trajectory directory.

**Tech Stack:** Bash, existing fake Schrödinger shell harness, real temporary CMS/trajectory directory fixtures.

## Global Constraints

- Default to automatic raw pair selection.
- Fail automatic raw or Align ambiguity rather than selecting the first match.
- Support `RAW_CMS`/`RAW_TRJ` and existing `ALIGN_CMS`/`ALIGN_TRJ` overrides.
- Select before deleting or creating MM/GBSA output.
- Use the same selected pair for normal and `DECOMP=1` routes.
- Default Align output to `mmgbsa_last100ns_align` so raw output is not overwritten.
- Preserve unrelated Align output while raw DECOMP runs.
- Preserve the user's dirty `run_serial_md.sh` and generated state/log files.

---

### Task 1: Explicit raw selection in the shared selector

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_trajectory_source.sh`
- Modify: `skills/science/md-pipeline/scripts/trajectory_source.sh`

**Interfaces:**
- Consumes: `select_trajectory_pair DIR raw ALIGN_CMS ALIGN_TRJ RAW_CMS RAW_TRJ`, with positional raw values defaulting to the same environment variables.
- Produces: `SELECTED_CMS`, `SELECTED_TRJ`, and `SELECTED_BASE` for a validated raw pair.

- [ ] **Step 1: Add failing explicit-raw selector tests**

Create a second raw pair so automatic raw selection is ambiguous. Assert an explicit raw CMS derives its matching `_trj`; assert an explicit raw trajectory overrides derivation; assert missing explicit CMS and trajectory inputs fail.

- [ ] **Step 2: Run the selector suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_trajectory_source.sh`

Expected: explicit raw calls fail because the current function ignores fifth and sixth arguments.

- [ ] **Step 3: Implement raw override resolution**

Add:

```bash
local raw_cms="${5:-${RAW_CMS:-}}"
local raw_trj="${6:-${RAW_TRJ:-}}"
```

In the raw branch, validate an explicit CMS or perform existing unique discovery, then use explicit `RAW_TRJ` or derive the matching trajectory. Retain final common existence checks.

- [ ] **Step 4: Run the selector suite and verify GREEN**

Run: `bash skills/science/md-pipeline/tests/test_trajectory_source.sh`

Expected: all raw, Align, ambiguity, missing-input, and entry-point integration checks pass.

### Task 2: Strict normal MMGBSA selection

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`
- Modify: `skills/science/md-pipeline/scripts/run_mmgbsa.sh`

**Interfaces:**
- Consumes: the shared selector variables and its selected pair.
- Produces: normal MM/GBSA thermal invocation against only the selected CMS, with selection completed before output deletion.

- [ ] **Step 1: Add failing runner behavior tests**

Add tests proving: two raw pairs return 1, invoke no fake external command, and preserve an existing output sentinel; `RAW_CMS` selects the requested raw CMS; `TRAJECTORY_SOURCE=align` selects an Align CMS, defaults to `mmgbsa_last100ns_align`, and preserves a raw-output sentinel; a missing selected trajectory fails before output deletion and creates no manifest.

- [ ] **Step 2: Run the MMGBSA suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: the legacy `head -1` path silently selects a raw CMS, ignores overrides, or deletes output before detecting trajectory problems.

- [ ] **Step 3: Integrate the shared selector**

Source `trajectory_source.sh`; define `TRAJECTORY_SOURCE`, `ALIGN_CMS`, `ALIGN_TRJ`, `RAW_CMS`, and `RAW_TRJ`; update metadata usage. Default an unset `OUT_NAME` according to the selected source. In `run_one`, call the selector first, assign `cms`, `trj`, and `name` from exported selected values, then construct/remove output. Update the existing missing-trajectory test to the new fail-fast, output-preserving behavior.

- [ ] **Step 4: Run the MMGBSA suite and verify normal-route GREEN**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: normal-route selection and fail-fast missing-trajectory tests pass; the new raw-plus-Align DECOMP test is added in Task 3.

### Task 3: Route DECOMP through the selected trajectory

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`
- Modify: `skills/science/md-pipeline/scripts/run_mmgbsa.sh`

**Interfaces:**
- Consumes: `run_decomp DIR CMS TRJ NAME OUT`.
- Produces: preparation, trajectory link, thermal calculation, and aggregation consistently using `TRJ`.

- [ ] **Step 1: Add the raw-plus-Align coexistence test and update fail-fast expectations**

Replace the old “exactly one `*_trj`” test with a fixture containing one complete raw pair and one complete Align pair; assert raw DECOMP succeeds and aggregation receives the raw trajectory.

- [ ] **Step 2: Run the focused MMGBSA suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: DECOMP still scans both trajectory directories and returns 2.

- [ ] **Step 3: Pass and consume the selected trajectory**

Change `run_decomp` to accept `trj` as its third argument, shift `name` and `out`, remove the `"$dir"/*_trj` scan and exact-count branch, and pass `trj` from `run_one`.

- [ ] **Step 4: Run MMGBSA and selector suites GREEN**

Run:

```bash
bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh
bash skills/science/md-pipeline/tests/test_trajectory_source.sh
```

Expected: all tests pass.

### Task 4: Documentation and verification

**Files:**
- Modify: `skills/science/md-pipeline/SKILL.md`
- Review: all files changed in Tasks 1-3.

**Interfaces:**
- Produces: user-facing raw/Align selection examples and final verification evidence.

- [ ] **Step 1: Document unified selection**

State that event analysis, PLIP, MM/GBSA, and DECOMP accept `TRAJECTORY_SOURCE=align`; document `RAW_CMS`/`RAW_TRJ` for raw ambiguity and existing Align overrides.

- [ ] **Step 2: Run syntax and all md-pipeline tests**

Run `bash -n` on changed shell files, every `test_*.sh`, ordinary-Python contract/adapter tests, and Schrödinger-Python preparation/aggregation tests.

- [ ] **Step 3: Review and integrate**

Run `git diff --check`, inspect the scoped diff and worktree status, request independent review with no Critical or Important findings, fast-forward into `master`, rerun merged-result tests, and remove the owned worktree and merged branch.

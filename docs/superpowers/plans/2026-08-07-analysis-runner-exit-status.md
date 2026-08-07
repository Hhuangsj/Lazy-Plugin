# Analysis Runner Exit Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all three MD analysis runners return non-zero when any requested directory fails while still attempting every directory.

**Architecture:** Keep each runner's existing per-directory subshell and add explicit return-status preservation at the stage and outer-loop boundaries. Use behavior-level shell tests with fake external executables so no Schrödinger, PLIP, or GPU work is submitted.

**Tech Stack:** Bash, existing `toolenv/tests/helpers.sh` test harness, fake command executables.

## Global Constraints

- Continue processing every requested MD directory.
- Return the first non-zero per-directory status after the loop.
- Preserve optional EAF-report skipping as a warning.
- Do not change output-directory validation, trajectory selection, hard-coded paths, or cleanup policy.
- Preserve the user's existing uncommitted `run_serial_md.sh` changes and generated files.

---

### Task 1: MMGBSA strict aggregate status

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`
- Modify: `skills/science/md-pipeline/scripts/run_mmgbsa.sh:240-249`

**Interfaces:**
- Consumes: `run_one <md-dir>` returning the thermal or DECOMP stage status.
- Produces: process exit status equal to the first non-zero directory status, or zero when all succeed.

- [ ] **Step 1: Replace the legacy false-success test and add mixed normal-mode coverage**

Rename `test_default_path_preserves_legacy_success_status_when_thermal_fails` to describe strict propagation and assert status `8`. Add a normal-mode two-directory test using `FAIL_DIRECTORY` that asserts both thermal calls occur and the process returns `8`.

- [ ] **Step 2: Run the focused shell suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: the new normal-mode failure assertions fail because `overall_rc` is currently updated only for `DECOMP=1`.

- [ ] **Step 3: Implement minimal aggregate status handling**

Change the outer loop condition to record any non-zero `rc` only while `overall_rc` is still zero:

```bash
if [ "$rc" -ne 0 ] && [ "$overall_rc" -eq 0 ]; then
    overall_rc=$rc
fi
```

- [ ] **Step 4: Run the focused suite and verify GREEN**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: all MMGBSA shell tests pass.

### Task 2: PLIP status preservation and aggregation

**Files:**
- Create: `skills/science/md-pipeline/tests/test_analysis_runner_status.sh`
- Modify: `skills/science/md-pipeline/scripts/run_plip.sh:63-89`

**Interfaces:**
- Consumes: exit status from `plip_interaction_analysis.py`.
- Produces: `run_one` returns the saved Python status after cleanup; outer process returns the first failed directory status.

- [ ] **Step 1: Add behavior tests for failure, continued processing, cleanup, and success**

Create a fake tool environment, fake `python3`, and two valid MD pairs. Make the fake return `23` for one selected CMS and zero for the other. Assert final status `23`, two invocations, and removal of both temporary output subdirectories. Add an all-success assertion returning zero.

- [ ] **Step 2: Run the new suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: PLIP failure test reports actual status zero.

- [ ] **Step 3: Return the captured status and aggregate outer-loop failures**

Add `return "$rc"` at the end of `run_one`; replace the unconditional outer loop/final echo with the same first-nonzero accumulator used by MMGBSA.

- [ ] **Step 4: Run the new suite and verify the PLIP cases GREEN**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: PLIP cases pass; analysis cases may remain RED until Task 3.

### Task 3: Analysis stage and aggregate status

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_analysis_runner_status.sh`
- Modify: `skills/science/md-pipeline/scripts/run_analysis.sh:57-132`

**Interfaces:**
- Consumes: required-stage statuses from AutoTRJ, EAF analyze/simulation, and report commands.
- Produces: per-directory failure without suppressing later directories; process returns the first failure.

- [ ] **Step 1: Add stage-failure tests**

Use fake `AutoTRJ` and fake `$SCHRODINGER/run`. Assert an AutoTRJ failure status is returned, the second directory is attempted, and an all-success invocation returns zero. Add a report failure case with an existing EAF to prove the report status is not overwritten.

- [ ] **Step 2: Run the analysis cases and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: analysis failure cases return zero before production changes.

- [ ] **Step 3: Fail each directory on required-stage errors and aggregate statuses**

Wrap required commands with `|| return $?`, retaining the explicit warning-only branch for a missing optional EAF. Add the first-nonzero outer-loop accumulator and final `exit "$overall_rc"`.

- [ ] **Step 4: Run the analysis suite and verify GREEN**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: all runner status cases pass.

### Task 4: Verification and review

**Files:**
- Review: all files changed by Tasks 1-3

**Interfaces:**
- Consumes: completed runner changes and tests.
- Produces: fresh evidence for syntax, focused regression, full relevant suite, and clean scoped diff.

- [ ] **Step 1: Run syntax checks**

Run: `bash -n skills/science/md-pipeline/scripts/run_analysis.sh skills/science/md-pipeline/scripts/run_plip.sh skills/science/md-pipeline/scripts/run_mmgbsa.sh skills/science/md-pipeline/tests/test_analysis_runner_status.sh skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

- [ ] **Step 2: Run all md-pipeline tests**

Run every `test_*.sh` and Python unittest file under `skills/science/md-pipeline/tests`, using the repository's existing commands.

- [ ] **Step 3: Inspect scoped diff and whitespace**

Run: `git diff --check` and `git diff -- skills/science/md-pipeline/scripts/run_analysis.sh skills/science/md-pipeline/scripts/run_plip.sh skills/science/md-pipeline/scripts/run_mmgbsa.sh skills/science/md-pipeline/tests`

- [ ] **Step 4: Commit only scoped files**

Stage the three runner files and their two test files. Do not stage `run_serial_md.sh` or generated state/log files. Commit with `fix(md): propagate analysis runner failures`.

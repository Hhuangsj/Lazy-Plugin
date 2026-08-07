# Safe Analysis Output Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent PLIP and MM/GBSA `OUT_NAME` values from escaping the selected MD directory before any output deletion or external command.

**Architecture:** Add one internal Bash helper defining the single-component output-name contract, then source and call it from both runners before their directory loops. Exercise the real runner entry points with fake external tools and sentinel files inside test-only temporary directories.

**Tech Stack:** Bash, existing `toolenv/tests/helpers.sh` shell harness, fake external executables.

## Global Constraints

- Accept a non-empty single path component other than `.` or `..`.
- Reject every value containing `/` with status 2.
- Validate before any requested directory is processed.
- Keep spaces, Unicode, leading dots, underscores, and hyphens compatible.
- Do not change result staging, rollback, trajectory selection, or cleanup policy.
- Preserve the user's uncommitted `run_serial_md.sh` and generated state/log files.

---

### Task 1: MMGBSA unsafe-name regression

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

**Interfaces:**
- Consumes: `OUT_NAME` environment input to the real `run_mmgbsa.sh` entry point.
- Produces: behavior tests requiring status 2, zero fake external calls, and intact sentinels for unsafe names.

- [ ] **Step 1: Add the failing unsafe-name behavior test**

Add a table covering `''`, `.`, `..`, `../victim`, `nested/output`, and `/absolute`. For `../victim`, create `$SANDBOX/victim/sentinel` and assert it remains present. The empty case intentionally fails RED because the current `${OUT_NAME:-...}` assignment silently replaces it with the default.

- [ ] **Step 2: Run the focused suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: unsafe names return zero or invoke the fake thermal command, and `../victim` removes the sentinel in the temporary sandbox.

### Task 2: Shared output-name validator and MMGBSA integration

**Files:**
- Create: `skills/science/md-pipeline/scripts/output_name.sh`
- Create: `skills/science/md-pipeline/tests/test_output_name.sh`
- Modify: `skills/science/md-pipeline/scripts/run_mmgbsa.sh`

**Interfaces:**
- Produces: `validate_analysis_output_name <name>` returning zero for a safe component and 2 for empty, `.`, `..`, or any string containing `/`.
- Consumes: MMGBSA `OUT_NAME`, validated once after argument-count validation and before the directory loop.

- [ ] **Step 1: Add direct contract tests**

Source `output_name.sh` and assert invalid values return 2 while `results`, `.hidden`, `safe name`, and `结果-1_2` return zero.

- [ ] **Step 2: Verify the direct test is RED because the helper is absent**

Run: `bash skills/science/md-pipeline/tests/test_output_name.sh`

Expected: failure while sourcing the missing helper.

- [ ] **Step 3: Implement the minimal shared helper and MMGBSA call site**

Implement:

```bash
validate_analysis_output_name() {
    local name="${1-}"
    case "$name" in
        ''|.|..|*/*)
            echo "ERROR: OUT_NAME must be one non-empty directory name, not '.' or '..': $name" >&2
            return 2
            ;;
    esac
}
```

Change MMGBSA defaulting to distinguish an unset variable from an explicitly empty value, matching PLIP's existing defaulting pattern. Source the helper by absolute script-relative path and exit with its status before entering the MMGBSA directory loop.

- [ ] **Step 4: Verify helper and MMGBSA suites GREEN**

Run:

```bash
bash skills/science/md-pipeline/tests/test_output_name.sh
bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh
```

Expected: all tests pass and the sentinel remains intact.

### Task 3: PLIP integration regression

**Files:**
- Modify: `skills/science/md-pipeline/scripts/run_plip.sh`
- Modify: `skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

**Interfaces:**
- Consumes: the same `validate_analysis_output_name` helper and PLIP `OUT_NAME`.
- Produces: PLIP status 2 with no fake Python call and intact sentinel for unsafe names; safe custom names continue to run.

- [ ] **Step 1: Add unsafe and safe custom-name PLIP tests**

Test `.`, `..`, `../victim`, `nested/output`, and `/absolute` as invalid. Assert the sibling sentinel survives and the fake call log stays empty. Test `safe name-结果` as valid and assert both directories are processed.

- [ ] **Step 2: Run the focused suite and verify RED**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: unsafe-name assertions fail before `run_plip.sh` sources or calls the shared validator.

- [ ] **Step 3: Source and invoke the shared validator in PLIP**

Source `output_name.sh` beside `trajectory_source.sh`; validate after argument-count checking and before the directory loop.

- [ ] **Step 4: Run the focused suite and verify GREEN**

Run: `bash skills/science/md-pipeline/tests/test_analysis_runner_status.sh`

Expected: all PLIP and analysis status tests pass.

### Task 4: Verification and integration

**Files:**
- Review: `output_name.sh`, both runner integrations, and three shell test files.

**Interfaces:**
- Produces: syntax, behavior, regression, review, and merged-result evidence.

- [ ] **Step 1: Run Bash syntax checks and every md-pipeline shell test**

Run `bash -n` on the changed shell files, then run every `skills/science/md-pipeline/tests/test_*.sh` file.

- [ ] **Step 2: Run unchanged Python decomposition suites with their required interpreters**

Run ordinary-Python contract/adapter tests and Schrödinger-Python preparation/aggregation tests using `/home/huangshengjie/software/Schrodinger/2023-4`.

- [ ] **Step 3: Inspect `git diff --check`, scoped diff, and worktree status**

Confirm only the output-name delivery unit changed and no user-owned dirty files entered the branch.

- [ ] **Step 4: Request independent review, integrate locally, and rerun the merged tests**

Require no Critical or Important findings. Fast-forward the verified branch into `master`, preserve user-owned dirty files, rerun the relevant suites, then remove the owned worktree and merged feature branch.

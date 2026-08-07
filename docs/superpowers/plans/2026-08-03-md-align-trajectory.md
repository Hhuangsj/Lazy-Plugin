# MD Align Trajectory Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit raw/Align trajectory source selector to md-pipeline analysis without changing the raw default behavior.

**Architecture:** A shared Bash selector resolves one validated CMS/trajectory pair for both `run_analysis.sh` and `run_plip.sh`. Align mode consumes existing `*_ALIGN-out.cms`/`*_ALIGN_trj` pairs, regenerates event-analysis EAF against the selected topology, and writes separate reports; raw mode keeps the current path.

**Tech Stack:** Bash, Schrödinger/Desmond command wrappers, shell regression tests, existing md-pipeline documentation.

## Global Constraints

- Default `TRAJECTORY_SOURCE` is `raw` and must preserve existing raw output names.
- Align mode must never reuse a raw EAF generated against a different CMS topology.
- Align mode must not delete or overwrite raw report directories by default.
- Do not modify the current checkout's pre-existing changes or trajectory results.
- Do not add dependencies or refactor unrelated MD/MMGBSA behavior.

---

### Task 1: Shared trajectory selection

**Files:**
- Create: `skills/science/md-pipeline/scripts/trajectory_source.sh`
- Create: `skills/science/md-pipeline/tests/test_trajectory_source.sh`

**Interfaces:**
- Consumes: a directory, source name (`raw` or `align`), optional `ALIGN_CMS` and `ALIGN_TRJ` overrides.
- Produces: exported `SELECTED_CMS`, `SELECTED_TRJ`, and `SELECTED_BASE` variables for caller scripts.

- [ ] **Step 1: Write the failing test**

  Add shell assertions for: raw main pair selection, unique Align pair selection, explicit Align CMS derivation, ambiguous Align rejection, missing Align pair rejection, and unknown source rejection.

- [ ] **Step 2: Run the test to verify it fails**

  Run: `bash skills/science/md-pipeline/tests/test_trajectory_source.sh`

  Expected: FAIL because `trajectory_source.sh` does not exist yet.

- [ ] **Step 3: Write the minimal selector**

  Implement `select_trajectory_pair()` with deterministic candidate filtering, strict pair validation, and actionable errors. Do not depend on Schrödinger or conda.

- [ ] **Step 4: Run the test to verify it passes**

  Run: `bash skills/science/md-pipeline/tests/test_trajectory_source.sh`

  Expected: all selector cases pass.

- [ ] **Step 5: Commit**

  ```bash
  git add skills/science/md-pipeline/scripts/trajectory_source.sh skills/science/md-pipeline/tests/test_trajectory_source.sh
  git commit -m "feat: add shared MD trajectory source selector"
  ```

### Task 2: Use Align pair in analysis entry points

**Files:**
- Modify: `skills/science/md-pipeline/scripts/run_analysis.sh`
- Modify: `skills/science/md-pipeline/scripts/run_plip.sh`

**Interfaces:**
- Consumes: `TRAJECTORY_SOURCE`, `ALIGN_CMS`, `ALIGN_TRJ`, and the selector outputs from Task 1.
- Produces: raw-compatible analysis by default; Align event reports in `analysis_align/` and PLIP outputs in `plip_last100ns_align/` by default.

- [ ] **Step 1: Add the source-selection integration test**

  Extend the shell test with static assertions that both entry points source `trajectory_source.sh` and expose `TRAJECTORY_SOURCE`.

- [ ] **Step 2: Run the test to verify the integration assertions fail**

  Run: `bash skills/science/md-pipeline/tests/test_trajectory_source.sh`

  Expected: FAIL on the entry-point integration assertions.

- [ ] **Step 3: Implement raw/align branches**

  In `run_analysis.sh`, keep raw AutoTRJ `-C ... -a`; in Align mode pass the selected Align trajectory to AutoTRJ without `-C` or `-a`, regenerate EAF from selected CMS/trj, and report to `analysis_align/`. In `run_plip.sh`, replace duplicate CMS/trj discovery with the selector and choose a separate default output directory for Align mode.

- [ ] **Step 4: Run focused checks**

  Run:

  ```bash
  bash skills/science/md-pipeline/tests/test_trajectory_source.sh
  bash -n skills/science/md-pipeline/scripts/trajectory_source.sh
  bash -n skills/science/md-pipeline/scripts/run_analysis.sh
  bash -n skills/science/md-pipeline/scripts/run_plip.sh
  ```

  Expected: all tests pass and all scripts have exit code 0.

- [ ] **Step 5: Commit**

  ```bash
  git add skills/science/md-pipeline/scripts/run_analysis.sh skills/science/md-pipeline/scripts/run_plip.sh skills/science/md-pipeline/tests/test_trajectory_source.sh
  git commit -m "feat: support aligned trajectories in MD analysis"
  ```

### Task 3: Document and verify the plugin update

**Files:**
- Modify: `skills/science/md-pipeline/SKILL.md`
- Modify: `skills/science/md-pipeline/references/original-readme.md`
- Modify: `skills/science/md-pipeline/references/troubleshooting.md`

**Interfaces:**
- Consumes: the `TRAJECTORY_SOURCE` behavior from Task 2.
- Produces: user-facing commands and troubleshooting notes for Align mode.

- [ ] **Step 1: Add usage documentation**

  Document `TRAJECTORY_SOURCE=align ./run_analysis.sh DIR` and the corresponding PLIP command, including explicit `ALIGN_CMS`/`ALIGN_TRJ` overrides and separate output locations.

- [ ] **Step 2: Run the complete relevant checks**

  Run:

  ```bash
  bash skills/science/md-pipeline/tests/test_trajectory_source.sh
  bash skills/science/md-pipeline/tests/test_env_shim.sh
  bash toolenv/tests/run_tests.sh
  bash -n skills/science/md-pipeline/scripts/trajectory_source.sh skills/science/md-pipeline/scripts/run_analysis.sh skills/science/md-pipeline/scripts/run_plip.sh
  git diff --check
  ```

  Expected: all tests pass, syntax checks pass, and `git diff --check` is silent.

- [ ] **Step 3: Commit**

  ```bash
  git add skills/science/md-pipeline/SKILL.md skills/science/md-pipeline/references/original-readme.md skills/science/md-pipeline/references/troubleshooting.md
  git commit -m "docs: document aligned MD trajectory analysis"
  ```

### Task 4: Review, validate, and push

**Files:**
- Review: all commits on `feat/md-align-trajectory` since `8436c52`.

**Interfaces:**
- Consumes: completed implementation and verification evidence from Tasks 1–3.
- Produces: pushed branch on the configured `origin` GitHub remote.

- [ ] **Step 1: Review the complete diff**

  Run `git diff 8436c52..HEAD --stat` and inspect the full diff for scope, raw compatibility, and accidental result-file changes.

- [ ] **Step 2: Run final verification**

  Re-run the complete relevant checks from Task 3 and confirm the worktree contains only intended source/docs/tests.

- [ ] **Step 3: Push the feature branch**

  Run `git push -u origin feat/md-align-trajectory`.

- [ ] **Step 4: Report exact commit and remote branch**

  Report the pushed branch, commit SHA, tests, and any unverified requirement (actual Schrödinger analysis is not run).

# Serial MD Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run_serial_md.sh` honor working-directory and immediate-submission contracts while delegating all post-MD analysis to the authoritative `run_analysis.sh` entry point.

**Architecture:** Keep the serial runner responsible for argument normalization, task claiming, GPU assignment, and AutoMD invocation. Resolve all state paths once after parsing, adapt the conflicting AutoMD/analysis frame variables at the process boundary, and replace inline shell-string analysis with one injectable `ANALYSIS_RUNNER` command.

**Tech Stack:** Bash 4.4+, existing zero-dependency shell test harness, fake command fixtures, Claude plugin validator, Python/pytest, Schrödinger 2023-4 Python.

## Global Constraints

- Preserve the authorized uncommitted `RECEPTOR_ASL`, `LIGAND_ASL`, `AUTOMD_CPU_HOST`, `AUTOMD_GPU_HOST`, and `--submit-immediately` features.
- Do not run or submit a real AutoMD, GPU, or production analysis job during tests.
- `FRAMES` remains AutoMD's numeric output-frame setting; `ANALYSIS_FRAMES` controls `run_analysis.sh` and defaults to `1:2001:20`.
- Relative `--list`, `--completed`, and `--failed` paths resolve against the final absolute workdir, independent of option order.
- `run_analysis.sh` is the only authoritative AutoTRJ/event-analysis implementation.
- Do not alter the restart rule for an existing matching `*-md` directory.
- Do not stage generated serial lists/logs or unrelated files.

---

### Task 1: Working-directory and option-path contract

**Files:**
- Create: `skills/science/md-pipeline/tests/test_run_serial_md.sh`
- Modify: `skills/science/md-pipeline/scripts/run_serial_md.sh`

**Interfaces:**
- Consumes: `run_serial_md.sh [--workdir DIR] [--list FILE] [--completed FILE] [--failed FILE]`.
- Produces: absolute `WORKDIR`, `PENDING_LIST`, `COMPLETED_LIST`, `FAILED_LIST`, and `LOG_FILE` values resolved once after argument parsing.

- [ ] **Step 1: Create the isolated serial-runner test harness**

Source `toolenv/tests/helpers.sh`, create a fake toolenv that prepends `$SANDBOX/bin`, and expose a helper that runs the real serial script without real dependencies:

```bash
RUN_SERIAL="$SKILL_DIR/scripts/run_serial_md.sh"

install_fake_environment() {
    mkdir -p "$SANDBOX/bin" "$SANDBOX/fake-schrodinger"
    : > "$SANDBOX/calls.log"
    printf '0\n' > "$SANDBOX/nvidia-calls"
    # fake-toolenv prints SCHRODINGER and PATH exports; fake AutoMD,
    # nvidia-smi, and analysis-runner record NUL-safe argv/environment.
}

run_serial_from() {
    local invocation_dir=$1
    shift
    (
        cd "$invocation_dir" || exit 98
        TOOLENV_BIN="$SANDBOX/fake-toolenv" \
        ANALYSIS_RUNNER="$SANDBOX/bin/fake-analysis" \
        PATH="/usr/bin:/bin" \
        bash "$RUN_SERIAL" "$@"
    ) > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}
```

The fake AutoMD reads its `-i` argument, records all argv, and creates `${input%.mae}-123-md`. The fake analysis runner records its argv and relevant environment. The fake `nvidia-smi` increments a counter and exits 97 so tests detect any unintended probe.

- [ ] **Step 2: Add failing tests for invocation and explicit workdirs**

Add tests equivalent to:

```bash
test_invocation_directory_supplies_all_default_paths() {
    install_fake_environment
    make_pending_workdir "$SANDBOX/work" sample.mae
    assert_status 0 run_serial_from "$SANDBOX/work" --dry-run --gpu 0
    assert_contains "$(cat "$SANDBOX/stderr.log")" \
        "$SANDBOX/work/md_completed_serial.list"
}

test_workdir_supplies_all_default_paths() {
    install_fake_environment
    make_pending_workdir "$SANDBOX/work" sample.mae
    assert_status 0 run_serial_from "$SANDBOX/caller" \
        --workdir "$SANDBOX/work" --dry-run --gpu 0
    assert_contains "$(cat "$SANDBOX/stderr.log")" \
        "$SANDBOX/work/md_completed_serial.list"
}
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```bash
bash skills/science/md-pipeline/tests/test_run_serial_md.sh
```

Expected: invocation-directory and `--workdir` tests fail because the runner still looks for `md_pending_serial.list` in its own script directory.

- [ ] **Step 4: Add option-order and missing-value RED tests**

Use `--list state/pending --workdir "$work" --completed state/done --failed state/fail` and assert the dry-run reports `$work/state/done`. For each value-taking option, invoke it without a following argument and assert status 2 plus `requires a value` on stderr.

- [ ] **Step 5: Implement one-time path normalization**

At startup capture `INVOCATION_DIR=$PWD`, initialize work/state paths to empty strings, parse raw option values, and call:

```bash
resolve_run_paths() {
    local value
    WORKDIR=${WORKDIR:-$INVOCATION_DIR}
    WORKDIR="$(cd "$WORKDIR" 2>/dev/null && pwd)" || {
        echo "Workdir not found: $WORKDIR" >&2
        return 2
    }
    for variable in PENDING_LIST COMPLETED_LIST FAILED_LIST; do
        value=${!variable}
        if [[ -z "$value" ]]; then
            case "$variable" in
                PENDING_LIST) value=md_pending_serial.list ;;
                COMPLETED_LIST) value=md_completed_serial.list ;;
                FAILED_LIST) value=md_failed_serial.list ;;
            esac
        fi
        [[ "$value" == /* ]] || value="$WORKDIR/$value"
        printf -v "$variable" '%s' "$value"
    done
    LOG_FILE="$WORKDIR/run_serial_md.log"
}
```

Before reading `$2`, each value-taking option calls a helper that checks `[[ $# -ge 2 ]]` and otherwise exits 2 with `Option $1 requires a value.` Update usage text to say the default workdir is the invocation directory and explicit relative state files are workdir-relative.

- [ ] **Step 6: Run the focused suite and verify GREEN**

Run `bash skills/science/md-pipeline/tests/test_run_serial_md.sh`.

Expected: all path and missing-value tests pass; no real external command runs.

---

### Task 2: GPU gate and authoritative analysis delegation

**Files:**
- Modify: `skills/science/md-pipeline/tests/test_run_serial_md.sh`
- Modify: `skills/science/md-pipeline/scripts/run_serial_md.sh`

**Interfaces:**
- Consumes: explicit `GPU_ID`/`GPU_IDS`, `DRY_RUN`, `SUBMIT_IMMEDIATELY`, generated absolute MD directory, `ANALYSIS_FRAMES`.
- Produces: no-probe immediate/dry-run GPU behavior and `ANALYSIS_RUNNER ABSOLUTE_MD_DIRECTORY` with adapted environment.

- [ ] **Step 1: Add a single-GPU immediate-submission RED test**

Run a real fake-command chain with `--gpu 2 --submit-immediately`. Assert status 0, zero fake `nvidia-smi` calls, an AutoMD call containing `CUDA_VISIBLE_DEVICES=2`, a fake-analysis call, and `sample.mae` only in the completed list.

- [ ] **Step 2: Verify the immediate-submission test fails for the expected reason**

Run the focused test function through the suite. Expected: status 97 or an unexpected GPU-probe count because the single-GPU main loop still calls `wait_for_free_gpu`.

- [ ] **Step 3: Implement the shared skip-wait condition**

In the single loop:

```bash
if [[ -n "$GPU_ID" && ( "$DRY_RUN" == true || "$SUBMIT_IMMEDIATELY" == true ) ]]; then
    gpu=$GPU_ID
else
    gpu="$(wait_for_free_gpu)"
fi
```

In `worker_loop`, skip `wait_for_gpu` when either dry-run or immediate submission is true. Keep the existing validation that immediate mode requires explicit GPU IDs.

- [ ] **Step 4: Add and pass a multi-GPU dry-run no-probe test**

Create two pending `.mae` files, run `--dry-run --gpus 0,1`, and assert status 0, zero fake `nvidia-smi` calls, and one printed AutoMD command for each input.

- [ ] **Step 5: Add analysis-boundary RED tests**

Set:

```text
FRAMES=4000
ANALYSIS_FRAMES=101:4001:40
RECEPTOR_ASL=chain.name A
LIGAND_ASL=chain.name B
AUTOMD_CPU_HOST=cpu-host
AUTOMD_GPU_HOST=gpu-host
```

Assert AutoMD receives `-o 4000`, `-P chain.name A`, `-L chain.name B`, `-H cpu-host`, and `-G gpu-host`. Assert fake analysis receives the absolute generated MD directory with `TRAJECTORY_SOURCE=raw`, `FRAMES=101:4001:40`, and both ASLs. The current implementation must fail because it executes inline AutoTRJ/event-analysis commands instead.

- [ ] **Step 6: Replace duplicate analysis with one runner invocation**

Define:

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYSIS_RUNNER="${ANALYSIS_RUNNER:-$SCRIPT_DIR/run_analysis.sh}"
ANALYSIS_FRAMES="${ANALYSIS_FRAMES:-1:2001:20}"
```

Remove `AUTOTRJ_SHELL_CMD` and `run_shell_or_print`. After locating `md_dir`, execute or print this array-safe boundary:

```bash
analysis_command=(env TRAJECTORY_SOURCE=raw FRAMES="$ANALYSIS_FRAMES" \
    "$ANALYSIS_RUNNER" "$md_dir")
run_or_print "${analysis_command[@]}"
```

Remove the hardcoded `/data1/.../miniforge3` `LD_LIBRARY_PATH` export and the direct hardcoded Schrödinger fallback. `env.sh` and the delegated runner own environment activation.

- [ ] **Step 7: Add analysis-failure and static-authority tests**

Make fake analysis exit 37, then assert serial status 1, `sample.mae` in the failed list, and absence from the completed list. Also assert the production file contains `run_analysis.sh`/`ANALYSIS_RUNNER` but no `AUTOTRJ_SHELL_CMD`, `event_analysis.py`, `bash -lc`, or `/data1/`.

- [ ] **Step 8: Run the serial suite GREEN**

Run `bash skills/science/md-pipeline/tests/test_run_serial_md.sh`.

Expected: all tests pass with zero real jobs and no unexpected warnings.

---

### Task 3: Documentation, complete regression, review, and commit

**Files:**
- Modify: `skills/science/md-pipeline/SKILL.md`
- Modify: `skills/science/md-pipeline/scripts/run_serial_md.sh`
- Modify: `skills/science/md-pipeline/tests/test_run_serial_md.sh`
- Review: `docs/superpowers/specs/2026-08-07-run-serial-md-orchestration-design.md`

**Interfaces:**
- Consumes: completed path/GPU/analysis contracts from Tasks 1-2.
- Produces: user-facing examples, full verification evidence, and one implementation commit containing the authorized pre-existing feature diff plus fixes.

- [ ] **Step 1: Update user-facing usage**

Document:

```bash
cd /path/to/md-workdir
$SKILL/scripts/run_serial_md.sh --dry-run --gpu 2
$SKILL/scripts/run_serial_md.sh --gpu 2 --submit-immediately
ANALYSIS_FRAMES='1:2001:20' LIGAND_ASL='chain.name B' \
  $SKILL/scripts/run_serial_md.sh --workdir /path/to/md-workdir --gpu 2
```

State that immediate mode bypasses only the runner's free-GPU gate, relative state files are workdir-relative, and analysis is delegated to `run_analysis.sh`.

- [ ] **Step 2: Run syntax and every shell suite**

```bash
bash -n skills/science/md-pipeline/scripts/run_serial_md.sh \
  skills/science/md-pipeline/tests/test_run_serial_md.sh
for test_file in skills/science/md-pipeline/tests/test_*.sh; do
  bash "$test_file" || exit $?
done
```

Expected: all shell tests pass.

- [ ] **Step 3: Run ordinary and Schrödinger Python regressions**

```bash
python3 -m pytest \
  skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py \
  skills/science/md-pipeline/tests/test_synergy_residue_adapter.py -q
/home/huangshengjie/software/Schrodinger/2023-4/run python3 -m pytest \
  skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py \
  skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py -q
```

Expected: ordinary tests pass with only documented dependency skips; Schrödinger tests all pass.

- [ ] **Step 4: Run repository/plugin verification**

```bash
bash toolenv/tests/run_tests.sh
claude plugin validate --strict .
git diff --check
```

Expected: toolenv reports `ALL PASS`, plugin validation passes, and no whitespace errors appear.

- [ ] **Step 5: Audit the final diff and authoritative path**

Confirm the only uncommitted implementation files are the serial runner, its new test, and `SKILL.md`; generated lists/logs remain ignored. Use `rg` to prove `run_serial_md.sh` has no inline AutoTRJ/event-analysis implementation, no `bash -lc`, and no `/data1/` path.

- [ ] **Step 6: Commit the verified implementation**

```bash
git add skills/science/md-pipeline/scripts/run_serial_md.sh \
  skills/science/md-pipeline/tests/test_run_serial_md.sh \
  skills/science/md-pipeline/SKILL.md
git commit -m "fix(md): harden serial MD orchestration"
```

The commit intentionally includes the user-authorized ASL/host/immediate-submission changes that were already present in `run_serial_md.sh`.

- [ ] **Step 7: Re-run focused verification on the committed tree**

Run the serial shell suite, Bash syntax check, `git diff --check HEAD^ HEAD`, plugin validation, and inspect `git status --short`.

Expected: all pass and the working tree contains no uncommitted serial implementation or generated runtime artifacts.

# Analysis Runner Exit Status Design

## Scope

Fix false-success exit statuses in the three MD analysis entry points:

- `run_analysis.sh`
- `run_plip.sh`
- `run_mmgbsa.sh`, including both normal and `DECOMP=1` modes

Output-directory validation, trajectory selection, hard-coded paths, and cleanup are separate delivery units and are not changed here.

## Required behavior

Each runner continues processing every requested MD directory. It records the first non-zero per-directory status and exits with that status after the loop. It exits zero only when every requested directory succeeds.

Within `run_analysis.sh`, a failed required stage must make that directory fail instead of being overwritten by a later `echo`. Optional behavior remains optional: absence of an EAF where the existing script explicitly permits skipping the report remains a warning, not a new failure.

Within `run_plip.sh`, cleanup still runs after the Python command, but the captured Python status is returned after cleanup.

Within `run_mmgbsa.sh`, normal and decomposition runs use the same aggregate exit-status rule. This intentionally replaces the legacy normal-mode false-success behavior.

## Implementation boundaries

A small, local status-accumulation pattern is sufficient. No shared framework or unrelated refactor will be introduced. Existing log messages and the per-directory subshell isolation are retained.

## Tests

Add or update shell regression tests to prove:

1. A required command failure produces a non-zero final process status.
2. A later directory is still attempted after an earlier failure.
3. All-success input produces status zero.
4. PLIP cleanup does not erase the captured failure status.
5. Both normal MMGBSA and `DECOMP=1` failures propagate.

Tests will be written and observed failing before production changes, then rerun after the minimal implementation. Relevant existing suites and shell syntax checks will be run before completion.

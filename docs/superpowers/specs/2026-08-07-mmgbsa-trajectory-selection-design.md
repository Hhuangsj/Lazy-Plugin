# MMGBSA Trajectory Selection Design

## Scope

Make MM/GBSA use the same strict raw/Align CMS and trajectory selection as the other MD analysis runners. Remove its silent first-CMS choice and the DECOMP-only requirement that the directory contain exactly one `*_trj` entry.

Output staging and rollback, serial-MD paths, and scientific MM/GBSA calculations are outside this delivery unit.

## Shared selector

Extend `select_trajectory_pair` with optional raw overrides while preserving its current callers:

```text
select_trajectory_pair DIR SOURCE ALIGN_CMS ALIGN_TRJ RAW_CMS RAW_TRJ
```

The positional raw arguments default to environment variables `RAW_CMS` and `RAW_TRJ`. For `SOURCE=raw`:

- Without `RAW_CMS`, require exactly one eligible raw `*-out.cms`.
- With `RAW_CMS`, resolve the explicit file directly, even when automatic discovery is ambiguous.
- Without `RAW_TRJ`, derive `<selected-base>_trj` beside the selected CMS.
- With `RAW_TRJ`, resolve and validate the explicit trajectory directory.

Existing Align behavior and `ALIGN_CMS`/`ALIGN_TRJ` compatibility remain unchanged. All selected CMS files and trajectory directories must exist. Unknown sources still fail.

## MMGBSA integration

`run_mmgbsa.sh` sources `trajectory_source.sh` and exposes `TRAJECTORY_SOURCE`, `ALIGN_CMS`, `ALIGN_TRJ`, `RAW_CMS`, and `RAW_TRJ`, defaulting to raw automatic selection.

For each MD directory, the runner selects one pair before constructing or deleting its output directory. Selection failure returns 1, preserves existing output, performs no external MM/GBSA call, and does not create a DECOMP manifest.

Normal thermal MM/GBSA receives the selected CMS. DECOMP receives both the selected CMS and selected trajectory; it no longer scans every `*_trj` entry. An unrelated or Align trajectory may coexist with the raw trajectory without causing raw DECOMP to fail.

## Alternatives considered

- Add a MMGBSA-only selector: smaller immediate change, but duplicates the raw/Align contract and can drift from analysis and PLIP.
- Keep `head -1` and emit a warning: preserves legacy behavior but still permits calculations against the wrong system.
- Require users to remove extra files manually: avoids code changes but conflicts with the supported raw-plus-Align workflow.

## Tests and documentation

Behavior tests prove automatic raw ambiguity fails before output deletion, `RAW_CMS` resolves it, Align selection reaches thermal MM/GBSA, and raw DECOMP succeeds when an Align pair coexists. Selector tests cover explicit raw CMS derivation, explicit raw trajectory override, and missing explicit inputs. The skill documentation describes the same variables for event analysis, PLIP, MM/GBSA, and DECOMP.

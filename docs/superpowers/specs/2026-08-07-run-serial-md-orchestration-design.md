# run_serial_md orchestration design

## Context

`run_serial_md.sh` currently combines four responsibilities: resolving run-state paths, assigning GPUs, launching AutoMD, and reimplementing trajectory analysis through shell command strings. Its documented invocation from an MD working directory does not match the implementation, which defaults to the plugin script directory. Changing `--workdir` also leaves the default pending/completed/failed/log paths behind in that script directory.

The current uncommitted feature work is intentional and will be retained:

- configurable receptor and ligand ASLs;
- configurable AutoMD CPU/GPU hosts;
- a `--submit-immediately` mode for explicitly selected GPUs;
- the same ligand ASL for post-MD analysis.

## Goals

- Make the invocation directory the default MD working directory.
- Make all default state files follow the selected working directory.
- Make relative explicit state-file paths deterministic and independent of later `cd` calls.
- Make `--submit-immediately` work consistently for one or multiple explicit GPUs.
- Ensure dry-run never waits for or probes GPU availability when GPU IDs are explicit.
- Keep one authoritative implementation of trajectory analysis by delegating to `run_analysis.sh`.
- Remove obsolete machine-specific runtime paths from the serial runner.
- Preserve existing task claiming, restart, and first-failure behavior unless directly affected by these goals.

## Path contract

Capture the invocation directory before argument parsing. `WORKDIR` initially remains unset; after parsing it becomes either the absolute `--workdir` directory or the absolute invocation directory.

The default files are then derived from that final directory:

- `md_pending_serial.list`
- `md_completed_serial.list`
- `md_failed_serial.list`
- `run_serial_md.log`
- `.run_serial_md.lock`
- `.run_serial_md.claimed.*`

Explicit absolute `--list`, `--completed`, and `--failed` paths remain absolute. Explicit relative paths resolve against the final working directory, regardless of option order. Missing option values fail with status 2 and an actionable message rather than an unbound-variable error.

## GPU submission contract

`--submit-immediately` remains valid only with `--gpu` or `--gpus`.

- Single explicit GPU: use that GPU directly when either dry-run or immediate submission is enabled; otherwise wait until it is stably free.
- Multiple explicit GPUs: each worker skips availability waiting when either dry-run or immediate submission is enabled; otherwise each worker waits for its GPU.
- Automatic GPU selection: unchanged and incompatible with immediate submission because no target GPU has been supplied.

This changes only the pre-submission availability gate. It does not change AutoMD's own job behavior or promise that a busy GPU can accept work.

## AutoMD and analysis boundary

The serial runner keeps AutoMD construction as an argument array. The existing variables remain supported:

- `RECEPTOR_ASL`
- `LIGAND_ASL`
- `AUTOMD_CPU_HOST`
- `AUTOMD_GPU_HOST`
- `MDTIME`
- `FRAMES` for the number of AutoMD output frames

After AutoMD returns successfully and the generated MD directory is found, the runner invokes the single authoritative analysis entry point:

```text
TRAJECTORY_SOURCE=raw FRAMES=ANALYSIS_FRAMES_VALUE run_analysis.sh ABSOLUTE_MD_DIRECTORY
```

The analysis frame range comes from `ANALYSIS_FRAMES`, defaulting to `1:2001:20`. This prevents AutoMD's numeric `FRAMES` value from leaking into `run_analysis.sh`, where `FRAMES` means `start:end:step`. `RECEPTOR_ASL` and `LIGAND_ASL` continue to flow through the environment.

The old `AUTOTRJ_SHELL_CMD`, `run_shell_or_print`, direct `event_analysis.py` call, and `/data1/...` library/Schrodinger fallbacks are removed. `env.sh` and `run_analysis.sh` remain responsible for resolved tool paths.

For dry-run, the runner prints the analysis invocation without executing it. An `ANALYSIS_RUNNER` override may point tests or advanced callers at a compatible entry point; its default is the sibling `run_analysis.sh`.

## Failure behavior

- Invalid arguments or incompatible options return 2 before creating run-state files.
- Missing pending input returns 1.
- AutoMD failure prevents analysis and records the item as failed using existing serial/multi-worker behavior.
- Analysis failure propagates through `run_md_and_analysis` and records the item as failed.
- A successful item is recorded only after both AutoMD and analysis succeed.

## Tests

Add a shell contract suite with isolated fake AutoMD, GPU, and analysis executables. It will prove:

1. invocation-directory defaults find the local pending list and report local state paths;
2. `--workdir` controls every default state path;
3. relative explicit list/completed/failed paths resolve against the final workdir independent of option order;
4. single-GPU immediate submission performs no GPU probe and completes the AutoMD → analysis chain;
5. multi-GPU dry-run performs no GPU probe;
6. analysis receives the generated absolute MD directory, raw trajectory source, configurable ASLs, and `ANALYSIS_FRAMES`, while AutoMD retains numeric `FRAMES`;
7. analysis failure is recorded as failure rather than completion;
8. missing option values return 2;
9. runtime behavior no longer depends on duplicate AutoTRJ/event-analysis shell commands or an obsolete `/data1` runtime path.

The final diff audit, rather than a brittle source-text unit test, confirms removal of the obsolete implementation symbols and paths.

After the focused suite passes, run Bash syntax checks, every MD-pipeline shell suite, ordinary Python tests, Schrödinger Python tests, toolenv tests, plugin validation, and `git diff --check`.

## Non-goals

- Changing AutoMD's simulation defaults or generated directory naming.
- Changing the existing rule that a matching `*-md` directory causes a restart skip.
- Adding a new shared analysis library when `run_analysis.sh` already provides the authoritative entry point.
- Running a real production MD job as part of automated verification.

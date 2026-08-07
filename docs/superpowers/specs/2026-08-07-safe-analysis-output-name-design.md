# Safe Analysis Output Name Design

## Scope

Prevent `OUT_NAME` from making the PLIP or MM/GBSA runners delete or write outside the selected MD directory. Result staging and rollback, trajectory selection, and other runner cleanup behavior remain separate delivery units.

## Design

Add one sourceable shell helper that validates an output directory name before either runner constructs or removes the output path. Both `run_plip.sh` and `run_mmgbsa.sh` use this helper so the safety rule has one authoritative implementation.

A valid value is a single non-empty path component: it is not `.` or `..` and contains no `/`. Spaces, Unicode, leading dots other than the exact `.`/`..` values, underscores, and hyphens remain compatible. Bash variables cannot contain NUL bytes, so no separate NUL rule is needed.

Invalid input prints an error identifying `OUT_NAME` and returns status 2. Validation occurs once before processing any requested MD directory, so no external analysis command, `mkdir`, or `rm -rf` runs for an invalid value.

## Alternatives considered

- Duplicate an inline check in both runners: smallest local diff, but creates two authorities that can drift.
- Restrict names to an ASCII allowlist: simple to reason about, but unnecessarily breaks safe existing names containing spaces or Unicode.
- Resolve the final path with `realpath`: insufficient by itself because a not-yet-created output path and symlink behavior complicate containment checks; rejecting directory separators directly matches the public `OUT_NAME` contract.

## Tests

Behavior tests invoke the real shell runners in temporary directories with fake external tools. They verify `..`, `.`, absolute paths, and nested paths fail with status 2 before external calls and leave sentinel files intact. They also verify safe custom names still execute successfully in both runners.

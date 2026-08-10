---
name: weekly-work-report
description: Collect approved local Git, file, ActivityWatch, task, and notes activity for a selected week and produce a privacy-preserving YAML work report. Use when the user asks to generate, update, validate, or automate a weekly work summary from this machine.
---

# Weekly work report

Read `assets/config.example.yaml`, copy it to a user-selected location, and replace each placeholder whitelist with an explicit approved directory. Do not scan a directory until it is listed in that configuration.

Check that PyYAML is available with `python3 -c 'import yaml'`. Ask before installing the dependency listed in `scripts/requirements.txt` when that check fails.

Run the plan command first. Show its data sources, time range, and root aliases to the user. Stop and ask the user to narrow the configuration when a root is the home directory, filesystem root, or contains `..`.

```bash
SKILL=<installed-weekly-work-report-directory>
python3 "$SKILL/scripts/collect.py" --config /path/to/weekly-report.yaml --week 2026-W33 --show-plan
```

Generate a report only after the user confirms the displayed plan. Do not install ActivityWatch, request privacy permissions, send data, upload data, or commit the report.

```bash
python3 "$SKILL/scripts/collect.py" --config /path/to/weekly-report.yaml --week 2026-W33
python3 "$SKILL/scripts/validate_report.py" /path/to/weekly-reports/2026-W33.yaml
```

Pass `--project <name-or-path-alias>` to restrict the report to one approved project. Read `references/schema.md` for the report contract and privacy boundaries.

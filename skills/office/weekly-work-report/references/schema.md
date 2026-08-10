# Weekly work report schema

`collect.py` writes YAML with `yaml.safe_dump`; `validate_report.py` reads it with `yaml.safe_load` and validates schema version 1. Dynamic `generated_at` is the only intentionally variable value for identical inputs.

```yaml
schema_version: 1
week: "2026-W33"
period:
  start: "2026-08-10T00:00:00+08:00"
  end: "2026-08-16T23:59:59+08:00"
timezone: "Asia/Shanghai"
summary:
  headline: ""
  outcomes: []
  blockers: []
  next_steps: []
projects: []
activity:
  total_seconds: 0
  by_category: []
  by_application: []
tasks:
  completed: []
  in_progress: []
  blocked: []
sources:
  - name: git
    status: collected
    records: 0
warnings: []
generated_at: "2026-08-17T00:00:00+08:00"
```

Each project uses a stable `path_alias` such as `root-1/project-a`; no generated field contains an absolute path. Git entries include aggregate commit statistics, sorted commit subjects, and the current branch when available. File entries contain root-relative names only. ActivityWatch stores app totals only; it never stores window titles. Notes contribute only a count of approved Markdown files changed during the period. Codex tasks are skipped unless this repository later registers a stable public API.

All sources are isolated. Missing roots, non-Git roots, unavailable ActivityWatch, malformed ActivityWatch responses, and unreadable files become warnings or a source status rather than aborting the report. `disabled`, `collected`, `partial`, `skipped`, and `failed` describe those outcomes.

Keep all privacy fields false. The collector rejects a configuration that requests absolute paths, window titles, or file contents. It also rejects home-directory roots, filesystem-root roots, parent-directory segments, symlink escapes, and known sensitive directories.

#!/usr/bin/env python3
"""Collect approved local activity into a privacy-preserving YAML report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import yaml


SCHEMA_VERSION = 1
SOURCE_NAMES = ("git", "files", "activitywatch", "codex_tasks", "notes")
DEFAULT_EXCLUDES = (".git", "node_modules", "vendor", "dist", "build", ".cache", ".env")
SENSITIVE_NAMES = {
    ".aws", ".docker", ".gnupg", ".kube", ".npmrc", ".pki", ".ssh", ".vault", ".zsh_history",
    "cookies", "keychains", "mail", "passwords", "secrets", "system keychains",
}


def default_config() -> dict[str, Any]:
    return {
        "timezone": "Asia/Shanghai",
        "week_starts_on": "monday",
        "output_dir": "./weekly-reports",
        "sources": {
            "git": {"enabled": False, "roots": [], "author_emails": []},
            "files": {"enabled": False, "roots": [], "exclude": list(DEFAULT_EXCLUDES)},
            "activitywatch": {"enabled": False, "base_url": "http://127.0.0.1:5600"},
            "codex_tasks": {"enabled": False},
            "notes": {"enabled": False, "roots": []},
        },
        "privacy": {
            "include_absolute_paths": False,
            "include_window_titles": False,
            "include_file_contents": False,
        },
    }


def merge_mapping(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        result[key] = merge_mapping(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    config = merge_mapping(default_config(), raw)
    if config["week_starts_on"] != "monday":
        raise ValueError("week_starts_on must be monday for ISO week reports")
    ZoneInfo(config["timezone"])
    for name in SOURCE_NAMES:
        source = config["sources"].get(name, {})
        if not isinstance(source, dict):
            raise ValueError(f"sources.{name} must be a mapping")
        source["enabled"] = bool(source.get("enabled", False))
        for key in ("roots", "author_emails", "exclude"):
            if key in source and not isinstance(source[key], list):
                raise ValueError(f"sources.{name}.{key} must be a list")
        config["sources"][name] = source
    if any(bool(config["privacy"].get(key, False)) for key in ("include_absolute_paths", "include_window_titles", "include_file_contents")):
        raise ValueError("privacy options must remain false for this skill")
    return config


def load_config(path: Path | None) -> tuple[dict[str, Any], list[str]]:
    if path is None:
        return normalize_config({}), ["configuration not supplied; all data sources are disabled"]
    if not path.is_file():
        return normalize_config({}), ["configuration file is unavailable; all data sources are disabled"]
    with path.open(encoding="utf-8") as handle:
        return normalize_config(yaml.safe_load(handle)), []


def parse_week(value: str) -> tuple[int, int]:
    try:
        year, week = value.split("-W", 1)
        parsed = (int(year), int(week))
        date.fromisocalendar(parsed[0], parsed[1], 1)
        return parsed
    except (TypeError, ValueError) as exc:
        raise ValueError("week must use ISO format YYYY-Www") from exc


def week_period(week: str, timezone: str) -> tuple[datetime, datetime]:
    year, number = parse_week(week)
    zone = ZoneInfo(timezone)
    start_day = date.fromisocalendar(year, number, 1)
    return (
        datetime.combine(start_day, time.min, zone),
        datetime.combine(start_day + timedelta(days=6), time(23, 59, 59), zone),
    )


def current_week(timezone: str) -> str:
    today = datetime.now(ZoneInfo(timezone)).date()
    year, number, _ = today.isocalendar()
    return f"{year}-W{number:02d}"


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def approved_root(value: str, label: str) -> tuple[Path | None, str | None]:
    candidate = Path(value).expanduser()
    if ".." in candidate.parts:
        return None, f"{label} contains a parent-directory segment; use an explicit approved path"
    if not candidate.exists():
        return None, f"{label} does not exist; skipped"
    resolved = candidate.resolve()
    home = Path.home().resolve()
    if resolved == home:
        return None, f"{label} is the home directory; narrow the approved whitelist"
    if resolved == resolved.anchor or not resolved.is_dir():
        return None, f"{label} is not an approved directory; skipped"
    return resolved, None


def root_entries(source: dict[str, Any], warnings: list[str]) -> list[tuple[Path, str]]:
    entries = []
    for index, value in enumerate(source.get("roots", []), start=1):
        label = f"root-{index}"
        root, warning = approved_root(str(value), label)
        if warning:
            warnings.append(warning)
        elif root is not None:
            entries.append((root, label))
    return entries


def safe_children(root: Path, excluded: set[str], warnings: list[str]):
    for directory, dirs, files in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        kept_dirs = []
        for name in sorted(dirs):
            child = directory_path / name
            if name in excluded or name in SENSITIVE_NAMES:
                continue
            if child.is_symlink():
                warnings.append("symlinked directory skipped")
                continue
            if is_relative_to(child.resolve(), root):
                kept_dirs.append(name)
            else:
                warnings.append("directory outside approved root skipped")
        dirs[:] = kept_dirs
        for name in sorted(files):
            child = directory_path / name
            if name in excluded or name in SENSITIVE_NAMES:
                continue
            if child.is_symlink():
                warnings.append("symlinked file skipped")
                continue
            try:
                if is_relative_to(child.resolve(), root):
                    yield child
                else:
                    warnings.append("file outside approved root skipped")
            except OSError:
                warnings.append("unreadable file skipped")


def run_git(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *arguments], text=True, capture_output=True, check=False, shell=False
    )


def redact_text(value: str) -> str:
    return value.replace(str(Path.home()), "~").replace("\x00", "")


def git_repositories(root: Path, warnings: list[str]) -> list[Path]:
    repositories = []
    if (root / ".git").exists():
        repositories.append(root)
    for directory, dirs, _ in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        dirs[:] = [name for name in sorted(dirs) if name not in {".git", "node_modules", "vendor", "dist", "build", ".cache"} and not (directory_path / name).is_symlink()]
        if directory_path != root and (directory_path / ".git").exists():
            repositories.append(directory_path)
            dirs[:] = []
    return sorted(set(repositories), key=lambda item: item.as_posix())


def collect_git(root: Path, root_alias: str, start: datetime, end: datetime, emails: list[str], warnings: list[str]) -> list[dict[str, Any]]:
    projects = []
    wanted = {email.casefold() for email in emails}
    repositories = git_repositories(root, warnings)
    if not repositories:
        warnings.append("no Git repositories found in an approved root")
    for repository in repositories:
        probe = run_git(["rev-parse", "--is-inside-work-tree"], repository)
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            warnings.append("non-Git directory skipped")
            continue
        log = run_git([
            "log", "--all", "--since", start.isoformat(), "--until", end.isoformat(),
            "--format=%H%x1f%ae%x1f%s%x1e",
        ], repository)
        if log.returncode != 0:
            warnings.append("Git history could not be read for an approved project")
            continue
        commits = []
        for record in log.stdout.split("\x1e"):
            if not record.strip():
                continue
            parts = record.rstrip("\n").split("\x1f", 2)
            if len(parts) == 3 and (not wanted or parts[1].casefold() in wanted):
                commits.append((parts[0], redact_text(parts[2].replace("\n", " "))))
        insertions = deletions = 0
        changed_paths: set[str] = set()
        for commit, _ in commits:
            stats = run_git(["show", "--format=", "--numstat", commit], repository)
            if stats.returncode != 0:
                warnings.append("Git diff statistics could not be read for one commit")
                continue
            for row in stats.stdout.splitlines():
                fields = row.split("\t", 2)
                if len(fields) != 3:
                    continue
                added, removed, filename = fields
                if added.isdigit():
                    insertions += int(added)
                if removed.isdigit():
                    deletions += int(removed)
                changed_paths.add(filename)
        branch = redact_text(run_git(["branch", "--show-current"], repository).stdout.strip())
        relative = repository.relative_to(root).as_posix()
        alias = root_alias if relative == "." else f"{root_alias}/{relative}"
        projects.append({
            "name": repository.name,
            "path_alias": alias,
            "outcomes": [],
            "git": {
                "commits": len(commits), "files_changed": len(changed_paths),
                "insertions": insertions, "deletions": deletions,
                "commit_summaries": sorted(summary for _, summary in commits),
                "branches": [branch] if branch else [],
            },
            "files": {"created": [], "modified": []},
        })
    return projects


def collect_files(root: Path, root_alias: str, start: datetime, end: datetime, excludes: list[str], warnings: list[str]) -> dict[str, Any]:
    excluded = set(DEFAULT_EXCLUDES) | set(excludes)
    created: list[str] = []
    modified: list[str] = []
    birthtime_unavailable = False
    for path in safe_children(root, excluded, warnings):
        try:
            stat = path.stat()
        except OSError:
            warnings.append("unreadable file skipped")
            continue
        modified_time = datetime.fromtimestamp(stat.st_mtime, start.tzinfo)
        if not start <= modified_time <= end:
            continue
        relative = path.relative_to(root).as_posix()
        birth = getattr(stat, "st_birthtime", None)
        if birth is None:
            birthtime_unavailable = True
            modified.append(relative)
        elif start <= datetime.fromtimestamp(birth, start.tzinfo) <= end:
            created.append(relative)
        else:
            modified.append(relative)
    if birthtime_unavailable:
        warnings.append("file creation time unavailable; recent files are listed as modified")
    return {
        "name": root.name,
        "path_alias": root_alias,
        "outcomes": [],
        "git": {"commits": 0, "files_changed": 0, "insertions": 0, "deletions": 0, "commit_summaries": []},
        "files": {"created": sorted(created), "modified": sorted(modified)},
    }


def collect_notes(root: Path, start: datetime, end: datetime, warnings: list[str]) -> int:
    records = 0
    for path in safe_children(root, {".git", ".obsidian", ".cache"}, warnings):
        if path.suffix.casefold() != ".md":
            continue
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, start.tzinfo)
        except OSError:
            warnings.append("unreadable note skipped")
            continue
        if start <= modified <= end:
            records += 1
    return records


def localhost_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def activitywatch_request(base_url: str, path: str, timeout: float = 2.0) -> Any:
    request = Request(f"{base_url.rstrip('/')}{path}", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def collect_activitywatch(source: dict[str, Any], start: datetime, end: datetime, warnings: list[str]) -> tuple[str, int, dict[str, Any]]:
    base_url = str(source.get("base_url", "http://127.0.0.1:5600"))
    if not localhost_url(base_url):
        warnings.append("ActivityWatch base_url must be an http localhost address")
        return "failed", 0, {"total_seconds": 0, "by_category": [], "by_application": []}
    try:
        buckets = activitywatch_request(base_url, "/api/0/buckets")
        if not isinstance(buckets, dict):
            raise ValueError("malformed bucket response")
        applications: defaultdict[str, float] = defaultdict(float)
        records = 0
        query = urlencode({"starttime": start.isoformat(), "endtime": end.isoformat()})
        for bucket_id in sorted(buckets):
            if not bucket_id.startswith("aw-watcher-window_"):
                continue
            events = activitywatch_request(base_url, f"/api/0/buckets/{quote(bucket_id, safe='')}/events?{query}")
            if not isinstance(events, list):
                warnings.append("ActivityWatch returned malformed events; bucket skipped")
                continue
            for event in events:
                if not isinstance(event, dict) or not isinstance(event.get("duration"), (int, float)):
                    warnings.append("ActivityWatch returned malformed event; event skipped")
                    continue
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                app = data.get("app") if isinstance(data.get("app"), str) else "unknown"
                applications[app] += float(event["duration"])
                records += 1
        by_application = [{"name": name, "seconds": round(seconds, 3)} for name, seconds in sorted(applications.items())]
        return "collected", records, {"total_seconds": round(sum(applications.values()), 3), "by_category": [], "by_application": by_application}
    except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
        warnings.append("ActivityWatch is unavailable or returned invalid data")
        return "partial", 0, {"total_seconds": 0, "by_category": [], "by_application": []}


def base_report(week: str, start: datetime, end: datetime, timezone: str, generated_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "week": week,
        "period": {"start": start.isoformat(), "end": end.isoformat()}, "timezone": timezone,
        "summary": {"headline": "", "outcomes": [], "blockers": [], "next_steps": []},
        "projects": [], "activity": {"total_seconds": 0, "by_category": [], "by_application": []},
        "tasks": {"completed": [], "in_progress": [], "blocked": []}, "sources": [], "warnings": [],
        "generated_at": generated_at,
    }


def merge_projects(projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for project in projects:
        current = indexed.setdefault(project["path_alias"], project)
        if current is project:
            continue
        current["files"]["created"] = sorted(set(current["files"]["created"]) | set(project["files"]["created"]))
        current["files"]["modified"] = sorted(set(current["files"]["modified"]) | set(project["files"]["modified"]))
    return [indexed[key] for key in sorted(indexed)]


def build_report(config: dict[str, Any], week: str | None = None, project: str | None = None, generated_at: str | None = None) -> dict[str, Any]:
    timezone = config["timezone"]
    selected_week = week or current_week(timezone)
    start, end = week_period(selected_week, timezone)
    report = base_report(selected_week, start, end, timezone, generated_at or datetime.now(ZoneInfo(timezone)).replace(microsecond=0).isoformat())
    warnings = report["warnings"]
    projects: list[dict[str, Any]] = []
    git = config["sources"]["git"]
    if git["enabled"]:
        roots = root_entries(git, warnings)
        for root, alias in roots:
            projects.extend(collect_git(root, alias, start, end, list(git.get("author_emails", [])), warnings))
        report["sources"].append({"name": "git", "status": "collected" if roots and projects else ("partial" if roots else "failed"), "records": sum(item["git"]["commits"] for item in projects)})
    else:
        report["sources"].append({"name": "git", "status": "disabled", "records": 0})
    files = config["sources"]["files"]
    if files["enabled"]:
        roots = root_entries(files, warnings)
        file_projects = [collect_files(root, alias, start, end, list(files.get("exclude", [])), warnings) for root, alias in roots]
        projects.extend(file_projects)
        status = "collected" if roots and len(roots) == len(files.get("roots", [])) else "partial"
        report["sources"].append({"name": "files", "status": status, "records": sum(len(item["files"]["created"]) + len(item["files"]["modified"]) for item in file_projects)})
    else:
        report["sources"].append({"name": "files", "status": "disabled", "records": 0})
    activitywatch = config["sources"]["activitywatch"]
    if activitywatch["enabled"]:
        status, records, activity = collect_activitywatch(activitywatch, start, end, warnings)
        report["activity"] = activity
        report["sources"].append({"name": "activitywatch", "status": status, "records": records})
    else:
        report["sources"].append({"name": "activitywatch", "status": "disabled", "records": 0})
    for name, message in (("codex_tasks", "Codex tasks skipped: no stable public task interface is registered"), ("notes", "notes collection records only approved Markdown files; content is not included")):
        source = config["sources"][name]
        if source["enabled"]:
            if name == "notes":
                roots = root_entries(source, warnings)
                records = sum(collect_notes(root, start, end, warnings) for root, _ in roots)
                report["sources"].append({"name": name, "status": "collected" if roots else "partial", "records": records})
            else:
                warnings.append(message)
                report["sources"].append({"name": name, "status": "skipped", "records": 0})
        else:
            report["sources"].append({"name": name, "status": "disabled", "records": 0})
    report["projects"] = merge_projects(projects)
    if project:
        report["projects"] = [item for item in report["projects"] if item["name"] == project or item["path_alias"] == project]
        if not report["projects"]:
            warnings.append("requested project did not match an approved project")
    return report


def contains_home_path(value: Any, home: str) -> bool:
    if isinstance(value, str):
        return home in value
    if isinstance(value, list):
        return any(contains_home_path(item, home) for item in value)
    if isinstance(value, dict):
        return any(contains_home_path(item, home) for item in value.values())
    return False


def validate_report(report: Any) -> list[str]:
    if not isinstance(report, dict):
        return ["report must be a mapping"]
    errors = []
    required = {"schema_version", "week", "period", "timezone", "summary", "projects", "activity", "tasks", "sources", "warnings", "generated_at"}
    errors.extend(f"missing required key: {key}" for key in sorted(required - set(report)))
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    if not isinstance(report.get("week"), str):
        errors.append("week must be a string")
    if not isinstance(report.get("period"), dict) or not {"start", "end"} <= set(report.get("period", {})):
        errors.append("period must contain start and end")
    if not isinstance(report.get("summary"), dict) or not isinstance(report.get("activity"), dict) or not isinstance(report.get("tasks"), dict):
        errors.append("summary, activity, and tasks must be mappings")
    if not isinstance(report.get("projects"), list) or not isinstance(report.get("sources"), list):
        errors.append("projects and sources must be lists")
    for project in report.get("projects", []):
        required_project = {"name", "path_alias", "outcomes", "git", "files"}
        if not isinstance(project, dict) or not required_project <= set(project):
            errors.append("each project must contain name, path_alias, outcomes, git, and files")
            continue
        required_git = {"commits", "files_changed", "insertions", "deletions", "commit_summaries"}
        if not isinstance(project["git"], dict) or not required_git <= set(project["git"]):
            errors.append("each project.git must contain aggregate statistics")
        if not isinstance(project["files"], dict) or not {"created", "modified"} <= set(project["files"]):
            errors.append("each project.files must contain created and modified")
    for source in report.get("sources", []):
        if not isinstance(source, dict) or not {"name", "status", "records"} <= set(source):
            errors.append("each source must contain name, status, and records")
    if contains_home_path(report, str(Path.home())):
        errors.append("report must not contain an absolute home path")
    return errors


def write_yaml_atomic(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = yaml.safe_dump(report, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--week")
    parser.add_argument("--project")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--show-plan", action="store_true")
    arguments = parser.parse_args(argv)
    config, config_warnings = load_config(arguments.config)
    week = arguments.week or current_week(config["timezone"])
    start, end = week_period(week, config["timezone"])
    if arguments.show_plan:
        plan = {"week": week, "period": {"start": start.isoformat(), "end": end.isoformat()}, "sources": [{"name": name, "enabled": config["sources"][name]["enabled"]} for name in SOURCE_NAMES], "approved_roots": {name: [f"root-{index}" for index, _ in enumerate(config["sources"][name].get("roots", []), 1)] for name in ("git", "files", "notes")}}
        print(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False))
        return 0
    report = build_report(config, week, arguments.project)
    report["warnings"] = config_warnings + report["warnings"]
    report["warnings"] = sorted(set(report["warnings"]))
    output = arguments.output or Path(config["output_dir"]) / f"{week}.yaml"
    write_yaml_atomic(output, report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

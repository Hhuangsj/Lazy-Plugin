import importlib.util
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class WeeklyWorkReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collect = __import__("collect")

    def test_iso_week_period_handles_cross_year_and_timezone(self):
        start, end = self.collect.week_period("2026-W01", "Asia/Shanghai")

        self.assertEqual("2025-12-29T00:00:00+08:00", start.isoformat())
        self.assertEqual("2026-01-04T23:59:59+08:00", end.isoformat())

    def test_rejects_home_root_and_missing_roots_without_aborting_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self.collect.normalize_config(
                {
                    "timezone": "Asia/Shanghai",
                    "sources": {
                        "git": {"enabled": True, "roots": [str(Path.home())]},
                        "files": {"enabled": True, "roots": [str(Path(temp_dir) / "missing")]},
                    },
                }
            )
            report = self.collect.build_report(config, "2026-W33")

        self.assertEqual("failed", report["sources"][0]["status"])
        self.assertEqual("partial", report["sources"][1]["status"])
        self.assertTrue(any("home directory" in warning for warning in report["warnings"]))
        self.assertTrue(any("does not exist" in warning for warning in report["warnings"]))

    def test_collects_multiple_git_repositories_and_filters_authors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "approved"
            first = root / "first"
            second = root / "second"
            self._commit(first, "keep.txt", "alpha\n", "kept", "wanted@example.test")
            self._commit(first, "skip.txt", "beta\n", "skipped", "other@example.test")
            self._commit(second, "keep.txt", "gamma\n", "also kept", "wanted@example.test")
            config = self.collect.normalize_config(
                {
                    "timezone": "Asia/Shanghai",
                    "sources": {
                        "git": {
                            "enabled": True,
                            "roots": [str(root)],
                            "author_emails": ["wanted@example.test"],
                        }
                    },
                }
            )
            report = self.collect.build_report(config, "2026-W33")

        self.assertEqual(2, len(report["projects"]))
        self.assertEqual([1, 1], [project["git"]["commits"] for project in report["projects"]])
        self.assertEqual(["kept", "also kept"], [project["git"]["commit_summaries"][0] for project in report["projects"]])
        self.assertNotIn(str(root), str(report))

    def test_files_excludes_sensitive_directories_and_symlink_escapes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "approved"
            root.mkdir()
            (root / "visible.txt").write_text("ok", encoding="utf-8")
            (root / ".env").write_text("secret", encoding="utf-8")
            (root / "node_modules").mkdir()
            (root / "node_modules" / "ignored.js").write_text("ignored", encoding="utf-8")
            outside = Path(temp_dir) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            (root / "escape.txt").symlink_to(outside)
            config = self.collect.normalize_config(
                {
                    "timezone": "Asia/Shanghai",
                    "sources": {"files": {"enabled": True, "roots": [str(root)]}},
                }
            )
            report = self.collect.build_report(config, "2026-W33")

        files = report["projects"][0]["files"]["modified"]
        self.assertEqual(["visible.txt"], files)
        self.assertNotIn("secret", str(report))
        self.assertTrue(any("symlink" in warning for warning in report["warnings"]))

    def test_activitywatch_failure_is_isolated_and_localhost_is_required(self):
        config = self.collect.normalize_config(
            {
                "timezone": "Asia/Shanghai",
                "sources": {
                    "activitywatch": {
                        "enabled": True,
                        "base_url": "http://example.test:5600",
                    }
                },
            }
        )
        report = self.collect.build_report(config, "2026-W33")

        source = next(item for item in report["sources"] if item["name"] == "activitywatch")
        self.assertEqual("failed", source["status"])
        self.assertTrue(any("localhost" in warning for warning in report["warnings"]))

    def test_activitywatch_malformed_events_are_warnings_not_failures(self):
        config = self.collect.normalize_config(
            {
                "timezone": "Asia/Shanghai",
                "sources": {"activitywatch": {"enabled": True}},
            }
        )
        with patch.object(self.collect, "activitywatch_request") as request:
            request.side_effect = [{"aw-watcher-window_test": {}}, [{"duration": "bad"}]]
            report = self.collect.build_report(config, "2026-W33")

        self.assertEqual(0, report["activity"]["total_seconds"])
        self.assertTrue(any("malformed" in warning for warning in report["warnings"]))

    def test_activitywatch_timeout_is_isolated(self):
        config = self.collect.normalize_config(
            {"timezone": "Asia/Shanghai", "sources": {"activitywatch": {"enabled": True}}}
        )
        with patch.object(self.collect, "activitywatch_request", side_effect=OSError("timed out")):
            report = self.collect.build_report(config, "2026-W33")

        source = next(item for item in report["sources"] if item["name"] == "activitywatch")
        self.assertEqual("partial", source["status"])
        self.assertEqual(0, report["activity"]["total_seconds"])

    def test_empty_week_and_non_git_root_produce_valid_report_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "not-a-repository"
            root.mkdir()
            config = self.collect.normalize_config(
                {"timezone": "Asia/Shanghai", "sources": {"git": {"enabled": True, "roots": [str(root)]}}}
            )
            report = self.collect.build_report(config, "2026-W33")

        self.assertEqual([], report["projects"])
        self.assertTrue(any("no Git repositories" in warning for warning in report["warnings"]))
        self.assertEqual([], self.collect.validate_report(report))

    def test_parent_directory_root_is_rejected_without_exposing_its_path(self):
        config = self.collect.normalize_config(
            {"timezone": "Asia/Shanghai", "sources": {"files": {"enabled": True, "roots": ["/tmp/../tmp"]}}}
        )
        report = self.collect.build_report(config, "2026-W33")

        self.assertTrue(any("parent-directory" in warning for warning in report["warnings"]))
        self.assertNotIn("/tmp/../tmp", str(report))

    def test_schema_validator_rejects_incomplete_project(self):
        report = {
            "schema_version": 1,
            "week": "2026-W33",
            "period": {"start": "2026-08-10T00:00:00+08:00", "end": "2026-08-16T23:59:59+08:00"},
            "timezone": "Asia/Shanghai",
            "summary": {},
            "projects": [{"name": "incomplete"}],
            "activity": {},
            "tasks": {},
            "sources": [],
            "warnings": [],
            "generated_at": "2026-08-17T00:00:00+08:00",
        }

        errors = self.collect.validate_report(report)

        self.assertTrue(any("project" in error for error in errors))

    def test_yaml_is_stable_safe_and_schema_valid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "approved"
            root.mkdir()
            (root / "报告: 一行\n二行.md").write_text("内容", encoding="utf-8")
            config = self.collect.normalize_config(
                {
                    "timezone": "Asia/Shanghai",
                    "sources": {"files": {"enabled": True, "roots": [str(root)]}},
                }
            )
            first = self.collect.build_report(config, "2026-W33", generated_at="2026-08-17T00:00:00+08:00")
            second = self.collect.build_report(config, "2026-W33", generated_at="2026-08-17T00:00:00+08:00")
            first_path = Path(temp_dir) / "one.yaml"
            second_path = Path(temp_dir) / "two.yaml"
            self.collect.write_yaml_atomic(first_path, first)
            self.collect.write_yaml_atomic(second_path, second)
            loaded = self.collect.load_yaml(first_path)
            first_text = first_path.read_text(encoding="utf-8")
            second_text = second_path.read_text(encoding="utf-8")

        self.assertEqual(first_text, second_text)
        self.assertEqual([], self.collect.validate_report(loaded))
        self.assertIn("报告", str(loaded))

    @staticmethod
    def _commit(repository, filename, content, subject, email):
        repository.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True)
        (repository / filename).write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", filename], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "-c",
                "user.name=Test User",
                "-c",
                f"user.email={email}",
                "commit",
                "--quiet",
                "--date=2026-08-12T12:00:00+08:00",
                "-m",
                subject,
            ],
            check=True,
            env={**__import__("os").environ, "GIT_AUTHOR_DATE": "2026-08-12T12:00:00+08:00"},
        )


if __name__ == "__main__":
    unittest.main()

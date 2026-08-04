import os
import sys
import tempfile
import unittest
import csv
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_stability import (
    filter_by_activity,
    filter_by_scope,
    find_reference,
    main,
    merge_tables,
    parse_first_number,
    read_table,
    resolve_column,
    resolve_id_column,
    summarize_column,
    write_candidate_csv,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


class TableLoadingTests(unittest.TestCase):
    def test_read_table_preserves_csv_headers_and_cell_strings(self):
        headers, rows = read_table(FIXTURES_DIR / "sample.csv")

        self.assertEqual("Customer ID", headers[0])
        self.assertEqual("REF-001", rows[0]["Customer ID"])

    def test_merge_tables_preserves_one_file_only_columns_and_provenance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.csv"
            second = Path(temp_dir) / "second.csv"
            first.write_text("Customer ID,First only\nREF-001,alpha\n", encoding="utf-8")
            second.write_text("Customer ID,Second only\nREF-002,beta\n", encoding="utf-8")

            headers, rows = merge_tables([first, second])

        self.assertEqual(
            ["Customer ID", "First only", "Second only", "__source_file"], headers
        )
        self.assertEqual("alpha", rows[0]["First only"])
        self.assertEqual("", rows[0]["Second only"])
        self.assertEqual("beta", rows[1]["Second only"])
        self.assertEqual(str(first), rows[0]["__source_file"])
        self.assertEqual(str(second), rows[1]["__source_file"])

    def test_read_xlsx_without_openpyxl_names_required_dependency(self):
        with patch.dict(sys.modules, {"openpyxl": None}):
            with self.assertRaisesRegex(ValueError, "openpyxl.*requirements\\.txt"):
                read_table(Path("table.xlsx"))


class FieldResolutionTests(unittest.TestCase):
    def test_resolve_id_column_uses_requested_exact_name(self):
        self.assertEqual(
            "CompoundID", resolve_id_column(["Customer ID", "CompoundID"], "CompoundID")
        )

    def test_resolve_id_column_uses_unique_standard_column(self):
        self.assertEqual("Customer ID", resolve_id_column(["Customer ID", "Assay"], None))

    def test_resolve_id_column_rejects_missing_or_ambiguous_columns(self):
        with self.assertRaisesRegex(ValueError, "Missing"):
            resolve_id_column(["Customer ID"], "CompoundID")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            resolve_id_column(["Customer ID", "ID"], None)

    def test_resolve_column_requires_exact_existing_name(self):
        self.assertEqual("Assay", resolve_column(["Customer ID", "Assay"], "Assay"))
        with self.assertRaisesRegex(ValueError, "Missing"):
            resolve_column(["Customer ID"], "Assay")

    def test_find_reference_matches_identifier_or_alias_token(self):
        rows = [
            {"Customer ID": "REF-001", "Alias": "reference, control"},
            {"Customer ID": "SAMPLE-002", "Alias": "secondary; backup"},
        ]

        self.assertEqual(
            "SAMPLE-002", find_reference(rows, "backup", ["Customer ID", "Alias"])["Customer ID"]
        )

    def test_find_reference_does_not_tokenize_non_alias_id_columns(self):
        rows = [{"NonAliasID": "CMP-001, legacy", "Alias": "reference"}]

        with self.assertRaisesRegex(ValueError, "not found"):
            find_reference(rows, "legacy", ["NonAliasID", "Alias"])

    def test_find_reference_tokenizes_alias_column(self):
        rows = [{"Alias": "CMP-001, legacy"}]

        self.assertEqual(rows[0], find_reference(rows, "legacy", ["Alias"]))

    def test_find_reference_rejects_ambiguous_match(self):
        rows = [
            {"Customer ID": "REF-001", "Alias": "control"},
            {"Customer ID": "REF-002", "Alias": "control"},
        ]

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            find_reference(rows, "control", ["Customer ID", "Alias"])


class ActivityAndSummaryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"Project": "A", "EC50": "<0.00381;0.008669", "SIF": "<0.10"},
            {"Project": "A", "EC50": "2", "SIF": ""},
            {"Project": "B", "EC50": "3.1", "SIF": "-"},
            {"Project": "B", "EC50": "not tested", "SIF": "4.5"},
        ]

    def test_parse_first_number_uses_the_first_numeric_token(self):
        self.assertEqual(0.00381, parse_first_number("<0.00381;0.008669"))
        self.assertIsNone(parse_first_number("-"))

    def test_activity_filter_supports_lower_and_higher_thresholds(self):
        lower = filter_by_activity(self.rows, "EC50", "lower", 2)
        higher = filter_by_activity(self.rows, "EC50", "higher", 2)

        self.assertEqual(["<0.00381;0.008669", "2"], [row["EC50"] for row in lower])
        self.assertEqual(["2", "3.1"], [row["EC50"] for row in higher])

    def test_activity_filter_keeps_all_rows_when_not_configured(self):
        self.assertEqual(self.rows, filter_by_activity(self.rows, None, None, None))

    def test_scope_keeps_all_rows_or_filters_by_exact_group_value(self):
        self.assertEqual(self.rows, filter_by_scope(self.rows, "Project", None))
        self.assertEqual(
            [self.rows[0], self.rows[1]],
            filter_by_scope(self.rows, "Project", "A"),
        )
        with self.assertRaisesRegex(ValueError, "group-by"):
            filter_by_scope(self.rows, None, "A")

    def test_stability_summary_counts_raw_values_without_rewriting_them(self):
        summary = summarize_column(self.rows, "SIF")

        self.assertEqual(4, summary["total"])
        self.assertEqual(3, summary["nonempty"])
        self.assertEqual(1, summary["missing"])
        self.assertEqual(2, summary["parseable"])
        self.assertEqual(2.3, summary["median"])
        self.assertEqual(0.1, summary["min"])
        self.assertEqual(4.5, summary["max"])
        self.assertIsNone(summary["reference_raw_value"])


class CandidateOutputTests(unittest.TestCase):
    def test_candidate_csv_writes_reference_first_and_preserves_position_order(self):
        rows = [
            {
                "Customer ID": "REF-001",
                "SIF": "<0.10",
                "SGF": "",
                "AA0": "W",
                "1": "A",
                "Sequence_Decomposition/AA1": "G",
            },
            {
                "Customer ID": "CAND-001",
                "SIF": "2.5",
                "SGF": "-",
                "AA0": "F",
                "1": "L",
                "Sequence_Decomposition/AA1": "P",
            },
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "candidates.csv"
            write_candidate_csv(
                rows,
                output,
                "Customer ID",
                ["SIF", "SGF"],
                ["AA0", "1", "Sequence_Decomposition/AA1"],
            )
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.reader(handle))

        self.assertEqual(
            ["Customer ID", "SIF", "SGF", "AA0", "1", "Sequence_Decomposition/AA1"],
            written[0],
        )
        self.assertEqual("REF-001", written[1][0])
        self.assertEqual("<0.10", written[1][1])
        self.assertEqual("", written[1][2])

    def test_candidate_csv_includes_requested_raw_activity_after_stability_columns(self):
        rows = [
            {
                "Customer ID": "REF-001",
                "SIF": "<0.10",
                "EC50": "<0.00381;0.008669",
                "AA0": "W",
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "candidates.csv"
            write_candidate_csv(
                rows,
                output,
                "Customer ID",
                ["SIF"],
                ["AA0"],
                activity_column="EC50",
            )
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.reader(handle))

        self.assertEqual(["Customer ID", "SIF", "EC50", "AA0"], written[0])
        self.assertEqual("<0.00381;0.008669", written[1][2])


class CliTests(unittest.TestCase):
    def _write_input(self, directory: str) -> Path:
        input_path = Path(directory) / "input.csv"
        input_path.write_text(
            "Customer ID,Project,EC50,SIF,SGF,AA0,1,Sequence_Decomposition/AA1\n"
            "REF-001,A,4,<0.10,0.25,W,A,G\n"
            "CAND-002,A,1.2,2.5,,F,L,P\n"
            "CAND-001,A,0.5,-,5,Y,V,Q\n"
            "OFF-SCOPE,B,0.1,9,7,C,D,E\n",
            encoding="utf-8",
        )
        return input_path

    def test_cli_writes_scope_summary_and_reference_first_sorted_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = self._write_input(temp_dir)
            output = Path(temp_dir) / "candidates.csv"
            summary = Path(temp_dir) / "summary.md"

            result = main(
                [
                    "--input", str(input_path),
                    "--output-csv", str(output),
                    "--summary", str(summary),
                    "--reference", "REF-001",
                    "--group-by", "Project",
                    "--activity-column", "EC50",
                    "--activity-direction", "lower",
                    "--activity-threshold", "2",
                    "--stability-column", "SIF",
                    "--stability-column", "SGF",
                ]
            )
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.reader(handle))
            summary_text = summary.read_text(encoding="utf-8")

        self.assertEqual(0, result)
        self.assertEqual(
            [
                "Customer ID", "SIF", "SGF", "EC50", "__source_file",
                "AA0", "1", "Sequence_Decomposition/AA1",
            ],
            written[0],
        )
        self.assertEqual(
            ["REF-001", "OFF-SCOPE", "CAND-001", "CAND-002"],
            [row[0] for row in written[1:]],
        )
        self.assertEqual("<0.10", written[1][1])
        self.assertEqual("4", written[1][3])
        self.assertIn("Per-group counts", summary_text)
        self.assertIn("A: 3", summary_text)
        self.assertIn("B: 1", summary_text)
        self.assertIn("reference_raw_value: <0.10", summary_text)

    def test_cli_matches_reference_tokens_from_normalized_alias_headers(self):
        for alias_header in (" alias ", "ALIAS"):
            with self.subTest(alias_header=alias_header), tempfile.TemporaryDirectory() as temp_dir:
                input_path = Path(temp_dir) / "input.csv"
                input_path.write_text(
                    f"Customer ID,{alias_header},SIF\n"
                    "REF-001,reference; control,<0.10\n"
                    "CAND-001,backup,2.5\n",
                    encoding="utf-8",
                )
                output = Path(temp_dir) / "candidates.csv"
                summary = Path(temp_dir) / "summary.md"

                result = main([
                    "--input", str(input_path),
                    "--output-csv", str(output),
                    "--summary", str(summary),
                    "--reference", "control",
                    "--stability-column", "SIF",
                ])
                with output.open(newline="", encoding="utf-8") as handle:
                    written = list(csv.reader(handle))

                self.assertEqual(0, result)
                self.assertEqual("REF-001", written[1][0])

    def test_cli_preserves_provenance_in_multi_source_candidate_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.csv"
            second = Path(temp_dir) / "second.csv"
            first.write_text(
                "Customer ID,EC50,SIF,AA0\nCAND-001,1,<0.10,W\n",
                encoding="utf-8",
            )
            second.write_text(
                "Customer ID,EC50,SIF,AA0\nCAND-002,2,2.5,F\n",
                encoding="utf-8",
            )
            output = Path(temp_dir) / "candidates.csv"
            summary = Path(temp_dir) / "summary.md"

            result = main([
                "--input", str(first), str(second),
                "--output-csv", str(output),
                "--summary", str(summary),
                "--activity-column", "EC50",
                "--activity-direction", "lower",
                "--activity-threshold", "3",
                "--stability-column", "SIF",
            ])
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.reader(handle))

        self.assertEqual(0, result)
        self.assertEqual(
            ["Customer ID", "SIF", "EC50", "__source_file", "AA0"],
            written[0],
        )
        self.assertEqual(
            [["CAND-001", str(first)], ["CAND-002", str(second)]],
            [[row[0], row[3]] for row in written[1:]],
        )

    def test_cli_overview_keeps_input_order_when_activity_has_no_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = self._write_input(temp_dir)
            output = Path(temp_dir) / "candidates.csv"
            summary = Path(temp_dir) / "summary.md"

            result = main([
                "--input", str(input_path),
                "--output-csv", str(output),
                "--summary", str(summary),
                "--activity-column", "EC50",
                "--activity-direction", "lower",
                "--stability-column", "SIF",
            ])
            with output.open(newline="", encoding="utf-8") as handle:
                written = list(csv.reader(handle))

        self.assertEqual(0, result)
        self.assertEqual(
            ["REF-001", "CAND-002", "CAND-001", "OFF-SCOPE"],
            [row[0] for row in written[1:]],
        )
        self.assertEqual(["4", "1.2", "0.5", "0.1"], [row[2] for row in written[1:]])

    def test_cli_rejects_source_file_as_a_user_selected_output_column(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = self._write_input(temp_dir)
            output = Path(temp_dir) / "candidates.csv"
            summary = Path(temp_dir) / "summary.md"
            base = [
                "--input", str(input_path),
                "--output-csv", str(output),
                "--summary", str(summary),
            ]

            for option in ("--stability-column", "--position-column"):
                with self.subTest(option=option), self.assertRaises(SystemExit):
                    main([*base, "--stability-column", "SIF", option, "__source_file"])

    def test_cli_rejects_invalid_scope_and_empty_or_out_of_scope_reference_results(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = self._write_input(temp_dir)
            output = Path(temp_dir) / "candidates.csv"
            summary = Path(temp_dir) / "summary.md"
            base = [
                "--input", str(input_path),
                "--output-csv", str(output),
                "--summary", str(summary),
                "--stability-column", "SIF",
            ]

            with self.assertRaises(SystemExit):
                main([*base, "--group-value", "A"])
            with self.assertRaises(SystemExit):
                main([*base, "--activity-threshold", "2"])
            with self.assertRaisesRegex(ValueError, "No rows remain"):
                main([
                    *base,
                    "--activity-column", "EC50",
                    "--activity-direction", "lower",
                    "--activity-threshold", "0.01",
                ])
            with self.assertRaisesRegex(ValueError, "not in the selected scope"):
                main([
                    *base,
                    "--reference", "REF-001",
                    "--group-by", "Project",
                    "--group-value", "B",
                ])
            with self.assertRaisesRegex(ValueError, "Missing column"):
                main([*base, "--activity-column", "Missing", "--activity-direction", "lower"])

    def test_cli_rejects_hard_linked_candidate_or_summary_output(self):
        for output_option in ("--output-csv", "--summary"):
            with self.subTest(output_option=output_option), tempfile.TemporaryDirectory() as temp_dir:
                input_path = self._write_input(temp_dir)
                original = input_path.read_text(encoding="utf-8")
                linked_output = Path(temp_dir) / f"{output_option[2:]}.csv"
                os.link(input_path, linked_output)
                candidate_output = (
                    linked_output
                    if output_option == "--output-csv"
                    else Path(temp_dir) / "candidates.csv"
                )
                summary_output = (
                    linked_output
                    if output_option == "--summary"
                    else Path(temp_dir) / "summary.md"
                )

                with self.assertRaises(SystemExit):
                    main([
                        "--input", str(input_path),
                        "--output-csv", str(candidate_output),
                        "--summary", str(summary_output),
                        "--stability-column", "SIF",
                    ])

                self.assertEqual(original, input_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

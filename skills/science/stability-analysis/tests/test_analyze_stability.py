import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from analyze_stability import (
    find_reference,
    merge_tables,
    read_table,
    resolve_column,
    resolve_id_column,
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

    def test_find_reference_rejects_ambiguous_match(self):
        rows = [
            {"Customer ID": "REF-001", "Alias": "control"},
            {"Customer ID": "REF-002", "Alias": "control"},
        ]

        with self.assertRaisesRegex(ValueError, "ambiguous"):
            find_reference(rows, "control", ["Customer ID", "Alias"])


if __name__ == "__main__":
    unittest.main()

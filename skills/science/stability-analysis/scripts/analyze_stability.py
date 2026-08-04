"""Table loading and field resolution helpers for stability analysis."""

import csv
from pathlib import Path


SUPPORTED_EXTENSIONS = ".csv, .xlsx"


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV or XLSX table while preserving its displayed cell values."""
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = [
                {header: value if value is not None else "" for header, value in row.items() if header is not None}
                for row in reader
            ]
        return headers, rows

    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ValueError(
                "Reading XLSX files requires openpyxl; install dependencies from requirements.txt."
            ) from exc

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            worksheet = workbook.worksheets[0]
            row_iterator = worksheet.iter_rows(values_only=True)
            headers: list[str] | None = None
            rows: list[dict[str, str]] = []
            for values in row_iterator:
                normalized = ["" if value is None else str(value) for value in values]
                if headers is None:
                    if any(value != "" for value in normalized):
                        headers = normalized
                    continue
                rows.append(dict(zip(headers, normalized)))
            return headers or [], rows
        finally:
            workbook.close()

    raise ValueError(
        f"Unsupported table format for {path}. Supported extensions: {SUPPORTED_EXTENSIONS}"
    )


def merge_tables(paths: list[Path]) -> tuple[list[str], list[dict[str, str]]]:
    """Merge input tables in order, retaining per-row source provenance."""
    headers: list[str] = []
    rows: list[dict[str, str]] = []
    for path in paths:
        table_headers, table_rows = read_table(path)
        for header in table_headers:
            if header not in headers:
                headers.append(header)
        for row in table_rows:
            rows.append({**row, "__source_file": str(path)})

    if "__source_file" not in headers:
        headers.append("__source_file")
    return headers, [{header: row.get(header, "") for header in headers} for row in rows]


def resolve_id_column(headers: list[str], requested: str | None) -> str:
    """Resolve an explicit ID column or the single conventional ID column."""
    if requested is not None:
        return resolve_column(headers, requested)

    candidates = [header for header in ("Customer ID", "CompoundID", "ID", "Alias") if header in headers]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError("Missing identifier column; specify an ID column explicitly.")
    raise ValueError(f"ambiguous identifier columns: {', '.join(candidates)}")


def resolve_column(headers: list[str], requested: str) -> str:
    """Return an exactly named column or raise a clear missing-column error."""
    if requested in headers:
        return requested
    raise ValueError(f"Missing column: {requested}")


def find_reference(
    rows: list[dict[str, str]], identifier: str, id_columns: list[str]
) -> dict[str, str]:
    """Find a unique row by an exact ID value or a comma/semicolon alias token."""
    matches: list[dict[str, str]] = []
    for row in rows:
        for column in id_columns:
            value = row.get(column, "")
            tokens = [token.strip() for token in value.replace(";", ",").split(",")]
            if value == identifier or identifier in tokens:
                matches.append(row)
                break

    if not matches:
        raise ValueError(f"Reference identifier not found: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous reference identifier: {identifier}")
    return matches[0]

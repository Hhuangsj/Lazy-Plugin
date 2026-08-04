"""Table loading and analysis helpers for user-provided stability tables."""

import argparse
import csv
import re
import statistics
from pathlib import Path


SUPPORTED_EXTENSIONS = ".csv, .xlsx"
FIRST_NUMBER_PATTERN = re.compile(
    r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
POSITION_COLUMN_PATTERN = re.compile(r"(?:^|/)AA\d+$")


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
            is_alias_column = column.strip().casefold() == "alias"
            matches_identifier = value == identifier
            if not matches_identifier and is_alias_column:
                alias_tokens = [
                    token.strip() for token in value.replace(";", ",").split(",")
                ]
                matches_identifier = identifier in alias_tokens
            if matches_identifier:
                matches.append(row)
                break

    if not matches:
        raise ValueError(f"Reference identifier not found: {identifier}")
    if len(matches) > 1:
        raise ValueError(f"ambiguous reference identifier: {identifier}")
    return matches[0]


def parse_first_number(raw: str) -> float | None:
    """Return the first numeric token in a displayed table cell."""
    match = FIRST_NUMBER_PATTERN.search(raw)
    return float(match.group()) if match else None


def filter_by_scope(
    rows: list[dict[str, str]], group_by: str | None, group_value: str | None
) -> list[dict[str, str]]:
    """Return rows in the exact requested group, or all rows when unscoped."""
    if group_value is not None and group_by is None:
        raise ValueError("--group-value requires --group-by")
    if group_by is None or group_value is None:
        return list(rows)
    return [row for row in rows if row.get(group_by, "") == group_value]


def filter_by_activity(
    rows: list[dict[str, str]],
    column: str | None,
    direction: str | None,
    threshold: float | None,
) -> list[dict[str, str]]:
    """Apply a numeric activity threshold without changing displayed cell values."""
    if column is None or direction is None or threshold is None:
        return list(rows)
    if direction not in {"lower", "higher"}:
        raise ValueError(f"Unsupported activity direction: {direction}")

    selected: list[dict[str, str]] = []
    for row in rows:
        value = parse_first_number(row.get(column, ""))
        if value is None:
            continue
        if direction == "lower" and value <= threshold:
            selected.append(row)
        if direction == "higher" and value >= threshold:
            selected.append(row)
    return selected


def summarize_column(rows: list[dict[str, str]], column: str) -> dict[str, object]:
    """Summarize raw stability cells using only their first numeric token."""
    raw_values = [row.get(column, "") for row in rows]
    nonempty_values = [value for value in raw_values if value.strip()]
    numeric_values = [
        number
        for value in nonempty_values
        if (number := parse_first_number(value)) is not None
    ]
    return {
        "total": len(raw_values),
        "nonempty": len(nonempty_values),
        "missing": len(raw_values) - len(nonempty_values),
        "parseable": len(numeric_values),
        "median": statistics.median(numeric_values) if numeric_values else None,
        "min": min(numeric_values) if numeric_values else None,
        "max": max(numeric_values) if numeric_values else None,
        "reference_raw_value": None,
    }


def _position_columns(headers: list[str], requested: list[str]) -> list[str]:
    """Resolve an explicit position override or detect conventional position columns."""
    if requested:
        resolved = {resolve_column(headers, column) for column in requested}
        return [header for header in headers if header in resolved]
    return [
        header
        for header in headers
        if header.isdecimal() or POSITION_COLUMN_PATTERN.search(header)
    ]


def write_candidate_csv(
    rows: list[dict[str, str]],
    output: Path,
    id_column: str,
    stability_columns: list[str],
    position_columns: list[str],
    activity_column: str | None = None,
) -> None:
    """Write selected candidate fields, retaining raw source strings and row order."""
    if not output.parent.exists():
        raise ValueError(f"Output directory does not exist: {output.parent}")

    output_columns = [id_column]
    for column in [*stability_columns, activity_column, *position_columns]:
        if column is None:
            continue
        if column not in output_columns:
            output_columns.append(column)

    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Customer ID", *output_columns[1:]])
        for row in rows:
            writer.writerow([row.get(column, "") for column in output_columns])


def _sort_by_activity(
    rows: list[dict[str, str]], column: str | None, direction: str | None
) -> list[dict[str, str]]:
    """Sort parseable activity values deterministically, keeping missing values last."""
    if column is None or direction is None:
        return list(rows)

    decorated = list(enumerate(rows))

    def sort_key(item: tuple[int, dict[str, str]]) -> tuple[bool, float, int]:
        index, row = item
        value = parse_first_number(row.get(column, ""))
        if value is None:
            return True, 0.0, index
        return False, value if direction == "lower" else -value, index

    return [
        row
        for _, row in sorted(decorated, key=sort_key)
    ]


def _group_counts(rows: list[dict[str, str]], group_by: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(group_by, "")
        counts[value] = counts.get(value, 0) + 1
    return list(counts.items())


def _write_summary(
    output: Path,
    input_paths: list[Path],
    total_rows: int,
    scope_rows: list[dict[str, str]],
    activity_rows: list[dict[str, str]],
    reference: dict[str, str] | None,
    id_column: str,
    group_by: str | None,
    group_value: str | None,
    activity_column: str | None,
    activity_direction: str | None,
    activity_threshold: float | None,
    stability_columns: list[str],
    candidate_output: Path,
) -> None:
    """Write a human-readable, raw-value-preserving analysis summary."""
    if not output.parent.exists():
        raise ValueError(f"Output directory does not exist: {output.parent}")

    filter_active = (
        activity_column is not None
        and activity_direction is not None
        and activity_threshold is not None
    )
    lines = ["# Stability analysis", "", "## Inputs", ""]
    lines.extend(f"- {path}" for path in input_paths)
    lines.extend(
        [
            "",
            "## Rows",
            "",
            f"- Input rows: {total_rows}",
            f"- Rows in scope: {len(scope_rows)}",
            f"- Activity-qualified rows: {len(activity_rows)}",
            "",
            "## Reference",
            "",
            (
                f"- Matched {id_column}: {reference.get(id_column, '')}"
                if reference is not None
                else "- No reference requested"
            ),
            "",
            "## Scope",
            "",
        ]
    )
    if group_by is None:
        lines.append("- Full input table")
    elif group_value is None:
        lines.append(f"- Group column: {group_by} (full table)")
        lines.append("- Per-group counts:")
        lines.extend(f"  - {value}: {count}" for value, count in _group_counts(scope_rows, group_by))
    else:
        lines.append(f"- {group_by} exactly equals: {group_value}")

    lines.extend(["", "## Activity", ""])
    if filter_active:
        lines.append(
            f"- Filtered: {activity_column} {activity_direction} {activity_threshold}"
        )
    else:
        lines.append("- Unfiltered")
    if activity_column is not None:
        activity_raw_values = [row.get(activity_column, "") for row in scope_rows]
        missing_activity = sum(
            parse_first_number(value) is None for value in activity_raw_values
        )
        censored_activity = sum(value.lstrip().startswith(("<", ">")) for value in activity_raw_values)
        lines.append(f"- Missing or unparseable activity values: {missing_activity}")
        lines.append(f"- Censored activity values: {censored_activity}")

    lines.extend(["", "## Stability columns", ""])
    for column in stability_columns:
        summary = summarize_column(activity_rows, column)
        if reference is not None:
            summary["reference_raw_value"] = reference.get(column, "")
        lines.extend(
            [
                f"### {column}",
                "",
                f"- total: {summary['total']}",
                f"- nonempty: {summary['nonempty']}",
                f"- missing: {summary['missing']}",
                f"- parseable: {summary['parseable']}",
                f"- median: {summary['median']}",
                f"- min: {summary['min']}",
                f"- max: {summary['max']}",
                f"- reference_raw_value: {summary['reference_raw_value'] if reference is not None else 'not available'}",
                "",
            ]
        )
    lines.extend(["## Outputs", "", f"- Candidate CSV: {candidate_output}", ""])
    output.write_text("\n".join(lines), encoding="utf-8")


def _same_path(first: Path, second: Path) -> bool:
    return first.resolve() == second.resolve()


def _would_overwrite_input(output: Path, input_path: Path) -> bool:
    """Detect matching paths and existing hard links before opening any output."""
    if _same_path(output, input_path):
        return True
    if output.exists() and input_path.exists():
        try:
            return output.samefile(input_path)
        except OSError:
            return False
    return False


def main(argv: list[str] | None = None) -> int:
    """Run stability analysis only for explicitly supplied input and output paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--reference")
    parser.add_argument("--id-column")
    parser.add_argument("--group-by")
    parser.add_argument("--group-value")
    parser.add_argument("--activity-column")
    parser.add_argument("--activity-direction", choices=("lower", "higher"))
    parser.add_argument("--activity-threshold", type=float)
    parser.add_argument("--stability-column", action="append", default=[])
    parser.add_argument("--position-column", action="append", default=[])
    args = parser.parse_args(argv)

    if args.group_value is not None and args.group_by is None:
        parser.error("--group-value requires --group-by")
    if args.activity_threshold is not None and (
        args.activity_column is None or args.activity_direction is None
    ):
        parser.error("--activity-threshold requires --activity-column and --activity-direction")
    if not args.stability_column:
        parser.error("at least one --stability-column is required")
    if _same_path(args.output_csv, args.summary):
        parser.error("--output-csv and --summary must be different paths")
    if any(
        _would_overwrite_input(output, input_path)
        for output in (args.output_csv, args.summary)
        for input_path in args.input
    ):
        parser.error("output paths must not overwrite an input table")

    headers, rows = merge_tables(args.input)
    if not rows:
        raise ValueError("Input tables contain no rows")
    id_column = resolve_id_column(headers, args.id_column)
    group_by = resolve_column(headers, args.group_by) if args.group_by else None
    activity_column = (
        resolve_column(headers, args.activity_column) if args.activity_column else None
    )
    stability_columns = [resolve_column(headers, column) for column in args.stability_column]
    position_columns = _position_columns(headers, args.position_column)

    reference = None
    if args.reference is not None:
        reference_columns = [id_column]
        if "Alias" in headers and "Alias" not in reference_columns:
            reference_columns.append("Alias")
        reference = find_reference(rows, args.reference, reference_columns)

    scope_rows = filter_by_scope(rows, group_by, args.group_value)
    if reference is not None and not any(row is reference for row in scope_rows):
        raise ValueError("Reference molecule is not in the selected scope")
    if not scope_rows:
        raise ValueError("No rows remain after applying scope")

    activity_rows = filter_by_activity(
        scope_rows,
        activity_column,
        args.activity_direction,
        args.activity_threshold,
    )
    if not activity_rows:
        raise ValueError("No rows remain after applying activity filter")

    candidate_rows = _sort_by_activity(
        [row for row in activity_rows if row is not reference],
        activity_column,
        args.activity_direction,
    )
    if reference is not None:
        candidate_rows.insert(0, reference)
    write_candidate_csv(
        candidate_rows,
        args.output_csv,
        id_column,
        stability_columns,
        position_columns,
        activity_column,
    )
    _write_summary(
        args.summary,
        args.input,
        len(rows),
        scope_rows,
        activity_rows,
        reference,
        id_column,
        group_by,
        args.group_value,
        activity_column,
        args.activity_direction,
        args.activity_threshold,
        stability_columns,
        args.output_csv,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

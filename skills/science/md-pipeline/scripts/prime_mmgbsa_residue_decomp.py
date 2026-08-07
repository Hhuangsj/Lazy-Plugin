#!/usr/bin/env python3
# @name: prime_mmgbsa_residue_decomp
# @description: Aggregate Prime atomic MM/GBSA properties by prepared ligand group.

"""Summarize Prime atomic MM/GBSA properties for prepared ligand groups."""

from __future__ import absolute_import

import argparse
import csv
import math
import os
import statistics
import tempfile
from collections.abc import Mapping
from pathlib import Path

from mmgbsa_decomp_contract import (
    DEFAULT_PROPERTIES,
    ContractError,
    load_json,
    update_manifest,
)
from prepare_ligand_decomp import _selector_asl


class AggregationError(ValueError):
    """Raised when Prime atomic data cannot satisfy the ligand contract."""


FRAME_COLUMNS = (
    "frame",
    "time_ps",
    "group_id",
    "group_name",
    "property",
    "value_kcal_mol",
)
SUMMARY_COLUMNS = (
    "group_id",
    "group_name",
    "property",
    "n_frames",
    "mean",
    "sd",
    "sem",
)
GROUP_TYPES = frozenset(("residue", "n_cap", "c_cap", "crosslink"))


def _schrodinger_dependencies():
    from schrodinger.application.desmond.packages import traj
    from schrodinger.structure import StructureReader
    from schrodinger.structutils import analyze

    return StructureReader, analyze, traj


def _normalise_properties(properties):
    if properties is None:
        properties = DEFAULT_PROPERTIES
    if not isinstance(properties, Mapping) or not properties:
        raise AggregationError("properties must be a non-empty mapping")
    normalised = list(properties.items())
    expected_labels = list(DEFAULT_PROPERTIES)
    labels = [label for label, _ in normalised]
    if any(not isinstance(label, str) or not label for label in labels):
        raise AggregationError("property labels must be non-empty strings")
    if len(set(labels)) != len(labels):
        raise AggregationError("properties contain duplicate labels")
    if any(label not in DEFAULT_PROPERTIES for label in labels):
        raise AggregationError("properties must be a subset of DEFAULT_PROPERTIES")
    if labels != [label for label in expected_labels if label in labels]:
        raise AggregationError("properties must retain DEFAULT_PROPERTIES order")
    for label, property_name in normalised:
        if property_name != DEFAULT_PROPERTIES[label]:
            raise AggregationError(
                "property {} must use its shared DEFAULT_PROPERTIES value".format(label)
            )
    return normalised


def _normalise_groups(residue_map):
    if not isinstance(residue_map, Mapping):
        raise AggregationError("residue map must be a JSON object")
    schema_version = residue_map.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise AggregationError("residue map schema_version must be 1")
    ligand_asl = residue_map.get("analysis_ligand_asl")
    if not isinstance(ligand_asl, str) or not ligand_asl:
        raise AggregationError("residue map has no analysis_ligand_asl")
    groups = residue_map.get("groups")
    if not isinstance(groups, list) or not groups:
        raise AggregationError("residue map has no groups")

    normalised = []
    seen_group_ids = set()
    for position, group in enumerate(groups):
        if not isinstance(group, Mapping):
            raise AggregationError("group {} must be an object".format(position))
        required_fields = {
            "group_id",
            "group_type",
            "group_name",
            "maestro_atom_indices",
            "selector",
        }
        missing_fields = sorted(required_fields - set(group))
        if missing_fields:
            raise AggregationError(
                "group {} missing required fields {}".format(position, missing_fields)
            )
        group_id = group.get("group_id")
        group_type = group.get("group_type")
        group_name = group.get("group_name")
        maestro_atom_indices = group.get("maestro_atom_indices")
        selector = group.get("selector")
        if not isinstance(group_id, str) or not group_id:
            raise AggregationError("group {} has invalid group_id".format(position))
        if group_id in seen_group_ids:
            raise AggregationError("duplicate group_id: {}".format(group_id))
        if group_type not in GROUP_TYPES:
            raise AggregationError("group {} has invalid group_type".format(group_id))
        if not isinstance(group_name, str):
            raise AggregationError("group {} has invalid group_name".format(group_id))
        if (
            not isinstance(maestro_atom_indices, list)
            or not maestro_atom_indices
            or any(
                isinstance(atom_index, bool)
                or not isinstance(atom_index, int)
                or atom_index < 1
                for atom_index in maestro_atom_indices
            )
            or len(set(maestro_atom_indices)) != len(maestro_atom_indices)
        ):
            raise AggregationError(
                "group {} has invalid maestro_atom_indices".format(group_id)
            )
        if not isinstance(selector, Mapping):
            raise AggregationError("group {} has invalid selector".format(group_id))
        if set(selector) != {"chain", "resnum", "inscode", "pdbres"}:
            raise AggregationError("group {} has invalid selector fields".format(group_id))
        if (
            not isinstance(selector["chain"], str)
            or isinstance(selector["resnum"], bool)
            or not isinstance(selector["resnum"], int)
            or not isinstance(selector["inscode"], str)
            or not isinstance(selector["pdbres"], str)
            or not selector["pdbres"]
        ):
            raise AggregationError("group {} has invalid selector values".format(group_id))
        try:
            asl = _selector_asl(selector)
        except (KeyError, TypeError, ValueError) as exc:
            raise AggregationError(
                "group {} has invalid selector: {}".format(group_id, exc)
            )
        seen_group_ids.add(group_id)
        normalised.append((group_id, group_name, asl))
    return ligand_asl, sorted(normalised, key=lambda group: group[0])


def _select_atoms(analyze, structure, asl, label):
    try:
        values = list(analyze.evaluate_asl(structure, asl))
    except Exception as exc:
        raise AggregationError("cannot evaluate {} ASL: {}".format(label, exc))
    if not values:
        raise AggregationError("{} ASL selected zero atoms".format(label))
    atom_count = getattr(structure, "atom_total", None)
    if isinstance(atom_count, bool) or not isinstance(atom_count, int):
        try:
            atom_count = len(structure.atom)
        except TypeError:
            atom_count = None
    selected = set()
    for atom_index in values:
        if (
            isinstance(atom_index, bool)
            or not isinstance(atom_index, int)
            or atom_index < 1
            or (atom_count is not None and atom_index > atom_count)
        ):
            raise AggregationError(
                "{} ASL returned invalid atom index {!r}".format(label, atom_index)
            )
        if atom_index in selected:
            raise AggregationError(
                "{} ASL returned duplicate atom index {}".format(label, atom_index)
            )
        selected.add(atom_index)
    return selected


def _validate_snapshot_partition(analyze, structure, groups, ligand_asl):
    ligand_atoms = _select_atoms(analyze, structure, ligand_asl, "ligand")
    assigned = set()
    selections = []
    for group_id, group_name, group_asl in groups:
        group_atoms = _select_atoms(
            analyze, structure, group_asl, "group {}".format(group_id)
        )
        overlap = assigned.intersection(group_atoms)
        if overlap:
            raise AggregationError(
                "group selector overlap for {}: {}".format(
                    group_id, sorted(overlap)
                )
            )
        assigned.update(group_atoms)
        selections.append((group_id, group_name, group_atoms))
    if assigned != ligand_atoms:
        raise AggregationError(
            "group selector drift from analysis_ligand_asl: missing={} extra={}".format(
                sorted(ligand_atoms - assigned), sorted(assigned - ligand_atoms)
            )
        )
    return ligand_atoms, selections


def _property_value(
    atom, property_name, atom_index, source_index, group_id, label
):
    context = (
        "source frame {}, group_id {}, label {}, Prime property {}"
        .format(source_index, group_id, label, property_name)
    )
    try:
        properties = atom.property
        value = properties[property_name]
    except (AttributeError, KeyError, TypeError):
        raise AggregationError(
            "{}: missing property on ligand atom {}".format(context, atom_index)
        )
    if isinstance(value, bool):
        raise AggregationError(
            "{} on ligand atom {} is not numeric".format(context, atom_index)
        )
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise AggregationError(
            "{} on ligand atom {} is not numeric".format(context, atom_index)
        )
    if not math.isfinite(value):
        raise AggregationError(
            "{} on ligand atom {} is not finite".format(context, atom_index)
        )
    return value


def _cache_snapshot_properties(
    structure, group_selections, properties, source_index
):
    atom_values = {}
    group_values = {}
    for group_id, _, atom_indices in group_selections:
        for label, property_name in properties:
            values = []
            for atom_index in sorted(atom_indices):
                value = _property_value(
                    structure.atom[atom_index],
                    property_name,
                    atom_index,
                    source_index,
                    group_id,
                    label,
                )
                atom_values[(atom_index, label)] = value
                values.append(value)
            group_values[(group_id, label)] = math.fsum(values)
    return atom_values, group_values


def _atomic_write_csv(path, columns, rows):
    output = Path(path)
    if not output.parent.is_dir():
        raise AggregationError("CSV output directory does not exist: {}".format(output.parent))
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}.".format(output.name), suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(temporary_name)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _backup_existing_output(path):
    output = Path(path)
    if not output.exists():
        return None
    descriptor, backup_name = tempfile.mkstemp(
        prefix=".{}.".format(output.name), suffix=".bak", dir=str(output.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as backup:
            with output.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    backup.write(chunk)
                backup.flush()
                os.fsync(backup.fileno())
        return Path(backup_name)
    except Exception:
        try:
            os.unlink(backup_name)
        except OSError:
            pass
        raise


def _unlink_if_present(path):
    if path is None:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


def _publish_csv_outputs(
    frame_csv_path,
    summary_csv_path,
    frame_rows,
    summary_rows,
    finalize_publication,
):
    publications = [
        {
            "path": Path(frame_csv_path),
            "columns": FRAME_COLUMNS,
            "rows": frame_rows,
            "temporary": None,
            "backup": None,
            "attempted": False,
        },
        {
            "path": Path(summary_csv_path),
            "columns": SUMMARY_COLUMNS,
            "rows": summary_rows,
            "temporary": None,
            "backup": None,
            "attempted": False,
        },
    ]
    try:
        for publication in publications:
            publication["temporary"] = _atomic_write_csv(
                publication["path"], publication["columns"], publication["rows"]
            )
        for publication in publications:
            publication["backup"] = _backup_existing_output(publication["path"])
        for publication in publications:
            publication["attempted"] = True
            os.replace(
                str(publication["temporary"]), str(publication["path"])
            )
            publication["temporary"] = None
        finalize_publication()
    except Exception as exc:
        rollback_issues = []
        for publication in reversed(publications):
            if not publication["attempted"]:
                continue
            try:
                if publication["backup"] is None:
                    _unlink_if_present(publication["path"])
                else:
                    os.replace(
                        str(publication["backup"]), str(publication["path"])
                    )
                    publication["backup"] = None
            except Exception as rollback_exc:
                rollback_issues.append(
                    "{}: {}".format(publication["path"], rollback_exc)
                )
        if rollback_issues:
            raise AggregationError(
                "{}; output rollback issue: {}".format(
                    exc, "; ".join(rollback_issues)
                )
            ) from exc
        raise
    finally:
        for publication in publications:
            for artifact in (publication["temporary"], publication["backup"]):
                try:
                    _unlink_if_present(artifact)
                except OSError:
                    pass


def _failure_log_path(frame_csv_path):
    frame_path = Path(frame_csv_path)
    return frame_path.parent / "prime_mmgbsa_residue_decomp.log"


def _mark_manifest_failed(manifest_path, frame_csv_path, error):
    log_path = _failure_log_path(frame_csv_path)
    try:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write("aggregation: {}\n".format(error))
    except OSError:
        pass
    update_manifest(
        manifest_path,
        "failed",
        stage="aggregation",
        return_code=2,
        log=log_path,
    )


def _manifest_is_terminal(manifest_path):
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return False
    return isinstance(manifest, Mapping) and manifest.get("status") in {
        "success", "failed"
    }


def _aggregation_error(exc):
    if isinstance(exc, AggregationError):
        return exc
    return AggregationError(str(exc))


def _record_manifest_failure(manifest_path, frame_csv_path, error):
    try:
        _mark_manifest_failed(manifest_path, frame_csv_path, error)
    except Exception as manifest_exc:
        if _manifest_is_terminal(manifest_path):
            return error
        return AggregationError(
            "{}; manifest failure transition issue: {}".format(error, manifest_exc)
        )
    return error


def _mark_manifest_success(manifest_path, frame_csv_path, summary_csv_path, properties, frames):
    manifest = load_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("status") != "running":
        raise AggregationError("manifest must be running before aggregation")
    paths = dict(manifest.get("paths") or {})
    paths.update({
        "frame_csv": str(frame_csv_path),
        "summary_csv": str(summary_csv_path),
    })
    update_manifest(
        manifest_path,
        "success",
        paths=paths,
        frames=frames,
        properties=dict(properties),
        aggregation={"status": "success"},
    )


def _summary_rows(values_by_group_property, groups, properties):
    rows = []
    for group_id, group_name, _ in groups:
        for label, _ in properties:
            values = values_by_group_property[(group_id, group_name, label)]
            count = len(values)
            mean = statistics.mean(values)
            sd = statistics.pstdev(values)
            rows.append({
                "group_id": group_id,
                "group_name": group_name,
                "property": label,
                "n_frames": count,
                "mean": mean,
                "sd": sd,
                "sem": sd / math.sqrt(count),
            })
    return rows


def aggregate_prime_mmgbsa(
    prime_maegz,
    residue_map_path,
    trajectory_path,
    start,
    end,
    step,
    frame_csv_path,
    summary_csv_path,
    manifest_path,
    properties=None,
):
    """Aggregate selected Prime snapshots and atomically write both CSV files."""
    try:
        if isinstance(start, bool) or isinstance(end, bool) or isinstance(step, bool):
            raise AggregationError("frame indices must be integers")
        if not all(isinstance(value, int) for value in (start, end, step)):
            raise AggregationError("frame indices must be integers")
        if start < 0 or end < start or step <= 0:
            raise AggregationError("invalid inclusive frame range")
        source_indices = list(range(start, end + 1, step))
        properties = _normalise_properties(properties)
        residue_map = load_json(residue_map_path)
        ligand_asl, groups = _normalise_groups(residue_map)
        manifest = load_json(manifest_path)
        if not isinstance(manifest, Mapping) or manifest.get("status") != "running":
            raise AggregationError("manifest must be running before aggregation")

        StructureReader, analyze, traj = _schrodinger_dependencies()
        structures = list(StructureReader(str(prime_maegz)))
        if len(structures) != len(source_indices):
            raise AggregationError(
                "Prime structure count {} does not match selected trajectory frames {}"
                .format(len(structures), len(source_indices))
            )
        trajectory = traj.read_traj(str(trajectory_path))
        if source_indices[-1] >= len(trajectory):
            raise AggregationError("inclusive frame range exceeds trajectory length")

        frame_rows = []
        values_by_group_property = {}
        for source_index, structure in zip(source_indices, structures):
            time_ps = float(trajectory[source_index].time)
            if not math.isfinite(time_ps):
                raise AggregationError(
                    "trajectory time for frame {} is not finite".format(source_index)
                )
            ligand_atoms, group_selections = _validate_snapshot_partition(
                analyze, structure, groups, ligand_asl
            )
            atom_values, group_values = _cache_snapshot_properties(
                structure, group_selections, properties, source_index
            )
            for label, property_name in properties:
                direct_sum = math.fsum(
                    atom_values[(atom_index, label)]
                    for atom_index in sorted(ligand_atoms)
                )
                group_sum = math.fsum(
                    group_values[(group_id, label)]
                    for group_id, _, _ in group_selections
                )
                if not math.isclose(group_sum, direct_sum, rel_tol=1e-9, abs_tol=1e-6):
                    raise AggregationError(
                        "group sum does not reconcile with ligand direct sum for "
                        "label {} (Prime property {}) at source frame {}"
                        .format(label, property_name, source_index)
                    )
            for group_id, group_name, _ in group_selections:
                for label, _ in properties:
                    value = group_values[(group_id, label)]
                    frame_rows.append({
                        "frame": source_index,
                        "time_ps": time_ps,
                        "group_id": group_id,
                        "group_name": group_name,
                        "property": label,
                        "value_kcal_mol": value,
                    })
                    values_by_group_property.setdefault(
                        (group_id, group_name, label), []
                    ).append(value)

        summary_rows = _summary_rows(values_by_group_property, groups, properties)
        _publish_csv_outputs(
            frame_csv_path,
            summary_csv_path,
            frame_rows,
            summary_rows,
            lambda: _mark_manifest_success(
                manifest_path,
                frame_csv_path,
                summary_csv_path,
                properties,
                {
                    "start": start,
                    "end": end,
                    "step": step,
                    "count": len(source_indices),
                },
            ),
        )
        return {"frame_rows": len(frame_rows), "summary_rows": len(summary_rows)}
    except Exception as exc:
        error = _aggregation_error(exc)
        error = _record_manifest_failure(manifest_path, frame_csv_path, error)
        if error is exc:
            raise
        raise error from exc


def _parse_property_names(values):
    if values is None:
        return None
    names = []
    for value in values:
        current_names = value.split(",")
        if any(not name for name in current_names):
            raise AggregationError("property list contains an empty name")
        names.extend(current_names)
    if not names:
        raise AggregationError("property list must not be empty")
    unknown = [name for name in names if name not in DEFAULT_PROPERTIES]
    if unknown:
        raise AggregationError("unknown property names: {}".format(", ".join(unknown)))
    if len(set(names)) != len(names):
        raise AggregationError("duplicate property names")
    return {name: DEFAULT_PROPERTIES[name] for name in names}


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime-maegz", required=True)
    parser.add_argument("--residue-map", required=True)
    parser.add_argument("--trajectory", required=True)
    parser.add_argument("--start", required=True, type=int)
    parser.add_argument("--end", required=True, type=int)
    parser.add_argument("--step", required=True, type=int)
    parser.add_argument("--frame-csv", required=True)
    parser.add_argument("--summary-csv", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--properties", nargs="+")
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        properties = _parse_property_names(args.properties)
    except Exception as exc:
        error = _aggregation_error(exc)
        error = _record_manifest_failure(args.manifest, args.frame_csv, error)
        parser.exit(2, "ERROR: {}\n".format(error))
    try:
        aggregate_prime_mmgbsa(
            prime_maegz=args.prime_maegz,
            residue_map_path=args.residue_map,
            trajectory_path=args.trajectory,
            start=args.start,
            end=args.end,
            step=args.step,
            frame_csv_path=args.frame_csv,
            summary_csv_path=args.summary_csv,
            manifest_path=args.manifest,
            properties=properties,
        )
    except (AggregationError, ContractError, OSError, ValueError) as exc:
        parser.exit(2, "ERROR: {}\n".format(exc))
    return 0


if __name__ == "__main__":
    main()

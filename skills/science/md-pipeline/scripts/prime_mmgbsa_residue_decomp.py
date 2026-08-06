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
    normalised = []
    seen_labels = set()
    seen_names = set()
    for label, property_name in properties.items():
        if not isinstance(label, str) or not label:
            raise AggregationError("property labels must be non-empty strings")
        if not isinstance(property_name, str) or not property_name:
            raise AggregationError("Prime property names must be non-empty strings")
        if label in seen_labels:
            raise AggregationError("duplicate property label: {}".format(label))
        if property_name in seen_names:
            raise AggregationError("duplicate Prime property name: {}".format(property_name))
        seen_labels.add(label)
        seen_names.add(property_name)
        normalised.append((label, property_name))
    return normalised


def _normalise_groups(residue_map):
    if not isinstance(residue_map, Mapping):
        raise AggregationError("residue map must be a JSON object")
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
        group_id = group.get("group_id")
        group_name = group.get("group_name")
        selector = group.get("selector")
        if not isinstance(group_id, str) or not group_id:
            raise AggregationError("group {} has invalid group_id".format(position))
        if group_id in seen_group_ids:
            raise AggregationError("duplicate group_id: {}".format(group_id))
        if not isinstance(group_name, str):
            raise AggregationError("group {} has invalid group_name".format(group_id))
        if not isinstance(selector, Mapping):
            raise AggregationError("group {} has invalid selector".format(group_id))
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
    try:
        atom_count = len(structure.atom) - 1
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


def _property_value(atom, property_name, atom_index):
    try:
        properties = atom.property
        value = properties[property_name]
    except (AttributeError, KeyError, TypeError):
        raise AggregationError(
            "missing property {} on ligand atom {}".format(
                property_name, atom_index
            )
        )
    if isinstance(value, bool):
        raise AggregationError(
            "property {} on ligand atom {} is not numeric".format(
                property_name, atom_index
            )
        )
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise AggregationError(
            "property {} on ligand atom {} is not numeric".format(
                property_name, atom_index
            )
        )
    if not math.isfinite(value):
        raise AggregationError(
            "property {} on ligand atom {} is not finite".format(
                property_name, atom_index
            )
        )
    return value


def _sum_property(structure, atom_indices, property_name):
    return math.fsum(
        _property_value(structure.atom[atom_index], property_name, atom_index)
        for atom_index in sorted(atom_indices)
    )


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


def _replace_csv_outputs(frame_csv_path, summary_csv_path, frame_rows, summary_rows):
    frame_temporary = None
    summary_temporary = None
    try:
        frame_temporary = _atomic_write_csv(frame_csv_path, FRAME_COLUMNS, frame_rows)
        summary_temporary = _atomic_write_csv(
            summary_csv_path, SUMMARY_COLUMNS, summary_rows
        )
        os.replace(str(frame_temporary), str(frame_csv_path))
        frame_temporary = None
        os.replace(str(summary_temporary), str(summary_csv_path))
        summary_temporary = None
    finally:
        for temporary_path in (frame_temporary, summary_temporary):
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass


def _failure_log_path(frame_csv_path):
    frame_path = Path(frame_csv_path)
    return frame_path.parent / "prime_mmgbsa_residue_decomp.log"


def _mark_manifest_failed(manifest_path, frame_csv_path, error):
    manifest = load_json(manifest_path)
    if not isinstance(manifest, Mapping) or manifest.get("status") != "running":
        return
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
            for label, property_name in properties:
                direct_sum = _sum_property(structure, ligand_atoms, property_name)
                group_sum = math.fsum(
                    _sum_property(structure, atom_indices, property_name)
                    for _, _, atom_indices in group_selections
                )
                if not math.isclose(group_sum, direct_sum, rel_tol=1e-9, abs_tol=1e-6):
                    raise AggregationError(
                        "group sum does not reconcile with ligand direct sum for {} at frame {}"
                        .format(label, source_index)
                    )
            for group_id, group_name, atom_indices in group_selections:
                for label, property_name in properties:
                    value = _sum_property(structure, atom_indices, property_name)
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
        _replace_csv_outputs(
            frame_csv_path, summary_csv_path, frame_rows, summary_rows
        )
        _mark_manifest_success(
            manifest_path,
            frame_csv_path,
            summary_csv_path,
            properties,
            {"start": start, "end": end, "step": step, "count": len(source_indices)},
        )
        return {"frame_rows": len(frame_rows), "summary_rows": len(summary_rows)}
    except Exception as exc:
        error = exc if isinstance(exc, AggregationError) else AggregationError(str(exc))
        try:
            _mark_manifest_failed(manifest_path, frame_csv_path, error)
        except (ContractError, OSError, ValueError) as manifest_exc:
            error = AggregationError(
                "{}; manifest failure transition issue: {}".format(error, manifest_exc)
            )
        raise error


def _parse_property_names(values):
    if not values:
        return None
    names = []
    for value in values:
        names.extend(name for name in value.split(",") if name)
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
    args = _build_parser().parse_args(argv)
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
            properties=_parse_property_names(args.properties),
        )
    except (AggregationError, ContractError, OSError, ValueError) as exc:
        parser.exit(2, "ERROR: {}\n".format(exc))
    return 0


if __name__ == "__main__":
    main()

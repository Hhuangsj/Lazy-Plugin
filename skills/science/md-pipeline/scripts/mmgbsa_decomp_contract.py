#!/usr/bin/env python3
# @name: mmgbsa_decomp_contract
# @description: Shared contract and irreversible manifest state machine for MM/GBSA residue decomposition.

"""Shared, Python-3.8-compatible contract for ligand MM/GBSA decomposition."""

from __future__ import absolute_import

import argparse
import json
import os
import tempfile
from contextlib import contextmanager
from collections.abc import Mapping
from numbers import Integral
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Schrödinger's supported hosts are POSIX.
    fcntl = None


DEFAULT_PROPERTIES = {
    "dG_Bind": "r_psp_MMGBSA_dG_Bind",
    "Coulomb": "r_psp_MMGBSA_dG_Bind(NS)_Coulomb",
    "Solv_GB": "r_psp_MMGBSA_dG_Bind(NS)_Solv_GB",
    "Covalent": "r_psp_MMGBSA_dG_Bind(NS)_Covalent",
    "vdW": "r_psp_MMGBSA_dG_Bind(NS)_vdW",
    "Hbond": "r_psp_MMGBSA_dG_Bind(NS)_Hbond",
    "Lipo": "r_psp_MMGBSA_dG_Bind(NS)_Lipo",
    "Packing": "r_psp_MMGBSA_dG_Bind(NS)_Packing",
    "SelfCont": "r_psp_MMGBSA_dG_Bind(NS)_SelfCont",
    "Lig_Strain": "r_psp_Lig_Strain_Energy",
}


class ContractError(ValueError):
    """Raised when a shared decomp contract or state transition is invalid."""


_TERMINAL_STATUSES = frozenset(("success", "failed"))
_VALID_STATUSES = frozenset(("running", "success", "failed"))


def _is_atom_index(value):
    return isinstance(value, Integral) and not isinstance(value, bool) and value >= 1


def _normalise_atom_indices(values, label):
    if isinstance(values, (str, bytes)):
        raise ContractError("{} must be an iterable of one-based atom indices".format(label))
    try:
        values = list(values)
    except TypeError:
        raise ContractError("{} must be an iterable of one-based atom indices".format(label))
    invalid = [value for value in values if not _is_atom_index(value)]
    if invalid:
        raise ContractError("{} contains invalid one-based atom indices: {}".format(label, invalid))
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ContractError("{} contains duplicate atom indices: {}".format(label, duplicates))
    return set(values)


def _normalise_groups(groups):
    if isinstance(groups, (str, bytes, Mapping)):
        raise ContractError("groups must be an iterable of mapping records")
    try:
        records = list(groups)
    except TypeError:
        raise ContractError("groups must be an iterable of mapping records")

    normalised = []
    seen_group_ids = set()
    for position, group in enumerate(records):
        if not isinstance(group, Mapping) or "maestro_atom_indices" not in group:
            raise ContractError(
                "each group must be a mapping with maestro_atom_indices"
            )
        current_id = group.get("group_id", position)
        atom_values = group["maestro_atom_indices"]
        if current_id in seen_group_ids:
            raise ContractError("duplicate group id: {}".format(current_id))
        seen_group_ids.add(current_id)
        atom_set = _normalise_atom_indices(atom_values, "group {}".format(current_id))
        if not atom_set:
            raise ContractError("group {} has no atoms".format(current_id))
        normalised.append((current_id, atom_set))
    if not normalised:
        raise ContractError("groups must not be empty")
    return normalised


def validate_maestro_partition(ligand_atom_indices, groups):
    """Validate an exact partition of a ligand's one-based Maestro atom IDs.

    groups must be an iterable of mapping records containing
    maestro_atom_indices. Every ligand atom must occur exactly once, and
    zero-based, non-integral, missing, extra, or duplicate IDs are rejected.
    """
    ligand_set = _normalise_atom_indices(ligand_atom_indices, "ligand atom set")
    if not ligand_set:
        raise ContractError("ligand atom set must not be empty")
    normalised_groups = _normalise_groups(groups)

    assigned = []
    for _, atom_set in normalised_groups:
        assigned.extend(atom_set)
    assigned_set = set(assigned)
    duplicates = sorted(atom for atom in assigned if assigned.count(atom) > 1)
    missing = sorted(ligand_set - assigned_set)
    extra = sorted(assigned_set - ligand_set)
    if duplicates or missing or extra:
        problems = []
        if duplicates:
            problems.append("duplicate={}".format(sorted(set(duplicates))))
        if missing:
            problems.append("missing={}".format(missing))
        if extra:
            problems.append("extra={}".format(extra))
        raise ContractError("invalid Maestro atom partition: " + "; ".join(problems))
    return None


def atomic_write_json(path, payload):
    """Serialize JSON and atomically replace path using a sibling temp file."""
    output = Path(path)
    if not output.parent.is_dir():
        raise ContractError("output directory does not exist: {}".format(output.parent))
    try:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    except (TypeError, ValueError, OverflowError) as exc:
        raise ContractError("payload is not JSON serializable: {}".format(exc))

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".{}.".format(output.name), suffix=".tmp", dir=str(output.parent)
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, str(output))
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise ContractError("atomic JSON write failed") from None


def load_json(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


@contextmanager
def _manifest_lock(manifest_path):
    """Serialize manifest operations without leaving a lock-file artifact."""
    if fcntl is None:
        raise ContractError("manifest locking requires a POSIX fcntl implementation")
    parent = Path(manifest_path).parent
    if not parent.is_dir():
        raise ContractError("manifest directory does not exist: {}".format(parent))
    lock_fd = os.open(str(parent), os.O_RDONLY)
    locked = False
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _copy_properties(properties):
    if properties is None:
        return dict(DEFAULT_PROPERTIES)
    if isinstance(properties, Mapping):
        return dict(properties)
    try:
        return list(properties)
    except TypeError:
        raise ContractError("properties must be a mapping or iterable")


def initialize_manifest(
    manifest_path,
    paths=None,
    ligand_asl=None,
    frames=None,
    properties=None,
    versions=None,
    **extra_fields
):
    """Create schema-v1 in the only non-terminal state: running."""
    path = Path(manifest_path)
    with _manifest_lock(path):
        if path.exists():
            raise ContractError("manifest already exists: {}".format(path))
        if "asl" in extra_fields and ligand_asl is None:
            ligand_asl = extra_fields.pop("asl")

        manifest = {
            "schema_version": 1,
            "status": "running",
            "paths": dict(paths or {}),
            "ligand_asl": ligand_asl,
            "frames": dict(frames or {}),
            "properties": _copy_properties(properties),
            "versions": dict(versions or {}),
        }
        for key, value in extra_fields.items():
            if key in manifest or key == "status":
                raise ContractError("cannot override manifest field: {}".format(key))
            manifest[key] = value
        atomic_write_json(path, manifest)
        return manifest


def _coerce_return_code(value):
    if isinstance(value, bool):
        raise ContractError("failure return_code must be numeric")
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ContractError("failure return_code must be numeric")


def _build_failure(failure, stage, return_code, log):
    if failure is not None:
        if not isinstance(failure, Mapping):
            raise ContractError("failure must be a mapping")
        if any(value is not None for value in (stage, return_code, log)):
            raise ContractError("use either failure or stage/return-code/log")
        stage = failure.get("stage")
        return_code = failure.get("return_code")
        log = failure.get("log")
    if not isinstance(stage, str) or not stage:
        raise ContractError("failed manifest requires a non-empty stage")
    if log is None or isinstance(log, (dict, list, tuple, set)):
        raise ContractError("failed manifest requires a log path")
    return {
        "stage": stage,
        "return_code": _coerce_return_code(return_code),
        "log": str(log),
    }


def update_manifest(
    manifest_path,
    status,
    failure=None,
    error=None,
    stage=None,
    return_code=None,
    log=None,
    **updates
):
    """Apply one allowed manifest transition and atomically persist it."""
    if status not in _VALID_STATUSES:
        raise ContractError("invalid manifest status: {}".format(status))
    path = Path(manifest_path)
    with _manifest_lock(path):
        if path.exists():
            try:
                manifest = load_json(path)
            except (OSError, ValueError, TypeError) as exc:
                raise ContractError("cannot load manifest {}: {}".format(path, exc))
            if not isinstance(manifest, dict):
                raise ContractError("manifest must contain a JSON object")
        else:
            manifest = {"schema_version": 1}

        current = manifest.get("status")
        if current is None:
            allowed = {"running"}
        elif current == "running":
            allowed = {"running", "success", "failed"}
        elif current in _TERMINAL_STATUSES:
            allowed = set()
        else:
            raise ContractError("invalid current manifest status: {}".format(current))
        if status not in allowed:
            raise ContractError(
                "manifest transition {} -> {} is not allowed".format(current, status)
            )

        if status == "failed":
            if failure is not None and error is not None:
                raise ContractError("use either failure or error")
            manifest["error"] = _build_failure(
                error if error is not None else failure,
                stage,
                return_code,
                log,
            )
        elif any(value is not None for value in (failure, error, stage, return_code, log)):
            raise ContractError("failure details are only valid for failed status")

        for key, value in updates.items():
            if key in ("schema_version", "status", "failure", "error"):
                raise ContractError("cannot override manifest field: {}".format(key))
            manifest[key] = value
        manifest["schema_version"] = 1
        manifest["status"] = status
        atomic_write_json(path, manifest)
        return manifest


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    failure_parser = subparsers.add_parser("manifest-fail", help="mark a manifest failed")
    failure_parser.add_argument("--manifest", required=True)
    failure_parser.add_argument("--stage", required=True)
    failure_parser.add_argument("--return-code", required=True, type=int)
    failure_parser.add_argument("--log", required=True)
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command != "manifest-fail":
        parser.error("a command is required")
    try:
        update_manifest(
            args.manifest,
            "failed",
            stage=args.stage,
            return_code=args.return_code,
            log=args.log,
        )
    except (ContractError, OSError, ValueError) as exc:
        parser.exit(2, "ERROR: {}\n".format(exc))
    return 0


if __name__ == "__main__":
    main()

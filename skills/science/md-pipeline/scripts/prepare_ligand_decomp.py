#!/usr/bin/env python3
# @name: prepare_ligand_decomp
# @description: Build atom-complete ligand residue groups and a non-destructive analysis CMS
# @requires: schrodinger
# @usage: prepare_ligand_decomp.py CMS --lig-asl ASL --out-dir DIR [--synergy-dir DIR] [--adapter-python PYTHON]

"""Prepare ligand residue-decomposition inputs from a Desmond CMS file."""

from __future__ import absolute_import

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from mmgbsa_decomp_contract import (
    ContractError,
    atomic_write_json,
    initialize_manifest,
    load_json,
    update_manifest,
    validate_maestro_partition,
)


ADAPTER_SCRIPT = Path(__file__).resolve().with_name("synergy_residue_adapter.py")
_ANALYSIS_CHAIN_CANDIDATES = ("L", "B", "C", "D", "E")
_OUTPUT_ARTIFACT_NAMES = (
    "decomp_manifest.json",
    "prepare_ligand_decomp.log",
    "residue_map.json",
    "atom_index_map.json",
    "ligand_graph.sdf",
    "prepare_result.json",
    "analysis.cms",
)
_LIBRARY_PATH_VARIABLES = (
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",
    "LIBPATH",
    "SHLIB_PATH",
)


class PreparationError(RuntimeError):
    """Raised when ligand preparation cannot satisfy the mapping contract."""


def _is_same_or_within(path, directory):
    try:
        Path(path).relative_to(directory)
    except ValueError:
        return False
    return True


def _candidate_synergy_path(synergy_dir):
    value = synergy_dir or os.environ.get("SYNERGY_FRAGMENT_DIR")
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _validate_path_domains(source_path, output_path, synergy_path):
    artifacts = tuple(output_path / name for name in _OUTPUT_ARTIFACT_NAMES)
    if output_path == source_path or source_path in artifacts:
        raise PreparationError(
            "path domains overlap: an output path would overwrite source CMS {}"
            .format(source_path)
        )
    if synergy_path is not None and _is_same_or_within(
        output_path, synergy_path
    ):
        raise PreparationError(
            "path domains overlap: output directory {} is inside read-only "
            "Synergy directory {}".format(output_path, synergy_path)
        )


def detect_mode_from_residues(residues):
    """Return ``single_unk`` only for one residue named UNK."""
    normalized = [
        (str(name).strip(), int(resnum), str(chain).strip())
        for name, resnum, chain in residues
    ]
    if len(normalized) == 1 and normalized[0][0] == "UNK":
        return "single_unk"
    return "pre_resolved"


def remap_rdkit_groups(groups, rdkit_to_maestro):
    """Copy adapter groups and add sorted one-based Maestro atom IDs."""
    mapped = []
    for group in groups:
        item = dict(group)
        try:
            item["maestro_atom_indices"] = sorted(
                rdkit_to_maestro[index]
                for index in group["rdkit_atom_indices"]
            )
        except (KeyError, TypeError) as exc:
            raise PreparationError(
                "cannot remap RDKit atom index for group {}: {}".format(
                    group.get("group_id", "<unknown>"), exc
                )
            )
        mapped.append(item)
    return mapped


def assign_hydrogens(groups, hydrogen_indices, neighbors):
    """Assign each explicit hydrogen to its sole bonded heavy-atom group."""
    owner = {}
    for group in groups:
        for atom_index in group["maestro_atom_indices"]:
            if atom_index in owner:
                raise PreparationError(
                    "heavy atom {} belongs to multiple groups".format(atom_index)
                )
            owner[atom_index] = group

    for hydrogen in sorted(hydrogen_indices):
        heavy_neighbors = [
            atom_index
            for atom_index in neighbors.get(hydrogen, ())
            if atom_index in owner
        ]
        if len(heavy_neighbors) != 1:
            raise PreparationError(
                "hydrogen {} has heavy owners {}".format(
                    hydrogen, heavy_neighbors
                )
            )
        owner[heavy_neighbors[0]]["maestro_atom_indices"].append(hydrogen)

    for group in groups:
        group["maestro_atom_indices"] = sorted(group["maestro_atom_indices"])
    return groups


def _normalized_rdkit_bonds(molecule):
    return tuple(sorted(
        (
            min(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()),
            max(bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()),
            round(float(bond.GetBondTypeAsDouble()), 6),
        )
        for bond in molecule.GetBonds()
    ))


def validate_sdf_round_trip(
    molecule, expected_elements, expected_charges, expected_bonds
):
    """Verify ordered heavy-atom chemistry after SDF serialization."""
    if molecule is None:
        raise PreparationError("SDF round trip did not yield a molecule")
    elements = tuple(atom.GetSymbol() for atom in molecule.GetAtoms())
    charges = tuple(atom.GetFormalCharge() for atom in molecule.GetAtoms())
    bonds = _normalized_rdkit_bonds(molecule)
    if elements != tuple(expected_elements):
        raise PreparationError(
            "SDF ordered elements differ from the selected CMS heavy atoms"
        )
    if charges != tuple(expected_charges):
        raise PreparationError(
            "SDF formal charges differ from the selected CMS heavy atoms"
        )
    if bonds != tuple(sorted(expected_bonds)):
        raise PreparationError(
            "SDF normalized bonds differ from the selected CMS heavy-atom graph"
        )
    return molecule


def _select_complete_ligand(cms_model, ligand_asl):
    selected = sorted(cms_model.select_atom(ligand_asl))
    if not selected:
        raise PreparationError(
            "ligand ASL {!r} selected zero atoms".format(ligand_asl)
        )
    if len(selected) != len(set(selected)):
        raise PreparationError(
            "ligand ASL {!r} returned duplicate atom IDs".format(ligand_asl)
        )
    molecule = {
        atom.index
        for atom in cms_model.getMoleculeAtoms(cms_model.atom[selected[0]])
    }
    if set(selected) != molecule:
        raise PreparationError(
            "ligand ASL {!r} selected {} atoms but must select one complete "
            "molecule of {} atoms".format(ligand_asl, len(selected), len(molecule))
        )
    return selected


def _ordered_residue_keys(cms_model, atom_indices):
    keys = []
    seen = set()
    for atom_index in atom_indices:
        atom = cms_model.atom[atom_index]
        key = (
            str(atom.chain).strip(),
            int(atom.resnum),
            str(atom.inscode).strip(),
            str(atom.pdbres).strip(),
        )
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys


def _source_heavy_graph(cms_model, heavy_atom_indices):
    heavy_position = {
        atom_index: position
        for position, atom_index in enumerate(heavy_atom_indices)
    }
    elements = tuple(
        cms_model.atom[atom_index].element
        for atom_index in heavy_atom_indices
    )
    charges = tuple(
        int(cms_model.atom[atom_index].formal_charge)
        for atom_index in heavy_atom_indices
    )
    bonds = []
    for bond in cms_model.fsys_ct.bond:
        left = bond.atom1.index
        right = bond.atom2.index
        if left in heavy_position and right in heavy_position:
            bonds.append((
                min(heavy_position[left], heavy_position[right]),
                max(heavy_position[left], heavy_position[right]),
                round(float(bond.order), 6),
            ))
    return elements, charges, tuple(sorted(bonds))


def _export_heavy_graph(cms_model, heavy_atom_indices, sdf_path):
    from rdkit import Chem

    heavy_structure = cms_model.fsys_ct.extract(heavy_atom_indices)
    heavy_structure.write(str(sdf_path), format="sd")
    supplier = Chem.SDMolSupplier(
        str(sdf_path), removeHs=False, sanitize=True
    )
    records = list(supplier)
    if len(records) != 1:
        raise PreparationError(
            "ligand SDF round trip yielded {} records".format(len(records))
        )
    expected = _source_heavy_graph(cms_model, heavy_atom_indices)
    return validate_sdf_round_trip(records[0], *expected)


def _pre_resolved_groups(cms_model, ligand_atom_indices):
    groups_by_key = {}
    ordered_keys = []
    for atom_index in ligand_atom_indices:
        atom = cms_model.atom[atom_index]
        key = (
            str(atom.chain).strip(),
            int(atom.resnum),
            str(atom.inscode).strip(),
            str(atom.pdbres).strip(),
        )
        if key not in groups_by_key:
            ordered_keys.append(key)
            groups_by_key[key] = []
        groups_by_key[key].append(atom_index)

    groups = []
    for position, key in enumerate(ordered_keys):
        groups.append({
            "group_id": "P{:03d}".format(position),
            "group_type": "residue",
            "group_name": key[3],
            "maestro_atom_indices": sorted(groups_by_key[key]),
        })
    return groups


def _escape_asl_string(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _selector_asl(selector):
    chain = selector["chain"] or " "
    inscode = selector["inscode"] or " "
    pdbres = selector["pdbres"]
    if len(pdbres) < 4:
        pdbres = pdbres.ljust(4)
    return (
        '(chain.name "{}" and res.num {} and res.inscode "{}" '
        'and res.ptype "{}")'
    ).format(
        _escape_asl_string(chain),
        int(selector["resnum"]),
        _escape_asl_string(inscode),
        _escape_asl_string(pdbres),
    )


def _selector_union_asl(groups):
    return "(" + " or ".join(
        _selector_asl(group["selector"])
        for group in groups
    ) + ")"


def _source_selectors(cms_model, groups):
    selectors = []
    seen = set()
    for group in groups:
        first = cms_model.atom[group["maestro_atom_indices"][0]]
        selector = {
            "chain": str(first.chain).strip(),
            "resnum": int(first.resnum),
            "inscode": str(first.inscode).strip(),
            "pdbres": str(first.pdbres).strip(),
        }
        selector_key = tuple(selector[key] for key in (
            "chain", "resnum", "inscode", "pdbres"
        ))
        if selector_key in seen:
            raise PreparationError(
                "multiple ligand groups share selector {}".format(selector)
            )
        seen.add(selector_key)
        selectors.append(selector)
    return selectors


def _selectors_are_exact(cms_model, groups, selectors):
    for group, selector in zip(groups, selectors):
        selected = set(cms_model.select_atom(_selector_asl(selector)))
        if selected != set(group["maestro_atom_indices"]):
            return False
    union_asl = "(" + " or ".join(
        _selector_asl(selector) for selector in selectors
    ) + ")"
    selected_union = set(cms_model.select_atom(union_asl))
    expected_union = {
        atom_index
        for group in groups
        for atom_index in group["maestro_atom_indices"]
    }
    return selected_union == expected_union


def _choose_analysis_chain(cms_model):
    used = {str(atom.chain).strip() for atom in cms_model.atom}
    for candidate in _ANALYSIS_CHAIN_CANDIDATES:
        if candidate not in used:
            return candidate
    raise PreparationError(
        "no unused analysis ligand chain is available from {}".format(
            ",".join(_ANALYSIS_CHAIN_CANDIDATES)
        )
    )


def _analysis_resname(group):
    canonical = str(group.get("canonical_resname") or "").strip()
    if canonical:
        return canonical
    if "rdkit_atom_indices" not in group:
        preserved = str(group.get("group_name") or "").strip()
        if preserved:
            return preserved
    group_type = group.get("group_type")
    if group_type == "n_cap":
        return "ACE"
    if group_type == "c_cap":
        return "NME"
    if group_type == "crosslink":
        return "XLK"
    return str(group["group_id"])


def _component_atom(cms_model, full_system_index):
    from schrodinger.application.desmond.packages import topo

    matches = [
        component.atom[component_index]
        for fsys_index, component_index, component, _ct_index
        in topo.cms_atom_index(cms_model)
        if fsys_index == full_system_index
    ]
    if len(matches) == 1:
        return matches[0]
    raise PreparationError(
        "full-system atom {} has {} component mappings".format(
            full_system_index, len(matches)
        )
    )


def _ct_chemistry_signature(structure):
    atoms = tuple(
        (
            atom.element,
            int(atom.formal_charge),
            tuple(atom.xyz),
        )
        for atom in structure.atom
    )
    bonds = tuple(sorted(
        (
            min(bond.atom1.index, bond.atom2.index),
            max(bond.atom1.index, bond.atom2.index),
            round(float(bond.order), 6),
        )
        for bond in structure.bond
    ))
    return structure.atom_total, atoms, bonds


def _immutable_cms_signature(cms_model):
    return (
        _ct_chemistry_signature(cms_model.fsys_ct),
        tuple(
            _ct_chemistry_signature(component)
            for component in cms_model.comp_ct
        ),
    )


def _atom_metadata(atom):
    return (
        str(atom.chain),
        int(atom.resnum),
        str(atom.inscode),
        str(atom.pdbres),
    )


def _non_target_metadata_signature(cms_model, target_atom_indices):
    from schrodinger.application.desmond.packages import topo

    target = set(target_atom_indices)
    full_system = tuple(
        (atom.index, _atom_metadata(atom))
        for atom in cms_model.fsys_ct.atom
        if atom.index not in target
    )
    components = tuple(
        (
            fsys_index,
            ct_index,
            component_index,
            _atom_metadata(component.atom[component_index]),
        )
        for fsys_index, component_index, component, ct_index
        in topo.cms_atom_index(cms_model)
        if fsys_index not in target
    )
    return full_system, components


def _write_analysis_cms(cms_model, groups, analysis_path):
    from schrodinger.application.desmond.packages import topo

    immutable_before = _immutable_cms_signature(cms_model)
    target_atom_indices = {
        atom_index
        for group in groups
        for atom_index in group["maestro_atom_indices"]
    }
    non_target_metadata_before = _non_target_metadata_signature(
        cms_model, target_atom_indices
    )
    chain = _choose_analysis_chain(cms_model)
    selectors = []
    for residue_number, group in enumerate(groups, start=1):
        pdbres = _analysis_resname(group)
        selector = {
            "chain": chain,
            "resnum": residue_number,
            "inscode": "",
            "pdbres": pdbres,
        }
        selectors.append(selector)
        for atom_index in group["maestro_atom_indices"]:
            component_atom = _component_atom(cms_model, atom_index)
            component_atom.chain = chain
            component_atom.resnum = residue_number
            component_atom.inscode = " "
            component_atom.pdbres = pdbres

    cms_model.synchronize_fsys_ct()
    if Path(analysis_path).resolve() == Path(analysis_path).parent.resolve():
        raise PreparationError("invalid analysis CMS output path")
    cms_model.write(str(analysis_path))
    _, written = topo.read_cms(str(analysis_path))
    if _immutable_cms_signature(written) != immutable_before:
        raise PreparationError(
            "analysis CMS changed atoms, chemistry, bonds, or coordinates"
        )
    if _non_target_metadata_signature(
        written, target_atom_indices
    ) != non_target_metadata_before:
        raise PreparationError(
            "analysis CMS changed non-target residue metadata"
        )

    for group, selector in zip(groups, selectors):
        for atom_index in group["maestro_atom_indices"]:
            full_atom = written.atom[atom_index]
            component_atom = _component_atom(written, atom_index)
            for atom in (full_atom, component_atom):
                actual = (
                    str(atom.chain).strip(),
                    int(atom.resnum),
                    str(atom.inscode).strip(),
                    str(atom.pdbres).strip(),
                )
                expected = (
                    selector["chain"],
                    selector["resnum"],
                    selector["inscode"],
                    selector["pdbres"],
                )
                if actual != expected:
                    raise PreparationError(
                        "analysis CMS residue metadata did not persist for "
                        "atom {}".format(atom_index)
                    )
    if not _selectors_are_exact(written, groups, selectors):
        raise PreparationError(
            "analysis CMS selectors do not uniquely recover ligand groups"
        )
    return selectors, _selector_union_asl([
        dict(group, selector=selector)
        for group, selector in zip(groups, selectors)
    ])


def _resolve_synergy_dir(synergy_path):
    if synergy_path is None:
        raise PreparationError(
            "single-UNK preparation requires --synergy-dir or "
            "SYNERGY_FRAGMENT_DIR"
        )
    try:
        path = Path(synergy_path).resolve(strict=True)
    except OSError as exc:
        raise PreparationError("invalid read-only Synergy directory: {}".format(exc))
    if not path.is_dir():
        raise PreparationError("Synergy path is not a directory: {}".format(path))
    return path


def _plain_python_environment():
    """Remove Schrödinger interpreter injection from an adapter subprocess."""
    environment = dict(os.environ)
    schrodinger_root = environment.get("SCHRODINGER")
    if not schrodinger_root:
        return environment
    root = Path(schrodinger_root).expanduser().resolve()

    def is_schrodinger_path(value):
        if not value:
            return False
        try:
            candidate = Path(value).expanduser().resolve()
        except OSError:
            return False
        return candidate == root or root in candidate.parents

    for key in ("PYTHONHOME", "PYTHONEXECUTABLE"):
        if is_schrodinger_path(environment.get(key)):
            environment.pop(key, None)
    for key in ("PATH", "PYTHONPATH") + _LIBRARY_PATH_VARIABLES:
        if key not in environment:
            continue
        environment[key] = os.pathsep.join(
            entry
            for entry in environment[key].split(os.pathsep)
            if not is_schrodinger_path(entry)
        )
    return environment


def _resolve_adapter_python(adapter_python, environment):
    requested = adapter_python or environment.get("SYNERGY_ADAPTER_PYTHON")
    executable_name = requested or "python3"
    candidate = shutil.which(
        str(executable_name), path=environment.get("PATH", os.defpath)
    )
    if candidate is None:
        raise PreparationError(
            "cannot resolve ordinary adapter Python executable: {}".format(
                executable_name
            )
        )
    candidate_path = Path(candidate).resolve()
    schrodinger_value = environment.get("SCHRODINGER")
    schrodinger_root = (
        Path(schrodinger_value).resolve() if schrodinger_value else None
    )
    if schrodinger_root is not None and (
        candidate_path == schrodinger_root
        or schrodinger_root in candidate_path.parents
    ):
        raise PreparationError(
            "adapter Python must be outside SCHRODINGER: {}".format(
                candidate_path
            )
        )

    probe = subprocess.run(
        [
            str(candidate_path),
            "-c",
            "import os,sys; import rdkit; print(os.path.realpath(sys.executable))",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise PreparationError(
            "ordinary adapter Python cannot import RDKit: {}: {}".format(
                candidate_path, probe.stderr.strip()
            )
        )
    resolved_probe = Path(probe.stdout.strip().splitlines()[-1]).resolve()
    if schrodinger_root is not None and (
        resolved_probe == schrodinger_root
        or schrodinger_root in resolved_probe.parents
    ):
        raise PreparationError(
            "adapter Python probe resolved inside SCHRODINGER: {}".format(
                resolved_probe
            )
        )
    return resolved_probe


def _run_synergy_adapter(
    sdf_path, output_dir, synergy_dir, adapter_python, log_path
):
    if not Path(ADAPTER_SCRIPT).is_file():
        raise PreparationError("Lazy Synergy adapter does not exist: {}".format(
            ADAPTER_SCRIPT
        ))
    child_environment = _plain_python_environment()
    resolved_python = _resolve_adapter_python(
        adapter_python, child_environment
    )
    resolved_synergy = _resolve_synergy_dir(synergy_dir)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".synergy-map.", suffix=".json", dir=str(output_dir)
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    command = [
        str(resolved_python),
        str(ADAPTER_SCRIPT),
        "--sdf", str(sdf_path),
        "--output", str(temporary_path),
        "--synergy-dir", str(resolved_synergy),
    ]
    try:
        completed = subprocess.run(
            command,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        with Path(log_path).open("a", encoding="utf-8") as handle:
            if completed.stdout:
                handle.write(completed.stdout)
            if completed.stderr:
                handle.write(completed.stderr)
        if completed.returncode != 0:
            raise PreparationError(
                "Lazy Synergy adapter failed with exit code {}".format(
                    completed.returncode
                )
            )
        return load_json(temporary_path)
    except OSError as exc:
        raise PreparationError("cannot execute Lazy Synergy adapter: {}".format(exc))
    finally:
        try:
            temporary_path.unlink()
        except OSError:
            pass


def _validate_adapter_payload(payload, heavy_atom_count):
    if not isinstance(payload, Mapping):
        raise PreparationError("Lazy Synergy adapter output is not a JSON object")
    if (
        isinstance(payload.get("schema_version"), bool)
        or payload.get("schema_version") != 1
        or payload.get("status") != "ok"
    ):
        raise PreparationError("Lazy Synergy adapter did not return schema-v1 ok")
    source_atom_count = payload.get("source_atom_count")
    if (
        isinstance(source_atom_count, bool)
        or not isinstance(source_atom_count, int)
        or source_atom_count != heavy_atom_count
    ):
        raise PreparationError(
            "Lazy Synergy adapter atom count does not match exported heavy atoms"
        )
    for field in ("unassigned_atom_indices", "duplicate_atom_indices"):
        if not isinstance(payload.get(field), list):
            raise PreparationError(
                "Lazy Synergy adapter field {} must be a list".format(field)
            )
    if payload["unassigned_atom_indices"] or payload[
        "duplicate_atom_indices"
    ]:
        raise PreparationError("Lazy Synergy adapter returned an invalid partition")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise PreparationError("Lazy Synergy adapter returned no groups")
    if not isinstance(payload.get("warnings"), list):
        raise PreparationError("Lazy Synergy adapter warnings must be a list")
    topology = payload.get("topology")
    if topology is not None and not isinstance(topology, Mapping):
        raise PreparationError("Lazy Synergy adapter topology must be a mapping")
    mapper_version = payload.get("mapper_version")
    if not isinstance(mapper_version, str) or not mapper_version:
        raise PreparationError(
            "Lazy Synergy adapter mapper_version must be a non-empty string"
        )

    seen_group_ids = set()
    assigned_indices = []
    required_group_fields = {
        "group_id",
        "group_type",
        "rdkit_atom_indices",
        "sequence_index",
        "display_name",
        "canonical_resname",
        "recognition_status",
        "residue_smiles",
        "connected_group_ids",
    }
    for position, group in enumerate(groups):
        if not isinstance(group, Mapping):
            raise PreparationError(
                "adapter group {} must be a mapping".format(position)
            )
        missing = sorted(required_group_fields - set(group))
        if missing:
            raise PreparationError(
                "adapter group {} missing required fields {}".format(
                    position, missing
                )
            )
        group_id = group.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            raise PreparationError(
                "adapter group {} has an invalid group_id".format(position)
            )
        if group_id in seen_group_ids:
            raise PreparationError(
                "adapter groups contain duplicate group_id {}".format(group_id)
            )
        seen_group_ids.add(group_id)
        if group.get("group_type") not in {
            "residue", "n_cap", "c_cap", "crosslink"
        }:
            raise PreparationError(
                "adapter group {} has an invalid group_type".format(position)
            )
        sequence_index = group.get("sequence_index")
        if (
            sequence_index is not None
            and (
                isinstance(sequence_index, bool)
                or not isinstance(sequence_index, int)
                or sequence_index < 0
            )
        ):
            raise PreparationError(
                "adapter group {} has an invalid sequence_index".format(position)
            )
        for field in ("display_name", "recognition_status", "residue_smiles"):
            if not isinstance(group.get(field), str):
                raise PreparationError(
                    "adapter group {} field {} must be a string".format(
                        position, field
                    )
                )
        canonical = group.get("canonical_resname")
        if canonical is not None and not isinstance(canonical, str):
            raise PreparationError(
                "adapter group {} canonical_resname must be null or a string"
                .format(position)
            )
        connections = group.get("connected_group_ids")
        if not isinstance(connections, list) or any(
            not isinstance(connection, str) for connection in connections
        ):
            raise PreparationError(
                "adapter group {} connected_group_ids must be a string list"
                .format(position)
            )
        atom_indices = group.get("rdkit_atom_indices")
        if not isinstance(atom_indices, list) or not atom_indices:
            raise PreparationError(
                "adapter group {} rdkit_atom_indices must be a non-empty list"
                .format(position)
            )
        invalid = [
            atom_index
            for atom_index in atom_indices
            if isinstance(atom_index, bool)
            or not isinstance(atom_index, int)
            or not 0 <= atom_index < heavy_atom_count
        ]
        if invalid:
            raise PreparationError(
                "adapter group {} has invalid RDKit atom indices {}".format(
                    position, invalid
                )
            )
        assigned_indices.extend(atom_indices)
    if sorted(assigned_indices) != list(range(heavy_atom_count)):
        raise PreparationError(
            "Lazy Synergy adapter groups are not an exact heavy-atom partition"
        )
    return groups


def _hydrogen_neighbors(cms_model, hydrogen_atom_indices):
    return {
        atom_index: [
            neighbor.index
            for neighbor in cms_model.atom[atom_index].bonded_atoms
        ]
        for atom_index in hydrogen_atom_indices
    }


def _write_log(log_path, message):
    with Path(log_path).open("a", encoding="utf-8") as handle:
        handle.write(str(message).rstrip() + "\n")


def _mark_manifest_failed_if_running(
    manifest_path, stage, return_code, log_path, mode
):
    manifest = load_json(manifest_path)
    if not isinstance(manifest, Mapping):
        raise PreparationError("manifest must contain a JSON object")
    status = manifest.get("status")
    if status in ("success", "failed"):
        return status
    if status != "running":
        raise PreparationError(
            "cannot fail manifest from unexpected status {!r}".format(status)
        )
    update_manifest(
        manifest_path,
        "failed",
        stage=stage,
        return_code=return_code,
        log=str(log_path),
        mode=mode,
    )
    return "failed"


def prepare_ligand_decomp(
    cms_path,
    ligand_asl,
    output_dir,
    synergy_dir=None,
    adapter_python=None,
):
    """Prepare mapping artifacts and leave the shared manifest running."""
    from schrodinger.application.desmond.packages import topo

    source_path = Path(cms_path).expanduser().resolve(strict=True)
    output_path = Path(output_dir).expanduser().resolve()
    synergy_path = _candidate_synergy_path(synergy_dir)
    _validate_path_domains(source_path, output_path, synergy_path)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest_path = output_path / "decomp_manifest.json"
    log_path = output_path / "prepare_ligand_decomp.log"
    residue_map_path = output_path / "residue_map.json"
    atom_index_map_path = output_path / "atom_index_map.json"
    ligand_graph_path = output_path / "ligand_graph.sdf"
    prepare_result_path = output_path / "prepare_result.json"
    analysis_path = output_path / "analysis.cms"
    paths = {
        "source_cms": str(source_path),
        "out_dir": str(output_path),
        "residue_map": str(residue_map_path),
        "atom_index_map": str(atom_index_map_path),
        "ligand_graph": str(ligand_graph_path),
        "prepare_result": str(prepare_result_path),
        "prepare_log": str(log_path),
    }
    stage = "manifest_initialization"
    manifest_created = False
    try:
        initialize_manifest(
            manifest_path,
            paths=paths,
            ligand_asl=ligand_asl,
            mode=None,
        )
        manifest_created = True
        stage = "ligand_selection"
        _, cms_model = topo.read_cms(str(source_path))
        ligand_atoms = _select_complete_ligand(cms_model, ligand_asl)
        residue_keys = _ordered_residue_keys(cms_model, ligand_atoms)
        mode = detect_mode_from_residues([
            (key[3], key[1], key[0]) for key in residue_keys
        ])
        update_manifest(manifest_path, "running", mode=mode)

        heavy_atoms = [
            atom_index
            for atom_index in ligand_atoms
            if int(cms_model.atom[atom_index].atomic_number) > 1
        ]
        hydrogen_atoms = [
            atom_index
            for atom_index in ligand_atoms
            if int(cms_model.atom[atom_index].atomic_number) == 1
        ]
        if not heavy_atoms:
            raise PreparationError("selected ligand contains no heavy atoms")

        stage = "heavy_graph_export"
        atom_index_map = {
            str(position): atom_index
            for position, atom_index in enumerate(heavy_atoms)
        }
        atomic_write_json(atom_index_map_path, atom_index_map)
        _export_heavy_graph(cms_model, heavy_atoms, ligand_graph_path)

        adapter_payload = None
        if mode == "single_unk":
            stage = "single_unk_mapping"
            adapter_payload = _run_synergy_adapter(
                ligand_graph_path,
                output_path,
                synergy_path,
                adapter_python,
                log_path,
            )
            adapter_groups = _validate_adapter_payload(
                adapter_payload, len(heavy_atoms)
            )
            rdkit_to_maestro = {
                position: atom_index
                for position, atom_index in enumerate(heavy_atoms)
            }
            groups = remap_rdkit_groups(adapter_groups, rdkit_to_maestro)
            validate_maestro_partition(heavy_atoms, groups)
            assign_hydrogens(
                groups,
                hydrogen_atoms,
                _hydrogen_neighbors(cms_model, hydrogen_atoms),
            )
            for group in groups:
                group["group_name"] = str(
                    group.get("canonical_resname")
                    or group.get("display_name")
                    or ""
                ).strip()
        else:
            stage = "pre_resolved_mapping"
            groups = _pre_resolved_groups(cms_model, ligand_atoms)

        validate_maestro_partition(ligand_atoms, groups)

        stage = "analysis_cms"
        write_analysis_copy = mode == "single_unk"
        selectors = None
        if mode == "pre_resolved":
            selectors = _source_selectors(cms_model, groups)
            write_analysis_copy = not _selectors_are_exact(
                cms_model, groups, selectors
            )
        if write_analysis_copy:
            if analysis_path.resolve() == source_path:
                raise PreparationError("analysis CMS path would overwrite source CMS")
            selectors, analysis_ligand_asl = _write_analysis_cms(
                cms_model, groups, analysis_path
            )
            analysis_cms = analysis_path
        else:
            for group, selector in zip(groups, selectors):
                group["selector"] = selector
            analysis_ligand_asl = _selector_union_asl(groups)
            analysis_cms = source_path

        if write_analysis_copy:
            for group, selector in zip(groups, selectors):
                group["selector"] = selector

        stage = "output_write"
        residue_map = {
            "schema_version": 1,
            "mode": mode,
            "source_cms": str(source_path),
            "analysis_cms": str(analysis_cms),
            "source_ligand_asl": ligand_asl,
            "analysis_ligand_asl": analysis_ligand_asl,
            "heavy_atom_count": len(heavy_atoms),
            "hydrogen_atom_count": len(hydrogen_atoms),
            "ligand_atom_count": len(ligand_atoms),
            "groups": groups,
            "warnings": list(
                adapter_payload.get("warnings", []) if adapter_payload else []
            ),
            "topology": adapter_payload.get("topology") if adapter_payload else None,
            "mapper_version": (
                adapter_payload.get("mapper_version") if adapter_payload else None
            ),
            "coverage": {
                "assigned_atom_count": len(ligand_atoms),
                "unassigned_atom_indices": [],
                "duplicate_atom_indices": [],
                "fraction": 1.0,
            },
        }
        atomic_write_json(residue_map_path, residue_map)
        result = {
            "mode": mode,
            "source_cms": str(source_path),
            "analysis_cms": str(analysis_cms),
            "analysis_ligand_asl": analysis_ligand_asl,
            "residue_map": str(residue_map_path),
            "atom_index_map": str(atom_index_map_path),
            "ligand_graph": str(ligand_graph_path),
            "manifest": str(manifest_path),
        }
        atomic_write_json(prepare_result_path, result)
        paths["analysis_cms"] = str(analysis_cms)
        update_manifest(
            manifest_path,
            "running",
            mode=mode,
            paths=paths,
            preparation={"status": "success"},
            coverage=residue_map["coverage"],
            warnings=residue_map["warnings"],
            versions={"synergy_mapper": residue_map["mapper_version"]},
        )
        return result
    except Exception as exc:
        message = str(exc)
        try:
            _write_log(log_path, "{}: {}".format(stage, message))
        except OSError:
            pass
        transition_issue = None
        if manifest_created:
            try:
                _mark_manifest_failed_if_running(
                    manifest_path,
                    stage,
                    2,
                    log_path,
                    locals().get("mode"),
                )
            except Exception as failure_exc:
                transition_issue = str(failure_exc)
                try:
                    _write_log(
                        log_path,
                        "manifest failure transition issue: {}".format(
                            transition_issue
                        ),
                    )
                except OSError:
                    pass
        if isinstance(exc, PreparationError) and transition_issue is None:
            raise
        if transition_issue is not None:
            message = "{}; manifest failure transition issue: {}".format(
                message, transition_issue
            )
        raise PreparationError(message) from exc


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cms", help="read-only source Desmond CMS")
    parser.add_argument("--lig-asl", required=True, help="ASL selecting one ligand molecule")
    parser.add_argument("--out-dir", required=True, help="output directory")
    parser.add_argument("--synergy-dir", help="read-only Synergy-Fragment directory")
    parser.add_argument("--adapter-python", help="plain Python used for the Lazy adapter")
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        prepare_ligand_decomp(
            args.cms,
            args.lig_asl,
            args.out_dir,
            synergy_dir=args.synergy_dir,
            adapter_python=args.adapter_python,
        )
    except PreparationError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

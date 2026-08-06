#!/usr/bin/env python3
# @name: prepare_ligand_decomp
# @description: Build atom-complete ligand residue groups and a non-destructive analysis CMS
# @requires: schrodinger
# @usage: prepare_ligand_decomp.py CMS --lig-asl ASL --out-dir DIR [--synergy-dir DIR] [--adapter-python PYTHON]

"""Prepare ligand residue-decomposition inputs from a Desmond CMS file."""

from __future__ import absolute_import

import argparse
import json
import os
import subprocess
import sys
import tempfile
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


class PreparationError(RuntimeError):
    """Raised when ligand preparation cannot satisfy the mapping contract."""


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
    offset = 0
    for component in cms_model.comp_ct:
        upper = offset + component.atom_total
        if offset < full_system_index <= upper:
            return component.atom[full_system_index - offset]
        offset = upper
    raise PreparationError(
        "cannot locate full-system atom {} in component CTs".format(
            full_system_index
        )
    )


def _immutable_cms_signature(cms_model):
    atoms = tuple(
        (
            atom.element,
            int(atom.formal_charge),
            tuple(atom.xyz),
        )
        for atom in cms_model.atom
    )
    bonds = tuple(sorted(
        (
            min(bond.atom1.index, bond.atom2.index),
            max(bond.atom1.index, bond.atom2.index),
            round(float(bond.order), 6),
        )
        for bond in cms_model.fsys_ct.bond
    ))
    return cms_model.atom_total, atoms, bonds


def _write_analysis_cms(cms_model, groups, analysis_path):
    from schrodinger.application.desmond.packages import topo

    immutable_before = _immutable_cms_signature(cms_model)
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


def _resolve_synergy_dir(synergy_dir):
    value = synergy_dir or os.environ.get("SYNERGY_FRAGMENT_DIR")
    if not value:
        raise PreparationError(
            "single-UNK preparation requires --synergy-dir or "
            "SYNERGY_FRAGMENT_DIR"
        )
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise PreparationError("invalid read-only Synergy directory: {}".format(exc))
    if not path.is_dir():
        raise PreparationError("Synergy path is not a directory: {}".format(path))
    return path


def _resolve_adapter_python(adapter_python):
    return (
        adapter_python
        or os.environ.get("SYNERGY_ADAPTER_PYTHON")
        or "python3"
    )


def _plain_python_environment():
    """Remove Schrödinger interpreter injection from an adapter subprocess."""
    environment = dict(os.environ)
    for key in (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
    ):
        environment.pop(key, None)
    schrodinger_root = environment.get("SCHRODINGER")
    if schrodinger_root and environment.get("LD_LIBRARY_PATH"):
        root = os.path.realpath(schrodinger_root)
        entries = environment["LD_LIBRARY_PATH"].split(os.pathsep)
        environment["LD_LIBRARY_PATH"] = os.pathsep.join(
            entry
            for entry in entries
            if entry
            and not os.path.realpath(entry).startswith(root + os.sep)
        )
    return environment


def _run_synergy_adapter(
    sdf_path, output_dir, synergy_dir, adapter_python, log_path
):
    if not Path(ADAPTER_SCRIPT).is_file():
        raise PreparationError("Lazy Synergy adapter does not exist: {}".format(
            ADAPTER_SCRIPT
        ))
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".synergy-map.", suffix=".json", dir=str(output_dir)
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)
    command = [
        str(_resolve_adapter_python(adapter_python)),
        str(ADAPTER_SCRIPT),
        "--sdf", str(sdf_path),
        "--output", str(temporary_path),
        "--synergy-dir", str(_resolve_synergy_dir(synergy_dir)),
    ]
    try:
        completed = subprocess.run(
            command,
            env=_plain_python_environment(),
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
    if not isinstance(payload, dict):
        raise PreparationError("Lazy Synergy adapter output is not a JSON object")
    if payload.get("schema_version") != 1 or payload.get("status") != "ok":
        raise PreparationError("Lazy Synergy adapter did not return schema-v1 ok")
    if payload.get("source_atom_count") != heavy_atom_count:
        raise PreparationError(
            "Lazy Synergy adapter atom count does not match exported heavy atoms"
        )
    if payload.get("unassigned_atom_indices") or payload.get(
        "duplicate_atom_indices"
    ):
        raise PreparationError("Lazy Synergy adapter returned an invalid partition")
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise PreparationError("Lazy Synergy adapter returned no groups")
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
                synergy_dir,
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
    except (ContractError, OSError, ValueError, TypeError, PreparationError) as exc:
        message = str(exc)
        try:
            _write_log(log_path, "{}: {}".format(stage, message))
        except OSError:
            pass
        if manifest_created:
            try:
                update_manifest(
                    manifest_path,
                    "failed",
                    stage=stage,
                    return_code=2,
                    log=str(log_path),
                    mode=locals().get("mode"),
                )
            except (ContractError, OSError, ValueError):
                pass
        if isinstance(exc, PreparationError):
            raise
        raise PreparationError(message)


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

#!/usr/bin/env python3
"""Map one heavy-atom peptide SDF to Lazy's schema-v1 residue groups.

This adapter is deliberately thin: Synergy-Fragment remains the authority for
backbone enumeration, component analysis, ordering, and monomer recognition.
"""

import argparse
import csv
import importlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path

from rdkit import Chem


MAPPER_VERSION = "lazy-synergy-adapter/1"
STANDARD_RESNAMES = {
    "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS",
    "Q": "GLN", "E": "GLU", "G": "GLY", "H": "HIS", "I": "ILE",
    "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO",
    "S": "SER", "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL",
}


class AdapterError(Exception):
    """An input or mapping error that must exit with argparse-compatible code 2."""


def _load_synergy(synergy_dir):
    """Import the read-only Synergy API without permanently changing sys.path."""
    fragment_dir = Path(synergy_dir).expanduser().resolve(strict=True)
    peptide_sequence_path = fragment_dir / "peptide_sequence.py"
    default_library = fragment_dir / "monomer_library_nonstandard_segments_simple.csv"
    dashboard_dir = fragment_dir.parent / "dashboard"
    if not peptide_sequence_path.is_file():
        raise AdapterError(f"missing Synergy API: {peptide_sequence_path}")
    if not default_library.is_file():
        raise AdapterError(f"missing default monomer library: {default_library}")
    if not dashboard_dir.is_dir():
        raise AdapterError(f"missing sibling dashboard directory: {dashboard_dir}")

    inserted = [str(fragment_dir), str(dashboard_dir)]
    sys.path[:0] = inserted
    previous_bytecode_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module = importlib.import_module("peptide_sequence")
        return (
            fragment_dir,
            default_library,
            module.enumerate_backbones,
            module.order_residues,
            module.analyze_components_detailed,
            module.sequence_peptide,
            module.ResidueIdentifier,
            module.residue_fragment_smiles,
        )
    except ImportError as exc:
        raise AdapterError(f"cannot import Synergy peptide_sequence API: {exc}") from exc
    finally:
        sys.dont_write_bytecode = previous_bytecode_setting
        del sys.path[:len(inserted)]


def _read_one_heavy_atom_molecule(sdf_path):
    path = Path(sdf_path)
    if not path.is_file():
        raise AdapterError(f"SDF does not exist: {path}")
    supplier = Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)
    molecules = list(supplier)
    if len(molecules) != 1:
        raise AdapterError(f"expected exactly one SDF record, found {len(molecules)}")
    mol = molecules[0]
    if mol is None:
        raise AdapterError("SDF record could not be parsed")
    if mol.GetNumHeavyAtoms() == 0:
        raise AdapterError("SDF must contain one heavy-atom molecule")
    return mol


def _group(group_id, group_type, atom_indices, sequence_index, display_name,
           canonical_resname, recognition_status, residue_smiles):
    return {
        "group_id": group_id,
        "group_type": group_type,
        "rdkit_atom_indices": sorted(atom_indices),
        "sequence_index": sequence_index,
        "display_name": display_name,
        "canonical_resname": canonical_resname,
        "recognition_status": recognition_status,
        "residue_smiles": residue_smiles,
        "connected_group_ids": [],
    }


def _fragment_smiles(mol, atom_indices):
    return Chem.MolFragmentToSmiles(
        mol, atomsToUse=sorted(atom_indices), isomericSmiles=True, canonical=True
    )


def _exact_library_identities(library_path, residue_smiles):
    """Return distinct library identities with the exact isomeric fragment key.

    This is a narrow collision guard over read-only CSV rows, not a second
    peptide recognizer. Synergy remains responsible for producing
    ``residue_smiles`` and for all recognition provenance.
    """
    target_mol = Chem.MolFromSmiles(residue_smiles)
    if target_mol is None:
        return frozenset()
    target = Chem.MolToSmiles(target_mol, isomericSmiles=True, canonical=True)
    identities = set()
    with Path(library_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            segment = str(row.get("segment_smiles", "")).strip()
            candidate_mol = Chem.MolFromSmiles(segment) if segment else None
            if candidate_mol is None:
                continue
            candidate = Chem.MolToSmiles(candidate_mol, isomericSmiles=True, canonical=True)
            if candidate == target:
                identities.add((
                    str(row.get("symbol", "")).strip(),
                    str(row.get("name", "")).strip(),
                ))
    return frozenset(identities)


def _residue_identity(entry, provenance, crosslinked, ambiguous):
    """Return a Lazy-facing identity from original-molecule Synergy metadata."""
    name = entry["name"] if entry is not None else ""
    if crosslinked:
        return name, None, "crosslinked"
    if entry is None:
        return "", None, "unknown"
    if ambiguous:
        return name, None, "ambiguous"
    if name in STANDARD_RESNAMES and provenance == "exact_isomeric":
        canonical = STANDARD_RESNAMES[name]
        return canonical, canonical, "identified"
    if provenance != "exact_isomeric":
        return name, None, "stereo_inexact"
    return name, None, "noncanonical"


def _recognize_original_backbones(mol, ordered_backbones, detail, identifier,
                                 sequence_peptide, residue_fragment_smiles):
    """Recognize original ordered fragments and verify Synergy's round trip.

    Atom ownership and names are both derived from ``ordered_backbones`` on
    the input SDF molecule. ``sequence_peptide`` is still reused, but only as
    a positional fragment/status consistency check after its SMILES round trip.
    """
    original_fragments = [
        residue_fragment_smiles(mol, backbone, detail.sidechains.get(index, ()))
        for index, backbone in enumerate(ordered_backbones)
    ]
    sequence = sequence_peptide(Chem.MolToSmiles(mol, isomericSmiles=True), identifier)
    if sequence.status != "ok" or len(sequence.residues) != len(original_fragments):
        raise AdapterError("Synergy sequence recognition did not match the ordered backbone")
    if [residue.residue_smiles for residue in sequence.residues] != original_fragments:
        raise AdapterError("Synergy round-trip residue fragments do not match input backbone order")

    recognized = []
    for index, (fragment, residue) in enumerate(zip(original_fragments, sequence.residues)):
        entry, provenance = identifier.identify_with_provenance(fragment)
        expected_status = "crosslinked" if index in detail.crosslinked else (
            "identified" if entry is not None else "unknown"
        )
        expected_name = "" if entry is None or index in detail.crosslinked else entry["name"]
        if residue.status != expected_status or residue.name != expected_name:
            raise AdapterError("Synergy round-trip recognition does not match input backbone order")
        recognized.append((fragment, entry, provenance))
    return sequence, recognized


def _topology_dict(topology, is_cyclic):
    if topology is None:
        return {"head_to_tail": bool(is_cyclic), "disulfide": False,
                "sidechain_bridge": False, "is_cyclic": bool(is_cyclic)}
    return {
        "head_to_tail": topology.head_to_tail,
        "disulfide": topology.disulfide,
        "sidechain_bridge": topology.sidechain_bridge,
        "is_cyclic": topology.is_cyclic,
    }


def _connect_groups(mol, groups):
    owner_by_atom = {}
    for group in groups:
        for atom_index in group["rdkit_atom_indices"]:
            owner_by_atom[atom_index] = group["group_id"]
    connections = {group["group_id"]: set() for group in groups}
    for bond in mol.GetBonds():
        left = owner_by_atom[bond.GetBeginAtomIdx()]
        right = owner_by_atom[bond.GetEndAtomIdx()]
        if left != right:
            connections[left].add(right)
            connections[right].add(left)
    for group in groups:
        group["connected_group_ids"] = sorted(connections[group["group_id"]])


def _validate_partition(groups, source_atom_count):
    assigned = [atom_index for group in groups for atom_index in group["rdkit_atom_indices"]]
    invalid = sorted({index for index in assigned if not 0 <= index < source_atom_count})
    counts = Counter(assigned)
    duplicates = sorted(index for index, count in counts.items() if count > 1)
    unassigned = sorted(set(range(source_atom_count)) - set(assigned))
    if invalid or duplicates or unassigned:
        problems = []
        if invalid:
            problems.append(f"out-of-range atom indices {invalid}")
        if duplicates:
            problems.append(f"duplicate atom indices {duplicates}")
        if unassigned:
            problems.append(f"unassigned atom indices {unassigned}")
        raise AdapterError("invalid atom partition: " + "; ".join(problems))
    return unassigned, duplicates


def build_mapping(mol, synergy_dir, library_path=None):
    """Build a schema-v1 payload using only read-only Synergy recognition APIs."""
    (
        fragment_dir,
        default_library,
        enumerate_backbones,
        order_residues,
        analyze_components_detailed,
        sequence_peptide,
        ResidueIdentifier,
        residue_fragment_smiles,
    ) = _load_synergy(synergy_dir)
    library = Path(library_path).expanduser().resolve() if library_path else default_library
    if not library.is_file():
        raise AdapterError(f"monomer library does not exist: {library}")

    identifier = ResidueIdentifier.from_csv(str(library))
    backbones = enumerate_backbones(mol)
    if not backbones:
        raise AdapterError("Synergy did not recognize a peptide backbone")
    ordered_backbones, is_cyclic, order_message = order_residues(mol, backbones)
    if order_message != "ok":
        raise AdapterError(f"Synergy residue ordering failed: {order_message}")
    detail = analyze_components_detailed(mol, ordered_backbones)
    if any(component.kind == "detached" for component in detail.components):
        raise AdapterError("detached non-backbone component is not assignable")
    sequence, recognized = _recognize_original_backbones(
        mol, ordered_backbones, detail, identifier, sequence_peptide,
        residue_fragment_smiles,
    )
    if sequence.is_cyclic != is_cyclic:
        raise AdapterError("Synergy round-trip cyclicity does not match input backbone order")

    xlink_components = [
        component for component in detail.components
        if component.kind in {"crosslink", "disulfide"}
    ]
    xlink_atoms = {atom for component in xlink_components for atom in component.atoms}
    sidechain_atoms = {
        index: set(detail.sidechains.get(index, ())) - xlink_atoms
        for index in range(len(ordered_backbones))
    }
    for component in detail.components:
        if component.kind == "free_acid":
            owners = {attachment.residue_index for attachment in component.attachments}
            if len(owners) != 1:
                raise AdapterError("free-acid component does not have one residue owner")
            sidechain_atoms[owners.pop()].update(component.atoms)

    warnings = []
    residues = []
    for sequence_index, (backbone, recognized_residue) in enumerate(zip(ordered_backbones, recognized)):
        residue_smiles, entry, provenance = recognized_residue
        ambiguous = len(_exact_library_identities(library, residue_smiles)) > 1
        display_name, canonical_resname, recognition_status = _residue_identity(
            entry, provenance, sequence_index in detail.crosslinked, ambiguous
        )
        group_id = f"P{sequence_index:03d}"
        if recognition_status != "identified":
            warnings.append(f"{group_id}: {recognition_status}")
        residues.append(_group(
            group_id,
            "residue",
            set(backbone) | sidechain_atoms[sequence_index],
            sequence_index,
            display_name,
            canonical_resname,
            recognition_status,
            residue_smiles,
        ))

    n_cap_atoms = set().union(*detail.n_cap_atoms.values()) if detail.n_cap_atoms else set()
    c_cap_atoms = set().union(*detail.c_cap_atoms.values()) if detail.c_cap_atoms else set()
    n_cap = None
    if n_cap_atoms:
        cap_name = sequence.n_cap.name if sequence.n_cap is not None else ""
        cap_smiles = sequence.n_cap.smiles if sequence.n_cap is not None else _fragment_smiles(mol, n_cap_atoms)
        cap_status = "identified" if cap_name else "unknown"
        if cap_status == "unknown":
            warnings.append("N_CAP: unknown")
        n_cap = _group("N_CAP", "n_cap", n_cap_atoms, None, cap_name or "N_CAP", None,
                       cap_status, cap_smiles)
    c_cap = None
    if c_cap_atoms:
        cap_name = sequence.c_cap.name if sequence.c_cap is not None else ""
        cap_smiles = sequence.c_cap.smiles if sequence.c_cap is not None else _fragment_smiles(mol, c_cap_atoms)
        cap_status = "identified" if cap_name else "unknown"
        if cap_status == "unknown":
            warnings.append("C_CAP: unknown")
        c_cap = _group("C_CAP", "c_cap", c_cap_atoms, None, cap_name or "C_CAP", None,
                       cap_status, cap_smiles)

    xlinks = []
    for index, component in enumerate(xlink_components):
        group_id = f"XLINK_{index:03d}"
        warnings.append(f"{group_id}: crosslinked")
        xlinks.append(_group(
            group_id,
            "crosslink",
            component.atoms,
            None,
            "disulfide" if component.kind == "disulfide" else "crosslink",
            None,
            "crosslinked",
            _fragment_smiles(mol, component.atoms),
        ))

    groups = ([n_cap] if n_cap is not None else []) + residues + ([c_cap] if c_cap is not None else []) + xlinks
    unassigned, duplicates = _validate_partition(groups, mol.GetNumAtoms())
    _connect_groups(mol, groups)
    return {
        "schema_version": 1,
        "status": "ok",
        "source_atom_count": mol.GetNumAtoms(),
        "groups": groups,
        "warnings": warnings,
        "unassigned_atom_indices": unassigned,
        "duplicate_atom_indices": duplicates,
        "topology": _topology_dict(sequence.topology, is_cyclic),
        "mapper_version": MAPPER_VERSION,
    }


def _atomic_write_json(output_path, payload):
    output = Path(output_path)
    if not output.parent.is_dir():
        raise AdapterError(f"output directory does not exist: {output.parent}")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    file_descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent, text=True
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, output)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdf", required=True, help="input SDF containing exactly one heavy-atom molecule")
    parser.add_argument("--output", required=True, help="schema-v1 JSON output path")
    parser.add_argument("--synergy-dir", required=True, help="read-only Synergy-Fragment directory")
    parser.add_argument("--library", help="optional read-only Synergy monomer-library CSV")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    try:
        mol = _read_one_heavy_atom_molecule(args.sdf)
        payload = build_mapping(mol, args.synergy_dir, args.library)
        _atomic_write_json(args.output, payload)
    except (AdapterError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import os
import re
import subprocess
import sys
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "skills/science/md-pipeline/scripts/synergy_residue_adapter.py"
SYNERGY_DIR = Path(
    os.environ.get(
        "SYNERGY_FRAGMENT_DIR",
        "/home/huangshengjie/workstations/Synergy/Synergy-Fragment",
    )
)


def _adapter_module():
    spec = importlib.util.spec_from_file_location("synergy_residue_adapter_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_sdf(path, smiles, add_disulfide=False):
    mol = Chem.MolFromSmiles(smiles)
    assert mol is not None
    if add_disulfide:
        sulfur_indices = [atom.GetIdx() for atom in mol.GetAtoms() if atom.GetSymbol() == "S"]
        assert len(sulfur_indices) == 2
        editable = Chem.RWMol(mol)
        editable.AddBond(*sulfur_indices, Chem.BondType.SINGLE)
        mol = editable.GetMol()
        Chem.SanitizeMol(mol)
    writer = Chem.SDWriter(str(path))
    writer.write(mol)
    writer.close()
    return mol


def _run_adapter(sdf_path, output_path, library_path=None):
    command = [
        sys.executable,
        str(SCRIPT),
        "--sdf",
        str(sdf_path),
        "--output",
        str(output_path),
        "--synergy-dir",
        str(SYNERGY_DIR),
    ]
    if library_path is not None:
        command.extend(["--library", str(library_path)])
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )


def _map_peptide(tmp_path, smiles, *, add_disulfide=False, name="ligand", library_path=None):
    sdf_path = tmp_path / f"{name}.sdf"
    source_mol = _write_sdf(sdf_path, smiles, add_disulfide=add_disulfide)
    output_path = tmp_path / f"{name}.json"
    completed = _run_adapter(sdf_path, output_path, library_path)
    assert completed.returncode == 0, completed.stderr
    return source_mol, json.loads(output_path.read_text(encoding="utf-8")), output_path


def _groups_by_id(payload):
    return {group["group_id"]: group for group in payload["groups"]}


def test_synergy_import_does_not_create_bytecode_without_caller_environment(tmp_path):
    """Dropping adapter-owned bytecode protection must fail by creating fake Synergy cache."""
    fragment_dir = tmp_path / "Synergy-Fragment"
    dashboard_dir = tmp_path / "dashboard"
    fragment_dir.mkdir()
    dashboard_dir.mkdir()
    (fragment_dir / "monomer_library_nonstandard_segments_simple.csv").write_text(
        "symbol,name,segment_smiles\n", encoding="utf-8"
    )
    (fragment_dir / "peptide_sequence.py").write_text(
        "class ResidueIdentifier: pass\n"
        "def enumerate_backbones(*args): pass\n"
        "def order_residues(*args): pass\n"
        "def analyze_components_detailed(*args): pass\n"
        "def sequence_peptide(*args): pass\n"
        "def residue_fragment_smiles(*args): pass\n",
        encoding="utf-8",
    )
    adapter = _adapter_module()
    previous_module = sys.modules.pop("peptide_sequence", None)
    previous_setting = sys.dont_write_bytecode
    sys.dont_write_bytecode = False
    try:
        adapter._load_synergy(fragment_dir)
        assert sys.dont_write_bytecode is False
        assert not (fragment_dir / "__pycache__").exists()
    finally:
        sys.dont_write_bytecode = previous_setting
        sys.modules.pop("peptide_sequence", None)
        if previous_module is not None:
            sys.modules["peptide_sequence"] = previous_module


def test_standard_l_residues_are_named_and_exactly_partitioned(tmp_path):
    """Removing canonical L-residue mapping or an atom from a group must fail."""
    source_mol, payload, _ = _map_peptide(
        tmp_path,
        "N[C@@H](C)C(=O)N[C@@H](CC(C)C)C(=O)O",
    )

    groups = _groups_by_id(payload)
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
    assert payload["mapper_version"] == "lazy-synergy-adapter/1"
    assert payload["source_atom_count"] == source_mol.GetNumAtoms()
    assert [group["group_id"] for group in payload["groups"]] == ["P000", "P001"]
    assert [groups[group_id]["canonical_resname"] for group_id in ("P000", "P001")] == [
        "ALA",
        "LEU",
    ]
    assert [groups[group_id]["recognition_status"] for group_id in ("P000", "P001")] == [
        "identified",
        "identified",
    ]
    assert groups["P000"]["connected_group_ids"] == ["P001"]
    assert groups["P001"]["connected_group_ids"] == ["P000"]
    assert payload["unassigned_atom_indices"] == []
    assert payload["duplicate_atom_indices"] == []
    assert sorted(index for group in payload["groups"] for index in group["rdkit_atom_indices"]) == list(
        range(source_mol.GetNumAtoms())
    )


def test_d_residue_is_not_l_canonical_and_warns(tmp_path):
    """Treating a D residue as canonical L-ALA must fail."""
    _, payload, _ = _map_peptide(tmp_path, "N[C@H](C)C(=O)O")

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] is None
    assert group["recognition_status"] == "noncanonical"
    assert any("P000" in warning and "noncanonical" in warning for warning in payload["warnings"])


def test_normalized_stereo_match_is_not_canonical(tmp_path):
    """Treating a normalized C=S lookup as exact canonical L-ALA must fail."""
    _, payload, _ = _map_peptide(tmp_path, "N[C@@H](C)C(=S)O")

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] is None
    assert group["recognition_status"] == "stereo_inexact"
    assert any("P000" in warning and "stereo_inexact" in warning for warning in payload["warnings"])


def test_gly_without_stereo_annotation_is_canonical(tmp_path):
    """Applying chiral-stereo rejection to achiral Gly must fail."""
    _, payload, _ = _map_peptide(tmp_path, "NCC(=O)O")

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] == "GLY"
    assert group["recognition_status"] == "identified"


def test_n_methyl_residue_is_not_canonical_and_warns(tmp_path):
    """Canonicalizing N-methyl-Ala as L-ALA must fail."""
    _, payload, _ = _map_peptide(tmp_path, "CN[C@@H](C)C(=O)O")

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] is None
    assert group["recognition_status"] == "noncanonical"
    assert any("P000" in warning and "noncanonical" in warning for warning in payload["warnings"])


def test_conflicting_exact_library_candidates_are_ambiguous(tmp_path):
    """Choosing the first conflicting exact library row as canonical must fail."""
    library_path = tmp_path / "ambiguous.csv"
    library_path.write_text(
        "symbol,name,segment_smiles\n"
        "M0001,A,C[C@H](N)C=O\n"
        "M0002,not-A,C[C@H](N)C=O\n",
        encoding="utf-8",
    )

    _, payload, _ = _map_peptide(
        tmp_path,
        "N[C@@H](C)C(=O)O",
        library_path=library_path,
    )

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] is None
    assert group["recognition_status"] == "ambiguous"
    assert any("P000" in warning and "ambiguous" in warning for warning in payload["warnings"])


def test_unknown_residue_stays_successful_with_warning(tmp_path):
    """Turning an unrecognized residue into an error or a canonical residue must fail."""
    _, payload, _ = _map_peptide(tmp_path, "N[C@@H](CCCl)C(=O)O")

    group = _groups_by_id(payload)["P000"]
    assert group["canonical_resname"] is None
    assert group["recognition_status"] == "unknown"
    assert any("P000" in warning and "unknown" in warning for warning in payload["warnings"])


def test_ac_and_nh2_caps_are_separate_connected_groups(tmp_path):
    """Folding a cap into its residue group must fail."""
    _, payload, _ = _map_peptide(tmp_path, "CC(=O)N[C@@H](C)C(=O)N")

    groups = _groups_by_id(payload)
    assert [group["group_id"] for group in payload["groups"]] == ["N_CAP", "P000", "C_CAP"]
    assert groups["N_CAP"]["group_type"] == "n_cap"
    assert groups["N_CAP"]["display_name"] == "Ac"
    assert groups["C_CAP"]["group_type"] == "c_cap"
    assert groups["C_CAP"]["display_name"] == "NH2"
    assert groups["P000"]["connected_group_ids"] == ["C_CAP", "N_CAP"]
    assert groups["N_CAP"]["connected_group_ids"] == ["P000"]
    assert groups["C_CAP"]["connected_group_ids"] == ["P000"]


def test_crosslink_component_is_its_own_group(tmp_path):
    """Leaving crosslink sulfur atoms in either residue group must fail."""
    source_mol, payload, _ = _map_peptide(
        tmp_path,
        "N[C@@H](CS)C(=O)N[C@@H](CS)C(=O)O",
        add_disulfide=True,
    )

    groups = _groups_by_id(payload)
    xlink = groups["XLINK_000"]
    sulfur_indices = {atom.GetIdx() for atom in source_mol.GetAtoms() if atom.GetSymbol() == "S"}
    assert xlink["group_type"] == "crosslink"
    assert xlink["recognition_status"] == "crosslinked"
    assert sulfur_indices <= set(xlink["rdkit_atom_indices"])
    assert sulfur_indices.isdisjoint(groups["P000"]["rdkit_atom_indices"])
    assert sulfur_indices.isdisjoint(groups["P001"]["rdkit_atom_indices"])
    assert set(xlink["connected_group_ids"]) == {"P000", "P001"}
    assert any("XLINK_000" in warning and "crosslinked" in warning for warning in payload["warnings"])


def _roundtrip_recognition_inputs(roundtrip_fragments):
    molecule = Chem.MolFromSmiles("CCC")
    ordered_backbones = [(0,), (1,), (2,)]
    detail = SimpleNamespace(sidechains={}, crosslinked={0, 2})
    residues = [
        SimpleNamespace(
            residue_smiles=fragment,
            status="crosslinked" if index in detail.crosslinked else "unknown",
            name="",
        )
        for index, fragment in enumerate(roundtrip_fragments)
    ]
    sequence = SimpleNamespace(status="ok", residues=residues)

    class Identifier:
        @staticmethod
        def identify_with_provenance(fragment):
            return None, None

    def sequence_peptide(smiles, identifier):
        return sequence

    def residue_fragment_smiles(mol, backbone, sidechain):
        return ("crosslink-a", "fixed", "crosslink-b")[backbone[0]]

    return (
        molecule,
        ordered_backbones,
        detail,
        Identifier(),
        sequence_peptide,
        residue_fragment_smiles,
    )


def test_crosslinked_roundtrip_accepts_bridge_fragment_reassignment():
    adapter = _adapter_module()

    sequence, recognized = adapter._recognize_original_backbones(
        *_roundtrip_recognition_inputs(
            ("crosslink-expanded", "fixed", "crosslink-trimmed")
        )
    )

    assert sequence.status == "ok"
    assert [item[0] for item in recognized] == [
        "crosslink-a",
        "fixed",
        "crosslink-b",
    ]


def test_roundtrip_rejects_noncrosslinked_positional_fragment_drift():
    adapter = _adapter_module()

    with pytest.raises(adapter.AdapterError, match="residue fragments"):
        adapter._recognize_original_backbones(
            *_roundtrip_recognition_inputs(
                ("crosslink-expanded", "changed", "crosslink-trimmed")
            )
        )


def test_free_acid_oxygen_belongs_to_c_terminal_residue(tmp_path):
    """Dropping or cap-classifying the free-acid oxygen must fail."""
    source_mol, payload, _ = _map_peptide(tmp_path, "N[C@@H](C)C(=O)O")

    groups = _groups_by_id(payload)
    oxygen_indices = {atom.GetIdx() for atom in source_mol.GetAtoms() if atom.GetSymbol() == "O"}
    assert "C_CAP" not in groups
    assert oxygen_indices <= set(groups["P000"]["rdkit_atom_indices"])
    assert payload["unassigned_atom_indices"] == []
    assert payload["duplicate_atom_indices"] == []


def test_detached_component_exits_2_without_success_json(tmp_path):
    """Accepting a detached non-backbone component must fail."""
    sdf_path = tmp_path / "detached.sdf"
    _write_sdf(sdf_path, "N[C@@H](C)C(=O)O.CC")
    output_path = tmp_path / "result.json"

    completed = _run_adapter(sdf_path, output_path)

    assert completed.returncode == 2
    assert not output_path.exists()


def test_unconnected_backbones_exit_2_without_success_json(tmp_path):
    """Returning success for a non-ok Synergy residue-order graph must fail."""
    sdf_path = tmp_path / "two_peptides.sdf"
    _write_sdf(sdf_path, "N[C@@H](C)C(=O)O.N[C@@H](C)C(=O)O")
    output_path = tmp_path / "result.json"

    completed = _run_adapter(sdf_path, output_path)

    assert completed.returncode == 2
    assert not output_path.exists()


@pytest.mark.parametrize(
    ("groups", "source_atom_count", "expected"),
    [
        ([{"rdkit_atom_indices": [0]}], 2, "unassigned atom indices [1]"),
        ([{"rdkit_atom_indices": [0, 0]}], 1, "duplicate atom indices [0]"),
    ],
)
def test_partition_validator_rejects_missing_and_duplicate_atoms(groups, source_atom_count, expected):
    """Removing strict partition validation must fail."""
    adapter = _adapter_module()

    with pytest.raises(adapter.AdapterError, match=re.escape(expected)):
        adapter._validate_partition(groups, source_atom_count)


@pytest.mark.parametrize("contents", ["", "two_records"])
def test_empty_or_multiple_sdf_exits_2_without_success_json(tmp_path, contents):
    """Accepting zero or multiple SDF records must fail."""
    sdf_path = tmp_path / f"{contents or 'empty'}.sdf"
    if contents == "two_records":
        first = _write_sdf(tmp_path / "first.sdf", "N[C@@H](C)C(=O)O")
        second = _write_sdf(tmp_path / "second.sdf", "NCC(=O)O")
        with Chem.SDWriter(str(sdf_path)) as writer:
            writer.write(first)
            writer.write(second)
    else:
        sdf_path.write_text("", encoding="utf-8")
    output_path = tmp_path / "result.json"

    completed = _run_adapter(sdf_path, output_path)

    assert completed.returncode == 2
    assert not output_path.exists()


def test_output_is_deterministic_and_indented(tmp_path):
    """Changing stable serialization or group ordering must fail."""
    sdf_path = tmp_path / "ligand.sdf"
    _write_sdf(sdf_path, "CC(=O)N[C@@H](C)C(=O)N")
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"

    first = _run_adapter(sdf_path, first_output)
    second = _run_adapter(sdf_path, second_output)

    assert first.returncode == second.returncode == 0
    assert first_output.read_bytes() == second_output.read_bytes()
    assert b'\n  "groups": [' in first_output.read_bytes()

import base64
import gzip
import hashlib
import json
from pathlib import Path
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from mmgbsa_decomp_contract import ContractError, validate_maestro_partition
import prepare_ligand_decomp as preparation
from prepare_ligand_decomp import (
    PreparationError,
    assign_hydrogens,
    detect_mode_from_residues,
    remap_rdkit_groups,
)


TWO_WATERS_CMS_GZ_B64 = """
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
"""


def _write_two_water_cms(path):
    encoded = "".join(TWO_WATERS_CMS_GZ_B64.split())
    path.write_bytes(gzip.decompress(base64.b64decode(encoded)))
    return path


def _relabel_first_molecule(path, pdbres):
    from schrodinger.application.desmond.packages import topo

    _, model = topo.read_cms(str(path))
    for atom_index in (1, 2, 3):
        atom = model.comp_ct[0].atom[atom_index]
        atom.pdbres = pdbres
        atom.resnum = 7
    model.synchronize_fsys_ct()
    model.write(str(path))


def _immutable_cms_signature(path):
    from schrodinger.application.desmond.packages import topo

    _, model = topo.read_cms(str(path))
    atoms = tuple(
        (
            atom.element,
            atom.formal_charge,
            tuple(atom.xyz),
        )
        for atom in model.atom
    )
    bonds = tuple(
        sorted(
            (
                min(bond.atom1.index, bond.atom2.index),
                max(bond.atom1.index, bond.atom2.index),
                float(bond.order),
            )
            for bond in model.bond
        )
    )
    return model.atom_total, atoms, bonds


def _write_fake_adapter(path, exit_code=0):
    if exit_code:
        body = "import sys\nraise SystemExit({})\n".format(exit_code)
    else:
        body = """\
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--sdf', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--synergy-dir', required=True)
args = parser.parse_args()
if not Path(args.synergy_dir).is_dir():
    raise SystemExit(9)
payload = {
    'schema_version': 1,
    'status': 'ok',
    'source_atom_count': 1,
    'groups': [{
        'group_id': 'P000',
        'group_type': 'residue',
        'rdkit_atom_indices': [0],
        'sequence_index': 0,
        'display_name': 'ALA',
        'canonical_resname': 'ALA',
        'recognition_status': 'identified',
        'residue_smiles': '[OH2]',
        'connected_group_ids': [],
    }],
    'warnings': [],
    'unassigned_atom_indices': [],
    'duplicate_atom_indices': [],
    'topology': {'is_cyclic': False},
    'mapper_version': 'fake-adapter/1',
}
Path(args.output).write_text(json.dumps(payload), encoding='utf-8')
"""
    path.write_text(body, encoding="utf-8")
    return path


def test_detect_mode_only_treats_one_unk_residue_as_single_unk():
    assert detect_mode_from_residues([(" UNK ", 7, " ")]) == "single_unk"
    assert detect_mode_from_residues([("ALA", 7, "L")]) == "pre_resolved"
    assert detect_mode_from_residues(
        [("UNK", 7, "L"), ("PHE", 8, "L")]
    ) == "pre_resolved"


def test_remap_rdkit_groups_converts_zero_based_indices_to_maestro_ids():
    source = [{"group_id": "P000", "rdkit_atom_indices": [2, 0]}]

    mapped = remap_rdkit_groups(source, {0: 101, 1: 102, 2: 103})

    assert mapped[0]["maestro_atom_indices"] == [101, 103]
    assert "maestro_atom_indices" not in source[0]


def test_assign_hydrogens_uses_the_sole_bonded_heavy_atom_owner():
    groups = [
        {"group_id": "P000", "maestro_atom_indices": [10]},
        {"group_id": "P001", "maestro_atom_indices": [20]},
    ]

    assigned = assign_hydrogens(
        groups,
        hydrogen_indices={11, 12, 21},
        neighbors={11: [10], 12: [10], 21: [20]},
    )

    assert assigned[0]["maestro_atom_indices"] == [10, 11, 12]
    assert assigned[1]["maestro_atom_indices"] == [20, 21]


@pytest.mark.parametrize(
    "neighbors",
    [
        {11: []},
        {11: [10, 20]},
    ],
)
def test_assign_hydrogens_rejects_non_unique_heavy_ownership(neighbors):
    groups = [
        {"group_id": "P000", "maestro_atom_indices": [10]},
        {"group_id": "P001", "maestro_atom_indices": [20]},
    ]

    with pytest.raises(PreparationError, match="hydrogen 11"):
        assign_hydrogens(groups, hydrogen_indices={11}, neighbors=neighbors)


def test_final_groups_must_be_an_exact_ligand_partition():
    valid = [
        {"group_id": "P000", "maestro_atom_indices": [10, 11]},
        {"group_id": "P001", "maestro_atom_indices": [20, 21]},
    ]
    validate_maestro_partition({10, 11, 20, 21}, valid)

    invalid = [
        {"group_id": "P000", "maestro_atom_indices": [10, 11]},
        {"group_id": "P001", "maestro_atom_indices": [11, 20]},
    ]
    with pytest.raises(ContractError, match="duplicate=.*missing="):
        validate_maestro_partition({10, 11, 20, 21}, invalid)


def test_sdf_round_trip_validation_checks_ordered_chemistry_and_bonds():
    from rdkit import Chem

    molecule = Chem.MolFromSmiles("[NH3+]C")
    preparation.validate_sdf_round_trip(
        molecule,
        expected_elements=("N", "C"),
        expected_charges=(1, 0),
        expected_bonds=((0, 1, 1.0),),
    )

    with pytest.raises(PreparationError, match="ordered elements"):
        preparation.validate_sdf_round_trip(
            molecule,
            expected_elements=("C", "N"),
            expected_charges=(1, 0),
            expected_bonds=((0, 1, 1.0),),
        )
    with pytest.raises(PreparationError, match="formal charges"):
        preparation.validate_sdf_round_trip(
            molecule,
            expected_elements=("N", "C"),
            expected_charges=(0, 0),
            expected_bonds=((0, 1, 1.0),),
        )
    with pytest.raises(PreparationError, match="normalized bonds"):
        preparation.validate_sdf_round_trip(
            molecule,
            expected_elements=("N", "C"),
            expected_charges=(1, 0),
            expected_bonds=((0, 1, 2.0),),
        )


def test_real_cms_io_rejects_partial_molecule_and_marks_manifest_failed(tmp_path):
    source = _write_two_water_cms(tmp_path / "source.cms")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    output_dir = tmp_path / "partial-output"

    with pytest.raises(PreparationError, match="one complete molecule"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-2",
            output_dir=output_dir,
        )

    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "ligand_selection"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest


def test_pre_resolved_selector_collision_writes_metadata_only_analysis_cms(
    tmp_path,
):
    from schrodinger.application.desmond.packages import topo

    source = _write_two_water_cms(tmp_path / "source.cms")
    source.chmod(0o444)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_signature = _immutable_cms_signature(source)
    output_dir = tmp_path / "pre-output"

    result = preparation.prepare_ligand_decomp(
        source,
        ligand_asl="atom.num 1-3",
        output_dir=output_dir,
        adapter_python="/definitely/not/invoked",
    )

    assert result["mode"] == "pre_resolved"
    assert Path(result["analysis_cms"]) != source
    assert _immutable_cms_signature(result["analysis_cms"]) == source_signature
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest

    residue_map = json.loads(
        (output_dir / "residue_map.json").read_text(encoding="utf-8")
    )
    assert residue_map["mapper_version"] is None
    assert residue_map["heavy_atom_count"] == 1
    assert residue_map["hydrogen_atom_count"] == 2
    assert residue_map["groups"] == [
        {
            "group_id": "P000",
            "group_type": "residue",
            "group_name": "SPC",
            "maestro_atom_indices": [1, 2, 3],
            "selector": {
                "chain": "L",
                "resnum": 1,
                "inscode": "",
                "pdbres": "SPC",
            },
        }
    ]
    assert json.loads(
        (output_dir / "atom_index_map.json").read_text(encoding="utf-8")
    ) == {"0": 1}

    _, analysis = topo.read_cms(result["analysis_cms"])
    assert analysis.select_atom(result["analysis_ligand_asl"]) == [1, 2, 3]
    assert analysis.atom[1].chain == "L"
    assert analysis.atom[1].resnum == 1
    assert analysis.atom[1].pdbres.strip() == "SPC"
    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "running"
    assert manifest["mode"] == "pre_resolved"


def test_single_unk_invokes_plain_python_adapter_and_maps_all_atoms(
    tmp_path, monkeypatch
):
    from schrodinger.application.desmond.packages import topo

    source = _write_two_water_cms(tmp_path / "source.cms")
    _relabel_first_molecule(source, "UNK")
    source.chmod(0o444)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_signature = _immutable_cms_signature(source)
    fake_adapter = _write_fake_adapter(tmp_path / "fake_adapter.py")
    synergy_dir = tmp_path / "synergy-read-only"
    synergy_dir.mkdir()
    synergy_dir.chmod(0o555)
    monkeypatch.setattr(preparation, "ADAPTER_SCRIPT", fake_adapter)
    monkeypatch.setenv("SYNERGY_ADAPTER_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("SYNERGY_FRAGMENT_DIR", str(synergy_dir))
    output_dir = tmp_path / "single-output"

    result = preparation.prepare_ligand_decomp(
        source,
        ligand_asl="atom.num 1-3",
        output_dir=output_dir,
    )

    assert result["mode"] == "single_unk"
    assert _immutable_cms_signature(result["analysis_cms"]) == source_signature
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
    residue_map = json.loads(
        (output_dir / "residue_map.json").read_text(encoding="utf-8")
    )
    assert residue_map["mapper_version"] == "fake-adapter/1"
    assert residue_map["groups"][0]["maestro_atom_indices"] == [1, 2, 3]
    assert residue_map["groups"][0]["selector"] == {
        "chain": "L",
        "resnum": 1,
        "inscode": "",
        "pdbres": "ALA",
    }

    _, analysis = topo.read_cms(result["analysis_cms"])
    assert analysis.select_atom(result["analysis_ligand_asl"]) == [1, 2, 3]
    assert analysis.atom[1].pdbres.strip() == "ALA"


def test_adapter_failure_transitions_running_manifest_to_failed(
    tmp_path, monkeypatch
):
    source = _write_two_water_cms(tmp_path / "source.cms")
    _relabel_first_molecule(source, "UNK")
    fake_adapter = _write_fake_adapter(tmp_path / "failing_adapter.py", exit_code=7)
    synergy_dir = tmp_path / "synergy"
    synergy_dir.mkdir()
    monkeypatch.setattr(preparation, "ADAPTER_SCRIPT", fake_adapter)
    output_dir = tmp_path / "failed-output"

    with pytest.raises(PreparationError, match="adapter failed with exit code 7"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
            synergy_dir=synergy_dir,
            adapter_python="/usr/bin/python3",
        )

    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "single_unk_mapping"

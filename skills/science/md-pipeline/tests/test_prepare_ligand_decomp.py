import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
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


def _write_bonded_ligand_cms(path, pdbres="UNK"):
    from schrodinger.application.desmond.packages import topo

    _write_two_water_cms(path)
    _, model = topo.read_cms(str(path))
    for atom_index in range(1, 7):
        atom = model.comp_ct[0].atom[atom_index]
        atom.pdbres = pdbres
        atom.resnum = 7
    for atom_index in (1, 4):
        model.comp_ct[0].atom[atom_index].formal_charge = 1
    model.comp_ct[0].addBond(1, 4, 1)
    model.synchronize_fsys_ct()
    model.write(str(path))
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


def _ct_chemistry_signature(structure):
    atoms = tuple(
        (atom.element, atom.formal_charge, tuple(atom.xyz))
        for atom in structure.atom
    )
    bonds = tuple(sorted(
        (
            min(bond.atom1.index, bond.atom2.index),
            max(bond.atom1.index, bond.atom2.index),
            float(bond.order),
        )
        for bond in structure.bond
    ))
    return structure.atom_total, atoms, bonds


def _immutable_cms_signature(path):
    from schrodinger.application.desmond.packages import topo

    _, model = topo.read_cms(str(path))
    return (
        _ct_chemistry_signature(model.fsys_ct),
        tuple(_ct_chemistry_signature(component) for component in model.comp_ct),
    )


def _non_target_metadata_signature(path, target_indices):
    from schrodinger.application.desmond.packages import topo

    _, model = topo.read_cms(str(path))
    target = set(target_indices)
    component_map = preparation._full_system_component_map(model)
    component_targets = {
        component_map[full_system_index]
        for full_system_index in target
    }
    full = tuple(
        (atom.index, atom.chain, atom.resnum, atom.inscode, atom.pdbres)
        for atom in model.fsys_ct.atom
        if atom.index not in target
    )
    components = tuple(
        (
            ct_index,
            component_index,
            component.atom[component_index].chain,
            component.atom[component_index].resnum,
            component.atom[component_index].inscode,
            component.atom[component_index].pdbres,
        )
        for ct_index, component in enumerate(model.comp_ct)
        for component_index in range(1, component.atom_total + 1)
        if (ct_index, component_index) not in component_targets
    )
    return full, components


def _write_fake_adapter(
    path, exit_code=0, malformed_group=False, record_python=False
):
    if exit_code:
        body = "import sys\nraise SystemExit({})\n".format(exit_code)
    else:
        body = """\
import argparse
import json
import sys
from pathlib import Path
from rdkit import Chem

parser = argparse.ArgumentParser()
parser.add_argument('--sdf', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--synergy-dir', required=True)
args = parser.parse_args()
if not Path(args.synergy_dir).is_dir():
    raise SystemExit(9)
mol = next(mol for mol in Chem.SDMolSupplier(args.sdf, removeHs=False) if mol)
payload = {
    'schema_version': 1,
    'status': 'ok',
    'source_atom_count': mol.GetNumAtoms(),
    'groups': [{
        'group_id': 'P000',
        'group_type': 'residue',
        'rdkit_atom_indices': list(range(mol.GetNumAtoms())),
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
    'mapper_version': str(Path(sys.executable).resolve()) if RECORD_PYTHON else 'fake-adapter/1',
}
if MALFORMED_GROUP:
    payload['groups'] = [{
        'group_id': 'P000',
        'rdkit_atom_indices': list(range(mol.GetNumAtoms())),
    }]
Path(args.output).write_text(json.dumps(payload), encoding='utf-8')
""".replace("RECORD_PYTHON", repr(record_python)).replace(
            "MALFORMED_GROUP", repr(malformed_group)
        )
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


def _aromatic_export_model(sdf_bond_orders):
    class Atom:
        def __init__(self, index):
            self.index = index
            self.element = "C"
            self.formal_charge = 0

    class Bond:
        def __init__(self, left, right, order):
            self.atom1 = Atom(left)
            self.atom2 = Atom(right)
            self.order = order

    class ExportedStructure:
        def write(self, path, format):
            assert format == "sd"
            atom_lines = "\n".join(
                "{:10.4f}{:10.4f}{:10.4f} C   0  0  0  0  0  0".format(
                    float(index), 0.0, 0.0
                )
                for index in range(6)
            )
            bond_lines = "\n".join(
                "{:3d}{:3d}{:3d}  0  0  0".format(
                    left, right, order
                )
                for (left, right), order in zip(
                    ((1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (1, 6)),
                    sdf_bond_orders,
                )
            )
            Path(path).write_text(
                "benzene\n  Lazy regression\n\n"
                "  6  6  0  0  0  0            999 V2000\n"
                + atom_lines
                + "\n"
                + bond_lines
                + "\nM  END\n$$$$\n",
                encoding="utf-8",
            )

    class Fsys:
        def __init__(self):
            self.bond = [
                Bond(left, right, order)
                for (left, right), order in zip(
                    ((1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (1, 6)),
                    (2, 1, 2, 1, 2, 1),
                )
            ]

        def extract(self, atom_indices):
            assert atom_indices == [1, 2, 3, 4, 5, 6]
            return ExportedStructure()

    class Model:
        atom = {index: Atom(index) for index in range(1, 7)}
        fsys_ct = Fsys()

    return Model()


def test_export_validates_raw_kekule_bonds_before_aromatic_sanitization(
    tmp_path,
):
    molecule = preparation._export_heavy_graph(
        _aromatic_export_model((2, 1, 2, 1, 2, 1)),
        [1, 2, 3, 4, 5, 6],
        tmp_path / "benzene.sdf",
    )

    assert sorted(
        bond.GetBondTypeAsDouble() for bond in molecule.GetBonds()
    ) == [1.5] * 6


def test_export_rejects_raw_kekule_drift_hidden_by_aromatic_sanitization(
    tmp_path,
):
    with pytest.raises(PreparationError, match="normalized bonds"):
        preparation._export_heavy_graph(
            _aromatic_export_model((1, 2, 1, 2, 1, 2)),
            [1, 2, 3, 4, 5, 6],
            tmp_path / "shifted-benzene.sdf",
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
    non_target_metadata = _non_target_metadata_signature(source, {1, 2, 3})
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
    assert _non_target_metadata_signature(
        result["analysis_cms"], {1, 2, 3}
    ) == non_target_metadata
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
    assert analysis.comp_ct[0].atom[1].chain == "L"
    assert analysis.comp_ct[0].atom[1].resnum == 1
    assert analysis.comp_ct[0].atom[1].pdbres.strip() == "SPC"
    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "running"
    assert manifest["mode"] == "pre_resolved"


def test_default_adapter_python_is_outside_schrodinger_and_imports_rdkit(
    tmp_path, monkeypatch
):
    from schrodinger.application.desmond.packages import topo

    source = _write_bonded_ligand_cms(tmp_path / "source.cms")
    source.chmod(0o444)
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_signature = _immutable_cms_signature(source)
    fake_adapter = _write_fake_adapter(
        tmp_path / "fake_adapter.py", record_python=True
    )
    synergy_dir = tmp_path / "synergy-read-only"
    synergy_dir.mkdir()
    synergy_dir.chmod(0o555)
    monkeypatch.setattr(preparation, "ADAPTER_SCRIPT", fake_adapter)
    monkeypatch.delenv("SYNERGY_ADAPTER_PYTHON", raising=False)
    monkeypatch.setenv("SYNERGY_FRAGMENT_DIR", str(synergy_dir))
    output_dir = tmp_path / "single-output"

    result = preparation.prepare_ligand_decomp(
        source,
        ligand_asl="atom.num 1-6",
        output_dir=output_dir,
    )

    assert result["mode"] == "single_unk"
    assert Path(result["analysis_cms"]).name == "analysis-out.cms"
    assert _immutable_cms_signature(result["analysis_cms"]) == source_signature
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest
    residue_map = json.loads(
        (output_dir / "residue_map.json").read_text(encoding="utf-8")
    )
    child_executable = Path(residue_map["mapper_version"]).resolve()
    schrodinger_root = Path(os.environ["SCHRODINGER"]).resolve()
    assert child_executable != schrodinger_root
    assert schrodinger_root not in child_executable.parents
    assert residue_map["groups"][0]["maestro_atom_indices"] == [1, 2, 3, 4, 5, 6]
    assert residue_map["groups"][0]["selector"] == {
        "chain": "L",
        "resnum": 1,
        "inscode": "",
        "pdbres": "ALA",
    }

    _, analysis = topo.read_cms(result["analysis_cms"])
    assert analysis.select_atom(result["analysis_ligand_asl"]) == [1, 2, 3, 4, 5, 6]
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
    monkeypatch.delenv("SYNERGY_ADAPTER_PYTHON", raising=False)
    output_dir = tmp_path / "failed-output"

    with pytest.raises(PreparationError, match="adapter failed with exit code 7"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
            synergy_dir=synergy_dir,
        )

    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "single_unk_mapping"


def test_real_cms_export_persists_two_heavy_atoms_in_fixed_bonded_order(tmp_path):
    from rdkit import Chem

    source = _write_bonded_ligand_cms(tmp_path / "source.cms", pdbres="LIG")
    output_dir = tmp_path / "bonded-output"

    preparation.prepare_ligand_decomp(
        source,
        ligand_asl="atom.num 1-6",
        output_dir=output_dir,
    )

    assert json.loads(
        (output_dir / "atom_index_map.json").read_text(encoding="utf-8")
    ) == {"0": 1, "1": 4}
    records = list(Chem.SDMolSupplier(
        str(output_dir / "ligand_graph.sdf"), removeHs=False, sanitize=True
    ))
    assert len(records) == 1
    assert [atom.GetSymbol() for atom in records[0].GetAtoms()] == ["O", "O"]
    assert [atom.GetFormalCharge() for atom in records[0].GetAtoms()] == [1, 1]
    assert [
        (
            bond.GetBeginAtomIdx(),
            bond.GetEndAtomIdx(),
            bond.GetBondTypeAsDouble(),
        )
        for bond in records[0].GetBonds()
    ] == [(0, 1, 1.0)]


def test_default_child_environment_preserves_unrelated_user_python_config(
    tmp_path, monkeypatch
):
    schrodinger_root = tmp_path / "schrodinger"
    schrodinger_bin = schrodinger_root / "internal" / "bin"
    schrodinger_python = schrodinger_root / "python"
    user_bin = tmp_path / "user-bin"
    user_python = tmp_path / "user-python"
    user_lib = tmp_path / "user-lib"
    for path in (schrodinger_bin, schrodinger_python, user_bin, user_python, user_lib):
        path.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SCHRODINGER", str(schrodinger_root))
    monkeypatch.setenv(
        "PATH", os.pathsep.join((str(schrodinger_bin), str(user_bin)))
    )
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join((str(schrodinger_python), str(user_python)))
    )
    monkeypatch.setenv("LD_LIBRARY_PATH", os.pathsep.join((
        str(schrodinger_root / "internal" / "lib"), str(user_lib)
    )))
    monkeypatch.setenv("PYTHONHOME", str(schrodinger_root / "internal"))
    monkeypatch.setenv("PYTHONSTARTUP", "/user/startup.py")
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("USER_PYTHON_CONFIG", "keep-me")

    child = preparation._plain_python_environment()

    assert child["PATH"] == str(user_bin)
    assert child["PYTHONPATH"] == str(user_python)
    assert child["LD_LIBRARY_PATH"] == str(user_lib)
    assert "PYTHONHOME" not in child
    assert child["PYTHONSTARTUP"] == "/user/startup.py"
    assert child["PYTHONNOUSERSITE"] == "1"
    assert child["USER_PYTHON_CONFIG"] == "keep-me"


def test_adapter_python_precedence_and_schrodinger_rejection(monkeypatch):
    child = preparation._plain_python_environment()
    ordinary = Path(shutil.which("python3", path=child["PATH"])).resolve()
    child["SYNERGY_ADAPTER_PYTHON"] = "/definitely/not/the-explicit-python"

    assert preparation._resolve_adapter_python(
        ordinary, child
    ) == ordinary

    schrodinger_python = (
        Path(os.environ["SCHRODINGER"]) / "internal" / "bin" / "python3"
    )
    with pytest.raises(PreparationError, match="outside SCHRODINGER"):
        preparation._resolve_adapter_python(schrodinger_python, child)


def test_component_lookup_matches_noncontiguous_cms_gid_maps():
    from types import SimpleNamespace

    first = SimpleNamespace(chain="A")
    mapped = SimpleNamespace(chain="B")
    inactive = SimpleNamespace(chain="I")
    components = [
        SimpleNamespace(atom_total=2, atom={1: first, 2: mapped}),
        SimpleNamespace(atom_total=1, atom={1: inactive}),
    ]
    gid_map = [-1, 42, 7]
    cms_model = SimpleNamespace(
        atom_total=2,
        gid_map=gid_map,
        gid=lambda atom_index: gid_map[atom_index],
        comp_ct=components,
        id_maps=[
            SimpleNamespace(start_gid=7, to_gid=[-1, 7, 42]),
            SimpleNamespace(start_gid=99, to_gid=[-1, 99]),
        ],
    )

    assert preparation._full_system_component_map(cms_model) == {
        1: (0, 2),
        2: (0, 1),
    }
    assert preparation._component_atom(cms_model, 1) is mapped
    preparation._component_atom(cms_model, 1).chain = "L"
    assert components[0].atom[2].chain == "L"
    assert components[0].atom[1].chain == "A"
    assert components[1].atom[1].chain == "I"


def test_component_lookup_uses_standard_cms_concatenation_without_gid_maps():
    from types import SimpleNamespace

    components = [
        SimpleNamespace(atom_total=2, atom={1: object(), 2: object()}),
        SimpleNamespace(atom_total=1, atom={1: object()}),
    ]
    cms_model = SimpleNamespace(
        atom_total=3,
        fep_ct=None,
        comp_ct=components,
        id_maps=[
            SimpleNamespace(start_gid=None, to_gid=None),
            SimpleNamespace(start_gid=None, to_gid=None),
        ],
    )

    assert preparation._full_system_component_map(cms_model) == {
        1: (0, 1),
        2: (0, 2),
        3: (1, 1),
    }


def test_component_lookup_rejects_concatenation_with_inactive_atoms():
    from types import SimpleNamespace

    cms_model = SimpleNamespace(
        atom_total=2,
        fep_ct=None,
        comp_ct=[SimpleNamespace(atom_total=3, atom={})],
        id_maps=[SimpleNamespace(start_gid=None, to_gid=None)],
    )

    with pytest.raises(PreparationError, match="neither"):
        preparation._full_system_component_map(cms_model)


@pytest.mark.parametrize(
    ("component_gid_maps", "message"),
    [
        (([-1, 7],), "absent"),
        (([-1, 42], [-1, 42]), "ambiguous"),
    ],
)
def test_component_lookup_rejects_absent_or_ambiguous_gid_mapping(
    component_gid_maps, message
):
    from types import SimpleNamespace

    components = [
        SimpleNamespace(atom_total=1, atom={1: object()})
        for _ in component_gid_maps
    ]
    cms_model = SimpleNamespace(
        atom_total=1,
        gid_map=[-1, 42],
        gid=lambda atom_index: [-1, 42][atom_index],
        comp_ct=components,
        id_maps=[
            SimpleNamespace(start_gid=None, to_gid=gid_map)
            for gid_map in component_gid_maps
        ],
    )

    with pytest.raises(PreparationError, match=message):
        preparation._full_system_component_map(cms_model)


@pytest.mark.parametrize("change", ["coordinate", "charge", "bond"])
def test_cms_signature_detects_component_only_chemistry_change(tmp_path, change):
    from schrodinger.application.desmond.packages import topo

    source = _write_two_water_cms(tmp_path / "source.cms")
    _, original = topo.read_cms(str(source))
    _, component_changed = topo.read_cms(str(source))
    if change == "coordinate":
        component_changed.comp_ct[0].atom[4].x += 2.0
    elif change == "charge":
        component_changed.comp_ct[0].atom[4].formal_charge = 1
    else:
        next(iter(component_changed.comp_ct[0].bond)).order = 2

    assert preparation._immutable_cms_signature(
        component_changed
    ) != preparation._immutable_cms_signature(original)


def test_non_target_metadata_signature_covers_component_view(tmp_path):
    from schrodinger.application.desmond.packages import topo

    source = _write_two_water_cms(tmp_path / "source.cms")
    _, original = topo.read_cms(str(source))
    _, metadata_changed = topo.read_cms(str(source))
    metadata_changed.comp_ct[0].atom[4].chain = "Z"

    assert preparation._non_target_metadata_signature(
        metadata_changed, {1, 2, 3}
    ) != preparation._non_target_metadata_signature(original, {1, 2, 3})


def test_output_inside_synergy_is_rejected_before_any_write(tmp_path):
    source = _write_two_water_cms(tmp_path / "source.cms")
    synergy_dir = tmp_path / "synergy"
    synergy_dir.mkdir()
    marker = synergy_dir / "marker.txt"
    marker.write_text("unchanged\n", encoding="utf-8")
    output_dir = synergy_dir / "results"

    with pytest.raises(PreparationError, match="path domains"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
            synergy_dir=synergy_dir,
        )

    assert not output_dir.exists()
    assert marker.read_text(encoding="utf-8") == "unchanged\n"
    assert sorted(path.name for path in synergy_dir.iterdir()) == ["marker.txt"]


def test_source_artifact_collision_is_rejected_before_any_write(tmp_path):
    output_dir = tmp_path / "domain"
    output_dir.mkdir()
    source = _write_two_water_cms(output_dir / "analysis-out.cms")
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(PreparationError, match="path domains"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
        )

    assert sorted(path.name for path in output_dir.iterdir()) == ["analysis-out.cms"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_digest


def test_unexpected_exception_after_initialize_marks_manifest_failed(
    tmp_path, monkeypatch
):
    source = _write_two_water_cms(tmp_path / "source.cms")
    output_dir = tmp_path / "unexpected-output"

    def raise_unexpected(*args, **kwargs):
        raise RuntimeError("injected unexpected failure")

    monkeypatch.setattr(preparation, "_export_heavy_graph", raise_unexpected)
    with pytest.raises(PreparationError, match="injected unexpected failure"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
        )

    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "heavy_graph_export"


def test_malformed_adapter_group_fails_schema_before_field_access(
    tmp_path, monkeypatch
):
    source = _write_bonded_ligand_cms(tmp_path / "source.cms")
    fake_adapter = _write_fake_adapter(
        tmp_path / "malformed_adapter.py", malformed_group=True
    )
    synergy_dir = tmp_path / "synergy"
    synergy_dir.mkdir()
    monkeypatch.setattr(preparation, "ADAPTER_SCRIPT", fake_adapter)
    monkeypatch.delenv("SYNERGY_ADAPTER_PYTHON", raising=False)
    output_dir = tmp_path / "malformed-output"

    with pytest.raises(PreparationError, match="adapter group 0 missing required fields"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-6",
            output_dir=output_dir,
            synergy_dir=synergy_dir,
        )

    manifest = json.loads(
        (output_dir / "decomp_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "single_unk_mapping"


def test_already_failed_manifest_is_not_transitioned_again(
    tmp_path, monkeypatch
):
    from mmgbsa_decomp_contract import update_manifest

    source = _write_two_water_cms(tmp_path / "source.cms")
    output_dir = tmp_path / "already-failed-output"
    manifest_path = output_dir / "decomp_manifest.json"
    injected_log = output_dir / "injected.log"

    def fail_terminally(*args, **kwargs):
        update_manifest(
            manifest_path,
            "failed",
            stage="injected_terminal",
            return_code=17,
            log=str(injected_log),
        )
        raise RuntimeError("failure after terminal transition")

    monkeypatch.setattr(preparation, "_export_heavy_graph", fail_terminally)
    with pytest.raises(PreparationError, match="failure after terminal transition"):
        preparation.prepare_ligand_decomp(
            source,
            ligand_asl="atom.num 1-3",
            output_dir=output_dir,
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["error"] == {
        "stage": "injected_terminal",
        "return_code": 17,
        "log": str(injected_log),
    }

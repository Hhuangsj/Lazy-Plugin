import importlib.util
import json
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "skills/science/md-pipeline/scripts/mmgbsa_decomp_contract.py"


def _module():
    spec = importlib.util.spec_from_file_location("mmgbsa_decomp_contract_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_default_properties_are_the_ordered_public_mapping():
    contract = _module()

    assert isinstance(contract.DEFAULT_PROPERTIES, (dict, OrderedDict))
    assert list(contract.DEFAULT_PROPERTIES.items()) == [
        ("dG_Bind", "r_psp_MMGBSA_dG_Bind"),
        ("Coulomb", "r_psp_MMGBSA_dG_Bind(NS)_Coulomb"),
        ("Solv_GB", "r_psp_MMGBSA_dG_Bind(NS)_Solv_GB"),
        ("Covalent", "r_psp_MMGBSA_dG_Bind(NS)_Covalent"),
        ("vdW", "r_psp_MMGBSA_dG_Bind(NS)_vdW"),
        ("Hbond", "r_psp_MMGBSA_dG_Bind(NS)_Hbond"),
        ("Lipo", "r_psp_MMGBSA_dG_Bind(NS)_Lipo"),
        ("Packing", "r_psp_MMGBSA_dG_Bind(NS)_Packing"),
        ("SelfCont", "r_psp_MMGBSA_dG_Bind(NS)_SelfCont"),
        ("Lig_Strain", "r_psp_Lig_Strain_Energy"),
    ]


def test_maestro_partition_accepts_exact_one_based_group_union():
    contract = _module()

    assert contract.validate_maestro_partition(
        {"P000": [1, 2], "N_CAP": [3]},
        [1, 2, 3],
    ) is None


@pytest.mark.parametrize(
    "groups,ligand_atoms",
    [
        ({"P000": [0, 1]}, [1, 2]),
        ({"P000": [1, 2, 2]}, [1, 2]),
        ({"P000": [1], "P001": [2]}, [1, 2, 2]),
        ({"P000": [1]}, [1, 2]),
        ({"P000": [1, 3]}, [1, 2]),
    ],
)
def test_maestro_partition_rejects_zero_missing_extra_or_duplicate_atoms(
    groups, ligand_atoms
):
    contract = _module()

    with pytest.raises(contract.ContractError):
        contract.validate_maestro_partition(ligand_atoms, groups)


def test_maestro_partition_reads_one_based_atoms_from_group_records():
    contract = _module()

    assert contract.validate_maestro_partition(
        [
            {"group_id": "P000", "maestro_atom_indices": [1, 2]},
            {"group_id": "C_CAP", "maestro_atom_indices": [3]},
        ],
        [1, 2, 3],
    ) is None


def test_atomic_json_write_replaces_destination_without_leaving_temp(tmp_path):
    contract = _module()
    output = tmp_path / "nested" / "manifest.json"
    output.parent.mkdir()

    contract.atomic_write_json(output, {"z": "末", "a": [1, 2]})

    assert json.loads(output.read_text(encoding="utf-8")) == {"a": [1, 2], "z": "末"}
    assert list(output.parent.glob(".*.tmp")) == []
    assert contract.load_json(output) == {"a": [1, 2], "z": "末"}


def test_initialize_manifest_writes_schema_one_running_contract(tmp_path):
    contract = _module()
    manifest_path = tmp_path / "decomp_manifest.json"

    manifest = contract.initialize_manifest(
        manifest_path,
        paths={"cms": "input.cms", "output_dir": "out"},
        ligand_asl="res.ptype UNK",
        frames={"start": 1, "end": 3, "step": 2},
        versions={"lazy": "dev"},
    )

    assert manifest["schema_version"] == 1
    assert manifest["status"] == "running"
    assert manifest["paths"] == {"cms": "input.cms", "output_dir": "out"}
    assert manifest["ligand_asl"] == "res.ptype UNK"
    assert manifest["frames"] == {"start": 1, "end": 3, "step": 2}
    assert list(manifest["properties"]) == list(contract.DEFAULT_PROPERTIES)
    assert manifest["versions"] == {"lazy": "dev"}
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["status"] == "running"


def test_manifest_state_machine_allows_running_success_and_blocks_terminal_reversal(tmp_path):
    contract = _module()
    manifest_path = tmp_path / "manifest.json"
    contract.initialize_manifest(manifest_path)

    assert contract.update_manifest(manifest_path, "running")["status"] == "running"
    assert contract.update_manifest(manifest_path, "success")["status"] == "success"

    with pytest.raises(contract.ContractError):
        contract.update_manifest(manifest_path, "running")


def test_manifest_state_machine_allows_none_to_running(tmp_path):
    contract = _module()
    manifest_path = tmp_path / "manifest.json"

    manifest = contract.update_manifest(manifest_path, "running")

    assert manifest == {"schema_version": 1, "status": "running"}


def test_manifest_failure_is_structured_and_return_code_is_numeric(tmp_path):
    contract = _module()
    manifest_path = tmp_path / "manifest.json"
    contract.initialize_manifest(manifest_path)

    manifest = contract.update_manifest(
        manifest_path,
        "failed",
        stage="aggregate",
        return_code=17,
        log=tmp_path / "aggregate.log",
    )

    assert manifest["status"] == "failed"
    assert manifest["error"] == {
        "stage": "aggregate",
        "return_code": 17,
        "log": str(tmp_path / "aggregate.log"),
    }

    with pytest.raises(contract.ContractError):
        contract.update_manifest(manifest_path, "success")


def test_manifest_failure_rejects_non_numeric_return_code(tmp_path):
    contract = _module()
    manifest_path = tmp_path / "manifest.json"
    contract.initialize_manifest(manifest_path)

    with pytest.raises(contract.ContractError):
        contract.update_manifest(
            manifest_path,
            "failed",
            stage="prepare",
            return_code="not-a-number",
            log="prepare.log",
        )


def test_manifest_fail_cli_records_failure_and_returns_zero(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    contract = _module()
    contract.initialize_manifest(manifest_path)
    log_path = tmp_path / "prepare.log"
    log_path.write_text("failed", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "manifest-fail",
            "--manifest",
            str(manifest_path),
            "--stage",
            "prepare",
            "--return-code",
            "9",
            "--log",
            str(log_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["error"] == {
        "stage": "prepare",
        "return_code": 9,
        "log": str(log_path),
    }

import csv
import json
import math
from pathlib import Path
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from mmgbsa_decomp_contract import DEFAULT_PROPERTIES, initialize_manifest, load_json
from prime_mmgbsa_residue_decomp import AggregationError, aggregate_prime_mmgbsa


class _Atom:
    def __init__(self, properties):
        self.property = dict(properties)


class _Structure:
    def __init__(self, atoms, selections):
        self.atom = [None] + list(atoms)
        self.selections = dict(selections)


class _Frame:
    def __init__(self, time):
        self.time = time


def _selector(resnum, pdbres):
    return {
        "chain": "L",
        "resnum": resnum,
        "inscode": "",
        "pdbres": pdbres,
    }


def _residue_map(groups, ligand_asl="ligand"):
    return {
        "schema_version": 1,
        "analysis_ligand_asl": ligand_asl,
        "groups": groups,
    }


def _group(group_id, group_name, resnum, pdbres):
    return {
        "group_id": group_id,
        "group_name": group_name,
        "selector": _selector(resnum, pdbres),
    }


def _structure(values, group_one=(1,), group_two=(2,), ligand=(1, 2)):
    atoms = [_Atom(properties) for properties in values]
    return _Structure(
        atoms,
        {
            "ligand": ligand,
            1: group_one,
            2: group_two,
        },
    )


def _install_schrodinger_fakes(monkeypatch, module, structures, trajectory):
    class Reader:
        def __init__(self, path):
            self._structures = list(structures)

        def __iter__(self):
            return iter(self._structures)

    class Analyze:
        @staticmethod
        def evaluate_asl(structure, asl):
            if asl == "ligand":
                return list(structure.selections["ligand"])
            if "res.num 1" in asl:
                return list(structure.selections[1])
            if "res.num 2" in asl:
                return list(structure.selections[2])
            raise AssertionError("unexpected ASL: {}".format(asl))

    class Trajectory:
        @staticmethod
        def read_traj(path):
            return list(trajectory)

    monkeypatch.setattr(
        module,
        "_schrodinger_dependencies",
        lambda: (Reader, Analyze, Trajectory),
    )


def _inputs(tmp_path, groups=None):
    residue_map_path = tmp_path / "residue_map.json"
    residue_map_path.write_text(
        json.dumps(_residue_map(groups or [
            _group("P001", "GLY", 2, "GLY"),
            _group("P000", "ALA", 1, "ALA"),
        ])),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "decomp_manifest.json"
    initialize_manifest(manifest_path)
    return {
        "prime": tmp_path / "prime-out.maegz",
        "residue_map": residue_map_path,
        "trajectory": tmp_path / "trajectory_trj",
        "frame_csv": tmp_path / "frames.csv",
        "summary_csv": tmp_path / "summary.csv",
        "manifest": manifest_path,
    }


def _run(paths, **kwargs):
    options = dict(kwargs)
    return aggregate_prime_mmgbsa(
        prime_maegz=paths["prime"],
        residue_map_path=paths["residue_map"],
        trajectory_path=paths["trajectory"],
        start=options.pop("start", 0),
        end=options.pop("end", 1),
        step=options.pop("step", 1),
        frame_csv_path=paths["frame_csv"],
        summary_csv_path=paths["summary_csv"],
        manifest_path=paths["manifest"],
        **options
    )


def test_aggregates_only_ligand_atoms_and_marks_manifest_success(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    binding_property = DEFAULT_PROPERTIES["dG_Bind"]
    coulomb_property = DEFAULT_PROPERTIES["Coulomb"]
    receptor = {binding_property: 999.0, coulomb_property: 999.0}
    structures = [
        _structure([
            {binding_property: 1.0, coulomb_property: 10.0},
            {binding_property: 3.0, coulomb_property: 30.0},
            receptor,
        ]),
        _structure([
            {binding_property: 2.0, coulomb_property: 20.0},
            {binding_property: 4.0, coulomb_property: 40.0},
            receptor,
        ]),
    ]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        structures,
        [_Frame(10.0), _Frame(20.0)],
    )

    _run(paths, properties={
        "dG_Bind": binding_property,
        "Coulomb": coulomb_property,
    })

    with paths["frame_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {"frame": "0", "time_ps": "10.0", "group_id": "P000", "group_name": "ALA", "property": "dG_Bind", "value_kcal_mol": "1.0"},
        {"frame": "0", "time_ps": "10.0", "group_id": "P000", "group_name": "ALA", "property": "Coulomb", "value_kcal_mol": "10.0"},
        {"frame": "0", "time_ps": "10.0", "group_id": "P001", "group_name": "GLY", "property": "dG_Bind", "value_kcal_mol": "3.0"},
        {"frame": "0", "time_ps": "10.0", "group_id": "P001", "group_name": "GLY", "property": "Coulomb", "value_kcal_mol": "30.0"},
        {"frame": "1", "time_ps": "20.0", "group_id": "P000", "group_name": "ALA", "property": "dG_Bind", "value_kcal_mol": "2.0"},
        {"frame": "1", "time_ps": "20.0", "group_id": "P000", "group_name": "ALA", "property": "Coulomb", "value_kcal_mol": "20.0"},
        {"frame": "1", "time_ps": "20.0", "group_id": "P001", "group_name": "GLY", "property": "dG_Bind", "value_kcal_mol": "4.0"},
        {"frame": "1", "time_ps": "20.0", "group_id": "P001", "group_name": "GLY", "property": "Coulomb", "value_kcal_mol": "40.0"},
    ]
    with paths["summary_csv"].open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle))
    assert [
        (row["group_id"], row["property"])
        for row in summary_rows
    ] == [
        ("P000", "dG_Bind"),
        ("P000", "Coulomb"),
        ("P001", "dG_Bind"),
        ("P001", "Coulomb"),
    ]
    assert load_json(paths["manifest"])["status"] == "success"


def test_summary_uses_population_standard_deviation_and_sem(tmp_path, monkeypatch):
    paths = _inputs(tmp_path, groups=[_group("P000", "ALA", 1, "ALA")])
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    structures = [
        _structure([{property_name: 2.0}], group_two=(), ligand=(1,)),
        _structure([{property_name: 6.0}], group_two=(), ligand=(1,)),
    ]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        structures,
        [_Frame(0.0), _Frame(5.0)],
    )

    _run(paths, properties={"dG_Bind": property_name})

    with paths["summary_csv"].open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["n_frames"] == "2"
    assert float(row["mean"]) == 4.0
    assert float(row["sd"]) == 2.0
    assert math.isclose(float(row["sem"]), math.sqrt(2.0))


def test_missing_ligand_property_fails_without_replacing_outputs(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    structures = [
        _structure([{property_name: 1.0}, {}]),
        _structure([{property_name: 2.0}, {property_name: 4.0}]),
    ]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        structures,
        [_Frame(0.0), _Frame(5.0)],
    )

    with pytest.raises(AggregationError, match="missing property"):
        _run(paths, properties={"dG_Bind": property_name})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    assert not list(tmp_path.glob(".frames.csv.*.tmp"))
    assert not list(tmp_path.glob(".summary.csv.*.tmp"))
    manifest = load_json(paths["manifest"])
    assert manifest["status"] == "failed"
    assert manifest["error"]["stage"] == "aggregation"


def test_selector_drift_fails_before_writing_outputs(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    structures = [
        _structure([{property_name: 1.0}, {property_name: 3.0}], ligand=(1,)),
        _structure([{property_name: 2.0}, {property_name: 4.0}]),
    ]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        structures,
        [_Frame(0.0), _Frame(5.0)],
    )

    with pytest.raises(AggregationError, match="drift"):
        _run(paths, properties={"dG_Bind": property_name})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"


def test_duplicate_group_identifiers_fail_without_outputs(tmp_path, monkeypatch):
    paths = _inputs(tmp_path, groups=[
        _group("P000", "ALA", 1, "ALA"),
        _group("P000", "GLY", 2, "GLY"),
    ])
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        [_structure([{property_name: 1.0}, {property_name: 3.0}])],
        [_Frame(0.0)],
    )

    with pytest.raises(AggregationError, match="duplicate group_id"):
        _run(paths, end=0, properties={"dG_Bind": property_name})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"


def test_prime_structure_count_must_match_inclusive_trajectory_indices(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        sys.modules["prime_mmgbsa_residue_decomp"],
        [_structure([{property_name: 1.0}, {property_name: 3.0}])],
        [_Frame(0.0), _Frame(5.0)],
    )

    with pytest.raises(AggregationError, match="Prime structure count"):
        _run(paths, properties={"dG_Bind": property_name})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"


def test_unexpected_schrodinger_error_marks_running_manifest_failed(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    module = sys.modules["prime_mmgbsa_residue_decomp"]
    monkeypatch.setattr(
        module,
        "_schrodinger_dependencies",
        lambda: (_ for _ in ()).throw(RuntimeError("Prime reader unavailable")),
    )

    with pytest.raises(AggregationError, match="Prime reader unavailable"):
        _run(paths, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"

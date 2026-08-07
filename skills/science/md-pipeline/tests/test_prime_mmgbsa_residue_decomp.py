import csv
import json
import math
import os
from collections import OrderedDict
from pathlib import Path
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from mmgbsa_decomp_contract import DEFAULT_PROPERTIES, initialize_manifest, load_json
import prime_mmgbsa_residue_decomp as aggregation_module
from prime_mmgbsa_residue_decomp import AggregationError, aggregate_prime_mmgbsa


class _Atom:
    def __init__(self, properties):
        self.property = dict(properties)


class _AtomContainer:
    def __init__(self, atoms):
        self._atoms = list(atoms)

    def __len__(self):
        return len(self._atoms)

    def __getitem__(self, atom_index):
        if atom_index < 1 or atom_index > len(self._atoms):
            raise IndexError(atom_index)
        return self._atoms[atom_index - 1]


class _Structure:
    def __init__(self, atoms, selections):
        self.atom = _AtomContainer(atoms)
        self.atom_total = len(atoms)
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
        "group_type": "residue",
        "group_name": group_name,
        "maestro_atom_indices": [resnum],
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


def _valid_structure(property_name=DEFAULT_PROPERTIES["dG_Bind"]):
    return _structure([{property_name: 1.0}, {property_name: 3.0}])


def _install_valid_snapshot(monkeypatch, paths, trajectory=None):
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_valid_structure()],
        trajectory or [_Frame(0.0)],
    )


def _write_existing_outputs(paths):
    paths["frame_csv"].write_bytes(b"old-frame\n")
    paths["summary_csv"].write_bytes(b"old-summary\n")


def _assert_no_publication_artifacts(tmp_path):
    assert not [path for path in tmp_path.iterdir() if path.suffix in {".tmp", ".bak"}]


def test_real_schrodinger_atom_container_accepts_final_one_based_atom():
    from schrodinger import structure
    from schrodinger.structutils import analyze

    real_structure = structure.create_new_structure()
    real_structure.addAtom("C", 0.0, 0.0, 0.0)
    real_structure.addAtom("N", 1.0, 0.0, 0.0)

    assert real_structure.atom_total == 2
    assert len(real_structure.atom) == 2
    assert real_structure.atom[2].element == "N"
    assert aggregation_module._select_atoms(
        analyze, real_structure, "atom.ele N", "final nitrogen"
    ) == {2}


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


def test_identical_csv_paths_fail_before_schrodinger_io_or_publication(
    tmp_path, monkeypatch
):
    paths = _inputs(tmp_path)
    paths["summary_csv"] = paths["frame_csv"]
    monkeypatch.setattr(
        aggregation_module,
        "_schrodinger_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("Schrodinger I/O must not run")),
    )

    with pytest.raises(AggregationError, match="distinct files"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert not paths["frame_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"


def test_samefile_csv_targets_fail_without_replacing_existing_output(
    tmp_path, monkeypatch
):
    paths = _inputs(tmp_path)
    paths["frame_csv"].write_bytes(b"old-output\n")
    os.link(paths["frame_csv"], paths["summary_csv"])
    monkeypatch.setattr(
        aggregation_module,
        "_schrodinger_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("Schrodinger I/O must not run")),
    )

    with pytest.raises(AggregationError, match="distinct files"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert paths["frame_csv"].read_bytes() == b"old-output\n"
    assert paths["summary_csv"].read_bytes() == b"old-output\n"
    assert load_json(paths["manifest"])["status"] == "failed"


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


def test_selector_overlap_fails_before_property_access(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_structure(
            [{property_name: 1.0}, {property_name: 3.0}],
            group_one=(1,),
            group_two=(1, 2),
        )],
        [_Frame(0.0)],
    )

    with pytest.raises(AggregationError, match="overlap"):
        _run(paths, end=0, properties={"dG_Bind": property_name})

    assert not paths["frame_csv"].exists()
    assert load_json(paths["manifest"])["status"] == "failed"


@pytest.mark.parametrize(("bad_properties", "reason"), [
    ({}, "missing"),
    ({DEFAULT_PROPERTIES["dG_Bind"]: "not-a-number"}, "not numeric"),
    ({DEFAULT_PROPERTIES["dG_Bind"]: float("nan")}, "not finite"),
])
def test_property_error_identifies_frame_group_label_and_prime_property(
    tmp_path, monkeypatch, bad_properties, reason
):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_structure([{property_name: 1.0}, bad_properties])],
        [_Frame(0.0), _Frame(1.0), _Frame(2.0), _Frame(3.0)],
    )

    with pytest.raises(
        AggregationError,
        match=(
            "source frame 3.*group_id P001.*label dG_Bind.*"
            "Prime property {}.*{}".format(property_name, reason)
        ),
    ):
        _run(paths, start=3, end=3, properties={"dG_Bind": property_name})

    assert load_json(paths["manifest"])["status"] == "failed"


def test_default_properties_keep_shared_order(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    values = {property_name: float(position) for position, property_name in enumerate(
        DEFAULT_PROPERTIES.values(), start=1
    )}
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_structure([values, values])],
        [_Frame(0.0)],
    )

    _run(paths, end=0)

    with paths["frame_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["property"] for row in rows[:len(DEFAULT_PROPERTIES)]] == list(
        DEFAULT_PROPERTIES
    )


def test_custom_property_subset_keeps_shared_order(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    properties = OrderedDict([
        ("Coulomb", DEFAULT_PROPERTIES["Coulomb"]),
        ("vdW", DEFAULT_PROPERTIES["vdW"]),
    ])
    values = {property_name: 1.0 for property_name in properties.values()}
    _install_schrodinger_fakes(
        monkeypatch, aggregation_module, [_structure([values, values])], [_Frame(0.0)]
    )

    _run(paths, end=0, properties=properties)

    with paths["frame_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["property"] for row in rows[:2]] == ["Coulomb", "vdW"]


@pytest.mark.parametrize("properties", [
    OrderedDict([("dG_Bind", "not-the-shared-property")]),
    OrderedDict([("unknown", "r_unknown")]),
    OrderedDict([
        ("Coulomb", DEFAULT_PROPERTIES["Coulomb"]),
        ("dG_Bind", DEFAULT_PROPERTIES["dG_Bind"]),
    ]),
])
def test_public_property_mapping_rejects_noncanonical_requests(
    tmp_path, monkeypatch, properties
):
    paths = _inputs(tmp_path)

    with pytest.raises(AggregationError, match="properties|property"):
        _run(paths, end=0, properties=properties)

    assert load_json(paths["manifest"])["status"] == "failed"


@pytest.mark.parametrize("mutation", [
    lambda payload: payload.update(schema_version=2),
    lambda payload: payload.update(schema_version=True),
    lambda payload: payload.pop("analysis_ligand_asl"),
    lambda payload: payload.update(groups="not-a-list"),
    lambda payload: payload["groups"][0].pop("group_type"),
    lambda payload: payload["groups"][0].update(group_type="not-a-group-type"),
    lambda payload: payload["groups"][0].update(maestro_atom_indices=[0]),
    lambda payload: payload["groups"][0].pop("selector"),
    lambda payload: payload["groups"][0]["selector"].update(extra="not-allowed"),
])
def test_residue_map_schema_is_validated_before_schrodinger_io(
    tmp_path, monkeypatch, mutation
):
    paths = _inputs(tmp_path)
    payload = json.loads(paths["residue_map"].read_text(encoding="utf-8"))
    mutation(payload)
    paths["residue_map"].write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        aggregation_module,
        "_schrodinger_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("Schrodinger I/O reached")),
    )

    with pytest.raises(AggregationError, match="residue map|group|selector|schema"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert load_json(paths["manifest"])["status"] == "failed"


@pytest.mark.parametrize("range_args", [
    {"start": -1, "end": 0, "step": 1},
    {"start": 1, "end": 0, "step": 1},
    {"start": 0, "end": 0, "step": 0},
    {"start": 0, "end": 0, "step": -1},
])
def test_invalid_inclusive_frame_ranges_fail_closed(tmp_path, range_args):
    paths = _inputs(tmp_path)

    with pytest.raises(AggregationError, match="frame range"):
        _run(paths, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]}, **range_args)

    assert load_json(paths["manifest"])["status"] == "failed"


def test_nonzero_source_indices_map_to_their_trajectory_times(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_valid_structure(property_name), _valid_structure(property_name)],
        [_Frame(0.0), _Frame(5.0), _Frame(10.0), _Frame(15.0), _Frame(20.0)],
    )

    _run(paths, start=2, end=4, step=2, properties={"dG_Bind": property_name})

    with paths["frame_csv"].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [(row["frame"], row["time_ps"]) for row in rows] == [
        ("2", "10.0"),
        ("2", "10.0"),
        ("4", "20.0"),
        ("4", "20.0"),
    ]


def test_adversarial_fsum_reconciliation_fails_closed(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    groups = [
        _group("P000", "ALA", 1, "ALA"),
        _group("P001", "GLY", 2, "GLY"),
    ]
    paths["residue_map"].write_text(
        json.dumps(_residue_map(groups, ligand_asl="ligand")), encoding="utf-8"
    )
    structure = _Structure(
        [_Atom({property_name: 1e16}), _Atom({property_name: -1e16}), _Atom({property_name: 1.0})],
        {"ligand": (1, 2, 3), 1: (1, 3), 2: (2,)},
    )
    _install_schrodinger_fakes(
        monkeypatch, aggregation_module, [structure], [_Frame(0.0)]
    )

    with pytest.raises(AggregationError, match="does not reconcile"):
        _run(paths, end=0, properties={"dG_Bind": property_name})

    assert load_json(paths["manifest"])["status"] == "failed"


@pytest.mark.parametrize("preexisting", [False, True])
def test_second_csv_replacement_rolls_back_pair(
    tmp_path, monkeypatch, preexisting
):
    paths = _inputs(tmp_path)
    if preexisting:
        _write_existing_outputs(paths)
    _install_valid_snapshot(monkeypatch, paths)
    original_replace = aggregation_module.os.replace

    def fail_summary_replace(source, destination):
        if (
            Path(destination) == paths["summary_csv"]
            and Path(source).name.startswith(".summary.csv.")
            and Path(source).suffix == ".tmp"
        ):
            raise OSError("injected second replacement failure")
        return original_replace(source, destination)

    monkeypatch.setattr(aggregation_module.os, "replace", fail_summary_replace)

    with pytest.raises(AggregationError, match="second replacement"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    if preexisting:
        assert paths["frame_csv"].read_bytes() == b"old-frame\n"
        assert paths["summary_csv"].read_bytes() == b"old-summary\n"
    else:
        assert not paths["frame_csv"].exists()
        assert not paths["summary_csv"].exists()
    _assert_no_publication_artifacts(tmp_path)
    assert load_json(paths["manifest"])["status"] == "failed"


def test_success_manifest_failure_restores_preexisting_pair(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    _write_existing_outputs(paths)
    _install_valid_snapshot(monkeypatch, paths)
    original_update_manifest = aggregation_module.update_manifest

    def fail_success_manifest(manifest_path, status, **kwargs):
        if status == "success":
            raise RuntimeError("injected success manifest failure")
        return original_update_manifest(manifest_path, status, **kwargs)

    monkeypatch.setattr(aggregation_module, "update_manifest", fail_success_manifest)

    with pytest.raises(AggregationError, match="success manifest failure"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert paths["frame_csv"].read_bytes() == b"old-frame\n"
    assert paths["summary_csv"].read_bytes() == b"old-summary\n"
    _assert_no_publication_artifacts(tmp_path)
    assert load_json(paths["manifest"])["status"] == "failed"


def test_success_manifest_failure_removes_new_pair(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    _install_valid_snapshot(monkeypatch, paths)
    original_update_manifest = aggregation_module.update_manifest

    def fail_success_manifest(manifest_path, status, **kwargs):
        if status == "success":
            raise RuntimeError("injected success manifest failure")
        return original_update_manifest(manifest_path, status, **kwargs)

    monkeypatch.setattr(aggregation_module, "update_manifest", fail_success_manifest)

    with pytest.raises(AggregationError, match="success manifest failure"):
        _run(paths, end=0, properties={"dG_Bind": DEFAULT_PROPERTIES["dG_Bind"]})

    assert not paths["frame_csv"].exists()
    assert not paths["summary_csv"].exists()
    _assert_no_publication_artifacts(tmp_path)
    assert load_json(paths["manifest"])["status"] == "failed"


@pytest.mark.parametrize("property_argument", [
    "dG_Bind,dG_Bind",
    "not-a-property",
    "Coulomb,dG_Bind",
    "dG_Bind,",
    ",dG_Bind",
    "dG_Bind,,Coulomb",
    "",
])
def test_cli_invalid_property_list_fails_existing_running_manifest(
    tmp_path, property_argument
):
    paths = _inputs(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        aggregation_module.main([
            "--prime-maegz", str(paths["prime"]),
            "--residue-map", str(paths["residue_map"]),
            "--trajectory", str(paths["trajectory"]),
            "--start", "0",
            "--end", "0",
            "--step", "1",
            "--frame-csv", str(paths["frame_csv"]),
            "--summary-csv", str(paths["summary_csv"]),
            "--manifest", str(paths["manifest"]),
            "--properties", property_argument,
        ])

    assert exit_info.value.code == 2
    assert load_json(paths["manifest"])["status"] == "failed"


def test_cli_bare_properties_fails_manifest_before_schrodinger_io(
    tmp_path, monkeypatch
):
    paths = _inputs(tmp_path)
    monkeypatch.setattr(
        aggregation_module,
        "_schrodinger_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("Schrodinger I/O reached")),
    )

    with pytest.raises(SystemExit) as exit_info:
        aggregation_module.main([
            "--prime-maegz", str(paths["prime"]),
            "--residue-map", str(paths["residue_map"]),
            "--trajectory", str(paths["trajectory"]),
            "--start", "0",
            "--end", "0",
            "--step", "1",
            "--frame-csv", str(paths["frame_csv"]),
            "--summary-csv", str(paths["summary_csv"]),
            "--manifest", str(paths["manifest"]),
            "--properties",
        ])

    assert exit_info.value.code == 2
    assert load_json(paths["manifest"])["status"] == "failed"


def test_manifest_failure_transition_error_preserves_original_error(tmp_path, monkeypatch):
    paths = _inputs(tmp_path)
    property_name = DEFAULT_PROPERTIES["dG_Bind"]
    _install_schrodinger_fakes(
        monkeypatch,
        aggregation_module,
        [_structure([{property_name: 1.0}, {}])],
        [_Frame(0.0)],
    )
    original_update_manifest = aggregation_module.update_manifest

    def fail_failed_transition(manifest_path, status, **kwargs):
        if status == "failed":
            raise RuntimeError("injected failed transition failure")
        return original_update_manifest(manifest_path, status, **kwargs)

    monkeypatch.setattr(aggregation_module, "update_manifest", fail_failed_transition)

    with pytest.raises(
        AggregationError,
        match="missing property.*manifest failure transition issue.*failed transition",
    ):
        _run(paths, end=0, properties={"dG_Bind": property_name})

    assert load_json(paths["manifest"])["status"] == "running"


def test_failure_does_not_overwrite_terminal_manifest(tmp_path):
    paths = _inputs(tmp_path)
    aggregation_module.update_manifest(paths["manifest"], "success")

    with pytest.raises(AggregationError, match="properties must be a subset"):
        _run(paths, end=0, properties={"unknown": "r_unknown"})

    assert load_json(paths["manifest"])["status"] == "success"

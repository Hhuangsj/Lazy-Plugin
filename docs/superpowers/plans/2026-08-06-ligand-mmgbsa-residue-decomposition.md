# Ligand MM/GBSA Residue Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `DECOMP=1` path to Lazy-Plugin that converts a single-`UNK` peptide ligand into atom-complete residue groups through Synergy-Fragment, or reuses pre-resolved ligand residues, then summarizes Prime MM/GBSA atomic properties per ligand group.

**Architecture:** Synergy-Fragment owns peptide graph segmentation and emits schema-v1 RDKit atom groups. Lazy-Plugin owns CMS extraction, RDKit-to-Maestro remapping, explicit-hydrogen ownership, non-destructive analysis CMS creation, thermal MM/GBSA orchestration, property aggregation, manifests, and agent-facing documentation. `run_mmgbsa.sh` remains the sole user entry point and preserves its existing behavior unless `DECOMP=1` is set.

**Tech Stack:** Bash, Python 3.8-compatible code, Schrödinger 2023-4 Python API, RDKit, Prime/thermal_mmgbsa.py, JSON schema-v1 contracts, CSV, pytest, and shell tests.

## Global Constraints

- Implement in isolated worktrees: one from `Synergy` branch `dev`, one from `Lazy-Plugin` branch `master`; do not touch either dirty primary worktree.
- Do not copy peptide segmentation logic into Lazy-Plugin; `Synergy-Fragment/residue_map.py` is the authoritative single-UNK mapper.
- Shared Synergy mapper code must run under Schrödinger 2023-4 Python 3.8 as well as the Synergy development Python.
- RDKit atom indices in the Synergy contract are zero-based; Maestro atom indices are one-based. Every conversion must name the index space explicitly.
- Original CMS and trajectory files are read-only. Only an analysis CMS copy may receive residue metadata changes.
- `DECOMP` unset or false must preserve the existing `run_mmgbsa.sh` command and output behavior.
- Standard proteinogenic residues use canonical three-letter names; only exact-stereo matches for the 19 chiral L residues qualify, while Gly is achiral. D, N-methylated, modified, and stereo-ambiguous residues must not be mislabeled as standard L residues.
- Unknown names and incomplete stereo matches warn but do not fail. Missing/duplicate atom ownership, graph mismatch, missing Prime properties, snapshot mapping drift, and failed numerical reconciliation fail closed.
- All ligand atoms, including explicit hydrogens, must belong to exactly one `Pnnn`, cap, or `XLINK_nnn` group.
- Use population SD (`statistics.pstdev`) and `SEM = SD / sqrt(n)` to match the supplied script's `numpy.std` behavior.
- Do not commit NPR CMS, trajectory, maegz, logs, or other large generated artifacts.

## File Structure

### Synergy repository

- Create `Synergy-Fragment/residue_map.py`: authoritative graph-to-group mapping and canonical standard-residue naming.
- Create `Synergy-Fragment/residue_map_cli.py`: one-molecule SDF CLI that writes schema-v1 JSON.
- Create `Synergy-Fragment/test_residue_map.py`: mapping, naming, coverage, crosslink, and CLI contract tests.

### Lazy-Plugin repository

- Create `skills/science/md-pipeline/scripts/mmgbsa_decomp_contract.py`: shared schema, property definitions, partition validation, atomic JSON writes, and manifest state transitions.
- Create `skills/science/md-pipeline/scripts/prepare_ligand_decomp.py`: CMS mode detection, SDF/index-map round trip, Synergy invocation, hydrogen ownership, and analysis CMS creation.
- Create `skills/science/md-pipeline/scripts/prime_mmgbsa_residue_decomp.py`: Prime maegz reader, ligand-only per-group aggregation, reconciliation, frame CSV, and summary CSV.
- Modify `skills/science/md-pipeline/scripts/run_mmgbsa.sh`: optional `DECOMP=1` orchestration.
- Create `skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py`.
- Create `skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py`.
- Create `skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py`.
- Create `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`.
- Create `toolenv/tools.d/synergy-fragment.sh` and modify `toolenv/tests/test_manifests.sh`.
- Modify `skills/science/md-pipeline/SKILL.md` and `skills/science/md-pipeline/references/troubleshooting.md`.

---

### Task 1: Build the authoritative Synergy atom-group mapper

**Repository:** Synergy isolated worktree

**Files:**
- Create: `Synergy-Fragment/residue_map.py`
- Create: `Synergy-Fragment/test_residue_map.py`

**Interfaces:**
- Consumes: `peptide_sequence.enumerate_backbones`, `order_residues`, `analyze_components_detailed`, `sequence_peptide`, and `ResidueIdentifier`.
- Produces: `build_residue_map(mol: Chem.Mol, identifier: ResidueIdentifier) -> dict` and `validate_rdkit_partition(payload: dict, atom_count: int) -> None`.
- JSON group atom indices are zero-based RDKit indices.

- [ ] **Step 1: Create failing tests for standard naming, unknown naming, and exact atom partitioning**

```python
from rdkit import Chem

import peptide_sequence as ps
from residue_map import build_residue_map


ROWS = [
    {"symbol": "M0227", "name": "A", "segment_smiles": "N[C@@H](C)C=O"},
    {"symbol": "M0261", "name": "F", "segment_smiles": "N[C@@H](Cc1ccccc1)C=O"},
]


def _assert_exact_partition(result, atom_count):
    atoms = [idx for group in result["groups"] for idx in group["rdkit_atom_indices"]]
    assert sorted(atoms) == list(range(atom_count))
    assert len(atoms) == len(set(atoms))
    assert result["unassigned_atom_indices"] == []
    assert result["duplicate_atom_indices"] == []


def test_standard_l_residues_get_three_letter_names():
    mol = Chem.MolFromSmiles("N[C@@H](C)C(=O)N[C@@H](Cc1ccccc1)C(=O)O")
    result = build_residue_map(mol, ps.ResidueIdentifier(ROWS))
    residues = [g for g in result["groups"] if g["group_type"] == "residue"]
    assert [g["group_id"] for g in residues] == ["P000", "P001"]
    assert [g["canonical_resname"] for g in residues] == ["ALA", "PHE"]
    _assert_exact_partition(result, mol.GetNumAtoms())


def test_d_residue_does_not_get_l_residue_name():
    mol = Chem.MolFromSmiles("N[C@H](C)C(=O)O")
    result = build_residue_map(mol, ps.ResidueIdentifier(ROWS))
    residue = next(g for g in result["groups"] if g["group_type"] == "residue")
    assert residue["canonical_resname"] is None
    assert residue["recognition_status"] in {"unknown", "stereo_inexact"}
```

- [ ] **Step 2: Run the focused tests and verify the missing module failure**

Run: `python3 -m pytest Synergy-Fragment/test_residue_map.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'residue_map'`.

- [ ] **Step 3: Implement canonical naming and deterministic group assembly**

Create `residue_map.py` with these public constants and functions:

```python
SCHEMA_VERSION = 1
MAPPER_VERSION = "synergy-residue-map/1"
STANDARD_RESNAMES = {
    "A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS",
    "Q": "GLN", "E": "GLU", "G": "GLY", "H": "HIS", "I": "ILE",
    "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO",
    "S": "SER", "T": "THR", "W": "TRP", "Y": "TYR", "V": "VAL",
    "ALA": "ALA", "ARG": "ARG", "ASN": "ASN", "ASP": "ASP",
    "CYS": "CYS", "GLN": "GLN", "GLU": "GLU", "GLY": "GLY",
    "HIS": "HIS", "ILE": "ILE", "LEU": "LEU", "LYS": "LYS",
    "MET": "MET", "PHE": "PHE", "PRO": "PRO", "SER": "SER",
    "THR": "THR", "TRP": "TRP", "TYR": "TYR", "VAL": "VAL",
}


class ResidueMapError(ValueError):
    pass


def canonical_resname(name, stereo_exact):
    key = (name or "").strip().upper()
    value = STANDARD_RESNAMES.get(key)
    if value == "GLY":
        return value
    return value if value and stereo_exact else None


def validate_rdkit_partition(payload, atom_count):
    flat = [idx for group in payload["groups"] for idx in group["rdkit_atom_indices"]]
    missing = sorted(set(range(atom_count)) - set(flat))
    duplicates = sorted({idx for idx in flat if flat.count(idx) > 1})
    payload["unassigned_atom_indices"] = missing
    payload["duplicate_atom_indices"] = duplicates
    if missing or duplicates:
        raise ResidueMapError(
            "atom partition is not exact: missing={} duplicate={}".format(missing, duplicates)
        )
```

`build_residue_map` must:

1. enumerate and order backbone tuples;
2. obtain residue recognition from `sequence_peptide`;
3. build each `Pnnn` from its four backbone atoms plus non-crosslink sidechain atoms;
4. assign free-acid terminal O atoms back to their attached residue;
5. emit N/C cap components as `N_CAP`/`C_CAP`;
6. emit each component with `kind in {"crosslink", "disulfide"}` as one `XLINK_nnn`, removing those atoms from residue groups;
7. reject detached components because they have no residue ownership;
8. sort residues by sequence index, then caps, then crosslinks;
9. call `validate_rdkit_partition` before returning `status="ok"`.

- [ ] **Step 4: Add crosslink, cap, unknown, and free-acid regression tests**

```python
def test_crosslink_is_a_non_overlapping_group():
    smi = "N[C@@H](CCCCNC(=O)C[C@H](N)C=O)C=O"
    mol = Chem.MolFromSmiles(smi)
    result = build_residue_map(mol, ps.ResidueIdentifier(ROWS))
    xlinks = [g for g in result["groups"] if g["group_type"] == "crosslink"]
    assert [g["group_id"] for g in xlinks] == ["XLINK_000"]
    assert xlinks[0]["connected_group_ids"] == ["P000", "P001"]
    _assert_exact_partition(result, mol.GetNumAtoms())


def test_ac_and_nh2_are_separate_groups():
    mol = Chem.MolFromSmiles("CC(=O)N[C@@H](C)C(=O)N")
    result = build_residue_map(mol, ps.ResidueIdentifier(ROWS))
    assert {g["group_id"] for g in result["groups"]} >= {"P000", "N_CAP", "C_CAP"}
    _assert_exact_partition(result, mol.GetNumAtoms())
```

- [ ] **Step 5: Run the complete Synergy mapper unit tests**

Run: `python3 -m pytest Synergy-Fragment/test_residue_map.py Synergy-Fragment/test_peptide_sequence.py -q`

Expected: PASS with no changes to existing peptide-sequence behavior.

- [ ] **Step 6: Commit the mapper core**

```bash
git add Synergy-Fragment/residue_map.py Synergy-Fragment/test_residue_map.py
git commit -m "feat(fragment): expose peptide residue atom map"
```

### Task 2: Add the versioned Synergy residue-map CLI

**Repository:** Synergy isolated worktree

**Files:**
- Create: `Synergy-Fragment/residue_map_cli.py`
- Modify: `Synergy-Fragment/test_residue_map.py`

**Interfaces:**
- Consumes: one heavy-atom SDF molecule and `build_residue_map`.
- Produces: `residue_map_cli.py --sdf INPUT --output OUTPUT [--library CSV]`; JSON is UTF-8, sorted-key, indented schema version 1.

- [ ] **Step 1: Add a failing subprocess contract test**

```python
import json
import subprocess
import sys


def test_cli_writes_schema_v1_json(tmp_path):
    mol = Chem.MolFromSmiles("N[C@@H](C)C(=O)O")
    sdf = tmp_path / "ligand.sdf"
    writer = Chem.SDWriter(str(sdf))
    writer.write(mol)
    writer.close()
    out = tmp_path / "map.json"
    proc = subprocess.run(
        [sys.executable, "Synergy-Fragment/residue_map_cli.py",
         "--sdf", str(sdf), "--output", str(out)],
        text=True, capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text())
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
```

- [ ] **Step 2: Verify the CLI test fails because the script is absent**

Run: `python3 -m pytest Synergy-Fragment/test_residue_map.py::test_cli_writes_schema_v1_json -q`

Expected: FAIL with Python unable to open `residue_map_cli.py`.

- [ ] **Step 3: Implement the CLI with a stable default library path and atomic output**

```python
def parse_args():
    parser = argparse.ArgumentParser(description="Map a peptide SDF to residue atom groups")
    parser.add_argument("--sdf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--library",
        default=str(Path(__file__).with_name("monomer_library_nonstandard_segments_simple.csv")),
    )
    return parser.parse_args()


def read_one_sdf(path):
    mols = [mol for mol in Chem.SDMolSupplier(str(path), removeHs=False) if mol is not None]
    if len(mols) != 1:
        raise ResidueMapError("expected exactly one SDF molecule, found {}".format(len(mols)))
    return mols[0]
```

Write to `<output>.tmp` and replace the destination only after `build_residue_map` succeeds. On `ResidueMapError`, print one `ERROR:` line to stderr, return exit code 2, and do not leave a success JSON.

- [ ] **Step 4: Test invalid SDF and deterministic repeated output**

Add tests asserting an empty SDF exits 2 and two runs on the same SDF produce byte-identical JSON.

- [ ] **Step 5: Run mapper and CLI tests under both Python environments**

Run:

```bash
python3 -m pytest Synergy-Fragment/test_residue_map.py -q
/home/huangshengjie/software/Schrodinger/2023-4/run python3 Synergy-Fragment/residue_map_cli.py --help
```

Expected: pytest PASS and Schrödinger Python prints CLI usage without syntax/import errors.

- [ ] **Step 6: Commit the CLI**

```bash
git add Synergy-Fragment/residue_map_cli.py Synergy-Fragment/test_residue_map.py
git commit -m "feat(fragment): add residue map CLI"
```

### Task 3: Define Lazy-Plugin's shared decomp contract and manifest states

**Repository:** Lazy-Plugin isolated worktree

**Files:**
- Create: `skills/science/md-pipeline/scripts/mmgbsa_decomp_contract.py`
- Create: `skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py`

**Interfaces:**
- Produces: `DEFAULT_PROPERTIES`, `ContractError`, `validate_maestro_partition`, `atomic_write_json`, `load_json`, `initialize_manifest`, and `update_manifest`.
- CLI: `mmgbsa_decomp_contract.py manifest-fail --manifest FILE --stage STAGE --return-code RC --log FILE`, for shell-visible failure transitions after preparation has initialized the manifest.
- Consumed by both preparation and Prime aggregation scripts.

- [ ] **Step 1: Write failing contract tests**

```python
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from mmgbsa_decomp_contract import (
    DEFAULT_PROPERTIES, ContractError, initialize_manifest,
    update_manifest, validate_maestro_partition,
)


def test_default_property_order_is_stable():
    assert list(DEFAULT_PROPERTIES) == [
        "dG_Bind", "Coulomb", "Solv_GB", "Covalent", "vdW",
        "Hbond", "Lipo", "Packing", "SelfCont", "Lig_Strain",
    ]


def test_partition_rejects_missing_and_duplicate_atoms():
    groups = [{"group_id": "P000", "maestro_atom_indices": [1, 2]},
              {"group_id": "P001", "maestro_atom_indices": [2]}]
    try:
        validate_maestro_partition({1, 2, 3}, groups)
    except ContractError as exc:
        assert "missing=[3]" in str(exc) and "duplicate=[2]" in str(exc)
    else:
        raise AssertionError("invalid partition was accepted")
```

- [ ] **Step 2: Verify tests fail on the missing module**

Run: `python3 -m pytest skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement constants, exact partition validation, and atomic JSON writes**

Use this exact property mapping:

```python
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
```

`validate_maestro_partition` must compare the set and multiplicity of one-based ligand atom indices. `atomic_write_json` must write beside the destination and use `os.replace` only after `json.dump` succeeds.

- [ ] **Step 4: Implement manifest transitions and reject success after failure**

```python
ALLOWED_TRANSITIONS = {
    None: {"running"},
    "running": {"running", "success", "failed"},
    "success": set(),
    "failed": set(),
}
```

`initialize_manifest` records schema version 1, `status="running"`, mode, paths, ASL, frame range, property mapping, and version fields. `update_manifest` merges warnings/output paths or one structured error and enforces `ALLOWED_TRANSITIONS`. The `manifest-fail` subcommand loads an existing running manifest, stores `stage`, numeric return code, and log path under `error`, then atomically transitions it to `failed`.

- [ ] **Step 5: Run the contract tests**

Run: `python3 -m pytest skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the shared contract**

```bash
git add skills/science/md-pipeline/scripts/mmgbsa_decomp_contract.py \
        skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py
git commit -m "feat(md): define MMGBSA decomp contract"
```

### Task 4: Prepare single-UNK and pre-resolved ligand mappings from CMS

**Repository:** Lazy-Plugin isolated worktree

**Files:**
- Create: `skills/science/md-pipeline/scripts/prepare_ligand_decomp.py`
- Create: `skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py`

**Interfaces:**
- Consumes: CMS path, ligand ASL, output directory, optional `SYNERGY_FRAGMENT_DIR`.
- Produces: `residue_map.json`, `atom_index_map.json`, `ligand_graph.sdf`, optional analysis CMS, and `prepare_result.json`.
- CLI: `$SCHRODINGER/run prepare_ligand_decomp.py CMS --lig-asl ASL --out-dir DIR [--synergy-dir DIR]`.
- `prepare_result.json` fields: `mode`, `analysis_cms`, `analysis_ligand_asl`, `residue_map`, `source_cms`.

- [ ] **Step 1: Write pure failing tests for mode detection, remapping, hydrogen ownership, and partition checks**

```python
from prepare_ligand_decomp import (
    assign_hydrogens, detect_mode_from_residues, remap_rdkit_groups,
)


def test_detect_modes():
    assert detect_mode_from_residues([("UNK", 1, "")]) == "single_unk"
    assert detect_mode_from_residues([("ALA", 1, "L"), ("PHE", 2, "L")]) == "pre_resolved"


def test_rdkit_zero_based_indices_remap_to_maestro_one_based_indices():
    groups = [{"group_id": "P000", "rdkit_atom_indices": [0, 2]}]
    mapped = remap_rdkit_groups(groups, {0: 101, 1: 102, 2: 103})
    assert mapped[0]["maestro_atom_indices"] == [101, 103]


def test_hydrogens_follow_their_heavy_atom_group():
    groups = [{"group_id": "P000", "maestro_atom_indices": [10]},
              {"group_id": "P001", "maestro_atom_indices": [20]}]
    bonds = {11: [10], 12: [10], 21: [20]}
    assign_hydrogens(groups, hydrogen_indices={11, 12, 21}, neighbors=bonds)
    assert groups[0]["maestro_atom_indices"] == [10, 11, 12]
    assert groups[1]["maestro_atom_indices"] == [20, 21]
```

- [ ] **Step 2: Verify the preparation tests fail on the missing module**

Run: `$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement ligand selection and mode detection with explicit molecule validation**

Use `topo.read_cms`, `cms.select_atom(ligand_asl)`, and `cms.getMoleculeAtoms()` to verify the ASL equals one complete molecule. Return actionable errors containing the ASL and selected atom count.

`detect_mode_from_residues` returns `single_unk` only for exactly one residue whose stripped `pdbres` is `UNK`; all other valid multi-residue ligand selections return `pre_resolved`.

At CLI startup, create `<out-dir>/decomp_manifest.json` with `status=running` and `mode=null`. After ligand selection, update the same running manifest with the detected mode. Every later preparation exception must atomically transition it to `failed` before returning nonzero.

Start the script with discoverable metadata:

```python
#!/usr/bin/env python3
# @name: prepare_ligand_decomp
# @description: Build atom-complete ligand residue groups and a non-destructive analysis CMS
# @requires: schrodinger
# @usage: prepare_ligand_decomp.py CMS --lig-asl ASL --out-dir DIR [--synergy-dir DIR]
```

Define these public pure helpers before the Schrödinger adapter code:

```python
class PreparationError(RuntimeError):
    pass


def detect_mode_from_residues(residues):
    normalized = [(name.strip(), int(num), chain.strip()) for name, num, chain in residues]
    return "single_unk" if len(normalized) == 1 and normalized[0][0] == "UNK" else "pre_resolved"


def remap_rdkit_groups(groups, rdkit_to_maestro):
    mapped = []
    for group in groups:
        item = dict(group)
        item["maestro_atom_indices"] = sorted(
            rdkit_to_maestro[idx] for idx in group["rdkit_atom_indices"]
        )
        mapped.append(item)
    return mapped


def assign_hydrogens(groups, hydrogen_indices, neighbors):
    owner = {idx: group for group in groups for idx in group["maestro_atom_indices"]}
    for hydrogen in sorted(hydrogen_indices):
        heavy = [idx for idx in neighbors[hydrogen] if idx in owner]
        if len(heavy) != 1:
            raise PreparationError("hydrogen {} has heavy owners {}".format(hydrogen, heavy))
        owner[heavy[0]]["maestro_atom_indices"].append(hydrogen)
    for group in groups:
        group["maestro_atom_indices"] = sorted(group["maestro_atom_indices"])
```

- [ ] **Step 4: Implement heavy-atom SDF export and graph round-trip validation**

For selected ligand gids:

1. preserve ordered one-based Maestro heavy-atom gids in `atom_index_map.json` as `{"0": gid0, ...}`;
2. extract heavy atoms in that order and write `ligand_graph.sdf`;
3. read the SDF with RDKit `removeHs=False`;
4. compare ordered elements, formal charges, and normalized bond triples `(min_idx, max_idx, order)`;
5. fail before Synergy invocation on any mismatch.

- [ ] **Step 5: Implement single-UNK Synergy mapping and strict remapping**

Resolve Synergy in this order:

1. `--synergy-dir`;
2. `SYNERGY_FRAGMENT_DIR` environment variable.

Require both `residue_map.py` and the default monomer library. Import `build_residue_map` and `ResidueIdentifier` under Schrödinger Python, run against the round-tripped SDF molecule, map zero-based RDKit indices back to one-based Maestro gids, then assign explicit hydrogens by their sole bonded heavy atom.

For each remapped group set `group_name = canonical_resname or display_name or ""`. After assigning analysis residue metadata, add a unique selector object containing `chain`, `resnum`, `inscode`, and `pdbres`; this selector is the authority used to recover the group from each Prime output snapshot.

Store the validated whole-ligand ASL at top level as `analysis_ligand_asl`; it must independently select the union of all group atoms and is used for numerical reconciliation in Prime snapshots.

- [ ] **Step 6: Implement pre-resolved mapping without Synergy**

Group selected ligand atoms by `(chain, resnum, inscode, pdbres)` in structure order. Assign `group_id=P000...`, preserve `group_name=pdbres`, and write the same residue-map schema with `mapper_version=null` and `mode=pre_resolved`.

Before accepting the original CMS as the analysis CMS, validate each `(chain, resnum, inscode, pdbres)` selector against the full solute CT. If it also matches receptor atoms, generate a metadata-only analysis CMS copy with a dedicated ligand chain exactly as in single-UNK mode; never allow selector collision.

- [ ] **Step 7: Generate the non-destructive analysis CMS for single-UNK mode**

Choose the first unused chain from `L`, `B`, `C`, `D`, `E`, then assign each group a unique residue number starting at 1. Use canonical three-letter `canonical_resname` when present; otherwise use `P000...`, `ACE`/`NME` for recognized caps, and `XLK` for crosslinks.

Set `atom.chain`, `atom.resnum`, and `atom.pdbres` through `cms.atom[gid]`, call `cms.synchronize_fsys_ct()`, and write with `cms.write(analysis_cms)`. Re-read the written CMS and verify:

- atom count, elements, bonds, and coordinates match the source;
- group residue metadata is present in both full-system and solute component CTs;
- the generated `analysis_ligand_asl` selects exactly the mapped ligand atoms.

- [ ] **Step 8: Run unit tests and syntax checks**

Run:

```bash
$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py -q
$SCHRODINGER/run python3 -m py_compile \
  skills/science/md-pipeline/scripts/mmgbsa_decomp_contract.py \
  skills/science/md-pipeline/scripts/prepare_ligand_decomp.py
```

Expected: PASS and no syntax errors.

- [ ] **Step 9: Commit preparation support**

```bash
git add skills/science/md-pipeline/scripts/prepare_ligand_decomp.py \
        skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py
git commit -m "feat(md): prepare ligand residue decomp inputs"
```

### Task 5: Aggregate Prime atomic properties by ligand group

**Repository:** Lazy-Plugin isolated worktree

**Files:**
- Create: `skills/science/md-pipeline/scripts/prime_mmgbsa_residue_decomp.py`
- Create: `skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py`

**Interfaces:**
- CLI: `$SCHRODINGER/run prime_mmgbsa_residue_decomp.py -m PRIME_OUT.maegz --map residue_map.json --trajectory DTR --start-frame N --end-frame N --step-size N --frames-out ligand_decomp_frames.csv --summary-out ligand_decomp_summary.csv --manifest decomp_manifest.json [--properties NAME,...]`.
- Produces long-form frame and summary CSVs and transitions the manifest to success or failed.

- [ ] **Step 1: Write failing tests using fake structure/atom objects**

```python
from prime_mmgbsa_residue_decomp import aggregate_snapshot, summarize_rows


class Atom:
    def __init__(self, chain, resnum, pdbres, props):
        self.chain = chain
        self.resnum = resnum
        self.pdbres = pdbres
        self.inscode = ""
        self.property = props


def test_aggregate_snapshot_is_ligand_only_and_reconciles():
    prop = "r_psp_MMGBSA_dG_Bind"
    atoms = [
        Atom("L", 1, "ALA", {prop: -1.5}),
        Atom("L", 1, "ALA", {prop: -0.5}),
        Atom("A", 10, "ASP", {prop: 9.0}),
    ]
    groups = [{"group_id": "P000", "group_name": "ALA",
               "selector": {"chain": "L", "resnum": 1, "pdbres": "ALA"}}]
    rows = aggregate_snapshot(atoms, atoms[:2], groups, {"dG_Bind": prop}, frame=0, time_ps=0.0)
    assert rows == [{"frame": 0, "time_ps": 0.0, "group_id": "P000",
                     "group_name": "ALA", "property": "dG_Bind",
                     "value_kcal_mol": -2.0}]


def test_summary_uses_population_sd_and_sem():
    rows = [
        {"group_id": "P000", "group_name": "ALA", "property": "vdW", "value_kcal_mol": -1.0},
        {"group_id": "P000", "group_name": "ALA", "property": "vdW", "value_kcal_mol": -3.0},
    ]
    summary = summarize_rows(rows)[0]
    assert summary["mean"] == -2.0
    assert summary["sd"] == 1.0
    assert round(summary["sem"], 8) == round(1.0 / (2 ** 0.5), 8)
```

- [ ] **Step 2: Verify tests fail on the missing module**

Run: `$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement strict property lookup and per-snapshot reconciliation**

For each structure from `structure.StructureReader(maegz)`:

1. resolve every group through the stored `(chain, resnum, pdbres)` selector;
2. reject zero matches, group overlap, and selector drift;
3. for every requested property, require the property on every matched ligand atom;
4. sum atom values per group;
5. independently sum all matched ligand atom values and compare to group sums with `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`;
6. append one long-form row per frame/group/property.

Read source times with `schrodinger.application.desmond.packages.traj.read_traj(trajectory)`. Build source frame indices with the inclusive sequence `range(start_frame, end_frame + 1, step_size)`, require its length to equal the number of Prime structures, and use each corresponding trajectory frame's `.time` as `time_ps`.

The receptor must never enter a ligand group, even when it has the same residue number or residue name.

Start the script with discoverable metadata:

```python
#!/usr/bin/env python3
# @name: prime_mmgbsa_residue_decomp
# @description: Sum Prime atomic MM/GBSA properties by ligand residue group
# @requires: schrodinger
# @usage: prime_mmgbsa_residue_decomp.py -m PRIME_OUT.maegz --map residue_map.json --trajectory DTR --start-frame N --end-frame N --step-size N --manifest decomp_manifest.json
```

Implement aggregation around these exact helpers:

```python
class DecompError(RuntimeError):
    pass


def _matches(atom, selector):
    return (
        atom.chain.strip() == selector["chain"].strip()
        and atom.resnum == selector["resnum"]
        and atom.inscode.strip() == selector.get("inscode", "").strip()
        and atom.pdbres.strip() == selector["pdbres"].strip()
    )


def aggregate_snapshot(atoms, ligand_atoms, groups, properties, frame, time_ps):
    rows = []
    matched_atom_ids = set()
    ligand_totals = {
        label: sum(float(atom.property[property_name]) for atom in ligand_atoms)
        for label, property_name in properties.items()
    }
    group_totals = {label: 0.0 for label in properties}
    for group in groups:
        selected = [atom for atom in atoms if _matches(atom, group["selector"])]
        if not selected:
            raise DecompError("frame {} group {} matched zero atoms".format(frame, group["group_id"]))
        for atom in selected:
            identity = id(atom)
            if identity in matched_atom_ids:
                raise DecompError("frame {} duplicate group atom".format(frame))
            matched_atom_ids.add(identity)
        for label, property_name in properties.items():
            missing = [atom for atom in selected if property_name not in atom.property]
            if missing:
                raise DecompError("frame {} group {} missing {}".format(
                    frame, group["group_id"], property_name))
            value = sum(float(atom.property[property_name]) for atom in selected)
            group_totals[label] += value
            rows.append({"frame": frame, "time_ps": time_ps,
                         "group_id": group["group_id"], "group_name": group["group_name"],
                         "property": label, "value_kcal_mol": value})
    expected_atom_ids = {id(atom) for atom in ligand_atoms}
    if matched_atom_ids != expected_atom_ids:
        raise DecompError("frame {} ligand/group atom sets differ".format(frame))
    for label in properties:
        if not math.isclose(group_totals[label], ligand_totals[label], rel_tol=1e-9, abs_tol=1e-6):
            raise DecompError("frame {} property {} reconciliation failed".format(frame, label))
    return rows
```

In production, obtain `ligand_atoms` independently with `schrodinger.structutils.analyze.evaluate_asl(structure, residue_map["analysis_ligand_asl"])`; do not derive them from the group selectors.

- [ ] **Step 4: Implement CSV outputs and summary statistics**

Write frame columns exactly as:

```text
frame,time_ps,group_id,group_name,property,value_kcal_mol
```

Write summary columns exactly as:

```text
group_id,group_name,property,n_frames,mean,sd,sem
```

Sort by group sequence and `DEFAULT_PROPERTIES` order. Write temporary files and replace destinations only after all snapshots pass.

- [ ] **Step 5: Add missing-property, selector-drift, and duplicate-group tests**

Each test must assert `DecompError` includes frame number, group or atom selector, and missing property name. Also assert no final CSV is left after failure.

- [ ] **Step 6: Run aggregation tests and compile under Schrödinger Python**

Run:

```bash
$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py -q
$SCHRODINGER/run python3 -m py_compile \
  skills/science/md-pipeline/scripts/prime_mmgbsa_residue_decomp.py
```

Expected: PASS.

- [ ] **Step 7: Commit the Prime decomp engine**

```bash
git add skills/science/md-pipeline/scripts/prime_mmgbsa_residue_decomp.py \
        skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py
git commit -m "feat(md): summarize Prime energy by ligand residue"
```

### Task 6: Integrate the optional path into run_mmgbsa.sh

**Repository:** Lazy-Plugin isolated worktree

**Files:**
- Modify: `skills/science/md-pipeline/scripts/run_mmgbsa.sh`
- Create: `skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

**Interfaces:**
- Existing invocation remains valid.
- New environment variables: `DECOMP=1`, optional `DECOMP_PROPERTIES`, optional `SYNERGY_FRAGMENT_DIR`.
- Uses preparation result to switch thermal MM/GBSA to the analysis CMS and generated ligand ASL.

- [ ] **Step 1: Add shell tests for unchanged default behavior and opt-in orchestration**

Use the existing `toolenv/tests/helpers.sh` sandbox pattern and fake `$SCHRODINGER/run` executable. Record every fake invocation in a log. The fake preparation call must create `prepare_result.json`, `residue_map.json`, and a placeholder analysis CMS; the fake thermal call must create exactly one expected `*-prime-out.maegz`; the fake aggregation call must create both CSV outputs and mark the test manifest successful.

Assertions:

```bash
test_default_path_does_not_call_decomp_scripts() {
    # DECOMP unset: one thermal_mmgbsa.py call, no prepare/decomp call.
    assert_contains "$calls" "thermal_mmgbsa.py"
    case "$calls" in *prepare_ligand_decomp.py*|*prime_mmgbsa_residue_decomp.py*) fail "unexpected decomp";; esac
}

test_decomp_path_calls_prepare_thermal_then_aggregate() {
    # DECOMP=1: exact call order is prepare -> thermal -> aggregate.
    assert_eq "$call_order" "prepare_ligand_decomp.py thermal_mmgbsa.py prime_mmgbsa_residue_decomp.py"
}
```

- [ ] **Step 2: Run the shell test and verify the opt-in test fails**

Run: `bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh`

Expected: default test PASS; decomp orchestration test FAIL because `DECOMP` is ignored.

- [ ] **Step 3: Add explicit decomp configuration without changing defaults**

At the parameter block add:

```bash
DECOMP="${DECOMP:-0}"
DECOMP_PROPERTIES="${DECOMP_PROPERTIES:-}"
SYNERGY_FRAGMENT_DIR="${SYNERGY_FRAGMENT_DIR:-}"
```

When `DECOMP=1`, run preparation before thermal MM/GBSA. Read `analysis_cms`, `analysis_ligand_asl`, and `residue_map` from `prepare_result.json` with Schrödinger Python JSON parsing, not `eval` or `source`.

The orchestration branch must have this control shape:

```bash
analysis_cms="$cms"
analysis_lig_asl="$LIG_ASL"
manifest="$out/decomp_manifest.json"
prep_extra=()
if [[ -n "$SYNERGY_FRAGMENT_DIR" ]]; then
    prep_extra=(--synergy-dir "$SYNERGY_FRAGMENT_DIR")
fi
if [[ "$DECOMP" == 1 ]]; then
    "$SCHRODINGER/run" "$HERE/prepare_ligand_decomp.py" "$cms" \
        --lig-asl "$LIG_ASL" --out-dir "$out" "${prep_extra[@]}" \
        || return $?
    prep_json="$out/prepare_result.json"
    analysis_cms=$("$SCHRODINGER/run" python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["analysis_cms"])' "$prep_json")
    analysis_lig_asl=$("$SCHRODINGER/run" python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["analysis_ligand_asl"])' "$prep_json")
fi
```

- [ ] **Step 4: Run thermal MM/GBSA against the prepared inputs and invoke aggregation**

Preserve `START`, `END`, `STEP`, `NJOBS`, and `HOST`. Pass the generated ligand ASL in single-UNK mode. After thermal succeeds, locate exactly one `${name}-prime-out.maegz`; fail on zero or multiple matches.

Invoke the decomp CLI with the fixed output paths, the detected main `*_trj` path, and the same `START`, `END`, and `STEP` passed to thermal MM/GBSA. Append `--properties "$DECOMP_PROPERTIES"` only when non-empty.

- [ ] **Step 5: Make failures visible in both exit status and manifest**

`run_one` must return nonzero on preparation, thermal, or aggregation failure. The outer loop accumulates an overall nonzero status instead of ending with the final `echo` status. Before returning, invoke `$SCHRODINGER/run mmgbsa_decomp_contract.py manifest-fail --manifest "$manifest" --stage "$stage" --return-code "$rc" --log "$log_file"` to mark the running manifest failed.

- [ ] **Step 6: Run shell, syntax, and existing env compatibility tests**

Run:

```bash
bash -n skills/science/md-pipeline/scripts/run_mmgbsa.sh
bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh
bash skills/science/md-pipeline/tests/test_env_shim.sh
```

Expected: all PASS.

- [ ] **Step 7: Commit the single-entry integration**

```bash
git add skills/science/md-pipeline/scripts/run_mmgbsa.sh \
        skills/science/md-pipeline/tests/test_run_mmgbsa.sh
git commit -m "feat(md): add optional ligand residue decomp"
```

### Task 7: Add optional Synergy discovery and agent-facing routing docs

**Repository:** Lazy-Plugin isolated worktree

**Files:**
- Create: `toolenv/tools.d/synergy-fragment.sh`
- Modify: `toolenv/tests/test_manifests.sh`
- Modify: `skills/science/md-pipeline/SKILL.md`
- Modify: `skills/science/md-pipeline/references/troubleshooting.md`

**Interfaces:**
- `toolenv which synergy-fragment` returns the Synergy-Fragment directory.
- Activation exports `SYNERGY_FRAGMENT_DIR`.
- Missing Synergy only blocks single-UNK decomp, never normal MM/GBSA or pre-resolved decomp.

- [ ] **Step 1: Add `synergy-fragment` to the manifest compliance test and verify failure**

Change:

```bash
EXPECTED="ambertools automd conda plip rdkit schrodinger synergy-fragment"
```

Run: `bash toolenv/tests/run_tests.sh test_manifests.sh`

Expected: FAIL because the manifest is absent.

- [ ] **Step 2: Create the optional tool manifest**

```bash
TOOL_NAME="synergy-fragment"
TOOL_DESC="Synergy-Fragment peptide residue atom mapper"
TOOL_HINT="Set SYNERGY_FRAGMENT_DIR or TOOLENV_SYNERGY_FRAGMENT to a Synergy-Fragment checkout"
tool_detect() {
    try_env SYNERGY_FRAGMENT_DIR
    try_env TOOLENV_SYNERGY_FRAGMENT
    try_glob --require residue_map.py \
        "$HOME/workstations/Synergy/Synergy-Fragment" \
        "$HOME/Synergy/Synergy-Fragment" \
        "/opt/Synergy/Synergy-Fragment"
}
tool_activate() {
    echo "export SYNERGY_FRAGMENT_DIR=$1"
}
```

- [ ] **Step 3: Document the agent routing table in SKILL.md**

Add a concise table with these exact decisions:

| Ligand form | Command | Synergy required | Expected grouping |
|---|---|---|---|
| One `UNK` residue | `DECOMP=1 LIG_ASL='res.ptype UNK' run_mmgbsa.sh DIR` | Yes | `Pnnn` + caps + `XLINK_nnn` |
| Pre-resolved peptide chain | `DECOMP=1 LIG_ASL='<chain/component ASL>' run_mmgbsa.sh DIR` | No | Existing ligand residues |
| Total MM/GBSA only | `run_mmgbsa.sh DIR` | No | No residue decomp |

Tell agents to read `decomp_manifest.json` first, then `ligand_decomp_summary.csv`, and to treat unknown naming as a warning but non-100% atom coverage as failure.

- [ ] **Step 4: Add troubleshooting entries for all strict failures**

Document exact diagnosis commands for:

- ligand ASL selecting zero or multiple molecules;
- Synergy not found;
- SDF/CMS graph mismatch;
- missing or duplicate atom ownership;
- missing Prime property;
- `Lig_Strain` unavailable on a particular Prime output;
- selector drift after Prime;
- group-sum reconciliation failure.

- [ ] **Step 5: Run manifest tests and verify script discovery**

Run:

```bash
bash toolenv/tests/run_tests.sh test_manifests.sh
./toolenv/toolenv index skills/science/md-pipeline/scripts | grep -E 'prepare_ligand_decomp|prime_mmgbsa_residue_decomp|run_mmgbsa'
```

Expected: manifest tests PASS and all three scripts appear with descriptions and usage.

- [ ] **Step 6: Commit discovery and documentation**

```bash
git add toolenv/tools.d/synergy-fragment.sh toolenv/tests/test_manifests.sh \
        skills/science/md-pipeline/SKILL.md \
        skills/science/md-pipeline/references/troubleshooting.md
git commit -m "docs(md): route ligand residue decomposition"
```

### Task 8: Run real Schrödinger smoke tests and final verification

**Repositories:** Both isolated worktrees; no generated artifact is committed

**Files:**
- Verify only; update implementation files only if a test exposes a defect, and add a regression test before the fix.

**Interfaces:**
- Consumes current NPR data from `/home/huangshengjie/workstations/NPR/complex-MD`.
- Produces disposable smoke-test output outside both repositories.

- [ ] **Step 1: Verify the single-UNK mapping on the current 257-atom ligand**

Run the preparation CLI against:

```text
/home/huangshengjie/workstations/NPR/complex-MD/NPR1-SYN-007714-16473-md/NPR1-SYN-007714-16473-md-out.cms
```

with `--lig-asl 'res.ptype UNK'` and an output directory created by `mktemp -d`.

Expected map invariants:

```text
mode = single_unk
residue groups = 13
N cap = 1
C cap = 1
crosslink groups = 1
heavy atoms = 124
explicit hydrogens = 133
total assigned atoms = 257
missing atoms = 0
duplicate atoms = 0
```

- [ ] **Step 2: Re-read the analysis CMS and compare immutable chemistry**

Use Schrödinger Python to compare source and analysis CMS ligand atom count, ordered elements, bonds, coordinates, and force-field atom types. Expected: all identical; only chain/residue metadata differs.

- [ ] **Step 3: Run thermal MM/GBSA on one or two snapshots**

Use a new output directory and `START=0 END=1 STEP=1 NJOBS=1 DECOMP=1`. Do not reuse or overwrite the full production MM/GBSA directory.

Expected:

- thermal MM/GBSA exits 0;
- one `*-prime-out.maegz` exists;
- all default atomic properties are present, including `r_psp_Lig_Strain_Energy`;
- frame and summary CSVs are non-empty;
- manifest status is `success`;
- every property reconciles for every tested frame.

- [ ] **Step 4: Run a pre-resolved peptide smoke test without Synergy**

Use a small CMS whose ligand has at least two residue records. Temporarily unset `SYNERGY_FRAGMENT_DIR` and run `DECOMP=1`.

Expected: `mode=pre_resolved`, no Synergy import or CLI call, 100% atom ownership, and successful summary output.

- [ ] **Step 5: Run complete regression suites**

Synergy worktree:

```bash
python3 -m pytest Synergy-Fragment/test_residue_map.py Synergy-Fragment/test_peptide_sequence.py -q
```

Lazy-Plugin worktree:

```bash
python3 -m pytest skills/science/md-pipeline/tests/test_mmgbsa_decomp_contract.py -q
$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prepare_ligand_decomp.py -q
$SCHRODINGER/run python3 -m pytest skills/science/md-pipeline/tests/test_prime_mmgbsa_residue_decomp.py -q
bash skills/science/md-pipeline/tests/test_run_mmgbsa.sh
bash skills/science/md-pipeline/tests/test_env_shim.sh
bash toolenv/tests/run_tests.sh
./toolenv/toolenv selftest
```

Expected: all PASS.

- [ ] **Step 6: Review diffs and repository boundaries**

Run `git diff --check`, `git status --short`, and `git log --oneline` in each worktree. Confirm:

- no production NPR artifacts are tracked;
- no primary-worktree user changes are included;
- no second peptide segmentation implementation exists in Lazy-Plugin;
- all new scripts are discoverable by metadata;
- each repository contains only its planned commits.

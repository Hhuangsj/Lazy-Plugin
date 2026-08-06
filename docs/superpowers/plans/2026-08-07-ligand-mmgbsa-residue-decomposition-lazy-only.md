# Ligand MM/GBSA Residue Decomposition — Lazy-Only Plan

> Supersedes the 2026-08-06 implementation plan. Synergy-Fragment is strictly read-only.

**Goal:** Add an opt-in `DECOMP=1` path to Lazy-Plugin that decomposes a single-`UNK` peptide ligand through a Lazy-owned adapter over existing Synergy-Fragment APIs, or directly reuses pre-resolved ligand residues, then summarizes Prime atomic MM/GBSA properties by ligand group.

**Architecture:** Lazy-Plugin owns the adapter, schema, CMS mapping, hydrogen ownership, analysis CMS, orchestration, aggregation, and documentation. For single-UNK ligands, the adapter runs with the normal development Python and imports the existing read-only `peptide_sequence` APIs. Schrödinger Python never imports Synergy. `run_mmgbsa.sh` is the sole user entry and remains unchanged unless `DECOMP=1`.

## Global Constraints

- Modify only the isolated Lazy-Plugin worktree; do not modify, commit, cherry-pick, or merge any Synergy repository file.
- Existing experimental commits on `feat/mmgbsa-residue-map` are excluded from the deliverable.
- Reuse `peptide_sequence.enumerate_backbones`, `order_residues`, `analyze_components_detailed`, `sequence_peptide`, and `ResidueIdentifier`; do not copy their peptide-recognition or backbone-search algorithms.
- The Lazy adapter may assemble residue/cap/crosslink ownership from those existing API results because Synergy cannot be changed.
- RDKit indices are zero-based; Maestro indices are one-based. Name index spaces explicitly.
- Source CMS and trajectory are read-only; only an analysis CMS copy may change residue metadata.
- `DECOMP` false/unset preserves existing MM/GBSA behavior.
- Canonical names require exact-stereo matches for the 19 chiral L residues; Gly is achiral. D, modified, N-methyl, and stereo-ambiguous residues stay noncanonical. Unknown naming warns but does not fail.
- Missing/duplicate ownership, graph mismatch, detached components, missing Prime properties, selector drift, and numerical reconciliation fail closed.
- Every ligand atom, including explicit H, belongs to exactly one `Pnnn`, cap, or `XLINK_nnn` group.
- Use `statistics.pstdev`; `SEM = SD / sqrt(n)`.
- Do not commit CMS, trajectories, maegz, logs, smoke outputs, or other generated NPR data.

### Task 1: Build the Lazy read-only Synergy adapter

**Files:**
- Create `skills/science/md-pipeline/scripts/synergy_residue_adapter.py`
- Create `skills/science/md-pipeline/tests/test_synergy_residue_adapter.py`

**Interface:** `synergy_residue_adapter.py --sdf INPUT --output OUTPUT --synergy-dir DIR [--library CSV]`. The input is exactly one heavy-atom molecule. Output is UTF-8, sorted-key, indented schema-v1 JSON written atomically. The script runs under normal `python3`, not `$SCHRODINGER/run`.

Requirements:

1. Add failing tests for two standard L residues, D residue protection, unknown warning, Ac/NH2 cap groups, a crosslink group, free-acid O ownership, exact partition, empty/multiple SDF rejection, deterministic output, and error exit 2 without a success JSON.
2. Resolve `--synergy-dir` read-only; prepend it and its sibling `dashboard` directory to `sys.path` only for imports. Require `peptide_sequence.py` and the default `monomer_library_nonstandard_segments_simple.csv`.
3. Import and reuse `enumerate_backbones`, `order_residues`, `analyze_components_detailed`, `sequence_peptide`, and `ResidueIdentifier`. Do not reproduce their graph-recognition algorithms.
4. Emit top-level `schema_version=1`, `status=ok`, `source_atom_count`, `groups`, `warnings`, `unassigned_atom_indices`, `duplicate_atom_indices`, `topology`, and `mapper_version="lazy-synergy-adapter/1"`.
5. Residues are `P000...`; N/C caps are `N_CAP`/`C_CAP`; crosslink/disulfide components are `XLINK_000...`. Crosslink atoms cannot remain in residue groups. Detached components fail.
6. Group fields include `group_id`, `group_type`, zero-based `rdkit_atom_indices`, `sequence_index`, `display_name`, `canonical_resname`, `recognition_status`, `residue_smiles`, and `connected_group_ids`.
7. Canonical mapping is the 20 standard one/three-letter names; only exact-stereo chiral matches qualify, except Gly. Unknown, stereo-inexact, and crosslinked statuses are warnings but keep `status=ok`.
8. Validate every input RDKit atom index is in range and appears exactly once.
9. Run:
   `PYTHONPATH=Synergy-Fragment:dashboard python3 -m pytest skills/science/md-pipeline/tests/test_synergy_residue_adapter.py -q`
10. Commit only these two files as `feat(md): add read-only Synergy residue adapter`.

### Task 2: Define the shared decomp contract and manifest

**Files:** create `scripts/mmgbsa_decomp_contract.py` and `tests/test_mmgbsa_decomp_contract.py` under `skills/science/md-pipeline`.

Expose `DEFAULT_PROPERTIES`, `ContractError`, `validate_maestro_partition`, `atomic_write_json`, `load_json`, `initialize_manifest`, and `update_manifest`. Add CLI `manifest-fail --manifest FILE --stage STAGE --return-code RC --log FILE`.

Use this exact ordered mapping: `dG_Bind=r_psp_MMGBSA_dG_Bind`, `Coulomb=r_psp_MMGBSA_dG_Bind(NS)_Coulomb`, `Solv_GB=r_psp_MMGBSA_dG_Bind(NS)_Solv_GB`, `Covalent=r_psp_MMGBSA_dG_Bind(NS)_Covalent`, `vdW=r_psp_MMGBSA_dG_Bind(NS)_vdW`, `Hbond=r_psp_MMGBSA_dG_Bind(NS)_Hbond`, `Lipo=r_psp_MMGBSA_dG_Bind(NS)_Lipo`, `Packing=r_psp_MMGBSA_dG_Bind(NS)_Packing`, `SelfCont=r_psp_MMGBSA_dG_Bind(NS)_SelfCont`, `Lig_Strain=r_psp_Lig_Strain_Energy`.

Validate one-based ligand atom set and multiplicity. JSON writes use a sibling temp plus `os.replace`. Manifest transitions are `None->{running}`, `running->{running,success,failed}`, and terminal success/failed. Initialize schema 1 with running status, paths, ASL, frames, properties and versions; structured failures record stage, numeric return code and log. Test red then green and commit as `feat(md): define MMGBSA decomp contract`.

### Task 3: Prepare single-UNK and pre-resolved ligand mappings

**Files:** create `scripts/prepare_ligand_decomp.py` and `tests/test_prepare_ligand_decomp.py`.

CLI: `$SCHRODINGER/run prepare_ligand_decomp.py CMS --lig-asl ASL --out-dir DIR [--synergy-dir DIR] [--adapter-python PYTHON]`. Produce `residue_map.json`, `atom_index_map.json`, `ligand_graph.sdf`, optional analysis CMS, `prepare_result.json`, and running/success-or-failed manifest state.

Requirements:

1. Pure helpers: `detect_mode_from_residues`, `remap_rdkit_groups`, and `assign_hydrogens`; test single UNK vs pre-resolved, zero→one-based mapping, sole-heavy-owner H assignment, and strict partition.
2. `topo.read_cms`, `cms.select_atom`, and `cms.getMoleculeAtoms()` must prove the ASL is one complete molecule.
3. Export selected heavy atoms in fixed order; persist `{rdkit_index: maestro_gid}`; re-read SDF and compare ordered elements, charges, and normalized bonds before mapping.
4. Single-UNK invokes the Lazy adapter as a subprocess using `--adapter-python` or `SYNERGY_ADAPTER_PYTHON` or `python3`, passing the read-only Synergy directory from argument/env. Schrödinger Python must not import Synergy modules.
5. Map adapter zero-based heavy indices to one-based Maestro gids; assign explicit H by sole bonded heavy owner; validate 100% exact ownership.
6. Pre-resolved mode groups `(chain,resnum,inscode,pdbres)` in structure order as `P000...`, preserves names, and never invokes Synergy. Resolve selector collisions with receptor by writing an analysis CMS copy.
7. Single-UNK writes a metadata-only analysis CMS copy. Choose first unused chain from `L,B,C,D,E`; assign group residue numbers from 1; use canonical names, otherwise stable `Pnnn`, `ACE`/`NME`, or `XLK`. Re-read and verify atoms, chemistry, coordinates, metadata, and ligand ASL.
8. Store unique selectors and an independent `analysis_ligand_asl` selecting the group union.
9. Run focused Schrödinger pytest and py_compile; commit as `feat(md): prepare ligand residue decomp inputs`.

### Task 4: Aggregate Prime atomic properties by ligand group

**Files:** create `scripts/prime_mmgbsa_residue_decomp.py` and `tests/test_prime_mmgbsa_residue_decomp.py`.

CLI consumes Prime maegz, residue map, trajectory, inclusive start/end/step, frame CSV, summary CSV, manifest and optional property list. For each snapshot, resolve every group by stored selector, reject zero/overlap/drift, require every property on every selected ligand atom, and reconcile group sums against an independent ligand ASL sum with `math.isclose(rel_tol=1e-9, abs_tol=1e-6)`.

Read times from `traj.read_traj`; selected source indices are `range(start,end+1,step)` and must match Prime structure count. Frame columns: `frame,time_ps,group_id,group_name,property,value_kcal_mol`. Summary columns: `group_id,group_name,property,n_frames,mean,sd,sem`. Sort by group and default property order; atomically replace outputs only after all snapshots pass. Test ligand-only behavior, population SD/SEM, missing property, selector drift, duplicate groups, no output on failure. Run focused Schrödinger tests and commit as `feat(md): summarize Prime energy by ligand residue`.

### Task 5: Integrate `DECOMP=1` into `run_mmgbsa.sh`

**Files:** modify `scripts/run_mmgbsa.sh`; create `tests/test_run_mmgbsa.sh`.

Add defaults `DECOMP=0`, optional `DECOMP_PROPERTIES`, `SYNERGY_FRAGMENT_DIR`, and `SYNERGY_ADAPTER_PYTHON`. When false, preserve the exact existing command/output path. When true, call prepare → thermal MM/GBSA → aggregate, reading `prepare_result.json` with Schrödinger Python JSON parsing. Pass the analysis CMS/ASL and the same frame range. Require exactly one expected `*-prime-out.maegz`. Propagate each failure and transition the manifest to failed with stage/return-code/log. Shell tests use fake Schrödinger executables to prove default behavior and exact call order. Run shell syntax, focused shell tests, and `test_env_shim.sh`; commit as `feat(md): add optional ligand residue decomp`.

### Task 6: Add read-only Synergy discovery and documentation

**Files:** create `toolenv/tools.d/synergy-fragment.sh`; modify `toolenv/tests/test_manifests.sh`, `skills/science/md-pipeline/SKILL.md`, and `references/troubleshooting.md`.

Manifest returns a Synergy-Fragment directory only when `peptide_sequence.py` and the default monomer library exist; activation exports `SYNERGY_FRAGMENT_DIR`. It never requires `residue_map.py` and never changes Synergy. Missing Synergy blocks only single-UNK decomp.

Document routing: one UNK → `DECOMP=1`, Synergy required, `Pnnn`/caps/XLINK; pre-resolved → `DECOMP=1`, no Synergy; total only → default command. Agents read manifest then summary; unknown is warning, incomplete coverage is failure. Document ASL cardinality, missing Synergy, graph mismatch, ownership, property, Lig_Strain, selector drift, and reconciliation diagnosis. Run toolenv tests/index; commit as `docs(md): route ligand residue decomposition`.

### Task 7: Run real smoke tests and final verification

No generated artifact is committed. On `NPR1-SYN-007714-16473-md-out.cms`, single-UNK preparation must yield 13 residues, one N cap, one C cap, one XLINK, 124 heavy atoms, 133 explicit H, 257 assigned, zero missing/duplicate. Re-read source/analysis chemistry and coordinates. Run one or two snapshot thermal MM/GBSA in a disposable directory and require all ten properties, nonempty CSVs, success manifest, and reconciliation. Run pre-resolved smoke without Synergy. Run all new Lazy focused tests, `test_env_shim.sh`, toolenv tests/selftest, `git diff --check`, status/log checks, and confirm no Synergy commit is part of the deliverable.


#!/usr/bin/env python3
"""
PROA/PROB interaction analysis for trajectory using PLIP.

Workflow:
1. Detect trajectory frame count:
   - <=1000 frames: analyze all frames
   - >1000 frames: analyze every 10th frame
2. Convert sampled trajectory to PDB frames.
   - GROMACS style: structure (tpr/gro/pdb) + xtc via gmx trjconv
   - Schrodinger/Desmond: direct sampled PDB export (recommended) or xtc fallback
3. Run PLIP per frame for inter-chain (A/B) interactions.
4. Summarize interaction types and residue-pair occupancies.
5. Generate required figures.

--------------------------------------------------------------------------
本仓库扩展(在原始 PROA/PROB 脚本基础上新增,向后兼容,默认行为不变):
  --last-ns N          只分析轨迹最后 N ns(schrodinger 模式,0=全轨迹)。
  --ligand-chain C     导出帧时把 UNK 肽配体重贴到链 C(与受体链区分)。
  --no-plip-peptides   不给 PLIP 传 --peptides。用于「肽=单个 UNK 残基」
                       (AutoMD 建模)的体系:此时 PLIP peptide 模式选不到
                       原子(实测 0 相互作用),必须让 PLIP 把 UNK 当配体自动识别。

典型用法(AutoMD 修饰肽体系,受体在链 A、肽是 UNK,后 100ns 全分辨率):
  plip_interaction_analysis.py --trajectory-type schrodinger \
      --cms X-out.cms --trj-dir X_trj \
      --schrodinger-keep-asl "protein or res.ptype UNK" \
      --ligand-chain B --chain-a B --chain-b A \
      --no-plip-peptides --last-ns 100 --jobs 8 --output-dir X/plip_last100ns
封装见 run_plip.sh。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import math
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

import matplotlib.pyplot as plt
import pandas as pd


INTERACTION_TAGS = {
    "hydrophobic": ("hydrophobic_interactions", "hydrophobic_interaction"),
    "hbond": ("hydrogen_bonds", "hydrogen_bond"),
    "water_bridge": ("water_bridges", "water_bridge"),
    "salt_bridge": ("salt_bridges", "salt_bridge"),
    "pi_stack": ("pi_stacks", "pi_stack"),
    "pi_cation": ("pi_cation_interactions", "pi_cation_interaction"),
    "halogen_bond": ("halogen_bonds", "halogen_bond"),
    "metal_complex": ("metal_complexes", "metal_complex"),
}


def run_cmd(cmd: Sequence[str], input_text: str | None = None) -> str:
    result = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({' '.join(cmd)}):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result.stdout + result.stderr


def get_total_frames(xtc: Path) -> int:
    output = run_cmd(["gmx", "check", "-f", str(xtc)])
    match = re.search(r"Last frame\s+(\d+)\s+time", output)
    if not match:
        raise RuntimeError("Failed to parse frame count from gmx check output.")
    last_frame_idx = int(match.group(1))
    return last_frame_idx + 1


def sample_stride(total_frames: int) -> int:
    return 1 if total_frames <= 1000 else 10


def get_total_frames_schrodinger(cms_file: Path, trj_dir: Path) -> int:
    schrodinger_root = os.environ.get("SCHRODINGER", "").strip()
    if not schrodinger_root:
        raise RuntimeError("SCHRODINGER environment variable is not set.")
    sch_run = Path(schrodinger_root).resolve() / "run"
    if not sch_run.exists():
        raise FileNotFoundError(f"Schrodinger run launcher not found: {sch_run}")

    py_code = (
        "from schrodinger.application.desmond.packages import topo, traj;"
        f"cms={repr(str(cms_file))};"
        f"trjdir={repr(str(trj_dir))};"
        "topo.read_cms(cms);"
        "tr=traj.read_traj(trjdir);"
        "print(f'TOTAL_FRAMES={len(tr)}')"
    )
    out = run_cmd([str(sch_run), "python3", "-c", py_code])
    match = re.search(r"TOTAL_FRAMES=(\d+)", out)
    if not match:
        raise RuntimeError(f"Failed to parse Schrodinger frame count. Output:\n{out}")
    return int(match.group(1))


def get_frames_and_dt_schrodinger(cms_file: Path, trj_dir: Path) -> Tuple[int, float]:
    """Return (n_frames, dt_ns) for a Schrodinger trajectory (uniform spacing)."""
    schrodinger_root = os.environ.get("SCHRODINGER", "").strip()
    if not schrodinger_root:
        raise RuntimeError("SCHRODINGER environment variable is not set.")
    sch_run = Path(schrodinger_root).resolve() / "run"
    py_code = (
        "from schrodinger.application.desmond.packages import topo, traj;"
        f"cms={repr(str(cms_file))};"
        f"trjdir={repr(str(trj_dir))};"
        "topo.read_cms(cms);"
        "tr=traj.read_traj(trjdir);"
        "print(f'NF={len(tr)} T0={tr[0].time} T1={tr[-1].time}')"
    )
    out = run_cmd([str(sch_run), "python3", "-c", py_code])
    m = re.search(r"NF=(\d+)\s+T0=([\d.eE+-]+)\s+T1=([\d.eE+-]+)", out)
    if not m:
        raise RuntimeError(f"Failed to parse Schrodinger frame times. Output:\n{out}")
    n = int(m.group(1))
    t0_ps = float(m.group(2))
    t1_ps = float(m.group(3))
    dt_ns = ((t1_ps - t0_ps) / (n - 1) / 1000.0) if n > 1 else 0.0
    return n, dt_ns


def extract_sampled_frames_schrodinger(
    cms_file: Path,
    trj_dir: Path,
    frame_dir: Path,
    stride: int,
    keep_asl: str = "protein",
    start_idx: int = 0,
    ligand_chain: str = "",
    ligand_relabel_asl: str = "res.ptype UNK",
) -> List[Path]:
    schrodinger_root = os.environ.get("SCHRODINGER", "").strip()
    if not schrodinger_root:
        raise RuntimeError("SCHRODINGER environment variable is not set.")
    sch_run = Path(schrodinger_root).resolve() / "run"
    if not sch_run.exists():
        raise FileNotFoundError(f"Schrodinger run launcher not found: {sch_run}")

    frame_dir.mkdir(parents=True, exist_ok=True)
    py_code = f"""
from pathlib import Path
from schrodinger.application.desmond.packages import topo, traj
from schrodinger.structure import StructureWriter
from schrodinger.structutils import analyze
import sys

cms = {repr(str(cms_file))}
trjdir = {repr(str(trj_dir))}
outdir = {repr(str(frame_dir))}
stride = {int(stride)}
start_idx = {int(start_idx)}
keep_asl = {repr(str(keep_asl))}
ligand_chain = {repr(str(ligand_chain))}
ligand_relabel_asl = {repr(str(ligand_relabel_asl))}

_msys, cms_model = topo.read_cms(cms)
trajectory = traj.read_traj(trjdir)
fsys_ct = cms_model.fsys_ct.copy()
keep_atoms = analyze.evaluate_asl(fsys_ct, keep_asl)
if not keep_atoms:
    sys.exit("ASL selected no atoms")

# Positions (1-based, within the extracted structure) of the ligand atoms to
# relabel. Resolved once on the topology: extract() preserves keep_atoms order.
relabel_pos = []
if ligand_chain and ligand_relabel_asl:
    lig_atoms = set(analyze.evaluate_asl(fsys_ct, ligand_relabel_asl))
    if not lig_atoms:
        sys.exit("ligand relabel ASL selected no atoms: " + ligand_relabel_asl)
    relabel_pos = [i + 1 for i, a in enumerate(keep_atoms) if a in lig_atoms]
    if not relabel_pos:
        sys.exit("ligand relabel ASL selected no atoms inside keep_asl")

Path(outdir).mkdir(parents=True, exist_ok=True)
for idx in range(start_idx, len(trajectory), stride):
    frame = trajectory[idx]
    topo.update_fsys_ct_from_frame_GF(fsys_ct, cms_model, frame)
    out_st = fsys_ct.extract(keep_atoms)
    if relabel_pos:
        # Relabel the peptide/ligand atoms to their own chain so PLIP/chain
        # detection can distinguish them from the receptor protein chain(s).
        for pos in relabel_pos:
            out_st.atom[pos].chain = ligand_chain
    out_path = Path(outdir) / f"frame_{{idx+1:05d}}.pdb"
    with StructureWriter(str(out_path)) as w:
        w.append(out_st)
"""
    run_cmd([str(sch_run), "python3", "-c", py_code])

    frame_files = sorted(frame_dir.glob("frame_*.pdb"))
    if not frame_files:
        raise RuntimeError("No Schrodinger-exported PDB frames found.")
    return frame_files


def convert_schrodinger_to_gromacs_inputs(
    cms_file: Path,
    trj_dir: Path,
    output_dir: Path,
    basename: str,
) -> Tuple[Path, Path]:
    """
    Convert Schrodinger/Desmond trajectory to gromacs-style inputs (xtc + pdb).

    Returns:
        Tuple[topology_pdb, xtc_file]
    """
    schrodinger_root = os.environ.get("SCHRODINGER", "").strip()
    if not schrodinger_root:
        raise RuntimeError("SCHRODINGER environment variable is not set.")

    schrodinger_root_path = Path(schrodinger_root).resolve()
    sch_run = schrodinger_root_path / "run"
    trj_convert = schrodinger_root_path / "internal/bin/trj_convert.py"
    if not sch_run.exists():
        raise FileNotFoundError(f"Schrodinger run launcher not found: {sch_run}")
    if not trj_convert.exists():
        raise FileNotFoundError(f"Schrodinger trj_convert.py not found: {trj_convert}")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_prefix = output_dir / basename
    xtc_file = out_prefix.with_suffix(".xtc")
    topology_pdb = output_dir / f"{basename}_topology.pdb"

    if not xtc_file.exists():
        print("[info] Converting Schrodinger trajectory to XTC ...")
        run_cmd(
            [
                str(sch_run),
                "python3",
                str(trj_convert),
                str(cms_file),
                str(out_prefix),
                "-t",
                str(trj_dir),
                "-output-trajectory-format",
                "xtc",
            ]
        )
    else:
        print(f"[info] Reusing existing converted XTC: {xtc_file}")

    if not topology_pdb.exists():
        print("[info] Exporting topology PDB from CMS ...")
        py_code = (
            "from schrodinger.application.desmond.packages import topo;"
            "from schrodinger.structure import StructureWriter;"
            f"cms={repr(str(cms_file))};"
            f"out={repr(str(topology_pdb))};"
            "msys,cms_model=topo.read_cms(cms);"
            "st=cms_model.fsys_ct.copy();"
            "w=StructureWriter(out);"
            "w.append(st);"
            "w.close();"
            "print(out)"
        )
        run_cmd([str(sch_run), "python3", "-c", py_code])
    else:
        print(f"[info] Reusing existing topology PDB: {topology_pdb}")

    return topology_pdb, xtc_file


def extract_sampled_multimodel_pdb(
    top: Path,
    xtc: Path,
    output_pdb: Path,
    stride: int,
    selection_group: str = "Protein",
) -> None:
    cmd = ["gmx", "trjconv", "-s", str(top), "-f", str(xtc), "-o", str(output_pdb)]
    if stride > 1:
        cmd.extend(["-skip", str(stride)])
    run_cmd(cmd, input_text=f"{selection_group}\n")


def split_models(multimodel_pdb: Path, frame_dir: Path) -> List[Path]:
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_files: List[Path] = []

    with multimodel_pdb.open("r", encoding="utf-8", errors="ignore") as fh:
        header_lines: List[str] = []
        model_lines: List[str] = []
        model_id = None
        before_first_model = True

        for line in fh:
            if line.startswith("MODEL"):
                before_first_model = False
                if model_id is not None and model_lines:
                    out_path = frame_dir / f"frame_{model_id:05d}.pdb"
                    write_model(out_path, header_lines, model_lines, model_id)
                    frame_files.append(out_path)
                parts = line.split()
                model_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else len(frame_files) + 1
                model_lines = []
            elif line.startswith("ENDMDL"):
                if model_id is None:
                    model_id = len(frame_files) + 1
                out_path = frame_dir / f"frame_{model_id:05d}.pdb"
                write_model(out_path, header_lines, model_lines, model_id)
                frame_files.append(out_path)
                model_id = None
                model_lines = []
            else:
                if before_first_model:
                    header_lines.append(line)
                elif model_id is not None:
                    model_lines.append(line)

        # Fallback: single model PDB without MODEL/ENDMDL
        if not frame_files and header_lines:
            out_path = frame_dir / "frame_00001.pdb"
            out_path.write_text("".join(header_lines), encoding="utf-8")
            frame_files.append(out_path)

    return frame_files


def write_model(out_path: Path, header_lines: Iterable[str], model_lines: Iterable[str], model_id: int) -> None:
    clean_body = [
        ln
        for ln in model_lines
        if not ln.startswith("MODEL") and not ln.startswith("ENDMDL")
    ]
    text = "".join(header_lines) + "MODEL        1\n" + "".join(clean_body) + "ENDMDL\n"
    if not text.endswith("\n"):
        text += "\n"
    out_path.write_text(text, encoding="utf-8")


def parse_interactions_from_xml(
    xml_path: Path,
    group_a: Set[str],
    group_b: Set[str],
    peptide_backbone_atom_indices: Set[int] | None = None,
    exclude_peptide_backbone: bool = False,
    peptide_atoms: List[Tuple[str, float, float, float]] | None = None,
) -> List[Dict[str, str]]:
    chain_set = set(group_a) | set(group_b)
    root = ET.parse(xml_path).getroot()
    rows: List[Dict[str, str]] = []

    for bs in root.findall("bindingsite"):
        interactions = bs.find("interactions")
        if interactions is None:
            continue

        for itype, (container_tag, item_tag) in INTERACTION_TAGS.items():
            container = interactions.find(container_tag)
            if container is None:
                continue

            for item in container.findall(item_tag):
                lig_chain = text_of(item, "reschain_lig")
                rec_chain = text_of(item, "reschain")
                if lig_chain not in chain_set or rec_chain not in chain_set:
                    continue
                if lig_chain == rec_chain:
                    continue
                if not (
                    (lig_chain in group_a and rec_chain in group_b)
                    or (lig_chain in group_b and rec_chain in group_a)
                ):
                    continue

                if lig_chain in group_a:
                    peptide_side = "ligand"
                elif rec_chain in group_a:
                    peptide_side = "protein"
                else:
                    peptide_side = "ligand"

                peptide_backbone_hit = False
                if itype == "hbond":
                    # sidechain field describes protein sidechain only; for peptide backbone
                    # exclusion we map hbond coordinates back to peptide atom names.
                    peptide_backbone_hit = hbond_peptide_backbone_hit_by_coo(
                        item=item,
                        peptide_side=peptide_side,
                        peptide_atoms=peptide_atoms or [],
                    )
                    if exclude_peptide_backbone and peptide_backbone_hit:
                        continue

                lig_res = format_residue(
                    lig_chain, text_of(item, "restype_lig"), text_of(item, "resnr_lig")
                )
                rec_res = format_residue(rec_chain, text_of(item, "restype"), text_of(item, "resnr"))
                pair = canonical_pair(lig_res, rec_res)

                rows.append(
                    {
                        "interaction_type": itype,
                        "ligand_residue": lig_res,
                        "receptor_residue": rec_res,
                        "pair": pair,
                        "peptide_backbone_atom": peptide_backbone_hit,
                    }
                )

    return rows


def text_of(elem: ET.Element, tag: str) -> str:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def format_residue(chain: str, aa: str, resnr: str) -> str:
    return f"{chain}:{aa}{resnr}"


def canonical_pair(res1: str, res2: str) -> str:
    return " -- ".join(sorted([res1, res2]))


def parse_chain_group(spec: str) -> List[str]:
    s = spec.strip()
    if not s:
        return []
    if any(sep in s for sep in [",", ";", " ", "\t"]):
        items = [x.strip() for x in re.split(r"[,;\s]+", s) if x.strip()]
    else:
        # Support compact form like "ABC" -> ["A", "B", "C"].
        items = list(s) if len(s) > 1 else [s]
    deduped = list(dict.fromkeys(items))
    return deduped


def pair_matches_chains(pair: str, chain_x: str, chain_y: str) -> bool:
    parts = [p.strip() for p in pair.split("--")]
    if len(parts) != 2:
        return False
    chains = {p.split(":")[0].strip() for p in parts if ":" in p}
    return chains == {chain_x, chain_y}


def detect_chains_from_pdb(frame_pdb: Path) -> List[str]:
    chains: List[str] = []
    seen: Set[str] = set()
    with frame_pdb.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            chain = line[21].strip() if len(line) > 21 else ""
            if chain and chain not in seen:
                seen.add(chain)
                chains.append(chain)
    return chains


def _safe_int(text: str) -> int | None:
    try:
        return int(str(text).strip())
    except Exception:
        return None


def peptide_backbone_atom_names() -> Set[str]:
    return {
        "N",
        "CA",
        "C",
        "O",
        "OXT",
        "OT1",
        "OT2",
        "H",
        "HN",
        "H1",
        "H2",
        "H3",
        "HT1",
        "HT2",
        "HT3",
        "HA",
        "HA1",
        "HA2",
        "HA3",
    }


def _safe_float(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except Exception:
        return None


def detect_peptide_backbone_atom_indices(frame_pdb: Path, peptide_chains: Set[str]) -> Set[int]:
    # Peptide backbone atoms including backbone H atoms.
    backbone_names = peptide_backbone_atom_names()
    idx_set: Set[int] = set()
    with frame_pdb.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.startswith("ATOM"):
                continue
            chain = line[21].strip() if len(line) > 21 else ""
            if chain not in peptide_chains:
                continue
            atom_name = line[12:16].strip() if len(line) >= 16 else ""
            if atom_name not in backbone_names:
                continue
            serial = _safe_int(line[6:11].strip() if len(line) >= 11 else "")
            if serial is not None:
                idx_set.add(serial)
    return idx_set


def load_peptide_atoms(frame_pdb: Path, peptide_chains: Set[str]) -> List[Tuple[str, float, float, float]]:
    atoms: List[Tuple[str, float, float, float]] = []
    with frame_pdb.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if not line.startswith("ATOM"):
                continue
            chain = line[21].strip() if len(line) > 21 else ""
            if chain not in peptide_chains:
                continue
            atom_name = line[12:16].strip() if len(line) >= 16 else ""
            x = _safe_float(line[30:38].strip() if len(line) >= 38 else "")
            y = _safe_float(line[38:46].strip() if len(line) >= 46 else "")
            z = _safe_float(line[46:54].strip() if len(line) >= 54 else "")
            if x is None or y is None or z is None or not atom_name:
                continue
            atoms.append((atom_name, x, y, z))
    return atoms


def hbond_peptide_backbone_hit_by_coo(
    item: ET.Element,
    peptide_side: str,
    peptide_atoms: List[Tuple[str, float, float, float]],
    max_dist: float = 0.8,
) -> bool:
    # For hydrogen bonds, determine peptide-involved atom by spatially mapping
    # reported interaction coordinate to nearest peptide atom in protonated PDB.
    if not peptide_atoms:
        return False
    coo_tag = "ligcoo" if peptide_side == "ligand" else "protcoo"
    coo = item.find(coo_tag)
    if coo is None:
        return False
    x = _safe_float(text_of(coo, "x"))
    y = _safe_float(text_of(coo, "y"))
    z = _safe_float(text_of(coo, "z"))
    if x is None or y is None or z is None:
        return False

    nearest_name = ""
    nearest_d2 = float("inf")
    for atom_name, ax, ay, az in peptide_atoms:
        d2 = (ax - x) ** 2 + (ay - y) ** 2 + (az - z) ** 2
        if d2 < nearest_d2:
            nearest_d2 = d2
            nearest_name = atom_name
    if nearest_name == "":
        return False
    if math.sqrt(nearest_d2) > max_dist:
        return False
    return nearest_name in peptide_backbone_atom_names()


def _extract_idx_list(parent: ET.Element, list_tag: str) -> List[int]:
    out: List[int] = []
    node = parent.find(list_tag)
    if node is None:
        return out
    for sub in node.findall("idx"):
        if sub.text:
            iv = _safe_int(sub.text)
            if iv is not None:
                out.append(iv)
    return out


def extract_side_atom_indices(
    item: ET.Element, interaction_type: str, side: str
) -> List[int]:
    assert side in ("ligand", "protein")
    if interaction_type == "hydrogen_bond":
        protisdon = text_of(item, "protisdon").lower() == "true"
        donor = _safe_int(text_of(item, "donoridx"))
        acceptor = _safe_int(text_of(item, "acceptoridx"))
        if side == "ligand":
            idx = acceptor if protisdon else donor
        else:
            idx = donor if protisdon else acceptor
        return [idx] if idx is not None else []

    if interaction_type == "hydrophobic":
        tag = "ligcarbonidx" if side == "ligand" else "protcarbonidx"
        idx = _safe_int(text_of(item, tag))
        return [idx] if idx is not None else []

    # For interaction types using atom index lists.
    list_tag = "lig_idx_list" if side == "ligand" else "prot_idx_list"
    idxs = _extract_idx_list(item, list_tag)
    if idxs:
        return idxs

    # Fallback for scalar tags containing lig/prot + idx.
    out: List[int] = []
    for child in item:
        tag = child.tag.lower()
        if side == "ligand":
            match_side = "lig" in tag
        else:
            match_side = "prot" in tag
        if not match_side or "idx" not in tag:
            continue
        if child.text:
            iv = _safe_int(child.text)
            if iv is not None:
                out.append(iv)
    return out


def analyze_single_frame(
    frame_pdb: Path,
    plip_dir: Path,
    peptide_chains: List[str],
    group_a: Set[str],
    group_b: Set[str],
    plip_maxthreads: int,
    exclude_peptide_backbone: bool = False,
    use_plip_peptides: bool = True,
) -> Tuple[str, List[Dict[str, str]]]:
    frame_id = frame_pdb.stem
    frame_out = plip_dir / frame_id
    frame_out.mkdir(parents=True, exist_ok=True)

    xml_candidates = sorted(frame_out.glob("*_report.xml"))
    if not xml_candidates:
        cmd = [
            "plip",
            "-f",
            str(frame_pdb),
            "-o",
            str(frame_out),
            "-xv",
            "--silent",
        ]
        if use_plip_peptides:
            # PLIP peptide mode: treat chain-a as a peptide ligand. Only works
            # when the peptide is a proper residue chain. For single-residue
            # UNK ligands, disable this so PLIP auto-detects UNK as the ligand.
            cmd.extend(["--peptides", *peptide_chains])
        if plip_maxthreads > 0:
            cmd.extend(["--maxthreads", str(plip_maxthreads)])
        run_cmd(cmd)
        xml_candidates = sorted(frame_out.glob("*_report.xml"))
    if not xml_candidates:
        return frame_id, []

    xml_path = xml_candidates[0]
    protonated_candidates = sorted(frame_out.glob("*_protonated.pdb"))
    backbone_ref_pdb = protonated_candidates[0] if protonated_candidates else frame_pdb
    peptide_backbone_atom_indices = detect_peptide_backbone_atom_indices(backbone_ref_pdb, group_a)
    peptide_atoms = load_peptide_atoms(backbone_ref_pdb, group_a)
    frame_events = parse_interactions_from_xml(
        xml_path=xml_path,
        group_a=group_a,
        group_b=group_b,
        peptide_backbone_atom_indices=peptide_backbone_atom_indices,
        exclude_peptide_backbone=exclude_peptide_backbone,
        peptide_atoms=peptide_atoms,
    )
    for ev in frame_events:
        ev["frame_id"] = frame_id
    return frame_id, frame_events


def save_events_csv(events: List[Dict[str, str]], output_csv: Path) -> None:
    if not events:
        with output_csv.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=[
                    "frame_id",
                    "interaction_type",
                    "ligand_residue",
                    "receptor_residue",
                    "pair",
                    "peptide_backbone_atom",
                ],
            )
            writer.writeheader()
        return

    with output_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "frame_id",
                "interaction_type",
                "ligand_residue",
                "receptor_residue",
                "pair",
                "peptide_backbone_atom",
            ],
        )
        writer.writeheader()
        writer.writerows(events)


def compute_summaries(
    events: List[Dict[str, str]],
    total_frames: int,
    chains: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    type_event_count: Counter[str] = Counter()
    type_frame_presence: defaultdict[str, Set[str]] = defaultdict(set)
    chain_type_frame_presence: defaultdict[Tuple[str, str], Set[str]] = defaultdict(set)
    pair_frame_presence: defaultdict[str, Set[str]] = defaultdict(set)
    residue_type_frame_presence: defaultdict[Tuple[str, str, str], Set[str]] = defaultdict(set)

    for ev in events:
        frame_id = ev["frame_id"]
        itype = ev["interaction_type"]
        type_event_count[itype] += 1
        type_frame_presence[itype].add(frame_id)
        pair_frame_presence[ev["pair"]].add(frame_id)

        lig_chain = ev["ligand_residue"].split(":")[0]
        rec_chain = ev["receptor_residue"].split(":")[0]
        chain_type_frame_presence[(lig_chain, itype)].add(frame_id)
        chain_type_frame_presence[(rec_chain, itype)].add(frame_id)
        residue_type_frame_presence[(lig_chain, ev["ligand_residue"], itype)].add(frame_id)
        residue_type_frame_presence[(rec_chain, ev["receptor_residue"], itype)].add(frame_id)

    all_types = list(INTERACTION_TAGS.keys())
    type_rows = []
    for itype in all_types:
        frames_with_type = len(type_frame_presence[itype])
        type_rows.append(
            {
                "interaction_type": itype,
                "event_count": int(type_event_count[itype]),
                "frames_with_interaction": frames_with_type,
                "occupancy": frames_with_type / total_frames if total_frames else 0.0,
                "occupancy_percent": 100.0 * frames_with_type / total_frames if total_frames else 0.0,
            }
        )
    type_df = pd.DataFrame(type_rows).sort_values("occupancy", ascending=False)

    chain_rows = []
    for chain in chains:
        for itype in all_types:
            frames_with = len(chain_type_frame_presence[(chain, itype)])
            chain_rows.append(
                {
                    "chain": chain,
                    "interaction_type": itype,
                    "frames_with_interaction": frames_with,
                    "occupancy": frames_with / total_frames if total_frames else 0.0,
                    "occupancy_percent": 100.0 * frames_with / total_frames if total_frames else 0.0,
                }
            )
    chain_df = pd.DataFrame(chain_rows)

    pair_rows = []
    for pair, frame_set in pair_frame_presence.items():
        frames_with = len(frame_set)
        pair_rows.append(
            {
                "residue_pair": pair,
                "frames_with_interaction": frames_with,
                "occupancy": frames_with / total_frames if total_frames else 0.0,
                "occupancy_percent": 100.0 * frames_with / total_frames if total_frames else 0.0,
            }
        )
    pair_df = pd.DataFrame(
        pair_rows,
        columns=[
            "residue_pair",
            "frames_with_interaction",
            "occupancy",
            "occupancy_percent",
        ],
    )
    if not pair_df.empty:
        pair_df = pair_df.sort_values("occupancy", ascending=False)

    residue_rows = []
    for (chain, residue, itype), frame_set in residue_type_frame_presence.items():
        frames_with = len(frame_set)
        residue_rows.append(
            {
                "chain": chain,
                "residue": residue,
                "interaction_type": itype,
                "frames_with_interaction": frames_with,
                "occupancy": frames_with / total_frames if total_frames else 0.0,
                "occupancy_percent": 100.0 * frames_with / total_frames if total_frames else 0.0,
            }
        )
    residue_df = pd.DataFrame(
        residue_rows,
        columns=[
            "chain",
            "residue",
            "interaction_type",
            "frames_with_interaction",
            "occupancy",
            "occupancy_percent",
        ],
    )
    if not residue_df.empty:
        residue_df = residue_df.sort_values(["chain", "occupancy"], ascending=[True, False])

    return type_df, chain_df, pair_df, residue_df


def plot_chain_type_occupancy(chain_df: pd.DataFrame, output_png: Path) -> None:
    pivot_df = (
        chain_df.pivot(index="chain", columns="interaction_type", values="occupancy_percent")
        .fillna(0.0)
        .sort_index()
    )
    interaction_order = [t for t in INTERACTION_TAGS.keys() if t in pivot_df.columns]
    pivot_df = pivot_df[interaction_order]

    colors = [
        "#4C78A8",
        "#F58518",
        "#54A24B",
        "#E45756",
        "#72B7B2",
        "#B279A2",
        "#FF9DA6",
        "#9D755D",
    ][: len(pivot_df.columns)]

    fig, ax = plt.subplots(figsize=(8, 5))
    pivot_df.plot(kind="bar", stacked=True, ax=ax, color=colors, width=0.65)
    ax.set_ylabel("Occupancy over analyzed frames (%)")
    ax.set_xlabel("Chain")
    ax.set_title("PROA/PROB interaction occupancy by interaction type")
    ax.legend(title="Interaction type", bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_png, dpi=300)
    plt.close(fig)


def plot_chain_residue_occupancy(
    residue_df: pd.DataFrame, chain: str, output_png: Path, top_n: int = 40
) -> None:
    chain_df = residue_df[residue_df["chain"] == chain].copy()
    if chain_df.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, f"No residue interactions detected on chain {chain}", ha="center", va="center")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
        return

    pivot_df = (
        chain_df.pivot_table(
            index="residue",
            columns="interaction_type",
            values="occupancy_percent",
            aggfunc="max",
            fill_value=0.0,
        )
        .fillna(0.0)
    )
    pivot_df["__total__"] = pivot_df.sum(axis=1)
    pivot_df = pivot_df.sort_values("__total__", ascending=False).head(top_n)
    pivot_df = pivot_df.drop(columns=["__total__"])

    interaction_order = [t for t in INTERACTION_TAGS.keys() if t in pivot_df.columns]
    pivot_df = pivot_df[interaction_order]

    n = len(pivot_df)
    height = max(5, min(0.35 * n + 1.5, 24))
    colors = [
        "#4C78A8",
        "#F58518",
        "#54A24B",
        "#E45756",
        "#72B7B2",
        "#B279A2",
        "#FF9DA6",
        "#9D755D",
    ][: len(pivot_df.columns)]

    fig, ax = plt.subplots(figsize=(10, height))
    pivot_df.plot(kind="barh", stacked=True, ax=ax, color=colors, width=0.8)
    ax.invert_yaxis()
    ax.set_xlabel("Occupancy over analyzed frames (%)")
    ax.set_ylabel(f"Residues on chain {chain}")
    ax.set_title(f"Chain {chain} residue interaction occupancy by interaction type")
    ax.legend(title="Interaction type", bbox_to_anchor=(1.02, 1), loc="upper left")
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_png, dpi=300)
    plt.close(fig)


def plot_residue_pair_bars(pair_df: pd.DataFrame, output_png: Path, title: str) -> None:
    if pair_df.empty:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No residue-pair interactions detected", ha="center", va="center")
        ax.set_axis_off()
        fig.tight_layout()
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
        return

    n = len(pair_df)
    height = max(5, min(0.35 * n + 1.5, 28))
    fig, ax = plt.subplots(figsize=(10, height))
    ax.barh(pair_df["residue_pair"], pair_df["occupancy_percent"], color="#4C78A8", alpha=0.9)
    ax.invert_yaxis()
    ax.set_xlabel("Occupancy over analyzed frames (%)")
    ax.set_ylabel("Residue pair")
    ax.set_title(title)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_png, dpi=300)
    plt.close(fig)


def compute_pair_occupancy_from_events(events: List[Dict[str, str]], total_frames: int) -> pd.DataFrame:
    pair_frame_presence: defaultdict[str, Set[str]] = defaultdict(set)
    for ev in events:
        pair = str(ev.get("pair", ""))
        frame_id = str(ev.get("frame_id", ""))
        if pair and frame_id:
            pair_frame_presence[pair].add(frame_id)

    rows = []
    for pair, frame_set in pair_frame_presence.items():
        frames_with = len(frame_set)
        rows.append(
            {
                "residue_pair": pair,
                "frames_with_interaction": frames_with,
                "occupancy": frames_with / total_frames if total_frames else 0.0,
                "occupancy_percent": 100.0 * frames_with / total_frames if total_frames else 0.0,
            }
        )
    df = pd.DataFrame(
        rows,
        columns=[
            "residue_pair",
            "frames_with_interaction",
            "occupancy",
            "occupancy_percent",
        ],
    )
    if not df.empty:
        df = df.sort_values("occupancy", ascending=False)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="PLIP-based PROA/PROB interaction analysis.")
    parser.add_argument(
        "--trajectory-type",
        choices=["gromacs", "converted", "schrodinger"],
        default="gromacs",
        help="Trajectory source type. Use 'converted' for Schrodinger-converted xtc+pdb/gro.",
    )
    parser.add_argument(
        "--top",
        default="md.tpr",
        help="Structure file passed to gmx trjconv -s (supports tpr/gro/pdb).",
    )
    parser.add_argument(
        "--tpr",
        default="",
        help="Deprecated alias for --top (kept for compatibility).",
    )
    parser.add_argument("--xtc", default="md.xtc", help="GROMACS trajectory file.")
    parser.add_argument("--cms", default="", help="Schrodinger CMS file for schrodinger mode.")
    parser.add_argument("--trj-dir", default="", help="Schrodinger trajectory directory for schrodinger mode.")
    parser.add_argument(
        "--schrodinger-frame-source",
        choices=["pdb", "xtc"],
        default="pdb",
        help="For schrodinger mode: direct sampled PDB export (pdb, recommended) or convert to xtc first.",
    )
    parser.add_argument(
        "--schrodinger-keep-asl",
        default="protein",
        help="ASL when exporting direct PDB frames in schrodinger mode, e.g. 'protein' or 'protein or ligand'.",
    )
    parser.add_argument(
        "--chain-a",
        default="A",
        help="Chain/group A. Supports A or comma form like A,B,C or compact ABC.",
    )
    parser.add_argument(
        "--chain-b",
        default="",
        help="Optional receptor chain/group B. If omitted, auto-detect all non-chain-a chains as receptor.",
    )
    parser.add_argument(
        "--output-dir",
        default="plip_proa_prob_analysis",
        help="Output directory for all results.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="Residue-pair occupancy threshold (percent) for filtered plot.",
    )
    parser.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="Keep extracted PDB frames and per-frame PLIP folders.",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Frame-level parallel workers for PLIP (recommended: 4-16).",
    )
    parser.add_argument(
        "--plip-maxthreads",
        type=int,
        default=1,
        help="PLIP internal threads per frame (use 1 when --jobs > 1).",
    )
    parser.add_argument(
        "--exclude-peptide-backbone",
        action="store_true",
        help="Exclude interactions where peptide(chain-a) uses backbone heavy atoms.",
    )
    parser.add_argument(
        "--last-ns",
        type=float,
        default=0.0,
        help="Only analyze the last N ns of the trajectory (0 = whole trajectory). "
             "schrodinger mode only.",
    )
    parser.add_argument(
        "--no-plip-peptides",
        action="store_true",
        help="Do not pass --peptides to PLIP. Use when the peptide is a single "
             "UNK residue ligand (AutoMD modeling), so PLIP auto-detects it as ligand.",
    )
    parser.add_argument(
        "--ligand-chain",
        default="",
        help="schrodinger mode: relabel the UNK peptide ligand to this chain id "
             "on export (e.g. 'B'), so it is a distinct chain from the receptor.",
    )
    parser.add_argument(
        "--ligand-relabel-asl",
        default="res.ptype UNK",
        help="schrodinger mode: ASL picking the atoms moved to --ligand-chain. "
             "Default matches AutoMD's single-UNK-residue ligand; use e.g. "
             "'chain.name \" \"' when the peptide is a normal residue chain.",
    )
    args = parser.parse_args()
    if args.jobs < 1:
        raise ValueError("--jobs must be >= 1.")
    if args.plip_maxthreads < 1:
        raise ValueError("--plip-maxthreads must be >= 1.")

    chains_a = parse_chain_group(args.chain_a)
    chains_b_input = parse_chain_group(args.chain_b)
    if not chains_a:
        raise ValueError("--chain-a is empty after parsing.")
    overlap = set(chains_a) & set(chains_b_input)
    if overlap:
        raise ValueError(f"--chain-a and --chain-b overlap: {sorted(overlap)}")
    group_a = set(chains_a)
    output_dir = Path(args.output_dir).resolve()
    frames_dir = output_dir / "frames_pdb"
    plip_dir = output_dir / "plip_outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    sampled_multimodel: Path | None = None
    if args.trajectory_type in ("gromacs", "converted"):
        top_input = args.tpr.strip() if args.tpr.strip() else args.top.strip()
        top = Path(top_input).resolve()
        xtc = Path(args.xtc).resolve()
        if not top.exists():
            raise FileNotFoundError(f"Structure file not found: {top}")
        if not xtc.exists():
            raise FileNotFoundError(f"XTC not found: {xtc}")
        total_frames = get_total_frames(xtc)
        stride = sample_stride(total_frames)
        analyzed_expected = (total_frames + stride - 1) // stride

        sampled_multimodel = output_dir / "sampled_frames.pdb"
        extract_sampled_multimodel_pdb(top, xtc, sampled_multimodel, stride, selection_group="Protein")
        frame_files = split_models(sampled_multimodel, frames_dir)
    else:
        if not args.cms.strip():
            raise ValueError("In schrodinger mode, --cms is required.")
        if not args.trj_dir.strip():
            raise ValueError("In schrodinger mode, --trj-dir is required.")
        cms_file = Path(args.cms).resolve()
        trj_dir = Path(args.trj_dir).resolve()
        if not cms_file.exists():
            raise FileNotFoundError(f"CMS not found: {cms_file}")
        if not trj_dir.exists():
            raise FileNotFoundError(f"Trajectory directory not found: {trj_dir}")

        total_frames, dt_ns = get_frames_and_dt_schrodinger(cms_file, trj_dir)
        start_idx = 0
        if args.last_ns and args.last_ns > 0:
            if dt_ns <= 0:
                raise RuntimeError("Cannot apply --last-ns: failed to determine frame spacing.")
            n_keep = int(round(args.last_ns / dt_ns))
            start_idx = max(0, total_frames - n_keep)
            print(f"[info] --last-ns={args.last_ns}: dt={dt_ns:.4f} ns/frame, "
                  f"keeping frames {start_idx}..{total_frames - 1} "
                  f"(~{(total_frames - 1 - start_idx) * dt_ns:.1f} ns window)")
        window_frames = total_frames - start_idx
        stride = sample_stride(window_frames)
        analyzed_expected = (window_frames + stride - 1) // stride
        if args.schrodinger_frame_source == "pdb":
            frame_files = extract_sampled_frames_schrodinger(
                cms_file=cms_file,
                trj_dir=trj_dir,
                frame_dir=frames_dir,
                stride=stride,
                keep_asl=args.schrodinger_keep_asl,
                start_idx=start_idx,
                ligand_chain=args.ligand_chain,
                ligand_relabel_asl=args.ligand_relabel_asl,
            )
        else:
            converted_dir = output_dir / "converted_for_gmx"
            converted_top, converted_xtc = convert_schrodinger_to_gromacs_inputs(
                cms_file=cms_file,
                trj_dir=trj_dir,
                output_dir=converted_dir,
                basename=cms_file.stem.replace("-out", ""),
            )
            sampled_multimodel = output_dir / "sampled_frames.pdb"
            extract_sampled_multimodel_pdb(
                converted_top,
                converted_xtc,
                sampled_multimodel,
                stride,
                selection_group="Protein",
            )
            frame_files = split_models(sampled_multimodel, frames_dir)

    if not frame_files:
        raise RuntimeError("No frame PDB files were generated.")

    if chains_b_input:
        chains_b = chains_b_input
    else:
        detected = detect_chains_from_pdb(frame_files[0])
        chains_b = [c for c in detected if c not in group_a]
        if not chains_b:
            raise RuntimeError(
                "Failed to auto-detect receptor chains. Please provide --chain-b explicitly."
            )
        print(f"[info] Auto-detected receptor chains: {','.join(chains_b)}")

    chains = list(dict.fromkeys(chains_a + chains_b))
    group_b = set(chains_b)

    all_events: List[Dict[str, str]] = []
    plip_dir.mkdir(parents=True, exist_ok=True)
    if args.jobs == 1:
        for idx, frame_pdb in enumerate(frame_files, start=1):
            _, frame_events = analyze_single_frame(
                frame_pdb=frame_pdb,
                plip_dir=plip_dir,
                peptide_chains=chains_a,
                group_a=group_a,
                group_b=group_b,
                plip_maxthreads=args.plip_maxthreads,
                exclude_peptide_backbone=args.exclude_peptide_backbone,
                use_plip_peptides=not args.no_plip_peptides,
            )
            all_events.extend(frame_events)
            if idx % 25 == 0:
                print(f"[progress] analyzed {idx}/{len(frame_files)} frames")
    else:
        completed = 0
        with cf.ProcessPoolExecutor(max_workers=args.jobs) as executor:
            futures = [
                executor.submit(
                    analyze_single_frame,
                    frame_pdb,
                    plip_dir,
                    chains_a,
                    group_a,
                    group_b,
                    args.plip_maxthreads,
                    args.exclude_peptide_backbone,
                    not args.no_plip_peptides,
                )
                for frame_pdb in frame_files
            ]
            for fut in cf.as_completed(futures):
                _, frame_events = fut.result()
                all_events.extend(frame_events)
                completed += 1
                if completed % 25 == 0 or completed == len(frame_files):
                    print(f"[progress] analyzed {completed}/{len(frame_files)} frames")

    total_analyzed = len(frame_files)
    events_csv = output_dir / "interaction_events.csv"
    save_events_csv(all_events, events_csv)

    type_df, chain_df, pair_df, residue_df = compute_summaries(all_events, total_analyzed, chains)
    type_df.to_csv(output_dir / "interaction_type_summary.csv", index=False)
    chain_df.to_csv(output_dir / "chain_type_occupancy.csv", index=False)
    pair_df.to_csv(output_dir / "residue_pair_occupancy.csv", index=False)
    residue_df.to_csv(output_dir / "chain_residue_type_occupancy.csv", index=False)

    for chain in chains:
        plot_chain_residue_occupancy(
            residue_df,
            chain,
            output_dir / f"plot_chain{chain}_residue_occupancy.png",
        )
    # Keep legacy filename and point it to first chain in group A.
    plot_chain_residue_occupancy(
        residue_df,
        chains_a[0],
        output_dir / "plot_chain_type_occupancy.png",
    )
    # Hbond-only pair occupancy (without peptide-backbone exclusion).
    hbond_events = [ev for ev in all_events if ev.get("interaction_type") == "hbond"]
    pair_df_hbond_all = compute_pair_occupancy_from_events(hbond_events, total_analyzed)
    pair_df_hbond_all.to_csv(output_dir / "residue_pair_occupancy_hbond_all.csv", index=False)
    plot_residue_pair_bars(
        pair_df_hbond_all,
        output_dir / "plot_residue_pair_occupancy_hbond_all.png",
        title="Hydrogen-bond pair occupancy (all hbonds)",
    )
    # Additional "all pairs" plot excluding peptide backbone atom participation.
    filtered_events = [ev for ev in all_events if not ev.get("peptide_backbone_atom", False)]
    type_df_no_backbone, chain_df_no_backbone, pair_df_no_backbone, residue_df_no_backbone = compute_summaries(
        filtered_events, total_analyzed, chains
    )
    type_df_no_backbone.to_csv(output_dir / "interaction_type_summary_no_peptide_backbone.csv", index=False)
    chain_df_no_backbone.to_csv(output_dir / "chain_type_occupancy_no_peptide_backbone.csv", index=False)
    pair_df_no_backbone.to_csv(output_dir / "residue_pair_occupancy_no_peptide_backbone.csv", index=False)
    residue_df_no_backbone.to_csv(output_dir / "chain_residue_type_occupancy_no_peptide_backbone.csv", index=False)
    # Keep legacy-style no-backbone figure names requested by user.
    plot_chain_residue_occupancy(
        residue_df_no_backbone,
        chains_a[0],
        output_dir / "plot_chain_type_occupancy_no_peptide_backbone.png",
    )
    for chain in chains:
        plot_chain_residue_occupancy(
            residue_df_no_backbone,
            chain,
            output_dir / f"plot_chain{chain}_residue_occupancy_no_peptide_backbone.png",
        )
    hbond_events_no_backbone = [
        ev for ev in filtered_events if ev.get("interaction_type") == "hbond"
    ]
    pair_df_hbond_no_backbone = compute_pair_occupancy_from_events(
        hbond_events_no_backbone, total_analyzed
    )
    pair_df_hbond_no_backbone.to_csv(
        output_dir / "residue_pair_occupancy_hbond_no_peptide_backbone.csv", index=False
    )
    plot_residue_pair_bars(
        pair_df_hbond_no_backbone,
        output_dir / "plot_residue_pair_occupancy_hbond_no_peptide_backbone.png",
        title="Hydrogen-bond pair occupancy (exclude peptide backbone)",
    )
    pair_ge = pair_df[pair_df["occupancy_percent"] >= args.threshold].copy()
    plot_residue_pair_bars(
        pair_ge,
        output_dir / f"plot_residue_pair_occupancy_ge{int(args.threshold)}.png",
        title=f"Residue-pair interaction occupancy (>= {args.threshold:.1f}%)",
    )
    # One figure per chain-pair across groups, e.g., D vs A/B/C.
    for ca in chains_a:
        for cb in chains_b:
            pair_subset = (
                pair_df[pair_df["residue_pair"].apply(lambda x: pair_matches_chains(str(x), ca, cb))]
                .copy()
            )
            if not pair_subset.empty:
                pair_subset = pair_subset.sort_values("occupancy", ascending=False)
            plot_residue_pair_bars(
                pair_subset,
                output_dir / f"plot_residue_pair_occupancy_{ca}_vs_{cb}.png",
                title=f"Residue-pair interaction occupancy ({ca} vs {cb})",
            )

    meta = {
        "trajectory_type": args.trajectory_type,
        "schrodinger_frame_source": args.schrodinger_frame_source if args.trajectory_type == "schrodinger" else "",
        "total_frames_in_source": total_frames,
        "sampling_stride": stride,
        "expected_analyzed_frames": analyzed_expected,
        "actual_analyzed_frames": total_analyzed,
        "chains_a": ",".join(chains_a),
        "chains_b": ",".join(chains_b),
        "chains_all": ",".join(chains),
        "total_detected_events": len(all_events),
        "events_after_excluding_peptide_backbone": len(filtered_events),
    }
    pd.DataFrame([meta]).to_csv(output_dir / "run_metadata.csv", index=False)

    if not args.keep_intermediate:
        if sampled_multimodel is not None and sampled_multimodel.exists():
            sampled_multimodel.unlink()
        if frames_dir.exists():
            shutil.rmtree(frames_dir)

    print("Analysis complete.")
    print(f"Output directory: {output_dir}")
    print(f"Frames in source trajectory: {total_frames}, stride: {stride}, analyzed: {total_analyzed}")
    print(f"Total events detected: {len(all_events)}")


if __name__ == "__main__":
    main()

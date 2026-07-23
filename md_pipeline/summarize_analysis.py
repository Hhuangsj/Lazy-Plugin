#!/usr/bin/env python3
"""
summarize_analysis.py — 汇总多个 MD 体系的 MMGBSA ΔG 与 PLIP 相互作用占据率,
输出一张合并表(CSV)和一张表格图片(PNG)。

读取每个 MD 目录下(由 run_mmgbsa.sh / run_plip.sh 产生):
  <dir>/mmgbsa_last100ns/<name>-prime-out.csv   列 r_psp_MMGBSA_dG_Bind(逐帧)
  <dir>/plip_last100ns/interaction_type_summary.csv   列 interaction_type, occupancy_percent

用法:
  summarize_analysis.py DIR [DIR ...] --out-csv summary.csv --out-png summary.png
  # 自定义标签(与目录一一对应,逗号分隔;缺省用目录名):
  summarize_analysis.py DIR1 DIR2 --labels "RecA 起点,RecA P9-Mod01" ...
  # 自定义子目录名:--mmgbsa-dir mmgbsa_last100ns --plip-dir plip_last100ns

依赖:pandas + matplotlib(md conda 环境已含)。中文标签需要系统有中文字体,
缺失时自动回退英文列名,不影响数值。
"""
from __future__ import annotations

import argparse
import csv
import math
import statistics as st
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


INTERACTIONS = [
    ("hydrophobic", "Hydrophobic %"),
    ("hbond", "H-bond %"),
    ("salt_bridge", "Salt-bridge %"),
    ("pi_cation", "Pi-cation %"),
    ("pi_stack", "Pi-stack %"),
    ("halogen_bond", "Halogen %"),
]


def read_mmgbsa(csv_path: Path):
    vals = []
    if not csv_path.exists():
        return vals
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            v = row.get("r_psp_MMGBSA_dG_Bind", "")
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    return vals


def read_occupancy(csv_path: Path):
    occ = {}
    if not csv_path.exists():
        return occ
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            try:
                occ[row["interaction_type"]] = float(row["occupancy_percent"])
            except (KeyError, TypeError, ValueError):
                pass
    return occ


def build_rows(dirs, labels, mmgbsa_dir, plip_dir):
    rows = []
    for d, lab in zip(dirs, labels):
        d = Path(d).resolve()
        # MMGBSA 输出以 cms 基名命名(可能 != 文件夹名),故用 glob 找,不假设文件夹名。
        mm_csvs = sorted((d / mmgbsa_dir).glob("*-prime-out.csv"))
        dg = read_mmgbsa(mm_csvs[0]) if mm_csvs else []
        occ = read_occupancy(d / plip_dir / "interaction_type_summary.csv")
        n = len(dg)
        mean = sum(dg) / n if n else math.nan
        sd = st.pstdev(dg) if n > 1 else 0.0
        sem = sd / math.sqrt(n) if n else math.nan
        row = {
            "system": lab,
            "folder": d.name,
            "n_frames_mmgbsa": n,
            "dG_bind_mean": mean,
            "dG_bind_sd": sd,
            "dG_bind_sem": sem,
        }
        for key, _ in INTERACTIONS:
            row[f"occ_{key}"] = occ.get(key, math.nan)
        rows.append(row)
    return rows


def write_csv(rows, out_csv: Path):
    fields = list(rows[0].keys())
    with out_csv.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def fmt(v, spec):
    return "-" if (v is None or (isinstance(v, float) and math.isnan(v))) else format(v, spec)


def render_png(rows, out_png: Path, title: str):
    headers = ["System", "ΔG_bind\n(kcal/mol)", "±SEM", "SD"] + [h for _, h in INTERACTIONS]
    table = []
    for r in rows:
        table.append([
            r["system"],
            fmt(r["dG_bind_mean"], "+.1f"),
            fmt(r["dG_bind_sem"], ".1f"),
            fmt(r["dG_bind_sd"], ".1f"),
            *[fmt(r[f"occ_{k}"], ".1f") for k, _ in INTERACTIONS],
        ])

    ncol = len(headers)
    fig_w = 1.7 + 1.15 * ncol
    fig_h = 1.1 + 0.55 * (len(table) + 1)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.axis("off")
    ax.set_title(title, fontweight="bold", pad=14, fontsize=12)

    # 第一列(体系名)加宽,其余等宽
    weights = [2.4] + [1.0] * (ncol - 1)
    total_w = sum(weights)
    col_widths = [w / total_w for w in weights]
    tbl = ax.table(cellText=table, colLabels=headers, loc="center", cellLoc="center",
                   colWidths=col_widths)
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(10)
    tbl.scale(1, 1.6)

    # 找 ΔG 最负(最强)的体系高亮
    dgs = [r["dG_bind_mean"] for r in rows if not math.isnan(r["dG_bind_mean"])]
    best = min(dgs) if dgs else None

    for (r, c), cell in tbl.get_celld().items():
        cell.set_edgecolor("#cccccc")
        if r == 0:
            cell.set_facecolor("#2f4b7c")
            cell.set_text_props(color="white", fontweight="bold")
        else:
            row = rows[r - 1]
            base = "#f2f5fa" if r % 2 else "#ffffff"
            cell.set_facecolor(base)
            if c == 0:
                cell.set_text_props(fontweight="bold")
            if c == 1 and best is not None and row["dG_bind_mean"] == best:
                cell.set_facecolor("#d7ecd9")
                cell.set_text_props(fontweight="bold", color="#1a7a2e")

    fig.tight_layout()
    fig.savefig(out_png, dpi=200, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="汇总 MMGBSA + PLIP 占据率成表(CSV + PNG)。")
    ap.add_argument("dirs", nargs="+", help="MD 体系目录")
    ap.add_argument("--labels", default="", help="逗号分隔的显示标签,与目录一一对应")
    ap.add_argument("--mmgbsa-dir", default="mmgbsa_last100ns")
    ap.add_argument("--plip-dir", default="plip_last100ns")
    ap.add_argument("--out-csv", default="analysis_summary.csv")
    ap.add_argument("--out-png", default="analysis_summary.png")
    ap.add_argument("--title", default="MMGBSA ΔG_bind & PLIP interaction occupancy (last 100 ns)")
    args = ap.parse_args()

    if args.labels.strip():
        labels = [s.strip() for s in args.labels.split(",")]
        if len(labels) != len(args.dirs):
            ap.error(f"--labels 数量({len(labels)})与目录数({len(args.dirs)})不一致")
    else:
        labels = [Path(d).resolve().name for d in args.dirs]

    rows = build_rows(args.dirs, labels, args.mmgbsa_dir, args.plip_dir)
    write_csv(rows, Path(args.out_csv))
    render_png(rows, Path(args.out_png), args.title)

    print(f"[ok] 表格 CSV: {args.out_csv}")
    print(f"[ok] 表格 PNG: {args.out_png}")
    print()
    hdr = f"{'System':<20}{'dG_bind':>10}{'SEM':>7}{'SD':>7}  " + \
          "".join(f"{h.split()[0]:>10}" for _, h in INTERACTIONS)
    print(hdr)
    for r in rows:
        line = f"{r['system']:<20}{fmt(r['dG_bind_mean'],'+.1f'):>10}{fmt(r['dG_bind_sem'],'.1f'):>7}" \
               f"{fmt(r['dG_bind_sd'],'.1f'):>7}  " + \
               "".join(f"{fmt(r[f'occ_{k}'],'.1f'):>10}" for k, _ in INTERACTIONS)
        print(line)


if __name__ == "__main__":
    main()

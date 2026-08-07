---
name: md-pipeline
description: Use when running or analyzing Desmond/Schrödinger molecular dynamics of protein-peptide or protein-ligand systems - serial MD on free GPUs, AutoTRJ clustering, PLIP interaction occupancy, Prime MMGBSA binding free energy, and merged summary tables. Also use when asked about MD trajectory analysis, 薛定谔/Desmond 模拟, 轨迹分析, 分析模拟结果, 结合自由能, 相互作用占据率, 聚类构象, or 跑 MD.
---

# MD 运行 + 轨迹分析流程

把「串行跑 Desmond MD → AutoTRJ 聚类 → SID 交互报告 → PLIP 相互作用 → Prime MMGBSA
→ 汇总成表」这条链做成可复用、跨机器、环境隔离的脚本集。AutoMD/AutoTRJ 已随包内置
(`scripts/AutoMD/`,GPLv3,上游 https://github.com/Wang-Lin-boop/AutoMD)。

## 先确认环境

```bash
SKILL=~/.claude/skills/md-pipeline
REPO=$(readlink -f "$SKILL"); while [ "$REPO" != / ] && [ ! -x "$REPO/toolenv/toolenv" ]; do REPO=$(dirname "$REPO"); done
TOOLENV="$REPO/toolenv/toolenv"
"$TOOLENV" check schrodinger automd conda conda:md
```

缺什么会直接说缺什么、怎么装。路径不对就写 `~/.config/toolenv/overrides.sh`
(例:`export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1`),不要改仓库里的文件。

## 有哪些脚本

不要凭记忆列举 —— 现场扫:

```bash
$TOOLENV index $SKILL/scripts
```

输出是一张表,含每个脚本的说明、依赖、用法。脚本都在 `scripts/`,直接执行即可
(`scripts/env.sh` 会把环境准备好)。

## 典型流程

```bash
cd <工作目录>                     # 放 .mae 输入 + md_pending_serial.list
cp $SKILL/scripts/md_pending_serial.list.template md_pending_serial.list
$SKILL/scripts/run_serial_md.sh --dry-run          # 先干跑看命令
nohup $SKILL/scripts/run_serial_md.sh > run.log 2>&1 &
$SKILL/scripts/run_analysis.sh <md-dir>...         # event_analysis + AutoTRJ
$SKILL/scripts/run_plip.sh    <md-dir>...          # 相互作用占据率
$SKILL/scripts/run_mmgbsa.sh  <md-dir>...          # 结合自由能
$SCHRODINGER/run $SKILL/scripts/summarize_analysis.py <md-dir>... --out-csv summary.csv
```

`run_serial_md.sh` 会把进度写进工作目录的 `md_completed_serial.list` /
`md_failed_serial.list`,已完成或已有 `*-md` 目录的体系自动跳过,可安全重启。

## 可选:ligand MM/GBSA 逐残基分解

入口仍是 `DECOMP=1` 的 `run_mmgbsa.sh`；它只在显式开启时增加分解准备和汇总，普通
`run_mmgbsa.sh DIR` 的 thermal MM/GBSA 路径不变。先根据 CMS 中的 residue 形态选
ligand ASL，再运行：

```bash
# 单个 UNK 肽
eval "$("$TOOLENV" env synergy-fragment)"
DECOMP=1 LIG_ASL='res.ptype UNK' $SKILL/scripts/run_mmgbsa.sh DIR

# 已经按 residue 建好的肽；把 ASL 换成该 ligand component
DECOMP=1 LIG_ASL='chain.name B and not water and not ions' \
  $SKILL/scripts/run_mmgbsa.sh DIR
```

路由只有三种：

| ligand 形态 | 命令 | 需要 Synergy | 预期分组 |
|---|---|---|---|
| 一个 `UNK` residue | `DECOMP=1 LIG_ASL='res.ptype UNK' run_mmgbsa.sh DIR` | 是 | `Pnnn`、caps、`XLINK_nnn` |
| pre-resolved peptide chain | `DECOMP=1 LIG_ASL='<chain/component ASL>' run_mmgbsa.sh DIR` | 否 | 输入中已有的 ligand residues |
| 只要 total MM/GBSA | `run_mmgbsa.sh DIR` | 否 | 不做 residue decomp |

single-UNK 路由需要一个只读的 Synergy-Fragment 目录。toolenv 只在该目录同时有
`peptide_sequence.py` 和 `monomer_library_nonstandard_segments_simple.csv` 时报告它，
`toolenv env synergy-fragment` 会导出 `SYNERGY_FRAGMENT_DIR` 给 runner；不把它声明为所有
MM/GBSA 的无条件依赖，也不修改
Synergy。缺少 Synergy 只阻塞 single-UNK；pre-resolved 和普通 MM/GBSA 仍可运行。

结果判断按机器可读产物进行：先读
`<out>/residue_decomp/decomp_manifest.json`，再读取 manifest 的
`paths.summary_csv` 指向的 summary；当前 runner 默认文件名是
`residue_decomp_summary.csv`。不要猜文件名，也不要只看日志判断状态。
unknown 名称、临时 `Pnnn`/`XLINK_nnn` 和立体识别不完整是 warning；分组覆盖率低于
100% 或有 missing/duplicate/overlap 必须视为失败。

已有 AutoTRJ Align 产物时，可用 `TRAJECTORY_SOURCE=align` 让 event_analysis 和 PLIP
读取同一对 `*_ALIGN-out.cms` + `*_ALIGN_trj`；Align event 报告写入 `analysis_align/`,
PLIP 默认写入 `plip_last100ns_align/`，不会覆盖 raw 结果。多个 Align 产物时用
`ALIGN_CMS=/path/to/*_ALIGN-out.cms` 指定目标，必要时再设置 `ALIGN_TRJ`。

## 分析前必做:查清配体到底是什么

**所有 `LIGAND_ASL` 默认值都可能不适用于你手上的体系**,选错的表现是配体聚类静默
失败(日志里 ERROR、退出码却是 0)、MMGBSA 直接拒跑。先看一眼链和残基构成:

```bash
$SCHRODINGER/run python3 -c "
from schrodinger.application.desmond.packages import topo
_, c = topo.read_cms('<体系>-out.cms')
ch = {}
for r in c.fsys_ct.residue: ch.setdefault(r.chain, []).append(r.pdbres.strip())
for k, v in ch.items(): print(repr(k), len(v), sorted(set(v))[:8])"
```

| 配体形态 | `LIGAND_ASL` | PLIP |
|---|---|---|
| 单个 `UNK` 残基(AutoMD 建模的修饰肽) | `res.ptype UNK`(默认) | 配体模式(默认) |
| 正常氨基酸残基、独立/空白链 | `not chain.name A and not water and not ions` | `PEPTIDE_MODE=1` |

别拿残基编号当配体 ASL —— 同一批体系编号未必一致(详见踩坑 8)。

## 改参数或出问题之前

**必读** `references/troubleshooting.md` —— 12 条实测踩坑,包括:配体 ASL 选错会静默
失败、且 `res.ptype UNK` 并非通用默认(8);正常残基肽的 PLIP 要走肽模式(9);
MMGBSA 的并行取决于 `-HOST localhost:N` 而非 `-NJOBS`(7);重跑分析前要清旧聚类
产物(11);**别在脚本运行中编辑它**,bash 会读错偏移把正在跑的作业弄崩(12)。
这些都是花了时间才定位到的,别重踩。

更早的完整说明留在 `references/original-readme.md`。

## 加新脚本

丢进 `scripts/`,文件头写四行元信息即可,不用改本文件:

```bash
#!/usr/bin/env bash
# @name: run_gmx
# @description: 一句话说明
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <dir>...
# 定位并激活依赖(向上找 toolenv,兼容 install.sh symlink 与 /plugin 安装)
_here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd); _r=${CLAUDE_PLUGIN_ROOT:-$_here}
while [ "$_r" != / ] && [ ! -f "$_r/toolenv/activate.sh" ]; do _r=$(dirname "$_r"); done
source "$_r/toolenv/activate.sh"
```

`activate.sh` 会读上面的 `@requires`,检查并激活;缺依赖时直接报出缺什么并退出。

新工具则在 `toolenv/tools.d/` 加一个 manifest(声明怎么找、怎么激活),
契约见那个目录里现有的六个文件。

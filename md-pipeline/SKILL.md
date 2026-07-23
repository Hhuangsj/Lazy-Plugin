---
name: md-pipeline
description: Use when running or analyzing Desmond/Schrödinger molecular dynamics of protein-peptide or protein-ligand systems - serial MD on free GPUs, AutoTRJ clustering, PLIP interaction occupancy, Prime MMGBSA binding free energy, and merged summary tables. Also use when asked about MD trajectory analysis, 结合自由能, 相互作用占据率, or 跑 MD.
---

# MD 运行 + 轨迹分析流程

把「串行跑 Desmond MD → AutoTRJ 聚类 → SID 交互报告 → PLIP 相互作用 → Prime MMGBSA
→ 汇总成表」这条链做成可复用、跨机器、环境隔离的脚本集。AutoMD/AutoTRJ 已随包内置
(`scripts/AutoMD/`,GPLv3,上游 https://github.com/Wang-Lin-boop/AutoMD)。

## 先确认环境

```bash
SKILL=~/.claude/skills/md-pipeline
TOOLENV=$(readlink -f "$SKILL")/../toolenv/toolenv
$TOOLENV check schrodinger automd conda conda:md
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
$SKILL/scripts/run_plip.sh    <md-dir>...          # 相互作用占据率
$SKILL/scripts/run_mmgbsa.sh  <md-dir>...          # 结合自由能
$SCHRODINGER/run $SKILL/scripts/summarize_analysis.py <md-dir>... --out-csv summary.csv
```

`run_serial_md.sh` 会把进度写进工作目录的 `md_completed_serial.list` /
`md_failed_serial.list`,已完成或已有 `*-md` 目录的体系自动跳过,可安全重启。

## 改参数或出问题之前

**必读** `references/troubleshooting.md` —— 7 条实测踩坑,包括:配体 ASL 必须用
`res.ptype UNK` 而不是 `ligand`;MMGBSA 的并行取决于 `-HOST localhost:N` 而非 `-NJOBS`;
PLIP 对「肽=单个 UNK 残基」体系必须走配体模式。这些都是花了时间才定位到的,别重踩。

更早的完整说明留在 `references/original-readme.md`。

## 加新脚本

丢进 `scripts/`,文件头写四行元信息即可,不用改本文件:

```bash
#!/usr/bin/env bash
# @name: run_gmx
# @description: 一句话说明
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <dir>...
source "$(dirname "$0")/../../toolenv/activate.sh"
```

`activate.sh` 会读上面的 `@requires`,检查并激活;缺依赖时直接报出缺什么并退出。

新工具则在 `toolenv/tools.d/` 加一个 manifest(声明怎么找、怎么激活),
契约见那个目录里现有的六个文件。

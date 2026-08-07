#!/usr/bin/env bash
# @name: run_plip
# @description: PLIP 肽-受体相互作用分析,逐帧算类型与残基对占据率(默认后 100ns)
# @requires: schrodinger, plip, conda, conda:md
# @usage: LAST_NS=100 JOBS=8 [TRAJECTORY_SOURCE=align ALIGN_CMS=...] run_plip.sh <md-dir>...
# run_plip.sh — 对已完成的 MD 目录跑 PLIP 肽–受体相互作用分析(默认后 100ns)
# ---------------------------------------------------------------------------
# 从实战沉淀:AutoMD 建模的修饰肽是单个 UNK 残基,PLIP 的 --peptides 肽模式
# 对它选不到原子(实测 0 相互作用)。因此本封装:
#   - 导出帧时用 keep_asl = "protein or res.ptype UNK"(否则丢掉整条肽);
#   - 把 UNK 肽重贴到独立链 B(空白链会被链检测忽略);
#   - 用 --no-plip-peptides 让 PLIP 把 UNK 当配体自动识别;
#   - 默认只分析轨迹最后 LAST_NS ns(全帧太贵/也没必要)。
# 详见 README「PLIP 相互作用分析」与 plip_interaction_analysis.py 顶部说明。
#
# 但这只是 UNK 体系的配方。肽若是**正常氨基酸残基**(ACE/NME 封端、独立或空白链),
# 配体模式识别不到,要反过来走 PLIP 肽模式:PEPTIDE_MODE=1 + 用 LIGAND_ASL 指明
# 哪些原子是肽(重贴链仍必要,空白链会被链检测忽略)。见踩坑记录 8、9。
#
# 用法:
#   ./run_plip.sh MD_DIR [MD_DIR ...]
#   LAST_NS=100 JOBS=8 ./run_plip.sh dir1 dir2      # 覆盖默认参数
#   CHAIN_A=B CHAIN_B=A KEEP_ASL='protein or res.ptype UNK' ./run_plip.sh dir
#   # 正常残基肽(受体在链 A):
#   LIG='not chain.name A and not water and not ions' \
#   PEPTIDE_MODE=1 LIGAND_ASL="$LIG" KEEP_ASL="chain.name A or ($LIG)" \
#   LIGAND_CHAIN=B CHAIN_A=B CHAIN_B=A ./run_plip.sh dir
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
# shellcheck disable=SC1091
source "$HERE/trajectory_source.sh"
md_env_check || { echo "环境自检未通过,中止。"; exit 1; }
command -v plip >/dev/null 2>&1 || { echo "ERROR: 找不到 plip(应在 conda '$MD_CONDA_ENV' 环境里)"; exit 1; }

# ===== 可覆盖参数 =====
PLIP_PY="${PLIP_PY:-$HERE/plip_interaction_analysis.py}"
LAST_NS="${LAST_NS:-100}"                         # 只分析最后 N ns(0=全轨迹)
JOBS="${JOBS:-8}"                                 # 逐帧 PLIP 并行 worker 数
TRAJECTORY_SOURCE="${TRAJECTORY_SOURCE:-raw}"     # raw 或已有 Align pair
ALIGN_CMS="${ALIGN_CMS:-}"                         # 可选:显式 Align CMS
ALIGN_TRJ="${ALIGN_TRJ:-}"                         # 可选:显式 Align 轨迹目录
KEEP_ASL="${KEEP_ASL:-protein or res.ptype UNK}"  # 导出帧保留:受体+UNK 肽
LIGAND_CHAIN="${LIGAND_CHAIN:-B}"                 # 把肽重贴到这条链
LIGAND_ASL="${LIGAND_ASL:-res.ptype UNK}"         # 哪些原子算「肽/配体」(重贴链用)
# 肽=单个 UNK 残基时用配体模式(PEPTIDE_MODE=0);肽是正常残基链时用 PLIP 肽模式(=1)
PEPTIDE_MODE="${PEPTIDE_MODE:-0}"
CHAIN_A="${CHAIN_A:-B}"                           # group A = 肽(配体侧)
CHAIN_B="${CHAIN_B:-A}"                           # group B = 受体
if [ -z "${OUT_NAME+x}" ]; then
    if [ "$TRAJECTORY_SOURCE" = align ]; then
        OUT_NAME="plip_last100ns_align"
    else
        OUT_NAME="plip_last100ns"
    fi
fi
THRESHOLD="${THRESHOLD:-20}"                      # 高占据残基对过滤阈值(%)

[ $# -ge 1 ] || { echo "用法: $0 MD_DIR [MD_DIR ...]"; exit 2; }

run_one() {
    local dir; dir="$(cd "$1" 2>/dev/null && pwd)" || { echo "ERROR: 目录无效: $1"; return 1; }
    select_trajectory_pair "$dir" "$TRAJECTORY_SOURCE" "$ALIGN_CMS" "$ALIGN_TRJ" || return 1
    local cms="$SELECTED_CMS" trj="$SELECTED_TRJ" name="$SELECTED_BASE"
    local out="$dir/$OUT_NAME"
    echo "==================== $(date '+%F %T') PLIP START $name ===================="
    rm -rf "$out"
    local pep_flag=(--no-plip-peptides)
    [ "$PEPTIDE_MODE" = "1" ] && pep_flag=()
    python3 "$PLIP_PY" \
        --trajectory-type schrodinger \
        --cms "$cms" --trj-dir "$trj" \
        --schrodinger-keep-asl "$KEEP_ASL" \
        --ligand-chain "$LIGAND_CHAIN" --ligand-relabel-asl "$LIGAND_ASL" \
        --chain-a "$CHAIN_A" --chain-b "$CHAIN_B" \
        "${pep_flag[@]}" --last-ns "$LAST_NS" \
        --jobs "$JOBS" --plip-maxthreads 1 \
        --threshold "$THRESHOLD" \
        --output-dir "$out"
    local rc=$?
    rm -rf "$out/plip_outputs" "$out/frames_pdb"   # 删逐帧中间目录,省空间
    echo "==================== $(date '+%F %T') PLIP DONE $name (rc=$rc) ===================="
    echo
    return "$rc"
}

overall_rc=0
for d in "$@"; do
    ( run_one "$d" )
    rc=$?
    if [ "$rc" -ne 0 ] && [ "$overall_rc" -eq 0 ]; then
        overall_rc=$rc
    fi
done
echo "########## PLIP ALL DONE $(date '+%F %T') ##########"
exit "$overall_rc"

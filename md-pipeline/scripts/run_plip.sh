#!/usr/bin/env bash
# @name: run_plip
# @description: PLIP 肽-受体相互作用分析,逐帧算类型与残基对占据率(默认后 100ns)
# @requires: schrodinger, plip, conda, conda:md
# @usage: LAST_NS=100 JOBS=8 run_plip.sh <md-dir>...
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
# 用法:
#   ./run_plip.sh MD_DIR [MD_DIR ...]
#   LAST_NS=100 JOBS=8 ./run_plip.sh dir1 dir2      # 覆盖默认参数
#   CHAIN_A=B CHAIN_B=A KEEP_ASL='protein or res.ptype UNK' ./run_plip.sh dir
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
md_env_check || { echo "环境自检未通过,中止。"; exit 1; }
command -v plip >/dev/null 2>&1 || { echo "ERROR: 找不到 plip(应在 conda '$MD_CONDA_ENV' 环境里)"; exit 1; }

# ===== 可覆盖参数 =====
PLIP_PY="${PLIP_PY:-$HERE/plip_interaction_analysis.py}"
LAST_NS="${LAST_NS:-100}"                         # 只分析最后 N ns(0=全轨迹)
JOBS="${JOBS:-8}"                                 # 逐帧 PLIP 并行 worker 数
KEEP_ASL="${KEEP_ASL:-protein or res.ptype UNK}"  # 导出帧保留:受体+UNK 肽
LIGAND_CHAIN="${LIGAND_CHAIN:-B}"                 # 把 UNK 肽重贴到这条链
CHAIN_A="${CHAIN_A:-B}"                           # group A = 肽(配体侧)
CHAIN_B="${CHAIN_B:-A}"                           # group B = 受体
OUT_NAME="${OUT_NAME:-plip_last100ns}"            # 输出子目录名
THRESHOLD="${THRESHOLD:-20}"                      # 高占据残基对过滤阈值(%)

[ $# -ge 1 ] || { echo "用法: $0 MD_DIR [MD_DIR ...]"; exit 2; }

run_one() {
    local dir; dir="$(cd "$1" 2>/dev/null && pwd)" || { echo "ERROR: 目录无效: $1"; return 1; }
    # 自动探测主 -out.cms(排除 AutoTRJ 的 PL_Analysis* 与 *_N-out.cms 中间文件);
    # 不假设「文件夹名 == 文件名」——有的目录二者不一致。
    local cms; cms="$(ls "$dir"/*-out.cms 2>/dev/null | grep -v PL_Analysis | grep -vE '_[0-9]+-out\.cms$' | head -1)"
    [ -n "$cms" ] || { echo "ERROR: $dir 下找不到主 -out.cms,跳过。"; return 1; }
    local name; name="$(basename "$cms")"; name="${name%-out.cms}"
    local trj="$dir/${name}_trj"
    [ -d "$trj" ] || { echo "ERROR: 找不到 $trj,跳过。"; return 1; }
    local out="$dir/$OUT_NAME"
    echo "==================== $(date '+%F %T') PLIP START $name ===================="
    rm -rf "$out"
    python3 "$PLIP_PY" \
        --trajectory-type schrodinger \
        --cms "$cms" --trj-dir "$trj" \
        --schrodinger-keep-asl "$KEEP_ASL" \
        --ligand-chain "$LIGAND_CHAIN" --chain-a "$CHAIN_A" --chain-b "$CHAIN_B" \
        --no-plip-peptides --last-ns "$LAST_NS" \
        --jobs "$JOBS" --plip-maxthreads 1 \
        --threshold "$THRESHOLD" \
        --output-dir "$out"
    local rc=$?
    rm -rf "$out/plip_outputs" "$out/frames_pdb"   # 删逐帧中间目录,省空间
    echo "==================== $(date '+%F %T') PLIP DONE $name (rc=$rc) ===================="
    echo
}

for d in "$@"; do ( run_one "$d" ); done
echo "########## PLIP ALL DONE $(date '+%F %T') ##########"

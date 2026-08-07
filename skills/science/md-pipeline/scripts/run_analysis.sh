#!/usr/bin/env bash
# @name: run_analysis
# @description: 对已完成的 MD 目录重跑分析(AutoTRJ 聚类 + SID 交互报告)
# @requires: schrodinger, automd, conda, conda:md
# @usage: [TRAJECTORY_SOURCE=align ALIGN_CMS=... ALIGN_TRJ=...] run_analysis.sh <md-dir>...
# run_analysis.sh — 对已完成的 MD 目录跑轨迹分析(AutoTRJ 聚类 + event_analysis 报告)
# ---------------------------------------------------------------------------
# 从实战沉淀:关键修正 = 配体 ASL 用 "res.ptype UNK"(AutoMD 建模时配体即 UNK 残基),
# 而不是 AutoTRJ 默认的 "ligand" 自动识别 —— 后者对多肽/修饰氨基酸类配体选不到原子,
# 会导致配体聚类和 MMGBSA 全部失败(见 README「踩坑记录」)。
#
# !! 先确认你的体系配体到底长什么样,别照抄默认值 !!(踩坑记录 8)
#   - 肽被建成单个 UNK 残基(AutoMD 典型)→ LIGAND_ASL='res.ptype UNK'(默认)
#   - 肽是正常氨基酸残基、落在受体之外的链 → LIGAND_ASL='not chain.name A and not water and not ions'
#   一行查清:
#     $SCHRODINGER/run python3 -c "
#     from schrodinger.application.desmond.packages import topo
#     _,c=topo.read_cms('X-out.cms')
#     print({(a.chain,a.pdbres.strip()) for a in c.fsys_ct.atom if not a.chain.strip()=='A'})"
#
# 用法:
#   ./run_analysis.sh MD_DIR [MD_DIR ...]
#   # 覆盖默认参数(环境变量方式):
#   LIGAND_ASL='res.ptype UNK' FRAMES='1:2001:20' ./run_analysis.sh dir1 dir2
#   TRAJECTORY_SOURCE=align ./run_analysis.sh dir1
#   ALIGN_CMS=/path/to/PL_Analysis_ALIGN-out.cms ./run_analysis.sh dir1
#   KEEP_CLEAN=1 ./run_analysis.sh dir1         # 保留 PL_Analysis_CLEAN 中间产物(默认删)
#   WITH_MMGBSA=1 ./run_analysis.sh dir1        # 谨慎!见 README 的 MMGBSA 注意事项
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
# shellcheck disable=SC1091
source "$HERE/trajectory_source.sh"
md_env_check || { echo "环境自检未通过,中止。"; exit 1; }

# ===== 可覆盖参数 =====
LIGAND_ASL="${LIGAND_ASL:-res.ptype UNK}"       # 配体选择(component 2)
RECEPTOR_ASL="${RECEPTOR_ASL:-protein}"         # 受体选择(component 1)
TRAJECTORY_SOURCE="${TRAJECTORY_SOURCE:-raw}"   # raw 或已有 Align pair
ALIGN_CMS="${ALIGN_CMS:-}"                       # 可选:显式 Align CMS
ALIGN_TRJ="${ALIGN_TRJ:-}"                       # 可选:显式 Align 轨迹目录
CLEAN_ASL="${CLEAN_ASL:-not solvent and not ions}"  # 清洗:去水去离子
FRAMES="${FRAMES:-1:2001:20}"                   # 帧范围 start:end:step
JOB="${JOB:-PL_Analysis}"                       # 作业名前缀
# 默认分析模块:蛋白聚类 + 配体聚类(AP + 化学哈希)。MMGBSA 默认不含(见 README)。
MODES="${MODES:-APCluster_5+LigandAPCluster_5+LigandCHCluster_5_1.0}"
WITH_MMGBSA="${WITH_MMGBSA:-0}"
[ "$WITH_MMGBSA" = "1" ] && MODES="${MODES}+MMGBSA"
# CLEAN 只是 ALIGN 的中间产物(ALIGN_trj 由它派生,聚类都读 ALIGN),跑完即可删,
# 每个体系省 ~55MB。设 KEEP_CLEAN=1 保留(需要单独看去水轨迹时)。
KEEP_CLEAN="${KEEP_CLEAN:-0}"

[ $# -ge 1 ] || { echo "用法: $0 MD_DIR [MD_DIR ...]"; exit 2; }

run_one() {
    local dir
    dir="$(cd "$1" 2>/dev/null && pwd)" || { echo "ERROR: 目录无效: $1"; return 1; }
    echo "==================== $(date '+%F %T') START ${dir##*/} ===================="
    cd "$dir" || return 1

    select_trajectory_pair "$dir" "$TRAJECTORY_SOURCE" "$ALIGN_CMS" "$ALIGN_TRJ" || return 1
    local cms="$SELECTED_CMS" trj="$SELECTED_TRJ" base="$SELECTED_BASE"

    echo "[$(date '+%F %T')] >>> AutoTRJ  modes='$MODES'  ligand='$LIGAND_ASL'  frames='$FRAMES'"
    local -a autotraj_args=(
        -i "$trj" -J "$JOB" -M "$MODES"
        -R "$RECEPTOR_ASL" -L "$LIGAND_ASL"
        -t "$FRAMES"
    )
    if [ "$TRAJECTORY_SOURCE" = raw ]; then
        autotraj_args+=( -C "$CLEAN_ASL" -a )
    fi
    AutoTRJ "${autotraj_args[@]}"
    local rc=$?
    echo "[$(date '+%F %T')] <<< AutoTRJ exit=$rc"
    [ "$rc" -eq 0 ] || return "$rc"

    # eaf(SID 事件分析文件):P8/P9 等体系 MD 阶段已自带算好的 -out.eaf;
    # P7 等没有的,现场三步生成:
    #   1) event_analysis.py analyze  -> -in.eaf(分析定义)
    #   2) analyze_simulation.py      -> -out.eaf(真正算轨迹数据,重活)
    #   3) event_analysis.py report   -> analysis/*.pdf
    local eaf report_dir event_prefix
    if [ "$TRAJECTORY_SOURCE" = align ]; then
        event_prefix="${base}_event_align"
        eaf="./${event_prefix}-out.eaf"
        report_dir="${REPORT_DIR:-analysis_align}"
        echo "[$(date '+%F %T')] >>> Align event_analysis: regenerate EAF from $cms"
        "$SCHRODINGER/run" event_analysis.py analyze "$cms" \
            -prot "$RECEPTOR_ASL" -lig "$LIGAND_ASL" -out "$event_prefix"
        rc=$?
        [ "$rc" -eq 0 ] || return "$rc"
        "$SCHRODINGER/run" analyze_simulation.py "$cms" "$trj" "$eaf" "./${event_prefix}-in.eaf"
        rc=$?
        echo "[$(date '+%F %T')] <<< Align eaf 生成 exit=$rc"
        [ "$rc" -eq 0 ] || return "$rc"
    else
        eaf="./${base}-out.eaf"
        report_dir="${REPORT_DIR:-analysis}"
        if [ ! -f "$eaf" ]; then
            echo "[$(date '+%F %T')] >>> 无 raw eaf,现场生成(analyze → analyze_simulation → report)"
            "$SCHRODINGER/run" event_analysis.py analyze "$cms" \
                -prot "$RECEPTOR_ASL" -lig "$LIGAND_ASL" -out "$base"
            rc=$?
            [ "$rc" -eq 0 ] || return "$rc"
            "$SCHRODINGER/run" analyze_simulation.py "$cms" "$trj" "$eaf" "./${base}-in.eaf"
            rc=$?
            echo "[$(date '+%F %T')] <<< eaf 生成 exit=$rc"
            [ "$rc" -eq 0 ] || return "$rc"
        fi
    fi
    if [ -f "$eaf" ]; then
        echo "[$(date '+%F %T')] >>> event_analysis.py report  ($eaf)"
        "$SCHRODINGER/run" event_analysis.py report "$eaf" -data -plots -data_dir "$report_dir"
        rc=$?
        echo "[$(date '+%F %T')] <<< event_analysis exit=$rc"
        [ "$rc" -eq 0 ] || return "$rc"
    else
        echo "WARN: 找不到可用 .eaf,跳过交互报告。"
    fi
    if [ "$TRAJECTORY_SOURCE" = raw ] && [ "$KEEP_CLEAN" != "1" ]; then
        # AutoTRJ 的 -a 是异步提交(踩坑 3):聚类作业可能还在读中间轨迹,
        # 等本目录的 $JOB_* 作业全部离开队列再删。
        local waited=0
        while "$SCHRODINGER/jobcontrol" -list 2>/dev/null \
              | grep -qE "[[:space:]]${JOB}_[^[:space:]]*[[:space:]]"; do
            sleep 30; waited=$((waited + 30))
            [ "$waited" -ge 7200 ] && { echo "WARN: 等待 ${JOB}_* 作业超时,保留 CLEAN 中间产物。"; break; }
        done
        if [ "$waited" -lt 7200 ]; then
            rm -rf "./${JOB}_CLEAN_trj" "./${JOB}_CLEAN-out.cms"
            echo "[$(date '+%F %T')] 已删除 ${JOB}_CLEAN 中间产物(KEEP_CLEAN=1 可保留)"
        fi
    fi
    echo "==================== $(date '+%F %T') DONE ${dir##*/} ===================="
    echo
}

# 每个目录放到子shell里跑,隔离 run_one 内的 `cd`,避免处理多个目录时
# 因工作目录未复位而在第二个目录起报 "目录无效"(相对路径场景踩过的坑)。
overall_rc=0
for d in "$@"; do
    ( run_one "$d" )
    rc=$?
    if [ "$rc" -ne 0 ] && [ "$overall_rc" -eq 0 ]; then
        overall_rc=$rc
    fi
done
echo "########## ALL DONE $(date '+%F %T') ##########"
exit "$overall_rc"

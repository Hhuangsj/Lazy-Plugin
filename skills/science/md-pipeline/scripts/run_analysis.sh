#!/usr/bin/env bash
# @name: run_analysis
# @description: 对已完成的 MD 目录重跑分析(AutoTRJ 聚类 + SID 交互报告)
# @requires: schrodinger, automd, conda, conda:md
# @usage: [LIGAND_ASL=... KEEP_CLEAN=1] run_analysis.sh <md-dir>...
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
#   KEEP_CLEAN=1 ./run_analysis.sh dir1         # 保留 PL_Analysis_CLEAN 中间产物(默认删)
#   WITH_MMGBSA=1 ./run_analysis.sh dir1        # 谨慎!见 README 的 MMGBSA 注意事项
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
md_env_check || { echo "环境自检未通过,中止。"; exit 1; }

# ===== 可覆盖参数 =====
LIGAND_ASL="${LIGAND_ASL:-res.ptype UNK}"       # 配体选择(component 2)
RECEPTOR_ASL="${RECEPTOR_ASL:-protein}"         # 受体选择(component 1)
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

    local trj; trj="$(ls -d ./*_trj 2>/dev/null | head -1)"
    [ -n "$trj" ] || { echo "ERROR: 找不到 *_trj 轨迹目录,跳过。"; return 1; }

    echo "[$(date '+%F %T')] >>> AutoTRJ  modes='$MODES'  ligand='$LIGAND_ASL'  frames='$FRAMES'"
    AutoTRJ -i "$trj" -J "$JOB" -M "$MODES" \
            -R "$RECEPTOR_ASL" -L "$LIGAND_ASL" \
            -t "$FRAMES" -C "$CLEAN_ASL" -a
    echo "[$(date '+%F %T')] <<< AutoTRJ exit=$?"

    # eaf(SID 事件分析文件):P8/P9 等体系 MD 阶段已自带算好的 -out.eaf;
    # P7 等没有的,现场三步生成:
    #   1) event_analysis.py analyze  -> -in.eaf(分析定义)
    #   2) analyze_simulation.py      -> -out.eaf(真正算轨迹数据,重活)
    #   3) event_analysis.py report   -> analysis/*.pdf
    local base; base="${dir##*/}"
    local eaf; eaf="$(ls ./*.eaf 2>/dev/null | grep -v -- '-in.eaf$' | head -1)"
    if [ -z "$eaf" ]; then
        local ocms; ocms="$(ls "./${base}-out.cms" 2>/dev/null | head -1)"
        [ -n "$ocms" ] || ocms="$(ls ./*-out.cms 2>/dev/null | grep -v PL_Analysis | head -1)"
        local otrj; otrj="$(ls -d "./${base}_trj" 2>/dev/null | head -1)"
        [ -n "$otrj" ] || otrj="$trj"
        if [ -n "$ocms" ]; then
            echo "[$(date '+%F %T')] >>> 无 eaf,现场生成(analyze → analyze_simulation → report)"
            "$SCHRODINGER/run" event_analysis.py analyze "$ocms" \
                -prot "$RECEPTOR_ASL" -lig "$LIGAND_ASL" -out "$base"
            "$SCHRODINGER/run" analyze_simulation.py "$ocms" "$otrj" "${base}-out.eaf" "${base}-in.eaf"
            echo "[$(date '+%F %T')] <<< eaf 生成 exit=$?"
            eaf="$(ls "./${base}-out.eaf" 2>/dev/null | head -1)"
        else
            echo "WARN: 找不到原始 -out.cms,无法生成 eaf。"
        fi
    fi
    if [ -n "$eaf" ]; then
        echo "[$(date '+%F %T')] >>> event_analysis.py report  ($eaf)"
        "$SCHRODINGER/run" event_analysis.py report "$eaf" -data -plots -data_dir analysis
        echo "[$(date '+%F %T')] <<< event_analysis exit=$?"
    else
        echo "WARN: 找不到可用 .eaf,跳过交互报告。"
    fi
    if [ "$KEEP_CLEAN" != "1" ]; then
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
for d in "$@"; do ( run_one "$d" ); done
echo "########## ALL DONE $(date '+%F %T') ##########"

#!/usr/bin/env bash
# @name: run_mmgbsa
# @description: Schrodinger Prime MMGBSA 逐帧结合自由能(默认后 100ns 每 20 帧)
# @requires: schrodinger, conda, conda:md
# @usage: START=1000 END=2000 STEP=20 NJOBS=4 run_mmgbsa.sh <md-dir>...
# run_mmgbsa.sh — 对已完成的 MD 目录跑薛定谔 thermal_mmgbsa(Prime MMGBSA,默认后 100ns 抽样)
# ---------------------------------------------------------------------------
# 从实战沉淀(见 README「MMGBSA 默认关闭」踩坑记录):
#   - 配体选择必须 -lig_asl "res.ptype UNK"(与建模一致),否则全帧 "ASL ... 匹配不到原子";
#   - 不加 -frozen / -atom_asl(CLEAN 后水/离子已删,冻结集会变空集导致读第1帧即中止);
#     thermal_mmgbsa 默认已删水/膜、分离配体与受体,无需再冻结。
#   - MMGBSA 每帧一次 Prime 能量计算,很贵。默认只取最后 LAST_NS ns、每 STEP 帧抽一帧。
# 帧号约定:与轨迹 0-based 索引一致。200ns/2001帧(0.1ns/帧)时,后 100ns = 帧 1000..2000。
#
# 用法:
#   ./run_mmgbsa.sh MD_DIR [MD_DIR ...]
#   START=1000 END=2000 STEP=20 NJOBS=8 ./run_mmgbsa.sh dir1     # 覆盖默认
#   LIG_ASL='res.ptype UNK' ./run_mmgbsa.sh dir1
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/env.sh"
md_env_check || { echo "环境自检未通过,中止。"; exit 1; }

# ===== 可覆盖参数(默认:后 100ns,每 20 帧一帧 ≈ 51 帧)=====
LIG_ASL="${LIG_ASL:-res.ptype UNK}"
START="${START:-1000}"     # 起始帧(0-based);后 100ns 起点
END="${END:-2000}"         # 结束帧
STEP="${STEP:-20}"         # 每 N 帧抽一帧
# NJOBS = 把帧切成几个 Prime 子作业,且下面 -HOST 用 localhost:$NJOBS 让它们并发。
# => NJOBS=8 时 8 个子作业同时跑 = 8 核。真正决定并发的是 -HOST 的 ":N",不是 hosts
#    的 processors(见 README 踩坑记录 7)。要几核就把 NJOBS 设几。
NJOBS="${NJOBS:-8}"
OUT_NAME="${OUT_NAME:-mmgbsa_last100ns}"
DECOMP="${DECOMP:-0}"
DECOMP_PROPERTIES="${DECOMP_PROPERTIES:-}"
SYNERGY_FRAGMENT_DIR="${SYNERGY_FRAGMENT_DIR:-}"
SYNERGY_ADAPTER_PYTHON="${SYNERGY_ADAPTER_PYTHON:-}"

[ $# -ge 1 ] || { echo "用法: $0 MD_DIR [MD_DIR ...]"; exit 2; }

mark_decomp_failed() {
    local manifest=$1 stage=$2 rc=$3 log=$4
    [ -f "$manifest" ] || return 0
    "$SCHRODINGER/run" python3 "$HERE/mmgbsa_decomp_contract.py" manifest-fail \
        --manifest "$manifest" --stage "$stage" --return-code "$rc" --log "$log" \
        >> "$log" 2>&1 || echo "WARN: 无法标记 decomp manifest 失败: $stage" >&2
}

read_prepare_result_field() {
    "$SCHRODINGER/run" python3 - "$1" "$2" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
value = result.get(sys.argv[2])
if not isinstance(value, str) or not value:
    raise ValueError("prepare_result missing non-empty {}".format(sys.argv[2]))
sys.stdout.write(value)
PY
}

run_decomp() {
    local dir=$1 cms=$2 name=$3 out=$4
    local decomp_dir="$out/residue_decomp"
    local manifest="$decomp_dir/decomp_manifest.json"
    local prepare_log="$decomp_dir/prepare_ligand_decomp.log"
    local thermal_log="$decomp_dir/thermal_mmgbsa.log"
    local aggregate_log="$decomp_dir/prime_mmgbsa_residue_decomp.log"
    local prepare_result="$decomp_dir/prepare_result.json"
    local analysis_cms analysis_ligand_asl residue_map trj prime_maegz rc
    local -a prepare_args aggregate_args trajectories prime_outputs

    mkdir -p "$decomp_dir"
    prepare_args=(python3 "$HERE/prepare_ligand_decomp.py" "$cms" --lig-asl "$LIG_ASL" --out-dir "$decomp_dir")
    [ -n "$SYNERGY_FRAGMENT_DIR" ] && prepare_args+=(--synergy-dir "$SYNERGY_FRAGMENT_DIR")
    [ -n "$SYNERGY_ADAPTER_PYTHON" ] && prepare_args+=(--adapter-python "$SYNERGY_ADAPTER_PYTHON")
    "$SCHRODINGER/run" "${prepare_args[@]}" >> "$prepare_log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        mark_decomp_failed "$manifest" prepare "$rc" "$prepare_log"
        return "$rc"
    fi

    analysis_cms="$(read_prepare_result_field "$prepare_result" analysis_cms 2>> "$prepare_log")"
    rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$analysis_cms" ]; then
        [ "$rc" -ne 0 ] || rc=2
        mark_decomp_failed "$manifest" prepare_result "$rc" "$prepare_log"
        return "$rc"
    fi
    analysis_ligand_asl="$(read_prepare_result_field "$prepare_result" analysis_ligand_asl 2>> "$prepare_log")"
    rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$analysis_ligand_asl" ]; then
        [ "$rc" -ne 0 ] || rc=2
        mark_decomp_failed "$manifest" prepare_result "$rc" "$prepare_log"
        return "$rc"
    fi
    residue_map="$(read_prepare_result_field "$prepare_result" residue_map 2>> "$prepare_log")"
    rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$residue_map" ]; then
        [ "$rc" -ne 0 ] || rc=2
        mark_decomp_failed "$manifest" prepare_result "$rc" "$prepare_log"
        return "$rc"
    fi

    shopt -s nullglob
    trajectories=("$dir"/*_trj)
    shopt -u nullglob
    if [ "${#trajectories[@]}" -ne 1 ]; then
        echo "ERROR: $dir 下主 *_trj 必须恰好一个。" >&2
        mark_decomp_failed "$manifest" trajectory 2 "$prepare_log"
        return 2
    fi
    trj=${trajectories[0]}

    ( cd "$out" && "$SCHRODINGER/run" thermal_mmgbsa.py "$analysis_cms" \
        -lig_asl "$analysis_ligand_asl" -j "$name" \
        -start_frame "$START" -end_frame "$END" -step_size "$STEP" \
        -NJOBS "$NJOBS" -HOST "localhost:$NJOBS" ) >> "$thermal_log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        mark_decomp_failed "$manifest" thermal "$rc" "$thermal_log"
        return "$rc"
    fi

    shopt -s nullglob
    prime_outputs=("$out"/*-prime-out.maegz)
    shopt -u nullglob
    if [ "${#prime_outputs[@]}" -ne 1 ]; then
        echo "ERROR: $out 下 expected *-prime-out.maegz 必须恰好一个。" >&2
        mark_decomp_failed "$manifest" thermal_output 2 "$thermal_log"
        return 2
    fi
    prime_maegz=${prime_outputs[0]}

    aggregate_args=(python3 "$HERE/prime_mmgbsa_residue_decomp.py" \
        --prime-maegz "$prime_maegz" --residue-map "$residue_map" --trajectory "$trj" \
        --start "$START" --end "$END" --step "$STEP" \
        --frame-csv "$decomp_dir/residue_decomp_frames.csv" \
        --summary-csv "$decomp_dir/residue_decomp_summary.csv" --manifest "$manifest")
    [ -n "$DECOMP_PROPERTIES" ] && aggregate_args+=(--properties "$DECOMP_PROPERTIES")
    "$SCHRODINGER/run" "${aggregate_args[@]}" >> "$aggregate_log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        mark_decomp_failed "$manifest" aggregation "$rc" "$aggregate_log"
        return "$rc"
    fi
}

run_one() {
    local dir; dir="$(cd "$1" 2>/dev/null && pwd)" || { echo "ERROR: 目录无效: $1"; return 1; }
    # 自动探测主 -out.cms(排除 PL_Analysis* 与 *_N-out.cms);不假设文件夹名==文件名。
    local cms; cms="$(ls "$dir"/*-out.cms 2>/dev/null | grep -v PL_Analysis | grep -vE '_[0-9]+-out\.cms$' | head -1)"
    [ -n "$cms" ] || { echo "ERROR: $dir 下找不到主 -out.cms,跳过。"; return 1; }
    local name; name="$(basename "$cms")"; name="${name%-out.cms}"
    local out="$dir/$OUT_NAME"
    echo "==================== $(date '+%F %T') MMGBSA START $name ===================="
    rm -rf "$out"; mkdir -p "$out"
    local rc
    if [ "$DECOMP" = 1 ]; then
        run_decomp "$dir" "$cms" "$name" "$out"
        rc=$?
    else
        # 关键:-HOST 必须带处理器数 localhost:N 才会并发!thermal_mmgbsa 把命令行 -HOST
        # 原样传给 prime_mmgbsa;只写 "localhost"(或不写)= 1 slot => 子作业串行(Max:1)。
        # 写 "localhost:N" 才会同时跑 N 个子作业 = N 核。仅在 hosts 里设 processors 无效。
        ( cd "$out" && "$SCHRODINGER/run" thermal_mmgbsa.py "$cms" \
            -lig_asl "$LIG_ASL" -j "$name" \
            -start_frame "$START" -end_frame "$END" -step_size "$STEP" \
            -NJOBS "$NJOBS" -HOST "localhost:$NJOBS" )
        rc=$?
    fi
    echo "  -> ${out}/${name}-prime-out.csv"
    echo "==================== $(date '+%F %T') MMGBSA DONE $name (rc=$rc) ===================="
    echo
    return "$rc"
}

overall_rc=0
for d in "$@"; do
    ( run_one "$d" )
    rc=$?
    [ "$rc" -eq 0 ] || overall_rc=$rc
done
echo "########## MMGBSA ALL DONE $(date '+%F %T') ##########"
exit "$overall_rc"

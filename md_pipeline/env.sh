# env.sh — MD/轨迹分析流程的统一环境设置(环境隔离核心)
# ---------------------------------------------------------------------------
# 目的:让本目录的脚本不依赖交互式 ~/.bashrc,在 nohup / cron / 任意 shell 下
#       都能正确拿到 Schrödinger、Desmond、AutoMD,以及隔离的 conda 环境。
#
# 用法:在其他脚本里 `source "$(dirname "${BASH_SOURCE[0]}")/env.sh"`
#       换机器/换路径时,只改下面这几行,或用同名环境变量覆盖即可。
# ---------------------------------------------------------------------------
# 本文件被 source,不要用 `set -e`;失败用 WARN 提示,不中断调用方。

# 本 env.sh 所在目录(即 md_pipeline 包根),用于定位包内自带的 AutoMD。
MD_PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ===== 可按机器修改的路径(用已存在的环境变量覆盖优先)=====
export SCHRODINGER="${SCHRODINGER:-/data1/home/huangshengjie/software/Schrodinger/2023-4}"
export Desmond="${Desmond:-$SCHRODINGER}"          # AutoTRJ 依赖 $Desmond
# AutoMD/AutoTRJ 已随包内置在 md_pipeline/AutoMD;默认用内置副本,
# 也可用环境变量 AUTOMD_DIR 覆盖指向外部安装。
AUTOMD_DIR="${AUTOMD_DIR:-$MD_PIPELINE_DIR/AutoMD}"
CONDA_ROOT="${CONDA_ROOT:-/data1/home/huangshengjie/miniforge3}"
MD_CONDA_ENV="${MD_CONDA_ENV:-md}"                 # 实际在用的隔离环境名

# ===== 激活隔离的 conda 环境(不污染调用方 base 环境)=====
if [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    . "$CONDA_ROOT/etc/profile.d/conda.sh"
    conda activate "$MD_CONDA_ENV" 2>/dev/null \
        || echo "WARN[env.sh]: conda 环境 '$MD_CONDA_ENV' 激活失败,请确认已 create。" >&2
else
    echo "WARN[env.sh]: 找不到 conda.sh: $CONDA_ROOT/etc/profile.d/conda.sh" >&2
fi

# ===== AutoMD / AutoTRJ 加入 PATH(去重)=====
case ":$PATH:" in
    *":$AUTOMD_DIR:"*) ;;
    *) export PATH="$AUTOMD_DIR:$PATH" ;;
esac

# ===== Schrödinger `run` 需要的动态库:隔离环境的 lib 优先 =====
# (与原 run_serial_md.sh 一致的做法,解决部分体系的 libstdc++ 等版本问题)
export LD_LIBRARY_PATH="$CONDA_ROOT/envs/$MD_CONDA_ENV/lib:${LD_LIBRARY_PATH:-}"

# ===== 自检:调用方可在 source 后执行 `md_env_check || exit 1` =====
md_env_check() {
    local ok=1
    [ -d "$SCHRODINGER" ]              || { echo "ERROR: SCHRODINGER 不存在: $SCHRODINGER" >&2; ok=0; }
    [ -d "$Desmond" ]                  || { echo "ERROR: Desmond 不存在: $Desmond" >&2; ok=0; }
    command -v AutoMD  >/dev/null 2>&1 || { echo "ERROR: AutoMD 不在 PATH ($AUTOMD_DIR)"  >&2; ok=0; }
    command -v AutoTRJ >/dev/null 2>&1 || { echo "ERROR: AutoTRJ 不在 PATH ($AUTOMD_DIR)" >&2; ok=0; }
    [ "${CONDA_DEFAULT_ENV:-}" = "$MD_CONDA_ENV" ] \
        || echo "WARN: 当前 conda 环境=${CONDA_DEFAULT_ENV:-none}(期望 $MD_CONDA_ENV)" >&2
    [ "$ok" = 1 ]
}

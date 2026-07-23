# env.sh —— md-pipeline 的环境入口(toolenv 薄壳)。
#
# 保持向后兼容:仍然可以 `source env.sh && md_env_check`,仍然导出
# SCHRODINGER / Desmond / AUTOMD_DIR。实际的"工具装在哪"交给 toolenv 解析,
# 换机器不需要改本文件。要纠正路径,写 ~/.config/toolenv/overrides.sh。
#
# 本文件被 source,不用 set -e;失败用 WARN/ERROR 提示,不中断调用方。

MD_PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_TOOLENV="$(cd "$MD_PIPELINE_DIR/../../toolenv" 2>/dev/null && pwd)/toolenv"

# skill 内置的 AutoMD 副本优先于系统安装
export TOOLENV_AUTOMD_BUNDLED="$MD_PIPELINE_DIR/AutoMD"

# conda 环境名:沿用旧变量名,允许覆盖
export MD_CONDA_ENV="${MD_CONDA_ENV:-md}"

if [ -x "$_TOOLENV" ]; then
    eval "$("$_TOOLENV" env conda schrodinger automd 2>/dev/null)"
else
    echo "WARN[env.sh]: 找不到 toolenv: $_TOOLENV" >&2
fi

# 激活隔离的 conda 环境(不污染调用方 base)
if [ -n "${CONDA_ROOT:-}" ] && [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    . "$CONDA_ROOT/etc/profile.d/conda.sh"
    conda activate "$MD_CONDA_ENV" 2>/dev/null \
        || echo "WARN[env.sh]: conda 环境 '$MD_CONDA_ENV' 激活失败,请确认已 create。" >&2
    # Schrödinger `run` 需要的动态库:隔离环境的 lib 优先
    export LD_LIBRARY_PATH="$CONDA_ROOT/envs/$MD_CONDA_ENV/lib:${LD_LIBRARY_PATH:-}"
fi

# 自检:调用方可在 source 后执行 `md_env_check || exit 1`
md_env_check() {
    "$_TOOLENV" check schrodinger automd conda "conda:$MD_CONDA_ENV" || return 1
    local ok=1
    command -v AutoMD  >/dev/null 2>&1 || { echo "ERROR: AutoMD 不在 PATH"  >&2; ok=0; }
    command -v AutoTRJ >/dev/null 2>&1 || { echo "ERROR: AutoTRJ 不在 PATH" >&2; ok=0; }
    [ "${CONDA_DEFAULT_ENV:-}" = "$MD_CONDA_ENV" ] \
        || echo "WARN: 当前 conda 环境=${CONDA_DEFAULT_ENV:-none}(期望 $MD_CONDA_ENV)" >&2
    [ "$ok" = 1 ]
}

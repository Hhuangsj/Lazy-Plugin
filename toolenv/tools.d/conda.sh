TOOL_NAME="conda"
TOOL_DESC="conda / miniforge 安装根目录"
TOOL_HINT="装 miniforge:https://github.com/conda-forge/miniforge#install"
tool_detect() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local root
    root=$(toolenv_conda_root) || return 1
    _te_hit "$root" "conda-root"
}
tool_activate() {
    local root=$1
    echo "export CONDA_ROOT=$root"
    echo ". \"$root/etc/profile.d/conda.sh\""
}

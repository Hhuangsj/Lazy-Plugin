TOOL_NAME="envtool"
TOOL_DESC="只认环境变量的假工具"
TOOL_HINT="export ENVTOOL_HOME=..."
tool_detect() {
    try_env ENVTOOL_HOME
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export ENVTOOL_HOME=$root"
    [ -n "$cenv" ] && echo "export ENVTOOL_CONDA_ENV=$cenv"
}

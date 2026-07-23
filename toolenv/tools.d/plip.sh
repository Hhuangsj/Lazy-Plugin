TOOL_NAME="plip"
TOOL_DESC="PLIP:蛋白-配体相互作用分析(python 包 + plip 命令)"
TOOL_HINT="在目标 conda 环境里:pip install plip"
tool_detect() {
    try_conda_env_bin plip
    try_conda_env_python "import plip"
    try_cmd plip --up 2
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_PLIP_ENV=$cenv"
    fi
}

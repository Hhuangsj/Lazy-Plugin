TOOL_NAME="rdkit"
TOOL_DESC="RDKit(python 包);TOOLENV_RDKIT_ENV 是能 import rdkit 的 conda 环境名"
TOOL_HINT="conda create -n chem -c conda-forge rdkit"
tool_detect() {
    try_conda_env_python "import rdkit"
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_RDKIT_ENV=$cenv"
    fi
}

TOOL_NAME="ambertools"
TOOL_DESC="AmberTools:antechamber / tleap / parmchk2 / cpptraj"
TOOL_HINT="conda create -n amber -c conda-forge ambertools"
tool_detect() {
    try_env AMBERHOME
    try_cmd antechamber --up 2
    try_conda_env_bin antechamber
    try_glob --require bin/antechamber "$HOME/software/amber*" "$HOME/amber*" "/opt/amber*"
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export AMBERHOME=$root"
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_AMBERTOOLS_ENV=$cenv"
    fi
}

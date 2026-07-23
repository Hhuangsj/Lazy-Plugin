TOOL_NAME="schrodinger"
TOOL_DESC="Schrödinger Suite(含 Desmond):\$SCHRODINGER/run、jobcontrol、prime_mmgbsa"
TOOL_HINT="需要已授权的 Schrödinger 安装;装好后 export SCHRODINGER=/path/to/Schrodinger/20XX-N"
tool_detect() {
    try_env SCHRODINGER
    try_cmd maestro --up 2
    try_glob "$HOME/software/Schrodinger/*" \
             "$HOME/Schrodinger/*" \
             "/opt/schrodinger/*" \
             "/opt/Schrodinger/*" \
             "/usr/local/schrodinger/*"
}
tool_activate() {
    local root=$1
    echo "export SCHRODINGER=$root"
    echo "export Desmond=$root"          # AutoTRJ 依赖 $Desmond
    echo "export PATH=$root:\$PATH"
}

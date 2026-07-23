TOOL_NAME="automd"
TOOL_DESC="AutoMD / AutoTRJ(第三方,GPLv3;md-pipeline skill 内置一份副本)"
TOOL_HINT="md-pipeline skill 已内置;或 git clone https://github.com/Wang-Lin-boop/AutoMD"
tool_detect() {
    try_env TOOLENV_AUTOMD_BUNDLED      # skill 内置副本,优先
    try_env AUTOMD_DIR
    try_cmd AutoTRJ --up 1
    try_glob --require AutoTRJ "$HOME/software/AutoMD" "$HOME/AutoMD" "/opt/AutoMD"
}
tool_activate() {
    local root=$1
    echo "export AUTOMD_DIR=$root"
    echo "export PATH=$root:\$PATH"
}

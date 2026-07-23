TOOL_NAME="faketool"
TOOL_DESC="测试用的假工具"
TOOL_HINT="这是测试 fixture,不需要安装"
tool_detect() {
    try_env FAKETOOL_HOME
    try_glob "$FAKETOOL_GLOB_BASE/faketool-*"
}
tool_activate() {
    local root=$1
    echo "export FAKETOOL_HOME=$root"
    echo "export PATH=$root/bin:\$PATH"
}

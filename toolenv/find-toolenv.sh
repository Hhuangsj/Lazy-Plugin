# find-toolenv.sh —— 定位 toolenv 可执行文件本身。被脚本 source。
# 不设 set -e/-u(被各种环境 source)。
#
# te_find_toolenv START_DIR
#   优先级(首个命中即用):
#     1. $TOOLENV_BIN            显式覆盖,指向 toolenv 可执行文件
#     2. $CLAUDE_PLUGIN_ROOT/toolenv/toolenv   plugin 安装形态
#     3. 从 START_DIR 逐级向上(≤6 级)找 <dir>/toolenv/toolenv
#     4. PATH 上的 toolenv
#   成功:路径写 stdout,返回 0;失败:中文说明写 stderr,返回 1。
te_find_toolenv() {
    local start=${1:-} d i=0
    if [ -n "${TOOLENV_BIN:-}" ] && [ -x "$TOOLENV_BIN" ]; then
        printf '%s\n' "$TOOLENV_BIN"; return 0
    fi
    if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "$CLAUDE_PLUGIN_ROOT/toolenv/toolenv" ]; then
        printf '%s\n' "$CLAUDE_PLUGIN_ROOT/toolenv/toolenv"; return 0
    fi
    d=$start
    while [ -n "$d" ] && [ "$i" -lt 6 ]; do
        if [ -x "$d/toolenv/toolenv" ]; then
            printf '%s\n' "$(readlink -f "$d/toolenv/toolenv")"; return 0
        fi
        [ "$d" = "/" ] && break
        d=$(dirname "$d")
        i=$((i + 1))
    done
    if command -v toolenv >/dev/null 2>&1; then
        printf '%s\n' "$(command -v toolenv)"; return 0
    fi
    echo "toolenv: 找不到 toolenv 可执行文件。设 TOOLENV_BIN 指向它,或确认仓库完整。" >&2
    return 1
}

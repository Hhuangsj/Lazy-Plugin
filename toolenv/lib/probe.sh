# probe.sh —— 探测原语。被 source,不设 set -e/-u。
#
# 约定:命中时设置 TOOLENV_HIT / TOOLENV_HIT_SOURCE / TOOLENV_HIT_ENV 并返回 0。
# TOOLENV_HIT 已非空时所有原语立即返回 0 —— 这样 tool_detect 里可以一行一个
# 候选顺序排列,天然实现"首个命中即停",不需要 || 链。

TOOLENV_HIT="${TOOLENV_HIT:-}"
TOOLENV_HIT_SOURCE="${TOOLENV_HIT_SOURCE:-}"
TOOLENV_HIT_ENV="${TOOLENV_HIT_ENV:-}"

_te_hit() {   # _te_hit <path> <source> [conda-env]
    TOOLENV_HIT=$1
    TOOLENV_HIT_SOURCE=$2
    TOOLENV_HIT_ENV=${3:-}
    return 0
}

# try_env VAR —— 环境变量 VAR 指向一个存在的目录
try_env() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local var=$1 val
    val=${!var:-}
    [ -n "$val" ] || return 1
    [ -d "$val" ] || return 1
    _te_hit "$(readlink -f "$val")" "env:$var"
}

# try_cmd CMD [--up N] —— PATH 上的 CMD,解析真实路径后向上 N 级
# 例:try_cmd antechamber --up 2  =>  /prefix/bin/antechamber 的 /prefix
try_cmd() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local cmd=$1 up=0 p i
    shift
    if [ "${1:-}" = "--up" ]; then up=${2:-0}; fi
    p=$(command -v "$cmd" 2>/dev/null) || return 1
    [ -n "$p" ] || return 1
    p=$(readlink -f "$p")
    for ((i = 0; i < up; i++)); do
        p=$(dirname "$p")
    done
    _te_hit "$p" "path:$cmd"
}

# try_glob PATTERN... —— 逐个 glob,同一 pattern 内按版本号取最大的目录
try_glob() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local pat d best
    for pat in "$@"; do
        best=""
        # 有意不加引号:这里就是要 glob 展开
        for d in $pat; do
            [ -d "$d" ] || continue
            if [ -z "$best" ]; then
                best=$d
            else
                best=$(printf '%s\n%s\n' "$best" "$d" | sort -V | tail -1)
            fi
        done
        if [ -n "$best" ]; then
            _te_hit "$(readlink -f "$best")" "glob:$pat"
            return 0
        fi
    done
    return 1
}

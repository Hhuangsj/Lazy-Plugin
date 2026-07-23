# activate.sh —— 被脚本 source 的一行入口:
#     source "$(dirname "$0")/../../toolenv/activate.sh"
# 读调用方脚本头的 @requires,检查并激活;缺依赖时打印缺什么并 exit 1。

_te_self=$(readlink -f "${BASH_SOURCE[0]}")
_te_home=$(dirname "$_te_self")
_te_caller=$(readlink -f "${BASH_SOURCE[1]:-$0}")

_te_reqs=$("$_te_home/toolenv" requires "$_te_caller")

if [ -n "$_te_reqs" ]; then
    # shellcheck disable=SC2086
    if ! "$_te_home/toolenv" check $_te_reqs; then
        echo "toolenv: $(basename "$_te_caller") 的依赖没装齐,已中止。" >&2
        echo "         看全貌:$_te_home/toolenv list" >&2
        exit 1
    fi
    # shellcheck disable=SC2086
    eval "$("$_te_home/toolenv" env $_te_reqs)"
fi

unset _te_self _te_home _te_caller _te_reqs

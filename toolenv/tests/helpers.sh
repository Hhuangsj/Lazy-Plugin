# helpers.sh —— 零依赖 bash 测试骨架。被测试文件 source。
# 不设 set -e:断言失败要继续跑完同一个测试函数。
set -u

TE_TESTS=0
TE_FAILS=0
_te_current=""
SANDBOX=""

fail() {
    TE_FAILS=$((TE_FAILS + 1))
    echo "    ✗ $_te_current: $*" >&2
}

assert_eq() {
    if [ "$1" != "$2" ]; then
        fail "expected '$2', got '$1'${3:+ ($3)}"
    fi
}

assert_ok() {
    if ! "$@"; then fail "expected success: $*"; fi
}

assert_fail() {
    if "$@"; then fail "expected failure: $*"; fi
}

assert_contains() {
    case "$1" in
        *"$2"*) ;;
        *) fail "'$1' does not contain '$2'" ;;
    esac
}

_te_sandbox_setup() {
    SANDBOX=$(mktemp -d "${TMPDIR:-/tmp}/toolenv-test.XXXXXX")
    export HOME="$SANDBOX/home"
    export TOOLENV_CACHE_DIR="$SANDBOX/cache"
    export TOOLENV_CONFIG_DIR="$SANDBOX/config"
    mkdir -p "$HOME" "$TOOLENV_CACHE_DIR" "$TOOLENV_CONFIG_DIR"
}

_te_sandbox_teardown() {
    [ -n "$SANDBOX" ] && [ -d "$SANDBOX" ] && rm -rf "$SANDBOX"
    SANDBOX=""
}

run_test() {
    local fn=$1 before=$TE_FAILS
    _te_current=$fn
    TE_TESTS=$((TE_TESTS + 1))
    _te_sandbox_setup
    "$fn"
    _te_sandbox_teardown
    if [ "$TE_FAILS" -eq "$before" ]; then echo "    ✓ $fn"; fi
}

run_all() {
    local fn
    for fn in $(declare -F | awk '{print $3}' | grep '^test_'); do
        run_test "$fn"
    done
    echo "  $TE_TESTS tests, $TE_FAILS failures"
    [ "$TE_FAILS" -eq 0 ]
}

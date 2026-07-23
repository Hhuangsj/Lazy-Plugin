#!/usr/bin/env bash
# 这些测试不要求工具真的装了,只验证 manifest 本身合规、可加载、不崩。
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"
. "$TOOLENV_HOME/lib/cache.sh"
. "$TOOLENV_HOME/lib/resolve.sh"

EXPECTED="ambertools automd conda plip rdkit schrodinger"

test_all_expected_manifests_present() {
    assert_eq "$(toolenv_list_manifests | tr '\n' ' ' | sed 's/ $//')" "$EXPECTED"
}

test_every_manifest_declares_required_fields() {
    local t
    for t in $EXPECTED; do
        toolenv_load_manifest "$t" || { fail "$t 加载失败"; continue; }
        [ -n "$TOOL_NAME" ] || fail "$t 缺 TOOL_NAME"
        [ -n "$TOOL_DESC" ] || fail "$t 缺 TOOL_DESC"
        [ -n "$TOOL_HINT" ] || fail "$t 缺 TOOL_HINT"
        declare -F tool_detect   >/dev/null || fail "$t 缺 tool_detect"
        declare -F tool_activate >/dev/null || fail "$t 缺 tool_activate"
    done
}

test_every_detect_runs_without_error_in_empty_sandbox() {
    # 干净沙箱里应当是"找不到",而不是报错或挂住
    local t rc
    for t in $EXPECTED; do
        TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""
        toolenv_load_manifest "$t"
        set +u
        tool_detect >/dev/null 2>&1
        rc=$?
        set -u
        # 找不到(1)和找到(0)都行,只要没有 shell 级崩溃
        if [ "$rc" -gt 1 ]; then fail "$t tool_detect 异常退出 rc=$rc"; fi
    done
}

test_every_activate_emits_only_assignments() {
    local t out line
    for t in $EXPECTED; do
        out=$(toolenv_activate_lines "$t" /fake/root fakeenv 2>/dev/null)
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            case "$line" in
                export\ *|.\ *|conda\ activate*) ;;
                *) fail "$t 的 tool_activate 输出了非赋值行: $line" ;;
            esac
        done <<< "$out"
    done
}

run_all

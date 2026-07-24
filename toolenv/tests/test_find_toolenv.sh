#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/find-toolenv.sh"

# 造一个假的 toolenv 树:<root>/toolenv/toolenv(可执行)
_make_fake_toolenv() {
    local root=$1
    mkdir -p "$root/toolenv"
    printf '#!/bin/sh\n' > "$root/toolenv/toolenv"
    chmod +x "$root/toolenv/toolenv"
}

test_env_bin_wins_first() {
    _make_fake_toolenv "$SANDBOX/repo"
    mkdir -p "$SANDBOX/elsewhere"
    printf '#!/bin/sh\n' > "$SANDBOX/elsewhere/toolenv"
    chmod +x "$SANDBOX/elsewhere/toolenv"
    local out
    out=$(TOOLENV_BIN="$SANDBOX/elsewhere/toolenv" \
          te_find_toolenv "$SANDBOX/repo/skills/x/y/scripts")
    assert_eq "$out" "$SANDBOX/elsewhere/toolenv"
}

test_plugin_root_hits() {
    _make_fake_toolenv "$SANDBOX/plug"
    local out
    out=$(env -u TOOLENV_BIN CLAUDE_PLUGIN_ROOT="$SANDBOX/plug" \
          bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv /nonexistent')
    assert_eq "$out" "$SANDBOX/plug/toolenv/toolenv"
}

test_walks_up_from_caller() {
    _make_fake_toolenv "$SANDBOX/repo"
    mkdir -p "$SANDBOX/repo/skills/science/md-pipeline/scripts"
    local out
    out=$(env -u TOOLENV_BIN -u CLAUDE_PLUGIN_ROOT \
          bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv "'"$SANDBOX"'/repo/skills/science/md-pipeline/scripts"')
    assert_eq "$out" "$SANDBOX/repo/toolenv/toolenv"
}

test_fails_when_nothing_found() {
    assert_fail env -u TOOLENV_BIN -u CLAUDE_PLUGIN_ROOT PATH=/usr/bin:/bin \
        bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv "'"$SANDBOX"'/empty/a/b" 2>/dev/null'
}

run_all

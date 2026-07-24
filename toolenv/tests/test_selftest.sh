#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"

TOOLENV="$TOOLENV_HOME/toolenv"
REPO=$(dirname "$TOOLENV_HOME")

test_selftest_passes_in_clean_env() {
    local out rc
    out=$("$TOOLENV" selftest 2>&1); rc=$?
    assert_eq "$rc" "0"
    assert_contains "$out" "clean-env"
}

test_install_creates_symlinks() {
    mkdir -p "$HOME/.claude/skills"
    "$REPO/install.sh" >/dev/null 2>&1
    assert_ok test -L "$HOME/.claude/skills/md-pipeline"
    assert_eq "$(readlink -f "$HOME/.claude/skills/md-pipeline")" "$REPO/skills/science/md-pipeline"
}

test_install_is_idempotent() {
    mkdir -p "$HOME/.claude/skills"
    "$REPO/install.sh" >/dev/null 2>&1
    local rc
    "$REPO/install.sh" >/dev/null 2>&1; rc=$?
    assert_eq "$rc" "0"
    assert_ok test -L "$HOME/.claude/skills/md-pipeline"
}

run_all

#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"

TOOLENV="$TOOLENV_HOME/toolenv"
export TOOLENV_TOOLS_DIR="$TESTS_DIR/fixtures/tools.d"

test_usage_on_unknown_subcommand() {
    local out rc
    out=$("$TOOLENV" bogus 2>&1); rc=$?
    assert_eq "$rc" "2"
    assert_contains "$out" "usage"
}

test_probe_writes_cache_and_reports() {
    mkdir -p "$SANDBOX/ft"
    local out
    out=$(FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe 2>&1)
    assert_contains "$out" "faketool"
    assert_ok test -f "$TOOLENV_CACHE_DIR/$(hostname -s).env"
}

test_list_shows_status_and_source() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out
    out=$("$TOOLENV" list 2>&1)
    assert_contains "$out" "faketool"
    assert_contains "$out" "found"
    assert_contains "$out" "$SANDBOX/ft"
    assert_contains "$out" "envtool"
    assert_contains "$out" "missing"
}

test_which_prints_path() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    assert_eq "$("$TOOLENV" which faketool)" "$SANDBOX/ft"
}

test_which_missing_fails_with_hint() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" which envtool 2>&1); rc=$?
    assert_eq "$rc" "1"
    assert_contains "$out" "ENVTOOL_HOME"
}

test_check_passes_silently() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" check faketool 2>&1); rc=$?
    assert_eq "$rc" "0"
    assert_eq "$out" ""
}

test_check_reports_each_missing() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" check faketool envtool 2>&1); rc=$?
    assert_eq "$rc" "1"
    assert_contains "$out" "missing: faketool"
    assert_contains "$out" "missing: envtool"
}

test_env_prints_export_lines() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out
    out=$("$TOOLENV" env faketool)
    assert_contains "$out" "export FAKETOOL_HOME=$SANDBOX/ft"
}

test_env_is_evalable() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local got
    got=$(bash -c 'eval "$('"$TOOLENV"' env faketool)"; echo "$FAKETOOL_HOME"')
    assert_eq "$got" "$SANDBOX/ft"
}

test_env_emits_nothing_when_missing() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" env envtool 2>/dev/null); rc=$?
    assert_eq "$rc" "1"
    assert_eq "$out" ""
}

test_probe_force_repicks_up_new_install() {
    "$TOOLENV" probe >/dev/null 2>&1
    assert_fail "$TOOLENV" which faketool
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe --force >/dev/null 2>&1
    assert_eq "$("$TOOLENV" which faketool)" "$SANDBOX/ft"
}

run_all

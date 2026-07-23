#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"

_reset() { TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""; }

test_try_env_hits_existing_dir() {
    _reset
    mkdir -p "$SANDBOX/amber"
    FAKE_HOME_VAR="$SANDBOX/amber"
    export FAKE_HOME_VAR
    assert_ok try_env FAKE_HOME_VAR
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FAKE_HOME_VAR"
}

test_try_env_misses_when_dir_absent() {
    _reset
    FAKE_HOME_VAR="$SANDBOX/nope"
    export FAKE_HOME_VAR
    assert_fail try_env FAKE_HOME_VAR
    assert_eq "$TOOLENV_HIT" ""
}

test_try_env_misses_when_var_unset() {
    _reset
    unset FAKE_HOME_VAR
    assert_fail try_env FAKE_HOME_VAR
}

test_try_cmd_walks_up() {
    _reset
    mkdir -p "$SANDBOX/amber/bin"
    printf '#!/bin/sh\n' > "$SANDBOX/amber/bin/antechamber"
    chmod +x "$SANDBOX/amber/bin/antechamber"
    PATH="$SANDBOX/amber/bin:$PATH" assert_ok try_cmd antechamber --up 2
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber"
    assert_eq "$TOOLENV_HIT_SOURCE" "path:antechamber"
}

test_try_cmd_default_up_is_zero() {
    _reset
    mkdir -p "$SANDBOX/amber/bin"
    printf '#!/bin/sh\n' > "$SANDBOX/amber/bin/tleap"
    chmod +x "$SANDBOX/amber/bin/tleap"
    PATH="$SANDBOX/amber/bin:$PATH" assert_ok try_cmd tleap
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber/bin/tleap"
}

test_try_cmd_misses_unknown_command() {
    _reset
    assert_fail try_cmd definitely-not-a-real-command-xyz --up 2
}

test_try_glob_picks_highest_version() {
    _reset
    mkdir -p "$SANDBOX/software/Schrodinger/2023-4"
    mkdir -p "$SANDBOX/software/Schrodinger/2024-1"
    assert_ok try_glob "$SANDBOX/software/Schrodinger/*"
    assert_eq "$TOOLENV_HIT" "$SANDBOX/software/Schrodinger/2024-1"
    assert_contains "$TOOLENV_HIT_SOURCE" "glob:"
}

test_try_glob_ignores_files_and_missing() {
    _reset
    touch "$SANDBOX/notadir"
    assert_fail try_glob "$SANDBOX/notadir" "$SANDBOX/nothing-here-*"
}

test_try_glob_require_skips_dirs_without_marker() {
    # 真实场景:~/software/Schrodinger/ 下既有真安装 2023-4(含 run),
    # 又有安装包目录 schrodinger2023-4-linux(无 run)。必须挑前者。
    _reset
    mkdir -p "$SANDBOX/S/2023-4" "$SANDBOX/S/schrodinger2023-4-linux"
    printf '#!/bin/sh\n' > "$SANDBOX/S/2023-4/run"
    chmod +x "$SANDBOX/S/2023-4/run"
    assert_ok try_glob --require run "$SANDBOX/S/*"
    assert_eq "$TOOLENV_HIT" "$SANDBOX/S/2023-4"
}

test_try_glob_require_still_prefers_highest_version() {
    _reset
    mkdir -p "$SANDBOX/S/2023-4" "$SANDBOX/S/2024-1"
    printf '#!/bin/sh\n' > "$SANDBOX/S/2023-4/run"
    printf '#!/bin/sh\n' > "$SANDBOX/S/2024-1/run"
    chmod +x "$SANDBOX/S/2023-4/run" "$SANDBOX/S/2024-1/run"
    assert_ok try_glob --require run "$SANDBOX/S/*"
    assert_eq "$TOOLENV_HIT" "$SANDBOX/S/2024-1"
}

test_try_glob_require_fails_when_no_candidate_qualifies() {
    _reset
    mkdir -p "$SANDBOX/S/installer-only"
    assert_fail try_glob --require run "$SANDBOX/S/*"
}

test_first_hit_wins() {
    _reset
    mkdir -p "$SANDBOX/first" "$SANDBOX/second"
    FIRST="$SANDBOX/first"; SECOND="$SANDBOX/second"
    export FIRST SECOND
    try_env FIRST
    try_env SECOND
    assert_eq "$TOOLENV_HIT" "$SANDBOX/first" "第二次探测不该覆盖第一次"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FIRST"
}

run_all

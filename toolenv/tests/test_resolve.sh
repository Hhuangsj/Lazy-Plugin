#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"
. "$TOOLENV_HOME/lib/cache.sh"
. "$TOOLENV_HOME/lib/resolve.sh"

export TOOLENV_TOOLS_DIR="$TESTS_DIR/fixtures/tools.d"

_reset() { TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""; }

test_list_manifests() {
    local out; out=$(toolenv_list_manifests | tr '\n' ' ')
    assert_eq "$out" "envtool faketool "
}

test_load_manifest_sets_fields() {
    assert_ok toolenv_load_manifest faketool
    toolenv_load_manifest faketool
    assert_eq "$TOOL_NAME" "faketool"
    assert_contains "$TOOL_DESC" "假工具"
}

test_load_manifest_clears_optional_validator_hook() {
    local fixture_tools="$TOOLENV_TOOLS_DIR"
    export TOOLENV_TOOLS_DIR="$TOOLENV_HOME/tools.d"
    toolenv_load_manifest synergy-fragment
    declare -F tool_validate_path >/dev/null || fail "Synergy manifest 缺 tool_validate_path"
    export TOOLENV_TOOLS_DIR="$fixture_tools"
    toolenv_load_manifest faketool
    if declare -F tool_validate_path >/dev/null; then
        fail "加载无 hook 的 manifest 后仍残留 tool_validate_path"
    fi
}

test_load_unknown_manifest_fails() {
    assert_fail toolenv_load_manifest nosuchtool
}

test_resolve_via_detect() {
    _reset
    mkdir -p "$SANDBOX/ft"
    export FAKETOOL_HOME="$SANDBOX/ft"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/ft"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FAKETOOL_HOME"
    unset FAKETOOL_HOME
}

test_resolve_via_glob_fallback() {
    _reset
    unset FAKETOOL_HOME
    mkdir -p "$SANDBOX/g/faketool-1.0" "$SANDBOX/g/faketool-2.0"
    export FAKETOOL_GLOB_BASE="$SANDBOX/g"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/g/faketool-2.0"
    unset FAKETOOL_GLOB_BASE
}

test_override_var_beats_detect() {
    _reset
    mkdir -p "$SANDBOX/ft" "$SANDBOX/override"
    export FAKETOOL_HOME="$SANDBOX/ft"
    export TOOLENV_FAKETOOL="$SANDBOX/override"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/override"
    assert_eq "$TOOLENV_HIT_SOURCE" "override"
    unset FAKETOOL_HOME TOOLENV_FAKETOOL
}

_real_toolenv_which() {
    REAL_WHICH_RUN=$((REAL_WHICH_RUN + 1))
    TOOLENV_TOOLS_DIR="$TOOLENV_HOME/tools.d" \
    TOOLENV_CACHE_DIR="$SANDBOX/real-cache-$REAL_WHICH_RUN" \
    TOOLENV_CONFIG_DIR="$SANDBOX/real-config" \
    "$TOOLENV_HOME/toolenv" which synergy-fragment
}

test_synergy_override_validation_and_precedence() {
    local incomplete="$SANDBOX/incomplete" complete="$SANDBOX/complete" env_dir="$SANDBOX/env"
    local fixture_tools="$TOOLENV_TOOLS_DIR"
    REAL_WHICH_RUN=0
    export TOOLENV_TOOLS_DIR="$TOOLENV_HOME/tools.d"
    mkdir -p "$incomplete" "$complete" "$env_dir"
    : > "$complete/peptide_sequence.py"
    : > "$complete/monomer_library_nonstandard_segments_simple.csv"
    : > "$env_dir/peptide_sequence.py"
    : > "$env_dir/monomer_library_nonstandard_segments_simple.csv"

    export TOOLENV_SYNERGY_FRAGMENT="$incomplete"
    export SYNERGY_FRAGMENT_DIR="$env_dir"
    _reset
    assert_fail toolenv_resolve synergy-fragment
    assert_eq "$TOOLENV_HIT" ""
    assert_fail _real_toolenv_which

    export TOOLENV_SYNERGY_FRAGMENT="$complete"
    _reset
    assert_ok toolenv_resolve synergy-fragment
    assert_eq "$TOOLENV_HIT" "$(readlink -f "$complete")"
    assert_eq "$TOOLENV_HIT_SOURCE" "override"
    assert_eq "$(_real_toolenv_which)" "$(readlink -f "$complete")"

    unset TOOLENV_SYNERGY_FRAGMENT
    _reset
    assert_ok toolenv_resolve synergy-fragment
    assert_eq "$TOOLENV_HIT" "$(readlink -f "$env_dir")"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:SYNERGY_FRAGMENT_DIR"
    unset SYNERGY_FRAGMENT_DIR
    export TOOLENV_TOOLS_DIR="$fixture_tools"
}

test_overrides_file_is_sourced() {
    _reset
    mkdir -p "$SANDBOX/from-file"
    cat > "$TOOLENV_CONFIG_DIR/overrides.sh" <<EOF
export TOOLENV_FAKETOOL="$SANDBOX/from-file"
EOF
    toolenv_load_overrides
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/from-file"
    unset TOOLENV_FAKETOOL
}

test_resolve_fails_when_nothing_found() {
    _reset
    unset FAKETOOL_HOME TOOLENV_FAKETOOL
    export FAKETOOL_GLOB_BASE="$SANDBOX/empty"
    assert_fail toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" ""
    unset FAKETOOL_GLOB_BASE
}

test_resolve_is_isolated_between_calls() {
    _reset
    mkdir -p "$SANDBOX/et"
    export ENVTOOL_HOME="$SANDBOX/et"
    toolenv_resolve envtool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/et"
    unset ENVTOOL_HOME FAKETOOL_HOME
    export FAKETOOL_GLOB_BASE="$SANDBOX/empty"
    assert_fail toolenv_resolve faketool
    unset FAKETOOL_GLOB_BASE
}

test_activate_lines() {
    local out
    out=$(toolenv_activate_lines faketool /opt/ft "")
    assert_contains "$out" "export FAKETOOL_HOME=/opt/ft"
    assert_contains "$out" "export PATH=/opt/ft/bin:\$PATH"
}

test_activate_lines_passes_conda_env() {
    local out
    out=$(toolenv_activate_lines envtool /opt/envs/chem chem)
    assert_contains "$out" "export ENVTOOL_CONDA_ENV=chem"
}

run_all

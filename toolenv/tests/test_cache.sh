#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/cache.sh"

test_cache_file_under_cache_dir() {
    assert_contains "$(toolenv_cache_file)" "$TOOLENV_CACHE_DIR/"
    assert_contains "$(toolenv_cache_file)" ".env"
}

test_put_flush_load_get_roundtrip() {
    toolenv_cache_clear
    toolenv_cache_put schrodinger found /opt/schrodinger/2024-1 "glob:/opt/*" ""
    toolenv_cache_put rdkit found /home/u/miniforge3/envs/chem "conda:chem" chem
    toolenv_cache_flush
    assert_ok test -f "$(toolenv_cache_file)"

    toolenv_cache_clear_memory
    assert_ok toolenv_cache_load
    assert_eq "$(toolenv_cache_get schrodinger PATH)" "/opt/schrodinger/2024-1"
    assert_eq "$(toolenv_cache_get schrodinger SOURCE)" "glob:/opt/*"
    assert_eq "$(toolenv_cache_get rdkit ENV)" "chem"
    assert_eq "$(toolenv_cache_get rdkit STATUS)" "found"
}

test_get_unknown_tool_fails() {
    toolenv_cache_clear
    toolenv_cache_put a found /x path:a ""
    toolenv_cache_flush
    assert_fail toolenv_cache_get nosuchtool PATH
}

test_missing_status_roundtrips() {
    toolenv_cache_clear
    toolenv_cache_put ambertools missing "" "" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get ambertools STATUS)" "missing"
    assert_eq "$(toolenv_cache_get ambertools PATH)" ""
}

test_tool_name_with_dash() {
    toolenv_cache_clear
    toolenv_cache_put my-tool found /x/y path:my-tool ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get my-tool PATH)" "/x/y"
}

test_cache_tools_lists_all() {
    toolenv_cache_clear
    toolenv_cache_put a found /a path:a ""
    toolenv_cache_put b missing "" "" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_tools | sort | tr '\n' ' ')" "a b "
}

test_load_fails_when_no_cache() {
    toolenv_cache_clear
    assert_fail toolenv_cache_load
}

test_paths_with_spaces_survive() {
    toolenv_cache_clear
    toolenv_cache_put weird found "/opt/my tools/x" "glob:/opt/*" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get weird PATH)" "/opt/my tools/x"
}

run_all

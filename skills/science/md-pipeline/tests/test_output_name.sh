#!/usr/bin/env bash
# Contract tests for the shared analysis output-name validator.
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$SKILL_DIR
while [ "$REPO" != / ] && [ ! -d "$REPO/toolenv" ]; do REPO=$(dirname "$REPO"); done
. "$REPO/toolenv/tests/helpers.sh"

OUTPUT_NAME_HELPER="$SKILL_DIR/scripts/output_name.sh"
[ -f "$OUTPUT_NAME_HELPER" ] || {
    echo "missing output-name helper: $OUTPUT_NAME_HELPER" >&2
    exit 1
}
. "$OUTPUT_NAME_HELPER"

assert_validation_status() {
    local expected=$1 value=$2 actual
    validate_analysis_output_name "$value" >/dev/null 2>&1
    actual=$?
    assert_eq "$actual" "$expected" "OUT_NAME=$(printf %q "$value")"
}

test_rejects_unsafe_output_names() {
    local value
    for value in '' . .. ../victim nested/output /absolute; do
        assert_validation_status 2 "$value"
    done
}

test_accepts_safe_single_component_names() {
    local value
    for value in results .hidden 'safe name' '结果-1_2'; do
        assert_validation_status 0 "$value"
    done
}

run_all

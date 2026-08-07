#!/usr/bin/env bash
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "$HERE/../scripts" && pwd)"
source "$SCRIPT_DIR/trajectory_source.sh"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

passed=0
failed=0

fail() {
    echo "FAIL: $1" >&2
    failed=$((failed + 1))
}

pass() {
    echo "PASS: $1"
    passed=$((passed + 1))
}

assert_pair() {
    local label="$1" expected_cms="$2" expected_trj="$3"
    if [ "$SELECTED_CMS" = "$expected_cms" ] && [ "$SELECTED_TRJ" = "$expected_trj" ]; then
        pass "$label"
    else
        fail "$label (cms='$SELECTED_CMS', trj='$SELECTED_TRJ')"
    fi
}

touch "$TMP_DIR/system-out.cms"
mkdir -p "$TMP_DIR/system_trj"
touch "$TMP_DIR/PL_Analysis_ALIGN-out.cms"
mkdir -p "$TMP_DIR/PL_Analysis_ALIGN_trj"

if select_trajectory_pair "$TMP_DIR" raw; then
    assert_pair "raw main pair" "$TMP_DIR/system-out.cms" "$TMP_DIR/system_trj"
else
    fail "raw main pair should be selectable"
fi

MIXED_DIR="$TMP_DIR/raw-with-custom-align"
mkdir -p "$MIXED_DIR/system_trj" "$MIXED_DIR/custom_ALIGN_trj"
touch "$MIXED_DIR/system-out.cms" "$MIXED_DIR/custom_ALIGN-out.cms"
if select_trajectory_pair "$MIXED_DIR" raw; then
    assert_pair "raw selection ignores custom Align pair" \
        "$MIXED_DIR/system-out.cms" "$MIXED_DIR/system_trj"
else
    fail "raw selection should ignore a custom Align pair"
fi

touch "$TMP_DIR/second-out.cms"
mkdir -p "$TMP_DIR/second_trj"
if select_trajectory_pair "$TMP_DIR" raw >/dev/null 2>&1; then
    fail "ambiguous raw pair should fail"
else
    pass "ambiguous raw pair fails"
fi

if select_trajectory_pair "$TMP_DIR" raw "" "" "$TMP_DIR/second-out.cms"; then
    assert_pair "explicit raw CMS derives trajectory" "$TMP_DIR/second-out.cms" "$TMP_DIR/second_trj"
else
    fail "explicit raw CMS should resolve ambiguity"
fi

mkdir -p "$TMP_DIR/custom_raw_trj"
if select_trajectory_pair "$TMP_DIR" raw "" "" \
        "$TMP_DIR/system-out.cms" "$TMP_DIR/custom_raw_trj"; then
    assert_pair "explicit raw trajectory overrides derivation" \
        "$TMP_DIR/system-out.cms" "$TMP_DIR/custom_raw_trj"
else
    fail "explicit raw trajectory should override derivation"
fi

if RAW_CMS="$TMP_DIR/system-out.cms" RAW_TRJ="$TMP_DIR/custom_raw_trj" \
        select_trajectory_pair "$TMP_DIR" raw; then
    assert_pair "raw environment overrides are honored" \
        "$TMP_DIR/system-out.cms" "$TMP_DIR/custom_raw_trj"
else
    fail "raw environment overrides should be honored"
fi

if select_trajectory_pair "$TMP_DIR" raw "" "" \
        "$TMP_DIR/missing-out.cms" >/dev/null 2>&1; then
    fail "missing explicit raw CMS should fail"
else
    pass "missing explicit raw CMS fails"
fi

if select_trajectory_pair "$TMP_DIR" raw "" "" \
        "$TMP_DIR/system-out.cms" "$TMP_DIR/missing_trj" >/dev/null 2>&1; then
    fail "missing explicit raw trajectory should fail"
else
    pass "missing explicit raw trajectory fails"
fi

if select_trajectory_pair "$TMP_DIR" align; then
    assert_pair "unique align pair" "$TMP_DIR/PL_Analysis_ALIGN-out.cms" "$TMP_DIR/PL_Analysis_ALIGN_trj"
else
    fail "unique align pair should be selectable"
fi

touch "$TMP_DIR/custom_ALIGN-out.cms"
mkdir -p "$TMP_DIR/custom_ALIGN_trj"
if select_trajectory_pair "$TMP_DIR" align "$TMP_DIR/custom_ALIGN-out.cms"; then
    assert_pair "explicit align CMS derives trajectory" "$TMP_DIR/custom_ALIGN-out.cms" "$TMP_DIR/custom_ALIGN_trj"
else
    fail "explicit align CMS should derive a matching trajectory"
fi

if select_trajectory_pair "$TMP_DIR" unknown >/dev/null 2>&1; then
    fail "unknown source should fail"
else
    pass "unknown source fails"
fi

if select_trajectory_pair "$TMP_DIR" align "$TMP_DIR/missing_ALIGN-out.cms" >/dev/null 2>&1; then
    fail "missing explicit align pair should fail"
else
    pass "missing explicit align pair fails"
fi

touch "$TMP_DIR/second_ALIGN-out.cms"
mkdir -p "$TMP_DIR/second_ALIGN_trj"
if select_trajectory_pair "$TMP_DIR" align >/dev/null 2>&1; then
    fail "ambiguous align pair should fail"
else
    pass "ambiguous align pair fails"
fi

for entrypoint in run_analysis.sh run_plip.sh run_mmgbsa.sh; do
    entrypoint_path="$SCRIPT_DIR/$entrypoint"
    if grep -q 'trajectory_source.sh' "$entrypoint_path" && grep -q 'TRAJECTORY_SOURCE' "$entrypoint_path"; then
        pass "$entrypoint integrates trajectory source selector: $entrypoint"
    else
        fail "$entrypoint integrates trajectory source selector"
    fi
done

if [ "$failed" -ne 0 ]; then
    echo "$failed failed, $passed passed" >&2
    exit 1
fi

echo "$passed passed"

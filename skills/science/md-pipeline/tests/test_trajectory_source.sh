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

for entrypoint in run_analysis.sh run_plip.sh; do
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

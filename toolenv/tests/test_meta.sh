#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/meta.sh"

TOOLENV="$TOOLENV_HOME/toolenv"
DEMO="$TESTS_DIR/fixtures/scripts/demo.sh"
export TOOLENV_TOOLS_DIR="$TESTS_DIR/fixtures/tools.d"

test_meta_get_reads_keys() {
    assert_eq "$(toolenv_meta_get "$DEMO" name)" "demo"
    assert_eq "$(toolenv_meta_get "$DEMO" description)" "演示脚本,验证元信息解析"
    assert_eq "$(toolenv_meta_get "$DEMO" usage)" "demo.sh <dir>..."
}

test_meta_get_unknown_key_fails() {
    assert_fail toolenv_meta_get "$DEMO" nosuchkey
}

test_meta_requires_normalizes_commas() {
    assert_eq "$(toolenv_meta_requires "$DEMO")" "faketool conda:demoenv"
}

test_meta_stops_at_first_code_line() {
    local f="$SANDBOX/x.sh"
    cat > "$f" <<'EOF'
#!/usr/bin/env bash
# @name: early
set -u
# @name: late
EOF
    assert_eq "$(toolenv_meta_get "$f" name)" "early"
}

test_requires_subcommand() {
    assert_eq "$("$TOOLENV" requires "$DEMO")" "faketool conda:demoenv"
}

test_index_outputs_markdown_table() {
    local out
    out=$("$TOOLENV" index "$TESTS_DIR/fixtures/scripts")
    assert_contains "$out" "| demo |"
    assert_contains "$out" "演示脚本"
    assert_contains "$out" "demo.sh <dir>..."
}

test_activate_fails_loudly_when_dep_missing() {
    local f="$SANDBOX/needy.sh" out rc
    cat > "$f" <<EOF
#!/usr/bin/env bash
# @name: needy
# @requires: envtool
source "$TOOLENV_HOME/activate.sh"
echo SHOULD-NOT-PRINT
EOF
    chmod +x "$f"
    out=$("$f" 2>&1); rc=$?
    assert_eq "$rc" "1"
    assert_contains "$out" "missing: envtool"
    case "$out" in *SHOULD-NOT-PRINT*) fail "依赖缺失时脚本主体不该执行" ;; esac
}

test_activate_exports_env_for_caller() {
    mkdir -p "$SANDBOX/ft"
    local f="$SANDBOX/good.sh" out
    cat > "$f" <<EOF
#!/usr/bin/env bash
# @name: good
# @requires: faketool
source "$TOOLENV_HOME/activate.sh"
echo "GOT=\$FAKETOOL_HOME"
EOF
    chmod +x "$f"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe --force >/dev/null 2>&1
    out=$("$f" 2>&1)
    assert_contains "$out" "GOT=$SANDBOX/ft"
}

test_run_subcommand_executes_with_env() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe --force >/dev/null 2>&1
    local f="$SANDBOX/plain.sh" out
    cat > "$f" <<'EOF'
#!/usr/bin/env bash
# @name: plain
# @requires: faketool
echo "GOT=${FAKETOOL_HOME:-unset}"
EOF
    chmod +x "$f"
    out=$("$TOOLENV" run "$f" 2>&1)
    assert_contains "$out" "GOT=$SANDBOX/ft"
}

run_all

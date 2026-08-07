#!/usr/bin/env bash
# Behavior tests for aggregate exit statuses in MD analysis shell entry points.
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$SKILL_DIR
while [ "$REPO" != / ] && [ ! -d "$REPO/toolenv" ]; do REPO=$(dirname "$REPO"); done
. "$REPO/toolenv/tests/helpers.sh"

RUN_PLIP="$SKILL_DIR/scripts/run_plip.sh"
RUN_ANALYSIS="$SKILL_DIR/scripts/run_analysis.sh"

install_fake_environment() {
    mkdir -p "$SANDBOX/fake-schrodinger" "$SANDBOX/bin"
    : > "$SANDBOX/calls.log"
    cat > "$SANDBOX/fake-toolenv" <<EOF
#!/usr/bin/env bash
case "\$1" in
    env)
        printf 'export SCHRODINGER=%q\n' "$SANDBOX/fake-schrodinger"
        printf 'export PATH=%q:\$PATH\n' "$SANDBOX/bin"
        ;;
    check) exit 0 ;;
    *) exit 2 ;;
esac
EOF
    chmod +x "$SANDBOX/fake-toolenv"
    printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/bin/AutoMD"
    cat > "$SANDBOX/bin/AutoTRJ" <<'EOF'
#!/usr/bin/env bash
printf 'autotrj %s\n' "$PWD" >> "$FAKE_CALL_LOG"
if [ "${FAIL_STAGE:-}" = autotrj ] && [ "$PWD" = "${FAIL_DIRECTORY:-}" ]; then
    exit 29
fi
exit 0
EOF
    printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/bin/plip"
    cat > "$SANDBOX/bin/python3" <<'EOF'
#!/usr/bin/env bash
cms=""
out=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --cms) cms=$2; shift 2 ;;
        --output-dir) out=$2; shift 2 ;;
        *) shift ;;
    esac
done
printf 'plip %s\n' "$cms" >> "$FAKE_CALL_LOG"
mkdir -p "$out/plip_outputs" "$out/frames_pdb"
case "$cms" in
    "$FAIL_DIRECTORY"/*) exit 23 ;;
esac
exit 0
EOF
    cat > "$SANDBOX/fake-schrodinger/run" <<'EOF'
#!/usr/bin/env bash
printf 'schrodinger %s %s\n' "$PWD" "$*" >> "$FAKE_CALL_LOG"
if [ "${FAIL_STAGE:-}" = report ] && [ "$PWD" = "${FAIL_DIRECTORY:-}" ] \
        && [ "${1:-}" = event_analysis.py ] && [ "${2:-}" = report ]; then
    exit 31
fi
if [ "${FAIL_STAGE:-}" = analyze ] && [ "$PWD" = "${FAIL_DIRECTORY:-}" ] \
        && [ "${1:-}" = event_analysis.py ] && [ "${2:-}" = analyze ]; then
    exit 33
fi
if [ "${FAIL_STAGE:-}" = simulation ] && [ "$PWD" = "${FAIL_DIRECTORY:-}" ] \
        && [ "${1:-}" = analyze_simulation.py ]; then
    exit 34
fi
exit 0
EOF
    chmod +x "$SANDBOX/bin/AutoMD" "$SANDBOX/bin/AutoTRJ" \
        "$SANDBOX/bin/plip" "$SANDBOX/bin/python3" "$SANDBOX/fake-schrodinger/run"
}

make_md_directory_at() {
    local directory=$1
    mkdir -p "$directory/system_trj"
    : > "$directory/system-out.cms"
    : > "$directory/system-out.eaf"
    printf '%s\n' "$directory"
}

run_analysis_many() {
    local first=$1 second=$2
    shift 2
    FAKE_CALL_LOG="$SANDBOX/calls.log" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_ANALYSIS" "$first" "$second" \
        > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

run_plip_many() {
    local first=$1 second=$2
    shift 2
    FAKE_CALL_LOG="$SANDBOX/calls.log" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_PLIP" "$first" "$second" \
        > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

assert_status() {
    local expected=$1
    shift
    "$@"
    local actual=$?
    assert_eq "$actual" "$expected"
}

assert_call_count() {
    assert_eq "$(wc -l < "$SANDBOX/calls.log")" "$1" "external call count"
}

test_plip_failure_survives_cleanup_and_later_directory_runs() {
    install_fake_environment
    local failed successful
    failed=$(make_md_directory_at "$SANDBOX/fail-first")
    successful=$(make_md_directory_at "$SANDBOX/success-second")

    assert_status 23 run_plip_many "$failed" "$successful" env \
        FAIL_DIRECTORY="$failed" OUT_NAME=plip-test

    assert_call_count 2
    [ ! -d "$failed/plip-test/plip_outputs" ] || fail "failed PLIP intermediates were not cleaned"
    [ ! -d "$failed/plip-test/frames_pdb" ] || fail "failed PLIP frames were not cleaned"
    [ ! -d "$successful/plip-test/plip_outputs" ] || fail "successful PLIP intermediates were not cleaned"
    [ ! -d "$successful/plip-test/frames_pdb" ] || fail "successful PLIP frames were not cleaned"
}

test_plip_all_success_returns_zero() {
    install_fake_environment
    local first second
    first=$(make_md_directory_at "$SANDBOX/success-first")
    second=$(make_md_directory_at "$SANDBOX/success-second")

    assert_status 0 run_plip_many "$first" "$second" env \
        FAIL_DIRECTORY="$SANDBOX/never" OUT_NAME=plip-test

    assert_call_count 2
}

test_analysis_autotrj_failure_propagates_and_later_directory_runs() {
    install_fake_environment
    local failed successful
    failed=$(make_md_directory_at "$SANDBOX/fail-first")
    successful=$(make_md_directory_at "$SANDBOX/success-second")

    assert_status 29 run_analysis_many "$failed" "$successful" env \
        FAIL_STAGE=autotrj FAIL_DIRECTORY="$failed" KEEP_CLEAN=1

    assert_eq "$(grep -c '^autotrj ' "$SANDBOX/calls.log")" 2 "AutoTRJ call count"
}

test_analysis_report_failure_propagates_and_later_directory_runs() {
    install_fake_environment
    local failed successful
    failed=$(make_md_directory_at "$SANDBOX/fail-first")
    successful=$(make_md_directory_at "$SANDBOX/success-second")

    assert_status 31 run_analysis_many "$failed" "$successful" env \
        FAIL_STAGE=report FAIL_DIRECTORY="$failed" KEEP_CLEAN=1

    assert_eq "$(grep -c '^autotrj ' "$SANDBOX/calls.log")" 2 "AutoTRJ call count"
    assert_eq "$(grep -c '^schrodinger ' "$SANDBOX/calls.log")" 2 "report call count"
}

assert_analysis_generation_failure() {
    local stage=$1 expected=$2
    install_fake_environment
    local failed successful
    failed=$(make_md_directory_at "$SANDBOX/fail-first")
    successful=$(make_md_directory_at "$SANDBOX/success-second")
    rm -f "$failed/system-out.eaf" "$successful/system-out.eaf"

    assert_status "$expected" run_analysis_many "$failed" "$successful" env \
        FAIL_STAGE="$stage" FAIL_DIRECTORY="$failed" KEEP_CLEAN=1

    assert_eq "$(grep -c '^autotrj ' "$SANDBOX/calls.log")" 2 "AutoTRJ call count"
}

test_analysis_eaf_analyze_failure_propagates() {
    assert_analysis_generation_failure analyze 33
}

test_analysis_eaf_simulation_failure_propagates() {
    assert_analysis_generation_failure simulation 34
}

test_analysis_missing_generated_eaf_remains_warning_only() {
    install_fake_environment
    local first second
    first=$(make_md_directory_at "$SANDBOX/success-first")
    second=$(make_md_directory_at "$SANDBOX/success-second")
    rm -f "$first/system-out.eaf" "$second/system-out.eaf"

    assert_status 0 run_analysis_many "$first" "$second" env \
        FAIL_STAGE=none FAIL_DIRECTORY="$SANDBOX/never" KEEP_CLEAN=1

    assert_contains "$(cat "$SANDBOX/stdout.log")" "WARN: 找不到可用 .eaf"
}

test_analysis_all_success_returns_zero() {
    install_fake_environment
    local first second
    first=$(make_md_directory_at "$SANDBOX/success-first")
    second=$(make_md_directory_at "$SANDBOX/success-second")

    assert_status 0 run_analysis_many "$first" "$second" env \
        FAIL_STAGE=none FAIL_DIRECTORY="$SANDBOX/never" KEEP_CLEAN=1

    assert_eq "$(grep -c '^autotrj ' "$SANDBOX/calls.log")" 2 "AutoTRJ call count"
    assert_eq "$(grep -c '^schrodinger ' "$SANDBOX/calls.log")" 2 "report call count"
}

run_all

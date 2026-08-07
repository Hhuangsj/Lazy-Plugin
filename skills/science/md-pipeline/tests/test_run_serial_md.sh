#!/usr/bin/env bash
# Contract tests for serial MD path resolution, GPU gating, and analysis delegation.
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$SKILL_DIR
while [ "$REPO" != / ] && [ ! -d "$REPO/toolenv" ]; do REPO=$(dirname "$REPO"); done
. "$REPO/toolenv/tests/helpers.sh"

RUN_SERIAL="$SKILL_DIR/scripts/run_serial_md.sh"
SERIAL_ENV_ARGS=()

install_fake_environment() {
    mkdir -p "$SANDBOX/bin" "$SANDBOX/fake-schrodinger" "$SANDBOX/records"
    printf '0\n' > "$SANDBOX/records/nvidia.count"
    : > "$SANDBOX/records/analysis.calls"
    SERIAL_ENV_ARGS=()

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

    cat > "$SANDBOX/bin/AutoMD" <<'EOF'
#!/usr/bin/env bash
input=""
printf '%s\0' "$@" > "$FAKE_RECORD_DIR/automd.argv"
printf '%s\n' "${CUDA_VISIBLE_DEVICES:-}" > "$FAKE_RECORD_DIR/automd.cuda"
printf '%s\n' "${CUDA_VISIBLE_DEVICES:-}" >> "$FAKE_RECORD_DIR/automd.cuda.calls"
while [ "$#" -gt 0 ]; do
    if [ "$1" = -i ]; then input=$2; break; fi
    shift
done
[ -n "$input" ] || exit 91
[ "${FAIL_AUTOMD:-0}" = 1 ] && exit 36
if [ "${AUTOMD_BARRIER_COUNT:-0}" -gt 0 ]; then
    : > "$FAKE_RECORD_DIR/automd.started.${CUDA_VISIBLE_DEVICES:-none}"
    for attempt in $(seq 1 250); do
        started=$(find "$FAKE_RECORD_DIR" -maxdepth 1 -name 'automd.started.*' | wc -l)
        [ "$started" -ge "$AUTOMD_BARRIER_COUNT" ] && break
        sleep 0.02
    done
    [ "$started" -ge "$AUTOMD_BARRIER_COUNT" ] || exit 94
fi
if [ "${BLOCK_AUTOMD:-0}" = 1 ]; then
    : > "$FAKE_RECORD_DIR/automd.started"
    while [ ! -f "$FAKE_RECORD_DIR/automd.release" ]; do sleep 0.02; done
fi
mkdir -p "${input%.mae}-123-md"
if [ "${SPAWN_LOCK_HOLDER:-0}" = 1 ]; then
    /bin/bash -c 'while [ ! -f "$FAKE_RECORD_DIR/lock-holder.release" ]; do sleep 0.02; done' &
    printf '%s\n' "$!" > "$FAKE_RECORD_DIR/lock-holder.pid"
fi
EOF

    cat > "$SANDBOX/bin/fake-analysis" <<'EOF'
#!/usr/bin/env bash
printf '%s\0' "$@" > "$FAKE_RECORD_DIR/analysis.argv"
printf '%s\n' "${1:-}" >> "$FAKE_RECORD_DIR/analysis.calls"
{
    printf 'TRAJECTORY_SOURCE=%s\n' "${TRAJECTORY_SOURCE:-}"
    printf 'FRAMES=%s\n' "${FRAMES:-}"
    printf 'RECEPTOR_ASL=%s\n' "${RECEPTOR_ASL:-}"
    printf 'LIGAND_ASL=%s\n' "${LIGAND_ASL:-}"
} > "$FAKE_RECORD_DIR/analysis.env"
if [ "${FAIL_ANALYSIS:-0}" = 1 ]; then exit 37; fi
if [ -n "${FAIL_ANALYSIS_MATCH:-}" ]; then
    case "${1:-}" in *"$FAIL_ANALYSIS_MATCH"*) exit 37 ;; esac
fi
exit 0
EOF

    cat > "$SANDBOX/bin/nvidia-smi" <<'EOF'
#!/usr/bin/env bash
count=$(< "$FAKE_RECORD_DIR/nvidia.count")
printf '%s\n' "$((count + 1))" > "$FAKE_RECORD_DIR/nvidia.count"
case "$*" in
    *--query-gpu=index,uuid*) printf '0, GPU-0\n1, GPU-1\n2, GPU-2\n' ;;
    *--query-compute-apps=*) ;;
    *--query-gpu=utilization.gpu,memory.used*) printf '0, 0\n' ;;
    *) exit 92 ;;
esac
EOF

    cat > "$SANDBOX/bin/bash" <<'EOF'
#!/bin/bash
if [ "${1:-}" = -lc ]; then
    printf '%s\0' "$@" > "$FAKE_RECORD_DIR/nested-bash.argv"
    exit 88
fi
exec /bin/bash "$@"
EOF

    chmod +x "$SANDBOX/fake-toolenv" "$SANDBOX/bin/AutoMD" \
        "$SANDBOX/bin/fake-analysis" "$SANDBOX/bin/nvidia-smi" \
        "$SANDBOX/bin/bash"
}

make_pending_workdir() {
    local directory=$1 input=$2 pending=${3:-md_pending_serial.list}
    mkdir -p "$directory/$(dirname "$pending")"
    printf '%s\n' "$input" > "$directory/$pending"
    : > "$directory/$input"
}

run_serial_from() {
    local invocation_directory=$1
    shift
    mkdir -p "$invocation_directory"
    (
        cd "$invocation_directory" || exit 98
        env "${SERIAL_ENV_ARGS[@]}" \
            FAKE_RECORD_DIR="$SANDBOX/records" \
            TOOLENV_BIN="$SANDBOX/fake-toolenv" \
            ANALYSIS_RUNNER="$SANDBOX/bin/fake-analysis" \
            PATH="/usr/bin:/bin" \
            /bin/bash "$RUN_SERIAL" "$@"
    ) > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

assert_status() {
    local expected=$1
    shift
    "$@"
    local actual=$?
    assert_eq "$actual" "$expected" "exit status"
}

load_argv() {
    local file=$1
    CALL_ARGS=()
    mapfile -d '' -t CALL_ARGS < "$file"
}

assert_argv_option() {
    local file=$1 option=$2 expected=$3 index
    load_argv "$file"
    for ((index = 0; index < ${#CALL_ARGS[@]}; index++)); do
        if [ "${CALL_ARGS[$index]}" = "$option" ]; then
            assert_eq "${CALL_ARGS[$((index + 1))]:-}" "$expected" "$option value"
            return
        fi
    done
    fail "argv in $file has no $option option"
}

test_invocation_directory_supplies_all_default_paths() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" sample.mae

    assert_status 0 run_serial_from "$work" --dry-run --gpu 0

    assert_contains "$(cat "$SANDBOX/stderr.log")" \
        "$work/md_completed_serial.list"
    [ -f "$work/md_completed_serial.list" ] || fail "default completed list was not created in invocation directory"
    [ -f "$work/md_failed_serial.list" ] || fail "default failed list was not created in invocation directory"
    [ -f "$work/run_serial_md.log" ] || fail "default log was not created in invocation directory"
}

test_workdir_supplies_all_default_paths() {
    install_fake_environment
    local work=$SANDBOX/work caller=$SANDBOX/caller
    make_pending_workdir "$work" sample.mae

    assert_status 0 run_serial_from "$caller" \
        --workdir "$work" --dry-run --gpu 0

    assert_contains "$(cat "$SANDBOX/stderr.log")" \
        "$work/md_completed_serial.list"
    [ -f "$work/md_completed_serial.list" ] || fail "completed list did not follow --workdir"
    [ -f "$work/md_failed_serial.list" ] || fail "failed list did not follow --workdir"
    [ -f "$work/run_serial_md.log" ] || fail "log did not follow --workdir"
}

test_relative_workdir_resolves_from_invocation_directory() {
    install_fake_environment
    local root=$SANDBOX/root work=$SANDBOX/root/work caller=$SANDBOX/root/caller
    make_pending_workdir "$work" sample.mae

    assert_status 0 run_serial_from "$caller" \
        --workdir ../work --dry-run --gpu 0

    assert_contains "$(cat "$SANDBOX/stderr.log")" \
        "$work/md_completed_serial.list"
    [ -f "$work/md_completed_serial.list" ] \
        || fail "relative --workdir did not resolve from invocation directory"
}

test_relative_state_paths_resolve_against_final_workdir_independent_of_order() {
    install_fake_environment
    local work=$SANDBOX/work caller=$SANDBOX/caller
    make_pending_workdir "$work" sample.mae state/pending.list

    assert_status 0 run_serial_from "$caller" \
        --list state/pending.list \
        --completed state/completed.list \
        --failed state/failed.list \
        --workdir "$work" --dry-run --gpu 0

    assert_contains "$(cat "$SANDBOX/stderr.log")" "$work/state/completed.list"
    [ -f "$work/state/completed.list" ] || fail "relative completed list did not resolve under workdir"
    [ -f "$work/state/failed.list" ] || fail "relative failed list did not resolve under workdir"
}

test_value_options_without_arguments_return_usage_status() {
    install_fake_environment
    local option
    local -a options=(
        --workdir --list --completed --failed --gpu --gpus --sleep
        --gpu-stable-checks --gpu-check-interval --max-free-util --max-free-mem-mb
    )

    for option in "${options[@]}"; do
        assert_status 2 run_serial_from "$SANDBOX/caller" "$option"
        assert_contains "$(cat "$SANDBOX/stderr.log")" "requires a value"
    done
}

test_value_options_reject_a_following_option_as_their_value() {
    install_fake_environment

    assert_status 2 run_serial_from "$SANDBOX/caller" --gpu --dry-run
    assert_contains "$(cat "$SANDBOX/stderr.log")" "Option --gpu requires a value"
}

test_numeric_options_reject_invalid_values_before_state_files_are_created() {
    install_fake_environment
    local index=0 work option value
    local -a cases=(
        '--sleep|0' '--sleep|abc' '--sleep|86401'
        '--gpu-stable-checks|0' '--gpu-stable-checks|abc' '--gpu-stable-checks|101'
        '--gpu-check-interval|abc' '--gpu-check-interval|86401'
        '--max-free-util|abc' '--max-free-util|101'
        '--max-free-mem-mb|abc' '--max-free-mem-mb|1000000001'
    )

    for entry in "${cases[@]}"; do
        option=${entry%%|*}
        value=${entry#*|}
        work="$SANDBOX/numeric-$index"
        index=$((index + 1))
        make_pending_workdir "$work" sample.mae

        assert_status 2 run_serial_from "$work" \
            "$option" "$value" --dry-run --gpu 0

        assert_contains "$(cat "$SANDBOX/stderr.log")" "$option"
        [ ! -e "$work/md_completed_serial.list" ] \
            || fail "$option=$value created state before validation"
    done
}

test_gpu_options_reject_empty_malformed_duplicate_and_non_numeric_values() {
    install_fake_environment
    local index=0 work option value
    local -a cases=(
        '--gpu|' '--gpu|abc' '--gpu|1025'
        '--gpus|' '--gpus|,' '--gpus|0,' '--gpus|,0' '--gpus|0,,1'
        '--gpus|a' '--gpus|0,a' '--gpus|0,0' '--gpus|01,1' '--gpus|1025'
    )

    for entry in "${cases[@]}"; do
        option=${entry%%|*}
        value=${entry#*|}
        work="$SANDBOX/gpu-$index"
        index=$((index + 1))
        make_pending_workdir "$work" sample.mae

        assert_status 2 run_serial_from "$work" \
            "$option" "$value" --submit-immediately

        assert_contains "$(cat "$SANDBOX/stderr.log")" "$option"
        [ ! -e "$work/md_completed_serial.list" ] \
            || fail "$option=$(printf %q "$value") created state before validation"
    done
}

test_gpu_options_normalize_leading_zeros_before_submission() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(FAIL_AUTOMD=1)

    assert_status 1 run_serial_from "$work" --gpu 002 --submit-immediately

    assert_eq "$(< "$SANDBOX/records/automd.cuda")" 2 "normalized CUDA device"
}

test_second_runner_for_same_workdir_fails_before_duplicate_submission() {
    install_fake_environment
    local work=$SANDBOX/work first_pid attempt
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(BLOCK_AUTOMD=1)

    run_serial_from "$work" --gpu 2 --submit-immediately &
    first_pid=$!
    for ((attempt = 0; attempt < 100; attempt++)); do
        [ -f "$SANDBOX/records/automd.started" ] && break
        sleep 0.02
    done
    if [ ! -f "$SANDBOX/records/automd.started" ]; then
        fail "first runner did not reach AutoMD"
        : > "$SANDBOX/records/automd.release"
        wait "$first_pid" 2>/dev/null || true
        return
    fi

    SERIAL_ENV_ARGS=()
    assert_status 1 run_serial_from "$work" --gpu 2 --submit-immediately
    assert_contains "$(cat "$SANDBOX/stderr.log")" "already running"

    : > "$SANDBOX/records/automd.release"
    wait "$first_pid" 2>/dev/null || fail "first runner failed after release"
}

test_external_descendant_cannot_keep_run_lock_after_runner_exits() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(SPAWN_LOCK_HOLDER=1)

    assert_status 0 run_serial_from "$work" --gpu 2 --submit-immediately
    [ -f "$SANDBOX/records/lock-holder.pid" ] \
        || fail "fake AutoMD did not leave a descendant process"

    SERIAL_ENV_ARGS=()
    assert_status 0 run_serial_from "$work" --gpu 2 --submit-immediately

    : > "$SANDBOX/records/lock-holder.release"
}

test_single_gpu_immediate_submission_skips_gpu_probe() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(FAIL_AUTOMD=1)

    assert_status 1 run_serial_from "$work" \
        --gpu 2 --submit-immediately --gpu-stable-checks 1

    assert_eq "$(< "$SANDBOX/records/nvidia.count")" 0 "nvidia-smi call count"
    assert_eq "$(< "$SANDBOX/records/automd.cuda")" 2 "selected CUDA device"
}

test_multi_gpu_dry_run_skips_gpu_probe() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" first.mae
    printf 'second.mae\n' >> "$work/md_pending_serial.list"
    : > "$work/second.mae"

    assert_status 0 run_serial_from "$work" \
        --dry-run --gpus 0,1 --gpu-stable-checks 1

    assert_eq "$(< "$SANDBOX/records/nvidia.count")" 0 "nvidia-smi call count"
    assert_eq "$(grep -c 'CUDA_VISIBLE_DEVICES=' "$SANDBOX/stdout.log")" 2 \
        "printed AutoMD command count"
}

test_multi_gpu_run_returns_nonzero_after_continuing_past_task_failure() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" first.mae
    printf 'second.mae\n' >> "$work/md_pending_serial.list"
    : > "$work/second.mae"
    SERIAL_ENV_ARGS=(FAIL_ANALYSIS_MATCH=first-123-md)

    assert_status 1 run_serial_from "$work" --gpus 0 --submit-immediately

    assert_eq "$(wc -l < "$SANDBOX/records/analysis.calls")" 2 \
        "analysis calls after one task failure"
    assert_contains "$(cat "$work/md_failed_serial.list")" first.mae
    assert_contains "$(cat "$work/md_completed_serial.list")" second.mae
}

test_two_gpu_parent_waits_for_all_workers_and_reports_any_failure() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" first.mae
    printf 'second.mae\n' >> "$work/md_pending_serial.list"
    : > "$work/second.mae"
    SERIAL_ENV_ARGS=(FAIL_ANALYSIS_MATCH=first-123-md AUTOMD_BARRIER_COUNT=2)

    assert_status 1 run_serial_from "$work" --gpus 0,1 --submit-immediately

    assert_eq "$(wc -l < "$SANDBOX/records/analysis.calls")" 2 \
        "analysis calls across two workers"
    assert_eq "$(sort -u "$SANDBOX/records/automd.cuda.calls")" $'0\n1' \
        "distinct CUDA devices used by both workers"
    assert_contains "$(cat "$work/md_failed_serial.list")" first.mae
    assert_contains "$(cat "$work/md_completed_serial.list")" second.mae
}

test_successful_run_delegates_analysis_with_adapted_environment() {
    install_fake_environment
    local work=$SANDBOX/work expected_md=$SANDBOX/work/sample-123-md
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(
        FRAMES=4000 ANALYSIS_FRAMES=101:4001:40
        RECEPTOR_ASL='chain.name A' LIGAND_ASL='chain.name B'
        AUTOMD_CPU_HOST=cpu-host AUTOMD_GPU_HOST=gpu-host
    )

    assert_status 0 run_serial_from "$work" \
        --gpu 2 --submit-immediately --gpu-stable-checks 1

    assert_argv_option "$SANDBOX/records/automd.argv" -o 4000
    assert_argv_option "$SANDBOX/records/automd.argv" -P 'chain.name A'
    assert_argv_option "$SANDBOX/records/automd.argv" -L 'chain.name B'
    assert_argv_option "$SANDBOX/records/automd.argv" -H cpu-host
    assert_argv_option "$SANDBOX/records/automd.argv" -G gpu-host
    load_argv "$SANDBOX/records/analysis.argv"
    assert_eq "${#CALL_ARGS[@]}" 1 "analysis argv length"
    assert_eq "${CALL_ARGS[0]:-}" "$expected_md" "analysis MD directory"
    assert_contains "$(cat "$SANDBOX/records/analysis.env")" 'TRAJECTORY_SOURCE=raw'
    assert_contains "$(cat "$SANDBOX/records/analysis.env")" 'FRAMES=101:4001:40'
    assert_contains "$(cat "$SANDBOX/records/analysis.env")" 'RECEPTOR_ASL=chain.name A'
    assert_contains "$(cat "$SANDBOX/records/analysis.env")" 'LIGAND_ASL=chain.name B'
    assert_contains "$(cat "$work/md_completed_serial.list")" sample.mae
    [ ! -s "$work/md_failed_serial.list" ] || fail "successful item was recorded as failed"
}

test_analysis_failure_is_recorded_without_completion() {
    install_fake_environment
    local work=$SANDBOX/work
    make_pending_workdir "$work" sample.mae
    SERIAL_ENV_ARGS=(FAIL_ANALYSIS=1)

    assert_status 1 run_serial_from "$work" \
        --gpu 2 --submit-immediately --gpu-stable-checks 1

    [ -f "$SANDBOX/records/analysis.argv" ] || fail "analysis runner was not invoked"
    assert_contains "$(cat "$work/md_failed_serial.list")" sample.mae
    [ ! -s "$work/md_completed_serial.list" ] || fail "failed analysis was recorded as completed"
}

run_all

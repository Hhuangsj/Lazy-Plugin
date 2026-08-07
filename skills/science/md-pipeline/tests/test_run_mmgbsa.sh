#!/usr/bin/env bash
# Contract tests for the optional ligand-residue MM/GBSA decomposition runner.
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$SKILL_DIR
while [ "$REPO" != "/" ] && [ ! -d "$REPO/toolenv" ]; do REPO=$(dirname "$REPO"); done
. "$REPO/toolenv/tests/helpers.sh"

RUN_MMGBSA="$SKILL_DIR/scripts/run_mmgbsa.sh"
SCRIPTS_DIR="$SKILL_DIR/scripts"

install_fake_environment() {
    mkdir -p "$SANDBOX/fake-schrodinger" "$SANDBOX/bin" "$SANDBOX/calls"
    printf '0\n' > "$SANDBOX/calls/count"
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
    printf '#!/usr/bin/env bash\nexit 0\n' > "$SANDBOX/bin/AutoTRJ"
    chmod +x "$SANDBOX/bin/AutoMD" "$SANDBOX/bin/AutoTRJ"
    cat > "$SANDBOX/fake-schrodinger/run" <<'EOF'
#!/usr/bin/env bash
set -u

call_number=$(< "$FAKE_CALL_DIR/count")
call_number=$((call_number + 1))
printf '%s\n' "$call_number" > "$FAKE_CALL_DIR/count"
printf '%s\0' "$@" > "$FAKE_CALL_DIR/$call_number.argv"

fails_for_path() {
    [ "${FAIL_STAGE:-}" = "$1" ] || return 1
    [ -z "${FAIL_DIRECTORY:-}" ] && return 0
    case "$2" in "$FAIL_DIRECTORY"/*) return 0;; esac
    return 1
}

if [ "$1" = python3 ] && [ "$2" = - ]; then
    shift
    exec /usr/bin/python3 "$@"
fi

if [ "$1" = thermal_mmgbsa.py ]; then
    if fails_for_path thermal "$2"; then exit 8; fi
    job_name=""
    while [ "$#" -gt 0 ]; do
        if [ "$1" = -j ]; then job_name=$2; shift 2; continue; fi
        shift
    done
    case "${PRIME_OUTPUTS:-1}" in
        0) ;;
        1) : > "${job_name}-prime-out.maegz" ;;
        2) : > "${job_name}-prime-out.maegz"; : > "extra-prime-out.maegz" ;;
    esac
    exit 0
fi

script_name=$(basename "$2")
case "$script_name" in
    prepare_ligand_decomp.py)
        out_dir=""
        cms=$3
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --out-dir ]; then out_dir=$2; break; fi
            shift
        done
        mkdir -p "$out_dir"
        printf '{"schema_version": 1, "status": "running"}\n' > "$out_dir/decomp_manifest.json"
        if fails_for_path prepare "$cms"; then exit 7; fi
        case "${PREPARE_RESULT_MODE:-valid}" in
            malformed)
                printf '{not-json\n' > "$out_dir/prepare_result.json"
                ;;
            missing)
                printf '{"analysis_ligand_asl": "chain.name L", "residue_map": "%s/residue map.json"}\n' "$out_dir" > "$out_dir/prepare_result.json"
                ;;
            non_string)
                printf '{"analysis_cms": 1, "analysis_ligand_asl": "chain.name L", "residue_map": "%s/residue map.json"}\n' "$out_dir" > "$out_dir/prepare_result.json"
                ;;
            empty)
                printf '{"analysis_cms": "", "analysis_ligand_asl": "chain.name L", "residue_map": "%s/residue map.json"}\n' "$out_dir" > "$out_dir/prepare_result.json"
                ;;
            valid)
                printf '{"analysis_cms": "%s/analysis cms.cms", "analysis_ligand_asl": "chain.name L and res.ptype UNK", "residue_map": "%s/residue map.json"}\n' "$out_dir" "$out_dir" > "$out_dir/prepare_result.json"
                : > "$out_dir/analysis cms.cms"
                : > "$out_dir/residue map.json"
                ;;
        esac
        exit 0
        ;;
    prime_mmgbsa_residue_decomp.py)
        trajectory=""
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --trajectory ]; then trajectory=$2; break; fi
            shift
        done
        if fails_for_path aggregation "$trajectory"; then exit 9; fi
        exit 0
        ;;
    mmgbsa_decomp_contract.py)
        stage=""
        contract_args=("$@")
        for ((argument_index = 0; argument_index < ${#contract_args[@]}; argument_index++)); do
            if [ "${contract_args[$argument_index]}" = --stage ]; then
                stage=${contract_args[$((argument_index + 1))]}
                break
            fi
        done
        if [ "${FAIL_MANIFEST_STAGE:-}" = "$stage" ]; then
            echo "injected manifest-fail failure for $stage" >&2
            exit 70
        fi
        shift
        exec /usr/bin/python3 "$@"
        ;;
esac

exit 2
EOF
    chmod +x "$SANDBOX/fake-schrodinger/run"
}

reset_call_capture() {
    rm -f "$SANDBOX/calls"/*.argv
    printf '0\n' > "$SANDBOX/calls/count"
}

make_md_directory() {
    make_md_directory_at "$SANDBOX/md"
}

make_md_directory_at() {
    local directory=$1
    mkdir -p "$directory"
    : > "$directory/complex-out.cms"
    mkdir "$directory/complex_trj"
    printf '%s\n' "$directory"
}

run_mmgbsa() {
    local directory=$1
    shift
    FAKE_CALL_DIR="$SANDBOX/calls" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_MMGBSA" "$directory" > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

run_mmgbsa_many() {
    local first=$1 second=$2
    shift 2
    FAKE_CALL_DIR="$SANDBOX/calls" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_MMGBSA" "$first" "$second" > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

assert_status() {
    local expected=$1
    shift
    "$@"
    local actual=$?
    assert_eq "$actual" "$expected"
}

load_call() {
    CALL_ARGS=()
    mapfile -d '' -t CALL_ARGS < "$SANDBOX/calls/$1.argv"
}

assert_call_count() {
    assert_eq "$(< "$SANDBOX/calls/count")" "$1"
}

assert_argv() {
    local index=$1 actual_index expected_index
    shift
    load_call "$index"
    assert_eq "${#CALL_ARGS[@]}" "$#" "argv length for call $index"
    expected_index=1
    for expected in "$@"; do
        actual_index=$((expected_index - 1))
        assert_eq "${CALL_ARGS[$actual_index]:-}" "$expected" "argv[$actual_index] for call $index"
        expected_index=$((expected_index + 1))
    done
}

call_kind() {
    load_call "$1"
    if [ "${CALL_ARGS[0]:-}" = thermal_mmgbsa.py ]; then
        printf 'thermal\n'
    elif [ "${CALL_ARGS[0]:-}" = python3 ] && [ "${CALL_ARGS[1]:-}" = - ]; then
        printf 'json\n'
    else
        case "$(basename "${CALL_ARGS[1]:-}")" in
            prepare_ligand_decomp.py) printf 'prepare\n' ;;
            prime_mmgbsa_residue_decomp.py) printf 'aggregation\n' ;;
            mmgbsa_decomp_contract.py) printf 'manifest\n' ;;
            *) printf 'unknown\n' ;;
        esac
    fi
}

assert_call_kinds() {
    local expected_index=1 expected
    assert_call_count "$#"
    for expected in "$@"; do
        assert_eq "$(call_kind "$expected_index")" "$expected" "call $expected_index"
        expected_index=$((expected_index + 1))
    done
}

assert_kind_count() {
    local wanted=$1 expected=$2 index count=0
    local total=$(< "$SANDBOX/calls/count")
    for ((index = 1; index <= total; index++)); do
        [ "$(call_kind "$index")" = "$wanted" ] && count=$((count + 1))
    done
    assert_eq "$count" "$expected" "$wanted call count"
}

assert_manifest_failure() {
    local directory=$1 stage=$2 code=$3 log=$4
    local manifest="$directory/mmgbsa_last100ns/residue_decomp/decomp_manifest.json"
    local -a values
    mapfile -t values < <(python3 - "$manifest" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
error = manifest.get("error", {})
print(manifest.get("status", ""))
print(error.get("stage", ""))
print(error.get("return_code", ""))
print(error.get("log", ""))
PY
)
    assert_eq "${values[0]:-}" failed "manifest status"
    assert_eq "${values[1]:-}" "$stage" "manifest stage"
    assert_eq "${values[2]:-}" "$code" "manifest return code"
    assert_eq "${values[3]:-}" "$log" "manifest log"
    [ -f "$log" ] || fail "manifest log does not exist: $log"
}

assert_default_thermal() {
    local directory=$1 decomp_value=$2
    reset_call_capture
    if [ -n "$decomp_value" ]; then
        assert_status 0 run_mmgbsa "$directory" env DECOMP="$decomp_value" START=11 END=22 STEP=3 NJOBS=4
    else
        assert_status 0 run_mmgbsa "$directory" env START=11 END=22 STEP=3 NJOBS=4
    fi
    assert_call_kinds thermal
    assert_argv 1 thermal_mmgbsa.py "$directory/complex-out.cms" \
        -lig_asl 'res.ptype UNK' -j complex \
        -start_frame 11 -end_frame 22 -step_size 3 -NJOBS 4 -HOST localhost:4
    assert_contains "$(cat "$SANDBOX/stdout.log")" "  -> $directory/mmgbsa_last100ns/complex-prime-out.csv"
}

test_default_unset_and_zero_keep_one_exact_thermal_invocation() {
    install_fake_environment
    local directory
    directory=$(make_md_directory)

    assert_default_thermal "$directory" ""
    assert_default_thermal "$directory" 0
}

test_decomp_uses_exact_argv_and_lossless_call_order() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"

    assert_status 0 run_mmgbsa "$directory" env \
        DECOMP=1 START=11 END=22 STEP=3 NJOBS=4 \
        LIG_ASL='res.ptype UNK and chain.name "A B"' \
        DECOMP_PROPERTIES='dG_Bind,Coulomb' \
        SYNERGY_FRAGMENT_DIR="$SANDBOX/synergy directory" \
        SYNERGY_ADAPTER_PYTHON="$SANDBOX/adapter python"

    assert_call_kinds prepare json json json thermal aggregation
    assert_argv 1 python3 "$SCRIPTS_DIR/prepare_ligand_decomp.py" "$directory/complex-out.cms" \
        --lig-asl 'res.ptype UNK and chain.name "A B"' --out-dir "$decomp_dir" \
        --synergy-dir "$SANDBOX/synergy directory" --adapter-python "$SANDBOX/adapter python"
    assert_argv 2 python3 - "$decomp_dir/prepare_result.json" analysis_cms
    assert_argv 3 python3 - "$decomp_dir/prepare_result.json" analysis_ligand_asl
    assert_argv 4 python3 - "$decomp_dir/prepare_result.json" residue_map
    assert_argv 5 thermal_mmgbsa.py "$decomp_dir/analysis cms.cms" \
        -lig_asl 'chain.name L and res.ptype UNK' -j complex \
        -start_frame 11 -end_frame 23 -step_size 3 -NJOBS 4 -HOST localhost:4
    assert_argv 6 python3 "$SCRIPTS_DIR/prime_mmgbsa_residue_decomp.py" \
        --prime-maegz "$directory/mmgbsa_last100ns/complex-prime-out.maegz" \
        --residue-map "$decomp_dir/residue map.json" --trajectory "$directory/complex_trj" \
        --start 11 --end 22 --step 3 \
        --frame-csv "$decomp_dir/residue_decomp_frames.csv" \
        --summary-csv "$decomp_dir/residue_decomp_summary.csv" --manifest "$decomp_dir/decomp_manifest.json" \
        --properties dG_Bind,Coulomb
}

test_decomp_omits_empty_optional_arguments() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"

    assert_status 0 run_mmgbsa "$directory" env DECOMP=1

    assert_call_kinds prepare json json json thermal aggregation
    assert_argv 1 python3 "$SCRIPTS_DIR/prepare_ligand_decomp.py" "$directory/complex-out.cms" \
        --lig-asl 'res.ptype UNK' --out-dir "$decomp_dir"
    assert_argv 6 python3 "$SCRIPTS_DIR/prime_mmgbsa_residue_decomp.py" \
        --prime-maegz "$directory/mmgbsa_last100ns/complex-prime-out.maegz" \
        --residue-map "$decomp_dir/residue map.json" --trajectory "$directory/complex_trj" \
        --start 1000 --end 2000 --step 20 \
        --frame-csv "$decomp_dir/residue_decomp_frames.csv" \
        --summary-csv "$decomp_dir/residue_decomp_summary.csv" --manifest "$decomp_dir/decomp_manifest.json"
}

test_decomp_links_source_trajectory_beside_analysis_cms() {
    install_fake_environment
    local directory decomp_dir linked_trajectory
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    linked_trajectory="$decomp_dir/complex_trj"

    assert_status 0 run_mmgbsa "$directory" env DECOMP=1

    [ -L "$linked_trajectory" ] || fail "analysis trajectory link is missing"
    assert_eq "$(readlink "$linked_trajectory")" "$directory/complex_trj"
}

assert_prepare_result_failure() {
    local mode=$1 directory=$2 decomp_dir
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    reset_call_capture

    assert_status 1 run_mmgbsa "$directory" env DECOMP=1 PREPARE_RESULT_MODE="$mode"

    assert_call_kinds prepare json manifest
    assert_manifest_failure "$directory" prepare_result 1 "$decomp_dir/prepare_ligand_decomp.log"
}

test_malformed_prepare_result_stops_before_thermal_and_marks_manifest() {
    install_fake_environment
    assert_prepare_result_failure malformed "$(make_md_directory_at "$SANDBOX/malformed")"
}

test_missing_prepare_result_field_stops_before_thermal_and_marks_manifest() {
    install_fake_environment
    assert_prepare_result_failure missing "$(make_md_directory_at "$SANDBOX/missing")"
}

test_non_string_prepare_result_field_stops_before_thermal_and_marks_manifest() {
    install_fake_environment
    assert_prepare_result_failure non_string "$(make_md_directory_at "$SANDBOX/non-string")"
}

test_empty_prepare_result_field_stops_before_thermal_and_marks_manifest() {
    install_fake_environment
    assert_prepare_result_failure empty "$(make_md_directory_at "$SANDBOX/empty")"
}

assert_stage_failure() {
    local stage=$1 code=$2 directory=$3 log=$4
    reset_call_capture
    assert_status "$code" run_mmgbsa "$directory" env DECOMP=1 FAIL_STAGE="$stage"
    assert_manifest_failure "$directory" "$stage" "$code" "$log"
}

test_stage_failures_preserve_stage_code_and_manifest_log() {
    install_fake_environment
    local directory decomp_dir

    directory=$(make_md_directory_at "$SANDBOX/prepare-failure")
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    assert_stage_failure prepare 7 "$directory" "$decomp_dir/prepare_ligand_decomp.log"
    assert_call_kinds prepare manifest

    directory=$(make_md_directory_at "$SANDBOX/thermal-failure")
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    assert_stage_failure thermal 8 "$directory" "$decomp_dir/thermal_mmgbsa.log"
    assert_call_kinds prepare json json json thermal manifest

    directory=$(make_md_directory_at "$SANDBOX/aggregation-failure")
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    assert_stage_failure aggregation 9 "$directory" "$decomp_dir/prime_mmgbsa_residue_decomp.log"
    assert_call_kinds prepare json json json thermal aggregation manifest
}

assert_manifest_fail_error_preserves_stage_code() {
    local stage=$1 code=$2 directory=$3
    reset_call_capture
    assert_status "$code" run_mmgbsa "$directory" env DECOMP=1 FAIL_STAGE="$stage" FAIL_MANIFEST_STAGE="$stage"
    assert_contains "$(cat "$SANDBOX/stderr.log")" "WARN: 无法标记 decomp manifest 失败: $stage"
}

test_manifest_fail_errors_do_not_mask_prepare_thermal_or_aggregation_rc() {
    install_fake_environment

    assert_manifest_fail_error_preserves_stage_code prepare 7 "$(make_md_directory_at "$SANDBOX/prepare-manifest")"
    assert_call_kinds prepare manifest
    assert_manifest_fail_error_preserves_stage_code thermal 8 "$(make_md_directory_at "$SANDBOX/thermal-manifest")"
    assert_call_kinds prepare json json json thermal manifest
    assert_manifest_fail_error_preserves_stage_code aggregation 9 "$(make_md_directory_at "$SANDBOX/aggregation-manifest")"
    assert_call_kinds prepare json json json thermal aggregation manifest
}

test_decomp_requires_exactly_one_main_trajectory() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    mkdir "$directory/second_trj"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1

    assert_call_kinds prepare json json json manifest
    assert_manifest_failure "$directory" trajectory 2 "$decomp_dir/prepare_ligand_decomp.log"
}

test_decomp_rejects_missing_main_trajectory() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"
    rmdir "$directory/complex_trj"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1

    assert_call_kinds prepare json json json manifest
    assert_manifest_failure "$directory" trajectory 2 "$decomp_dir/prepare_ligand_decomp.log"
}

test_decomp_requires_exactly_one_prime_maegz() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1 PRIME_OUTPUTS=2

    assert_call_kinds prepare json json json thermal manifest
    assert_manifest_failure "$directory" thermal_output 2 "$decomp_dir/thermal_mmgbsa.log"
}

test_decomp_rejects_missing_prime_maegz() {
    install_fake_environment
    local directory decomp_dir
    directory=$(make_md_directory)
    decomp_dir="$directory/mmgbsa_last100ns/residue_decomp"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1 PRIME_OUTPUTS=0

    assert_call_kinds prepare json json json thermal manifest
    assert_manifest_failure "$directory" thermal_output 2 "$decomp_dir/thermal_mmgbsa.log"
}

assert_mixed_directories() {
    local first=$1 second=$2 failed=$3
    reset_call_capture
    assert_status 7 run_mmgbsa_many "$first" "$second" env \
        DECOMP=1 FAIL_STAGE=prepare FAIL_DIRECTORY="$failed"
    assert_kind_count prepare 2
    assert_kind_count manifest 1
    assert_kind_count thermal 1
    assert_kind_count aggregation 1
}

test_mixed_directories_fail_then_success_keep_nonzero_and_run_successful_dir() {
    install_fake_environment
    local failed successful
    failed=$(make_md_directory_at "$SANDBOX/fail-first")
    successful=$(make_md_directory_at "$SANDBOX/success-second")

    assert_mixed_directories "$failed" "$successful" "$failed"
}

test_mixed_directories_success_then_fail_keep_nonzero_and_run_successful_dir() {
    install_fake_environment
    local successful failed
    successful=$(make_md_directory_at "$SANDBOX/success-first")
    failed=$(make_md_directory_at "$SANDBOX/fail-second")

    assert_mixed_directories "$successful" "$failed" "$failed"
}

run_all

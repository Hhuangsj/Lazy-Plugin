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
    mkdir -p "$SANDBOX/fake-schrodinger" "$SANDBOX/bin"
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
{
    printf 'CALL'
    for argument in "$@"; do printf '\t%s' "$argument"; done
    printf '\n'
} >> "$FAKE_CALL_LOG"

if [ "$1" = python3 ] && [ "$2" = - ]; then
    shift
    exec /usr/bin/python3 "$@"
fi

if [ "$1" = thermal_mmgbsa.py ]; then
    if [ "${FAIL_STAGE:-}" = thermal ]; then exit 8; fi
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
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --out-dir ]; then out_dir=$2; break; fi
            shift
        done
        mkdir -p "$out_dir"
        printf '{"schema_version": 1, "status": "running"}\n' > "$out_dir/decomp_manifest.json"
        if [ "${FAIL_STAGE:-}" = prepare ]; then exit 7; fi
        printf '{"analysis_cms": "%s/analysis cms.cms", "analysis_ligand_asl": "chain.name L and res.ptype UNK", "residue_map": "%s/residue map.json"}\n' "$out_dir" "$out_dir" > "$out_dir/prepare_result.json"
        : > "$out_dir/analysis cms.cms"
        : > "$out_dir/residue map.json"
        exit 0
        ;;
    prime_mmgbsa_residue_decomp.py)
        if [ "${FAIL_STAGE:-}" = aggregation ]; then exit 9; fi
        exit 0
        ;;
    mmgbsa_decomp_contract.py)
        shift
        exec /usr/bin/python3 "$@"
        ;;
esac

exit 2
EOF
    chmod +x "$SANDBOX/fake-schrodinger/run"
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
    FAKE_CALL_LOG="$SANDBOX/calls.log" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_MMGBSA" "$directory" > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

run_mmgbsa_many() {
    local first=$1 second=$2
    shift 2
    FAKE_CALL_LOG="$SANDBOX/calls.log" \
    TOOLENV_BIN="$SANDBOX/fake-toolenv" \
    PATH="/usr/bin:/bin" \
    "$@" bash "$RUN_MMGBSA" "$first" "$second" > "$SANDBOX/stdout.log" 2> "$SANDBOX/stderr.log"
}

call_log() {
    [ -f "$SANDBOX/calls.log" ] && cat "$SANDBOX/calls.log"
}

assert_status() {
    local expected=$1
    shift
    "$@"
    local actual=$?
    assert_eq "$actual" "$expected"
}

assert_manifest_failure() {
    local directory=$1 stage=$2 code=$3
    local manifest="$directory/mmgbsa_last100ns/residue_decomp/decomp_manifest.json"
    local payload
    payload=$(cat "$manifest")
    assert_contains "$payload" "\"stage\": \"$stage\""
    assert_contains "$payload" "\"return_code\": $code"
}

test_default_keeps_thermal_command_and_skips_decomp() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 0 run_mmgbsa "$directory" env START=11 END=22 STEP=3 NJOBS=4

    calls=$(call_log)
    assert_contains "$calls" $'CALL\tthermal_mmgbsa.py\t'
    assert_contains "$calls" $'\t-lig_asl\tres.ptype UNK\t-j\tcomplex\t-start_frame\t11\t-end_frame\t22\t-step_size\t3\t-NJOBS\t4\t-HOST\tlocalhost:4'
    case "$calls" in
        *prepare_ligand_decomp.py*|*prime_mmgbsa_residue_decomp.py*|*mmgbsa_decomp_contract.py*)
            fail "default branch invoked a decomposition program"
            ;;
    esac
}

test_decomp_runs_prepare_thermal_aggregate_with_quoted_contract_arguments() {
    install_fake_environment
    local directory calls prepare_line thermal_line aggregate_line
    directory=$(make_md_directory)

    assert_status 0 run_mmgbsa "$directory" env \
        DECOMP=1 START=11 END=22 STEP=3 NJOBS=4 \
        LIG_ASL='res.ptype UNK and chain.name "A B"' \
        DECOMP_PROPERTIES='dG_Bind,Coulomb' \
        SYNERGY_FRAGMENT_DIR="$SANDBOX/synergy directory" \
        SYNERGY_ADAPTER_PYTHON="$SANDBOX/adapter python"

    calls=$(call_log)
    prepare_line=$(printf '%s\n' "$calls" | grep 'prepare_ligand_decomp.py')
    thermal_line=$(printf '%s\n' "$calls" | grep 'thermal_mmgbsa.py')
    aggregate_line=$(printf '%s\n' "$calls" | grep 'prime_mmgbsa_residue_decomp.py')
    assert_contains "$prepare_line" $'\t--lig-asl\tres.ptype UNK and chain.name "A B"'
    assert_contains "$prepare_line" $'\t--synergy-dir\t'"$SANDBOX/synergy directory"
    assert_contains "$prepare_line" $'\t--adapter-python\t'"$SANDBOX/adapter python"
    assert_contains "$thermal_line" $'\t'"$directory/mmgbsa_last100ns/residue_decomp/analysis cms.cms"$'\t-lig_asl\tchain.name L and res.ptype UNK'
    assert_contains "$thermal_line" $'\t-start_frame\t11\t-end_frame\t22\t-step_size\t3\t-NJOBS\t4\t-HOST\tlocalhost:4'
    assert_contains "$aggregate_line" $'\t--trajectory\t'"$directory/complex_trj"$'\t--start\t11\t--end\t22\t--step\t3'
    assert_contains "$aggregate_line" $'\t--properties\tdG_Bind,Coulomb'
    case "$calls" in
        *mmgbsa_decomp_contract.py*) fail "success branch marked its manifest failed" ;;
    esac
    if [ "$(printf '%s\n' "$calls" | grep -E 'prepare_ligand_decomp.py|thermal_mmgbsa.py|prime_mmgbsa_residue_decomp.py' | sed -E 's/.*(prepare_ligand_decomp.py|thermal_mmgbsa.py|prime_mmgbsa_residue_decomp.py).*/\1/' | tr '\n' ' ')" != 'prepare_ligand_decomp.py thermal_mmgbsa.py prime_mmgbsa_residue_decomp.py ' ]; then
        fail "decomposition stages were not prepare -> thermal -> aggregate"
    fi
}

test_decomp_omits_empty_optional_arguments() {
    install_fake_environment
    local directory calls prepare_line aggregate_line
    directory=$(make_md_directory)

    assert_status 0 run_mmgbsa "$directory" env DECOMP=1

    calls=$(call_log)
    prepare_line=$(printf '%s\n' "$calls" | grep 'prepare_ligand_decomp.py')
    aggregate_line=$(printf '%s\n' "$calls" | grep 'prime_mmgbsa_residue_decomp.py')
    case "$prepare_line" in *--synergy-dir*|*--adapter-python*) fail "empty preparation option was passed";; esac
    case "$aggregate_line" in *--properties*) fail "empty property option was passed";; esac
}

test_prepare_failure_returns_its_code_and_marks_manifest() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 7 run_mmgbsa "$directory" env DECOMP=1 FAIL_STAGE=prepare

    calls=$(call_log)
    assert_contains "$calls" 'prepare_ligand_decomp.py'
    assert_contains "$calls" 'mmgbsa_decomp_contract.py'
    case "$calls" in *thermal_mmgbsa.py*|*prime_mmgbsa_residue_decomp.py*) fail "prepare failure continued";; esac
    assert_manifest_failure "$directory" prepare 7
}

test_thermal_failure_returns_its_code_and_marks_manifest() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 8 run_mmgbsa "$directory" env DECOMP=1 FAIL_STAGE=thermal

    calls=$(call_log)
    assert_contains "$calls" 'thermal_mmgbsa.py'
    assert_contains "$calls" 'mmgbsa_decomp_contract.py'
    case "$calls" in *prime_mmgbsa_residue_decomp.py*) fail "thermal failure continued";; esac
    assert_manifest_failure "$directory" thermal 8
}

test_multiple_directories_keep_failure_after_final_echo() {
    install_fake_environment
    local first second calls count
    first=$(make_md_directory)
    second=$(make_md_directory_at "$SANDBOX/md-second")

    assert_status 7 run_mmgbsa_many "$first" "$second" env DECOMP=1 FAIL_STAGE=prepare

    calls=$(call_log)
    count=$(printf '%s\n' "$calls" | grep -c 'prepare_ligand_decomp.py')
    assert_eq "$count" 2
    assert_manifest_failure "$first" prepare 7
    assert_manifest_failure "$second" prepare 7
}

test_aggregation_failure_returns_its_code_and_marks_manifest() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 9 run_mmgbsa "$directory" env DECOMP=1 FAIL_STAGE=aggregation

    calls=$(call_log)
    assert_contains "$calls" 'prime_mmgbsa_residue_decomp.py'
    assert_contains "$calls" 'mmgbsa_decomp_contract.py'
    assert_manifest_failure "$directory" aggregation 9
}

test_decomp_requires_exactly_one_main_trajectory() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)
    mkdir "$directory/second_trj"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1

    calls=$(call_log)
    case "$calls" in *thermal_mmgbsa.py*) fail "ambiguous trajectory started thermal MMGBSA";; esac
    assert_manifest_failure "$directory" trajectory 2
}

test_decomp_rejects_missing_main_trajectory() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)
    rmdir "$directory/complex_trj"

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1

    calls=$(call_log)
    case "$calls" in *thermal_mmgbsa.py*) fail "missing trajectory started thermal MMGBSA";; esac
    assert_manifest_failure "$directory" trajectory 2
}

test_decomp_requires_exactly_one_prime_maegz() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1 PRIME_OUTPUTS=2

    calls=$(call_log)
    case "$calls" in *prime_mmgbsa_residue_decomp.py*) fail "ambiguous Prime output started aggregation";; esac
    assert_manifest_failure "$directory" thermal_output 2
}

test_decomp_rejects_missing_prime_maegz() {
    install_fake_environment
    local directory calls
    directory=$(make_md_directory)

    assert_status 2 run_mmgbsa "$directory" env DECOMP=1 PRIME_OUTPUTS=0

    calls=$(call_log)
    case "$calls" in *prime_mmgbsa_residue_decomp.py*) fail "missing Prime output started aggregation";; esac
    assert_manifest_failure "$directory" thermal_output 2
}

run_all

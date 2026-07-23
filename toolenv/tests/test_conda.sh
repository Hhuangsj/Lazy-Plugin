#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"

_reset() { TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""; }

# 造一个假 conda 安装:root/condabin/conda + root/envs/<name>/bin/
_fake_conda() {
    local root="$SANDBOX/miniforge3"
    mkdir -p "$root/condabin" "$root/etc/profile.d" "$root/bin"
    printf '#!/bin/sh\n' > "$root/condabin/conda"
    chmod +x "$root/condabin/conda"
    touch "$root/etc/profile.d/conda.sh"
    local e
    for e in "$@"; do mkdir -p "$root/envs/$e/bin"; done
    echo "$root"
}

test_conda_root_from_override() {
    local root; root=$(_fake_conda md)
    assert_eq "$(TOOLENV_CONDA="$root" toolenv_conda_root)" "$root"
}

test_conda_root_from_conda_root_var() {
    local root; root=$(_fake_conda md)
    assert_eq "$(CONDA_ROOT="$root" TOOLENV_CONDA= toolenv_conda_root)" "$root"
}

test_conda_root_from_path_condabin() {
    local root; root=$(_fake_conda md)
    local got
    got=$(TOOLENV_CONDA= CONDA_ROOT= CONDA_EXE= PATH="$root/condabin:$PATH" toolenv_conda_root)
    assert_eq "$got" "$root"
}

test_conda_root_fails_when_absent() {
    assert_fail env TOOLENV_CONDA= CONDA_ROOT= CONDA_EXE= PATH="/nonexistent" HOME="$SANDBOX/home" \
        bash -c '. "'"$TOOLENV_HOME"'/lib/probe.sh"; . "'"$TOOLENV_HOME"'/lib/conda.sh"; toolenv_conda_root'
}

test_conda_envs_lists_base_first_then_envs() {
    local root; root=$(_fake_conda md peptide)
    local out
    out=$(TOOLENV_CONDA="$root" toolenv_conda_envs)
    assert_eq "$(printf '%s\n' "$out" | head -1 | cut -f1)" "base"
    assert_contains "$out" "md"
    assert_contains "$out" "peptide"
    assert_eq "$(printf '%s\n' "$out" | head -1 | cut -f2)" "$root"
}

test_has_env() {
    local root; root=$(_fake_conda md)
    assert_ok env TOOLENV_CONDA="$root" bash -c \
        '. "'"$TOOLENV_HOME"'/lib/probe.sh"; . "'"$TOOLENV_HOME"'/lib/conda.sh"; toolenv_conda_has_env md'
    assert_fail env TOOLENV_CONDA="$root" bash -c \
        '. "'"$TOOLENV_HOME"'/lib/probe.sh"; . "'"$TOOLENV_HOME"'/lib/conda.sh"; toolenv_conda_has_env nosuchenv'
}

test_try_conda_env_bin_finds_exe() {
    _reset
    local root; root=$(_fake_conda md amber)
    printf '#!/bin/sh\n' > "$root/envs/amber/bin/antechamber"
    chmod +x "$root/envs/amber/bin/antechamber"
    export TOOLENV_CONDA="$root"
    assert_ok try_conda_env_bin antechamber
    assert_eq "$TOOLENV_HIT" "$root/envs/amber"
    assert_eq "$TOOLENV_HIT_ENV" "amber"
    assert_eq "$TOOLENV_HIT_SOURCE" "conda:amber"
    unset TOOLENV_CONDA
}

test_try_conda_env_bin_misses() {
    _reset
    local root; root=$(_fake_conda md)
    export TOOLENV_CONDA="$root"
    assert_fail try_conda_env_bin antechamber
    assert_eq "$TOOLENV_HIT" ""
    unset TOOLENV_CONDA
}

test_try_conda_env_python_runs_import() {
    _reset
    local root; root=$(_fake_conda md chem)
    # 假 python:import rdkit 成功,其它退出 1
    cat > "$root/envs/chem/bin/python" <<'PY'
#!/bin/sh
case "$2" in
  *rdkit*) exit 0 ;;
  *) exit 1 ;;
esac
PY
    chmod +x "$root/envs/chem/bin/python"
    export TOOLENV_CONDA="$root"
    assert_ok try_conda_env_python "import rdkit"
    assert_eq "$TOOLENV_HIT_ENV" "chem"
    _reset
    assert_fail try_conda_env_python "import nothing"
    unset TOOLENV_CONDA
}

run_all

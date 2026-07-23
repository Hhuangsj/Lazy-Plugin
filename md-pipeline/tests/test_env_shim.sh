#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$(dirname "$SKILL_DIR")
REAL_HOME=$HOME
export REAL_HOME
. "$REPO/toolenv/tests/helpers.sh"

ENV_SH="$SKILL_DIR/scripts/env.sh"

test_env_sh_exports_schrodinger_and_automd() {
    local out
    out=$(HOME="$REAL_HOME" bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; echo "S=$SCHRODINGER"; echo "D=$Desmond"; echo "A=$AUTOMD_DIR"')
    assert_contains "$out" "S=/"
    assert_contains "$out" "D=/"
    assert_contains "$out" "A=/"
}

test_automd_resolves_to_bundled_copy() {
    local out
    out=$(HOME="$REAL_HOME" bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; command -v AutoTRJ')
    assert_eq "$out" "$SKILL_DIR/scripts/AutoMD/AutoTRJ"
}

test_md_env_check_is_defined_and_runs() {
    assert_ok env HOME="$REAL_HOME" bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; declare -F md_env_check >/dev/null'
}

test_env_sh_works_in_clean_shell() {
    # 这是原 env.sh 的既有验收标准:不依赖交互式 .bashrc
    local out
    out=$(env -i HOME="$REAL_HOME" PATH=/usr/bin:/bin bash -c \
        '. "'"$ENV_SH"'" >/dev/null 2>&1; echo "S=$SCHRODINGER"')
    assert_contains "$out" "S=/"
}

test_paths_come_from_toolenv_not_hardcoded() {
    # 薄壳的意义所在:换机器时不改本文件,改 overrides.sh 就能纠正路径。
    # 老版 env.sh 把路径写死在文件顶部,这条会失败。
    mkdir -p "$SANDBOX/fake-schrodinger" "$SANDBOX/cfg"
    printf '#!/bin/sh\n' > "$SANDBOX/fake-schrodinger/run"
    chmod +x "$SANDBOX/fake-schrodinger/run"
    cat > "$SANDBOX/cfg/overrides.sh" <<EOF
export TOOLENV_SCHRODINGER="$SANDBOX/fake-schrodinger"
EOF
    local out
    out=$(HOME="$REAL_HOME" \
          TOOLENV_CONFIG_DIR="$SANDBOX/cfg" \
          TOOLENV_CACHE_DIR="$SANDBOX/cache" \
          bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; echo "S=$SCHRODINGER"')
    assert_eq "$out" "S=$SANDBOX/fake-schrodinger"
}

run_all

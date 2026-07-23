# toolenv 共享环境层 + md-pipeline Skill 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `md_pipeline` 变成一个 Claude Code skill,底下垫一个纯 bash 的工具发现层 `toolenv`,让脚本在任意机器上自动找到 Schrödinger/AutoMD/conda/PLIP/AmberTools/RDKit 并激活。

**Architecture:** `toolenv` 是一个独立的 bash 包(CLI + 探测原语库 + 每工具一个 manifest),把"工具装在哪"这件事从脚本里彻底剥离。探测结果按 hostname 缓存,可被 `~/.config/toolenv/overrides.sh` 覆盖。脚本在文件头用 `# @requires:` 声明依赖,`source toolenv/activate.sh` 一行完成检查与激活。Skill 目录通过 `install.sh` symlink 到 `~/.claude/skills/`。

**Tech Stack:** Bash 4.4(本机版本)、conda/miniforge、无第三方依赖。

## Global Constraints

- **纯 Bash,零外部依赖**:不得使用 `jq`、`yq`、`bats`、`shellcheck`、`python` —— 本机均无(conda 有,但探测 conda 的代码自身不能依赖 python)。
- **Bash 4.4 兼容**:可用关联数组、`${!var}` 间接展开、`<<<`;不可用 bash 5 独有特性(如 `${var@Q}`、`EPOCHSECONDS`)。
- **被 `source` 的文件不得设置 `set -e` / `set -u`**,不得污染调用方 shell 的 `set` 选项;失败用返回值 + stderr 表达。
- **可执行 CLI 用 `set -u`,不用 `set -e`**(探测逻辑大量依赖非零返回值)。
- **路径一律 `readlink -f` 规范化**,保证 symlink 后仍能定位仓库真实位置。
- **可覆盖的环境变量**(实现必须尊重,测试依赖它们做沙箱):
  - `TOOLENV_HOME` —— toolenv 包目录,默认由脚本自身位置推导
  - `TOOLENV_TOOLS_DIR` —— manifest 目录,默认 `$TOOLENV_HOME/tools.d`
  - `TOOLENV_CACHE_DIR` —— 默认 `${XDG_CACHE_HOME:-$HOME/.cache}/toolenv`
  - `TOOLENV_CONFIG_DIR` —— 默认 `${XDG_CONFIG_HOME:-$HOME/.config}/toolenv`
- **仓库根**:`/data1/home/huangshengjie/workstations/skills`,以下所有相对路径均相对于它。
- **对 spec 的偏离**:spec 写了"全部 shell 文件过 `shellcheck`",本机没有 shellcheck,改为由仓库内自带的 `toolenv/tests/run_tests.sh` 保证;若日后装了 shellcheck 再补。

## 文件结构

| 文件 | 职责 |
|------|------|
| `toolenv/lib/probe.sh` | 探测原语:`try_env` / `try_cmd` / `try_glob`,命中即锁定 |
| `toolenv/lib/conda.sh` | conda 根目录与环境枚举、`try_conda_env_bin` / `try_conda_env_python` |
| `toolenv/lib/cache.sh` | 缓存读写(可 source 的 key=value 文件,按 hostname 分文件) |
| `toolenv/lib/resolve.sh` | manifest 加载 + 优先级仲裁 + 单工具解析 |
| `toolenv/lib/meta.sh` | 脚本头 `# @key: value` 元信息解析 |
| `toolenv/toolenv` | CLI 分发:probe/list/which/check/env/requires/index/run/selftest |
| `toolenv/activate.sh` | 脚本一行 source 的入口,解析调用方 `@requires` 并激活 |
| `toolenv/tools.d/*.sh` | 每工具一个 manifest,声明 `TOOL_DESC`/`TOOL_HINT`/`tool_detect`/`tool_activate` |
| `toolenv/tests/helpers.sh` | 断言与沙箱 |
| `toolenv/tests/run_tests.sh` | 测试入口,跑全部 `test_*.sh` |
| `md-pipeline/SKILL.md` | skill 说明(不列举脚本,指向 `toolenv index`) |
| `md-pipeline/scripts/` | 现有 `run_*.sh` / `*.py` / `AutoMD/` |
| `md-pipeline/references/troubleshooting.md` | 现 README 的"踩坑记录" |
| `install.sh` | symlink skill 到 `~/.claude/skills/` + 首次 probe |

---

### Task 1: 测试骨架 + 基础探测原语

**Files:**
- Create: `toolenv/tests/helpers.sh`
- Create: `toolenv/tests/run_tests.sh`
- Create: `toolenv/tests/test_probe.sh`
- Create: `toolenv/lib/probe.sh`

**Interfaces:**
- Consumes: 无(第一个任务)
- Produces:
  - `try_env VAR` —— `$VAR` 指向存在目录则命中
  - `try_cmd CMD [--up N]` —— `PATH` 上找到 `CMD`,`readlink -f` 后向上 N 级作为根路径
  - `try_glob PATTERN...` —— 逐个 glob,取版本号排序最大的目录
  - 三者命中时设置全局 `TOOLENV_HIT`(根路径)、`TOOLENV_HIT_SOURCE`(来源标签)、`TOOLENV_HIT_ENV`(conda 环境名,本任务恒为空),并 `return 0`;`TOOLENV_HIT` 非空时任何原语直接 `return 0` 不再探测(实现"首个命中即停")
  - 测试约定:测试文件 source `helpers.sh`,定义 `test_*` 函数,末尾调用 `run_all`
  - `helpers.sh` 提供:`assert_eq ACTUAL EXPECTED [MSG]`、`assert_ok CMD...`、`assert_fail CMD...`、`assert_contains HAYSTACK NEEDLE`、`$SANDBOX`(每个测试独立临时目录,`HOME`/`TOOLENV_CACHE_DIR`/`TOOLENV_CONFIG_DIR` 均已指向它)

- [ ] **Step 1: 写测试骨架**

创建 `toolenv/tests/helpers.sh`:

```bash
# helpers.sh —— 零依赖 bash 测试骨架。被测试文件 source。
# 不设 set -e:断言失败要继续跑完同一个测试函数。
set -u

TE_TESTS=0
TE_FAILS=0
_te_current=""
SANDBOX=""

fail() {
    TE_FAILS=$((TE_FAILS + 1))
    echo "    ✗ $_te_current: $*" >&2
}

assert_eq() {
    if [ "$1" != "$2" ]; then
        fail "expected '$2', got '$1'${3:+ ($3)}"
    fi
}

assert_ok() {
    if ! "$@"; then fail "expected success: $*"; fi
}

assert_fail() {
    if "$@"; then fail "expected failure: $*"; fi
}

assert_contains() {
    case "$1" in
        *"$2"*) ;;
        *) fail "'$1' does not contain '$2'" ;;
    esac
}

_te_sandbox_setup() {
    SANDBOX=$(mktemp -d "${TMPDIR:-/tmp}/toolenv-test.XXXXXX")
    export HOME="$SANDBOX/home"
    export TOOLENV_CACHE_DIR="$SANDBOX/cache"
    export TOOLENV_CONFIG_DIR="$SANDBOX/config"
    mkdir -p "$HOME" "$TOOLENV_CACHE_DIR" "$TOOLENV_CONFIG_DIR"
}

_te_sandbox_teardown() {
    [ -n "$SANDBOX" ] && [ -d "$SANDBOX" ] && rm -rf "$SANDBOX"
    SANDBOX=""
}

run_test() {
    local fn=$1 before=$TE_FAILS
    _te_current=$fn
    TE_TESTS=$((TE_TESTS + 1))
    _te_sandbox_setup
    "$fn"
    _te_sandbox_teardown
    if [ "$TE_FAILS" -eq "$before" ]; then echo "    ✓ $fn"; fi
}

run_all() {
    local fn
    for fn in $(declare -F | awk '{print $3}' | grep '^test_'); do
        run_test "$fn"
    done
    echo "  $TE_TESTS tests, $TE_FAILS failures"
    [ "$TE_FAILS" -eq 0 ]
}
```

创建 `toolenv/tests/run_tests.sh`:

```bash
#!/usr/bin/env bash
# 跑全部 toolenv 测试。用法:toolenv/tests/run_tests.sh [test_probe.sh ...]
set -u

TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
export TOOLENV_HOME=$(dirname "$TESTS_DIR")

files=("$@")
if [ ${#files[@]} -eq 0 ]; then
    files=("$TESTS_DIR"/test_*.sh)
fi

rc=0
for f in "${files[@]}"; do
    [ -f "$f" ] || f="$TESTS_DIR/$f"
    echo "== $(basename "$f")"
    bash "$f" || rc=1
done

if [ "$rc" -eq 0 ]; then echo "ALL PASS"; else echo "FAILURES" >&2; fi
exit "$rc"
```

```bash
chmod +x toolenv/tests/run_tests.sh
```

- [ ] **Step 2: 写失败的测试**

创建 `toolenv/tests/test_probe.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"

_reset() { TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""; }

test_try_env_hits_existing_dir() {
    _reset
    mkdir -p "$SANDBOX/amber"
    FAKE_HOME_VAR="$SANDBOX/amber"
    export FAKE_HOME_VAR
    assert_ok try_env FAKE_HOME_VAR
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FAKE_HOME_VAR"
}

test_try_env_misses_when_dir_absent() {
    _reset
    FAKE_HOME_VAR="$SANDBOX/nope"
    export FAKE_HOME_VAR
    assert_fail try_env FAKE_HOME_VAR
    assert_eq "$TOOLENV_HIT" ""
}

test_try_env_misses_when_var_unset() {
    _reset
    unset FAKE_HOME_VAR
    assert_fail try_env FAKE_HOME_VAR
}

test_try_cmd_walks_up() {
    _reset
    mkdir -p "$SANDBOX/amber/bin"
    printf '#!/bin/sh\n' > "$SANDBOX/amber/bin/antechamber"
    chmod +x "$SANDBOX/amber/bin/antechamber"
    PATH="$SANDBOX/amber/bin:$PATH" assert_ok try_cmd antechamber --up 2
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber"
    assert_eq "$TOOLENV_HIT_SOURCE" "path:antechamber"
}

test_try_cmd_default_up_is_zero() {
    _reset
    mkdir -p "$SANDBOX/amber/bin"
    printf '#!/bin/sh\n' > "$SANDBOX/amber/bin/tleap"
    chmod +x "$SANDBOX/amber/bin/tleap"
    PATH="$SANDBOX/amber/bin:$PATH" assert_ok try_cmd tleap
    assert_eq "$TOOLENV_HIT" "$SANDBOX/amber/bin/tleap"
}

test_try_cmd_misses_unknown_command() {
    _reset
    assert_fail try_cmd definitely-not-a-real-command-xyz --up 2
}

test_try_glob_picks_highest_version() {
    _reset
    mkdir -p "$SANDBOX/software/Schrodinger/2023-4"
    mkdir -p "$SANDBOX/software/Schrodinger/2024-1"
    assert_ok try_glob "$SANDBOX/software/Schrodinger/*"
    assert_eq "$TOOLENV_HIT" "$SANDBOX/software/Schrodinger/2024-1"
    assert_contains "$TOOLENV_HIT_SOURCE" "glob:"
}

test_try_glob_ignores_files_and_missing() {
    _reset
    touch "$SANDBOX/notadir"
    assert_fail try_glob "$SANDBOX/notadir" "$SANDBOX/nothing-here-*"
}

test_first_hit_wins() {
    _reset
    mkdir -p "$SANDBOX/first" "$SANDBOX/second"
    FIRST="$SANDBOX/first"; SECOND="$SANDBOX/second"
    export FIRST SECOND
    try_env FIRST
    try_env SECOND
    assert_eq "$TOOLENV_HIT" "$SANDBOX/first" "第二次探测不该覆盖第一次"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FIRST"
}

run_all
```

- [ ] **Step 3: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: FAIL —— `lib/probe.sh: No such file or directory`

- [ ] **Step 4: 实现 probe.sh**

创建 `toolenv/lib/probe.sh`:

```bash
# probe.sh —— 探测原语。被 source,不设 set -e/-u。
#
# 约定:命中时设置 TOOLENV_HIT / TOOLENV_HIT_SOURCE / TOOLENV_HIT_ENV 并返回 0。
# TOOLENV_HIT 已非空时所有原语立即返回 0 —— 这样 tool_detect 里可以一行一个
# 候选顺序排列,天然实现"首个命中即停",不需要 || 链。

TOOLENV_HIT="${TOOLENV_HIT:-}"
TOOLENV_HIT_SOURCE="${TOOLENV_HIT_SOURCE:-}"
TOOLENV_HIT_ENV="${TOOLENV_HIT_ENV:-}"

_te_hit() {   # _te_hit <path> <source> [conda-env]
    TOOLENV_HIT=$1
    TOOLENV_HIT_SOURCE=$2
    TOOLENV_HIT_ENV=${3:-}
    return 0
}

# try_env VAR —— 环境变量 VAR 指向一个存在的目录
try_env() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local var=$1 val
    val=${!var:-}
    [ -n "$val" ] || return 1
    [ -d "$val" ] || return 1
    _te_hit "$(readlink -f "$val")" "env:$var"
}

# try_cmd CMD [--up N] —— PATH 上的 CMD,解析真实路径后向上 N 级
# 例:try_cmd antechamber --up 2  =>  /prefix/bin/antechamber 的 /prefix
try_cmd() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local cmd=$1 up=0 p i
    shift
    if [ "${1:-}" = "--up" ]; then up=${2:-0}; fi
    p=$(command -v "$cmd" 2>/dev/null) || return 1
    [ -n "$p" ] || return 1
    p=$(readlink -f "$p")
    for ((i = 0; i < up; i++)); do
        p=$(dirname "$p")
    done
    _te_hit "$p" "path:$cmd"
}

# try_glob PATTERN... —— 逐个 glob,同一 pattern 内按版本号取最大的目录
try_glob() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local pat d best
    for pat in "$@"; do
        best=""
        # 有意不加引号:这里就是要 glob 展开
        for d in $pat; do
            [ -d "$d" ] || continue
            if [ -z "$best" ]; then
                best=$d
            else
                best=$(printf '%s\n%s\n' "$best" "$d" | sort -V | tail -1)
            fi
        done
        if [ -n "$best" ]; then
            _te_hit "$(readlink -f "$best")" "glob:$pat"
            return 0
        fi
    done
    return 1
}
```

- [ ] **Step 5: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: 9 tests, 0 failures / `ALL PASS`

- [ ] **Step 6: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/lib/probe.sh toolenv/tests/
git commit -m "feat(toolenv): 探测原语 try_env/try_cmd/try_glob + 测试骨架"
```

---

### Task 2: conda 探测

**Files:**
- Create: `toolenv/lib/conda.sh`
- Create: `toolenv/tests/test_conda.sh`

**Interfaces:**
- Consumes: Task 1 的 `TOOLENV_HIT` / `_te_hit` 约定(`conda.sh` 由调用方在 `probe.sh` 之后 source)
- Produces:
  - `toolenv_conda_root` —— stdout 打印 conda 根目录,找不到返回 1
  - `toolenv_conda_envs` —— stdout 每行 `名字<TAB>前缀路径`,`base` 排第一
  - `try_conda_env_bin EXE` —— 某个 conda 环境的 `bin/EXE` 可执行则命中,`TOOLENV_HIT`=环境前缀、`TOOLENV_HIT_ENV`=环境名、`TOOLENV_HIT_SOURCE`=`conda:环境名`
  - `try_conda_env_python IMPORT_STMT` —— 某环境的 `bin/python -c "IMPORT_STMT"` 成功则命中,字段同上
  - `toolenv_conda_has_env NAME` —— 环境存在返回 0(供 `conda:md` 这类 spec 用)

- [ ] **Step 1: 写失败的测试**

创建 `toolenv/tests/test_conda.sh`:

```bash
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
    TOOLENV_CONDA="$root" assert_eq "$(TOOLENV_CONDA="$root" toolenv_conda_root)" "$root"
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
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_conda.sh`
Expected: FAIL —— `lib/conda.sh: No such file or directory`

- [ ] **Step 3: 实现 conda.sh**

创建 `toolenv/lib/conda.sh`:

```bash
# conda.sh —— conda 安装与环境的发现。依赖 probe.sh 已被 source。

# toolenv_conda_root —— 打印 conda 根目录;找不到返回 1
toolenv_conda_root() {
    local c p
    if [ -n "${TOOLENV_CONDA:-}" ] && [ -d "${TOOLENV_CONDA}" ]; then
        readlink -f "$TOOLENV_CONDA"; return 0
    fi
    if [ -n "${CONDA_ROOT:-}" ] && [ -d "${CONDA_ROOT}" ]; then
        readlink -f "$CONDA_ROOT"; return 0
    fi
    if [ -n "${CONDA_EXE:-}" ] && [ -x "${CONDA_EXE}" ]; then
        # <root>/bin/conda 或 <root>/condabin/conda
        readlink -f "$(dirname "$(dirname "$(readlink -f "$CONDA_EXE")")")"; return 0
    fi
    if c=$(command -v conda 2>/dev/null) && [ -n "$c" ]; then
        readlink -f "$(dirname "$(dirname "$(readlink -f "$c")")")"; return 0
    fi
    for p in "$HOME"/miniforge3 "$HOME"/mambaforge "$HOME"/miniconda3 "$HOME"/anaconda3 \
             /opt/miniforge3 /opt/miniconda3 /opt/anaconda3; do
        if [ -d "$p/envs" ] || [ -x "$p/condabin/conda" ]; then
            readlink -f "$p"; return 0
        fi
    done
    return 1
}

# toolenv_conda_envs —— 每行 "名字<TAB>前缀";base 排第一
toolenv_conda_envs() {
    local root d name
    root=$(toolenv_conda_root) || return 1
    printf 'base\t%s\n' "$root"
    for d in "$root"/envs/*/; do
        [ -d "$d" ] || continue
        d=${d%/}
        name=$(basename "$d")
        printf '%s\t%s\n' "$name" "$d"
    done
    # 装在根目录之外的环境(conda create -p)
    if [ -f "$HOME/.conda/environments.txt" ]; then
        while IFS= read -r d; do
            [ -n "$d" ] || continue
            [ -d "$d" ] || continue
            case "$d" in "$root"|"$root"/envs/*) continue ;; esac
            printf '%s\t%s\n' "$(basename "$d")" "$d"
        done < "$HOME/.conda/environments.txt"
    fi
}

# toolenv_conda_has_env NAME
toolenv_conda_has_env() {
    local want=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ "$name" = "$want" ] && return 0
    done < <(toolenv_conda_envs)
    return 1
}

# try_conda_env_bin EXE —— 哪个环境的 bin/EXE 可执行
try_conda_env_bin() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local exe=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ -x "$prefix/bin/$exe" ] || continue
        _te_hit "$prefix" "conda:$name" "$name"
        return 0
    done < <(toolenv_conda_envs)
    return 1
}

# try_conda_env_python IMPORT_STMT —— 哪个环境的 python 能跑通这句 import
try_conda_env_python() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local stmt=$1 name prefix
    while IFS=$'\t' read -r name prefix; do
        [ -x "$prefix/bin/python" ] || continue
        "$prefix/bin/python" -c "$stmt" >/dev/null 2>&1 || continue
        _te_hit "$prefix" "conda:$name" "$name"
        return 0
    done < <(toolenv_conda_envs)
    return 1
}
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`(test_probe.sh 9 项 + test_conda.sh 9 项)

- [ ] **Step 5: 手工验证真机 conda 能被发现**

Run: `cd /data1/home/huangshengjie/workstations/skills && bash -c '. toolenv/lib/probe.sh; . toolenv/lib/conda.sh; toolenv_conda_root; toolenv_conda_envs | head -5'`
Expected: 打印 `/data1/home/huangshengjie/miniforge3`,随后 `base` 与若干环境(应含 `md`)

- [ ] **Step 6: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/lib/conda.sh toolenv/tests/test_conda.sh
git commit -m "feat(toolenv): conda 根目录/环境枚举与 bin、python import 探测"
```

---

### Task 3: 缓存层

**Files:**
- Create: `toolenv/lib/cache.sh`
- Create: `toolenv/tests/test_cache.sh`

**Interfaces:**
- Consumes: 无(独立于探测)
- Produces:
  - `toolenv_cache_file` —— 打印缓存文件路径 `$TOOLENV_CACHE_DIR/<hostname>.env`
  - `toolenv_cache_put TOOL STATUS PATH SOURCE ENV` —— 追加一条记录到内存表
  - `toolenv_cache_flush` —— 把内存表写入缓存文件(原子:写临时文件再 `mv`)
  - `toolenv_cache_load` —— source 缓存文件,失败(不存在)返回 1
  - `toolenv_cache_get TOOL FIELD` —— 打印字段值(FIELD ∈ `STATUS|PATH|SOURCE|ENV`),无记录返回 1
  - `toolenv_cache_tools` —— 打印缓存里所有工具名,每行一个
  - `toolenv_cache_clear` —— 删除缓存文件与内存表
  - 工具名中的 `-` 在变量名里转成 `_`

- [ ] **Step 1: 写失败的测试**

创建 `toolenv/tests/test_cache.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/cache.sh"

test_cache_file_under_cache_dir() {
    assert_contains "$(toolenv_cache_file)" "$TOOLENV_CACHE_DIR/"
    assert_contains "$(toolenv_cache_file)" ".env"
}

test_put_flush_load_get_roundtrip() {
    toolenv_cache_clear
    toolenv_cache_put schrodinger found /opt/schrodinger/2024-1 "glob:/opt/*" ""
    toolenv_cache_put rdkit found /home/u/miniforge3/envs/chem "conda:chem" chem
    toolenv_cache_flush
    assert_ok test -f "$(toolenv_cache_file)"

    # 新 shell 读回来
    toolenv_cache_clear_memory
    assert_ok toolenv_cache_load
    assert_eq "$(toolenv_cache_get schrodinger PATH)" "/opt/schrodinger/2024-1"
    assert_eq "$(toolenv_cache_get schrodinger SOURCE)" "glob:/opt/*"
    assert_eq "$(toolenv_cache_get rdkit ENV)" "chem"
    assert_eq "$(toolenv_cache_get rdkit STATUS)" "found"
}

test_get_unknown_tool_fails() {
    toolenv_cache_clear
    toolenv_cache_put a found /x path:a ""
    toolenv_cache_flush
    assert_fail toolenv_cache_get nosuchtool PATH
}

test_missing_status_roundtrips() {
    toolenv_cache_clear
    toolenv_cache_put ambertools missing "" "" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get ambertools STATUS)" "missing"
    assert_eq "$(toolenv_cache_get ambertools PATH)" ""
}

test_tool_name_with_dash() {
    toolenv_cache_clear
    toolenv_cache_put my-tool found /x/y path:my-tool ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get my-tool PATH)" "/x/y"
}

test_cache_tools_lists_all() {
    toolenv_cache_clear
    toolenv_cache_put a found /a path:a ""
    toolenv_cache_put b missing "" "" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_tools | sort | tr '\n' ' ')" "a b "
}

test_load_fails_when_no_cache() {
    toolenv_cache_clear
    assert_fail toolenv_cache_load
}

test_paths_with_spaces_survive() {
    toolenv_cache_clear
    toolenv_cache_put weird found "/opt/my tools/x" "glob:/opt/*" ""
    toolenv_cache_flush
    toolenv_cache_clear_memory
    toolenv_cache_load
    assert_eq "$(toolenv_cache_get weird PATH)" "/opt/my tools/x"
}

run_all
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_cache.sh`
Expected: FAIL —— `lib/cache.sh: No such file or directory`

- [ ] **Step 3: 实现 cache.sh**

创建 `toolenv/lib/cache.sh`:

```bash
# cache.sh —— 探测结果缓存。格式是可 source 的 bash 赋值,按 hostname 分文件。

TOOLENV_CACHE_DIR="${TOOLENV_CACHE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/toolenv}"
_TE_CACHE_TOOLS="${_TE_CACHE_TOOLS:-}"

toolenv_cache_file() {
    printf '%s/%s.env\n' "$TOOLENV_CACHE_DIR" "$(hostname -s 2>/dev/null || hostname)"
}

_te_key() {   # 工具名 -> 变量名安全片段
    printf '%s' "$1" | tr -c 'A-Za-z0-9_' '_'
}

_te_quote() { # 单引号安全转义
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# toolenv_cache_put TOOL STATUS PATH SOURCE ENV
toolenv_cache_put() {
    local tool=$1 status=$2 path=$3 source=$4 cenv=$5 k
    k=$(_te_key "$tool")
    eval "_TE_R_${k}_NAME=\$tool"
    eval "_TE_R_${k}_STATUS=\$status"
    eval "_TE_R_${k}_PATH=\$path"
    eval "_TE_R_${k}_SOURCE=\$source"
    eval "_TE_R_${k}_ENV=\$cenv"
    case " $_TE_CACHE_TOOLS " in
        *" $tool "*) ;;
        *) _TE_CACHE_TOOLS="$_TE_CACHE_TOOLS $tool" ;;
    esac
}

toolenv_cache_flush() {
    local f tmp tool k
    f=$(toolenv_cache_file)
    mkdir -p "$(dirname "$f")" || return 1
    tmp="$f.tmp.$$"
    {
        echo "# toolenv cache v1 — 由 'toolenv probe' 生成,可安全删除"
        echo "# host=$(hostname 2>/dev/null) date=$(date -Iseconds 2>/dev/null)"
        echo "_TE_CACHE_TOOLS=$(_te_quote "$_TE_CACHE_TOOLS")"
        for tool in $_TE_CACHE_TOOLS; do
            k=$(_te_key "$tool")
            eval "echo \"_TE_R_${k}_NAME=\$(_te_quote \"\$_TE_R_${k}_NAME\")\""
            eval "echo \"_TE_R_${k}_STATUS=\$(_te_quote \"\$_TE_R_${k}_STATUS\")\""
            eval "echo \"_TE_R_${k}_PATH=\$(_te_quote \"\$_TE_R_${k}_PATH\")\""
            eval "echo \"_TE_R_${k}_SOURCE=\$(_te_quote \"\$_TE_R_${k}_SOURCE\")\""
            eval "echo \"_TE_R_${k}_ENV=\$(_te_quote \"\$_TE_R_${k}_ENV\")\""
        done
    } > "$tmp" || return 1
    mv -f "$tmp" "$f"
}

toolenv_cache_load() {
    local f
    f=$(toolenv_cache_file)
    [ -f "$f" ] || return 1
    # shellcheck disable=SC1090
    . "$f"
}

# toolenv_cache_get TOOL FIELD   (FIELD: STATUS|PATH|SOURCE|ENV|NAME)
toolenv_cache_get() {
    local tool=$1 field=$2 k var
    k=$(_te_key "$tool")
    var="_TE_R_${k}_NAME"
    [ -n "${!var:-}" ] || return 1
    var="_TE_R_${k}_${field}"
    printf '%s\n' "${!var:-}"
}

toolenv_cache_tools() {
    local t
    for t in $_TE_CACHE_TOOLS; do printf '%s\n' "$t"; done
}

toolenv_cache_clear_memory() {
    local tool k
    for tool in $_TE_CACHE_TOOLS; do
        k=$(_te_key "$tool")
        unset "_TE_R_${k}_NAME" "_TE_R_${k}_STATUS" "_TE_R_${k}_PATH" \
              "_TE_R_${k}_SOURCE" "_TE_R_${k}_ENV"
    done
    _TE_CACHE_TOOLS=""
}

toolenv_cache_clear() {
    toolenv_cache_clear_memory
    rm -f "$(toolenv_cache_file)"
}
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`

- [ ] **Step 5: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/lib/cache.sh toolenv/tests/test_cache.sh
git commit -m "feat(toolenv): 按 hostname 分文件的探测结果缓存"
```

---

### Task 4: manifest 加载与解析引擎

**Files:**
- Create: `toolenv/lib/resolve.sh`
- Create: `toolenv/tests/test_resolve.sh`
- Create: `toolenv/tests/fixtures/tools.d/faketool.sh`
- Create: `toolenv/tests/fixtures/tools.d/envtool.sh`

**Interfaces:**
- Consumes: `probe.sh`(`TOOLENV_HIT` 约定)、`conda.sh`、`cache.sh`
- Produces:
  - `toolenv_tools_dir` —— 打印 manifest 目录
  - `toolenv_list_manifests` —— 打印所有工具名(按文件名去 `.sh`,字典序)
  - `toolenv_load_manifest TOOL` —— source manifest,失败返回 1;成功后 `TOOL_NAME`/`TOOL_DESC`/`TOOL_HINT`/`tool_detect`/`tool_activate` 可用
  - `toolenv_load_overrides` —— source `$TOOLENV_CONFIG_DIR/overrides.sh`(存在才 source)
  - `toolenv_resolve TOOL` —— 解析单个工具,成功时设置 `TOOLENV_HIT`/`TOOLENV_HIT_SOURCE`/`TOOLENV_HIT_ENV` 并返回 0,失败返回 1。优先级:`TOOLENV_<TOOL大写>` 覆盖变量 > manifest 的 `tool_detect`
  - `toolenv_activate_lines TOOL PATH ENV` —— 打印该工具的 `export` 行

manifest 契约(写给未来加工具的人):

```bash
TOOL_NAME="x"; TOOL_DESC="一句话"; TOOL_HINT="装不上时怎么装"
tool_detect()   { ... }              # 用 try_* 原语,命中即锁定
tool_activate() { local root=$1 cenv=${2:-}; echo "export ..."; }
```

- [ ] **Step 1: 写测试用的假 manifest**

创建 `toolenv/tests/fixtures/tools.d/faketool.sh`:

```bash
TOOL_NAME="faketool"
TOOL_DESC="测试用的假工具"
TOOL_HINT="这是测试 fixture,不需要安装"
tool_detect() {
    try_env FAKETOOL_HOME
    try_glob "$FAKETOOL_GLOB_BASE/faketool-*"
}
tool_activate() {
    local root=$1
    echo "export FAKETOOL_HOME=$root"
    echo "export PATH=$root/bin:\$PATH"
}
```

创建 `toolenv/tests/fixtures/tools.d/envtool.sh`:

```bash
TOOL_NAME="envtool"
TOOL_DESC="只认环境变量的假工具"
TOOL_HINT="export ENVTOOL_HOME=..."
tool_detect() {
    try_env ENVTOOL_HOME
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export ENVTOOL_HOME=$root"
    [ -n "$cenv" ] && echo "export ENVTOOL_CONDA_ENV=$cenv"
}
```

- [ ] **Step 2: 写失败的测试**

创建 `toolenv/tests/test_resolve.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"
. "$TOOLENV_HOME/lib/cache.sh"
. "$TOOLENV_HOME/lib/resolve.sh"

export TOOLENV_TOOLS_DIR="$TESTS_DIR/fixtures/tools.d"

_reset() { TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""; }

test_list_manifests() {
    local out; out=$(toolenv_list_manifests | tr '\n' ' ')
    assert_eq "$out" "envtool faketool "
}

test_load_manifest_sets_fields() {
    assert_ok toolenv_load_manifest faketool
    toolenv_load_manifest faketool
    assert_eq "$TOOL_NAME" "faketool"
    assert_contains "$TOOL_DESC" "假工具"
}

test_load_unknown_manifest_fails() {
    assert_fail toolenv_load_manifest nosuchtool
}

test_resolve_via_detect() {
    _reset
    mkdir -p "$SANDBOX/ft"
    export FAKETOOL_HOME="$SANDBOX/ft"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/ft"
    assert_eq "$TOOLENV_HIT_SOURCE" "env:FAKETOOL_HOME"
    unset FAKETOOL_HOME
}

test_resolve_via_glob_fallback() {
    _reset
    unset FAKETOOL_HOME
    mkdir -p "$SANDBOX/g/faketool-1.0" "$SANDBOX/g/faketool-2.0"
    export FAKETOOL_GLOB_BASE="$SANDBOX/g"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/g/faketool-2.0"
    unset FAKETOOL_GLOB_BASE
}

test_override_var_beats_detect() {
    _reset
    mkdir -p "$SANDBOX/ft" "$SANDBOX/override"
    export FAKETOOL_HOME="$SANDBOX/ft"
    export TOOLENV_FAKETOOL="$SANDBOX/override"
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/override"
    assert_eq "$TOOLENV_HIT_SOURCE" "override"
    unset FAKETOOL_HOME TOOLENV_FAKETOOL
}

test_overrides_file_is_sourced() {
    _reset
    mkdir -p "$SANDBOX/from-file"
    cat > "$TOOLENV_CONFIG_DIR/overrides.sh" <<EOF
export TOOLENV_FAKETOOL="$SANDBOX/from-file"
EOF
    toolenv_load_overrides
    assert_ok toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/from-file"
    unset TOOLENV_FAKETOOL
}

test_resolve_fails_when_nothing_found() {
    _reset
    unset FAKETOOL_HOME TOOLENV_FAKETOOL
    export FAKETOOL_GLOB_BASE="$SANDBOX/empty"
    assert_fail toolenv_resolve faketool
    assert_eq "$TOOLENV_HIT" ""
    unset FAKETOOL_GLOB_BASE
}

test_resolve_is_isolated_between_calls() {
    _reset
    mkdir -p "$SANDBOX/et"
    export ENVTOOL_HOME="$SANDBOX/et"
    toolenv_resolve envtool
    assert_eq "$TOOLENV_HIT" "$SANDBOX/et"
    unset ENVTOOL_HOME FAKETOOL_HOME
    export FAKETOOL_GLOB_BASE="$SANDBOX/empty"
    assert_fail toolenv_resolve faketool "上一次的命中不该泄漏到下一次解析"
    unset FAKETOOL_GLOB_BASE
}

test_activate_lines() {
    local out
    out=$(toolenv_activate_lines faketool /opt/ft "")
    assert_contains "$out" "export FAKETOOL_HOME=/opt/ft"
    assert_contains "$out" "export PATH=/opt/ft/bin:\$PATH"
}

test_activate_lines_passes_conda_env() {
    local out
    out=$(toolenv_activate_lines envtool /opt/envs/chem chem)
    assert_contains "$out" "export ENVTOOL_CONDA_ENV=chem"
}

run_all
```

- [ ] **Step 3: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_resolve.sh`
Expected: FAIL —— `lib/resolve.sh: No such file or directory`

- [ ] **Step 4: 实现 resolve.sh**

创建 `toolenv/lib/resolve.sh`:

```bash
# resolve.sh —— manifest 加载与优先级仲裁。依赖 probe.sh / conda.sh 已被 source。

toolenv_tools_dir() {
    printf '%s\n' "${TOOLENV_TOOLS_DIR:-$TOOLENV_HOME/tools.d}"
}

toolenv_list_manifests() {
    local d f
    d=$(toolenv_tools_dir)
    [ -d "$d" ] || return 1
    for f in "$d"/*.sh; do
        [ -f "$f" ] || continue
        basename "$f" .sh
    done | sort
}

toolenv_load_manifest() {
    local tool=$1 f
    f="$(toolenv_tools_dir)/$tool.sh"
    [ -f "$f" ] || { echo "toolenv: 没有这个工具的 manifest: $tool ($f)" >&2; return 1; }
    TOOL_NAME=""; TOOL_DESC=""; TOOL_HINT=""
    unset -f tool_detect tool_activate 2>/dev/null
    # shellcheck disable=SC1090
    . "$f" || return 1
    [ -n "$TOOL_NAME" ] || TOOL_NAME=$tool
    return 0
}

toolenv_load_overrides() {
    local f="${TOOLENV_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/toolenv}/overrides.sh"
    [ -f "$f" ] || return 0
    # shellcheck disable=SC1090
    . "$f"
}

_te_override_var() {   # 工具名 -> TOOLENV_XXX
    printf 'TOOLENV_%s' "$(printf '%s' "$1" | tr 'a-z-' 'A-Z_')"
}

# toolenv_resolve TOOL —— 解析一个工具
toolenv_resolve() {
    local tool=$1 ovar oval
    TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""
    ovar=$(_te_override_var "$tool")
    oval=${!ovar:-}
    if [ -n "$oval" ]; then
        if [ -d "$oval" ]; then
            _te_hit "$(readlink -f "$oval")" "override"
            return 0
        fi
        echo "toolenv: $ovar 指向的目录不存在: $oval" >&2
        return 1
    fi
    toolenv_load_manifest "$tool" || return 1
    tool_detect
    [ -n "$TOOLENV_HIT" ]
}

# toolenv_activate_lines TOOL PATH [CONDA_ENV]
toolenv_activate_lines() {
    local tool=$1 path=$2 cenv=${3:-}
    toolenv_load_manifest "$tool" || return 1
    tool_activate "$path" "$cenv"
}
```

- [ ] **Step 5: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`

- [ ] **Step 6: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/lib/resolve.sh toolenv/tests/test_resolve.sh toolenv/tests/fixtures
git commit -m "feat(toolenv): manifest 加载与 override>detect 优先级仲裁"
```

---

### Task 5: CLI —— probe / list / which / check / env

**Files:**
- Create: `toolenv/toolenv`
- Create: `toolenv/tests/test_cli.sh`

**Interfaces:**
- Consumes: Task 1-4 全部 lib
- Produces:
  - `toolenv probe [--force]` —— 解析全部 manifest,写缓存;stdout 打印进度行
  - `toolenv list` —— 表格,列:TOOL / STATUS / SOURCE / PATH
  - `toolenv which TOOL` —— 打印路径;未找到时非零退出且 stderr 带 `TOOL_HINT`
  - `toolenv check SPEC...` —— SPEC 是 `工具名` 或 `conda:环境名`;全部满足则静默退出 0,否则每个缺失打一行 `missing: <spec> — <hint>` 到 stderr,退出 1
  - `toolenv env TOOL...` —— 打印可 eval 的 export 行;有缺失则不输出任何 export 行并退出 1
  - 缓存缺失时自动 probe 一次;`--force` 强制重新探测
  - 未知子命令退出码 2 并打印用法

- [ ] **Step 1: 写失败的测试**

创建 `toolenv/tests/test_cli.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"

TOOLENV="$TOOLENV_HOME/toolenv"
export TOOLENV_TOOLS_DIR="$TESTS_DIR/fixtures/tools.d"

test_usage_on_unknown_subcommand() {
    local out rc
    out=$("$TOOLENV" bogus 2>&1); rc=$?
    assert_eq "$rc" "2"
    assert_contains "$out" "usage"
}

test_probe_writes_cache_and_reports() {
    mkdir -p "$SANDBOX/ft"
    local out
    out=$(FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe 2>&1)
    assert_contains "$out" "faketool"
    assert_ok test -f "$TOOLENV_CACHE_DIR/$(hostname -s).env"
}

test_list_shows_status_and_source() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out
    out=$("$TOOLENV" list 2>&1)
    assert_contains "$out" "faketool"
    assert_contains "$out" "found"
    assert_contains "$out" "$SANDBOX/ft"
    assert_contains "$out" "envtool"
    assert_contains "$out" "missing"
}

test_which_prints_path() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    assert_eq "$("$TOOLENV" which faketool)" "$SANDBOX/ft"
}

test_which_missing_fails_with_hint() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" which envtool 2>&1); rc=$?
    assert_eq "$rc" "1"
    assert_contains "$out" "ENVTOOL_HOME"
}

test_check_passes_silently() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" check faketool 2>&1); rc=$?
    assert_eq "$rc" "0"
    assert_eq "$out" ""
}

test_check_reports_each_missing() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" check faketool envtool 2>&1); rc=$?
    assert_eq "$rc" "1"
    assert_contains "$out" "missing: faketool"
    assert_contains "$out" "missing: envtool"
}

test_env_prints_export_lines() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local out
    out=$("$TOOLENV" env faketool)
    assert_contains "$out" "export FAKETOOL_HOME=$SANDBOX/ft"
}

test_env_is_evalable() {
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe >/dev/null 2>&1
    local got
    got=$(bash -c 'eval "$('"$TOOLENV"' env faketool)"; echo "$FAKETOOL_HOME"')
    assert_eq "$got" "$SANDBOX/ft"
}

test_env_emits_nothing_when_missing() {
    "$TOOLENV" probe >/dev/null 2>&1
    local out rc
    out=$("$TOOLENV" env envtool 2>/dev/null); rc=$?
    assert_eq "$rc" "1"
    assert_eq "$out" ""
}

test_probe_force_repicks_up_new_install() {
    "$TOOLENV" probe >/dev/null 2>&1
    assert_fail "$TOOLENV" which faketool
    mkdir -p "$SANDBOX/ft"
    FAKETOOL_HOME="$SANDBOX/ft" "$TOOLENV" probe --force >/dev/null 2>&1
    assert_eq "$("$TOOLENV" which faketool)" "$SANDBOX/ft"
}

run_all
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_cli.sh`
Expected: FAIL —— `toolenv: No such file or directory`

- [ ] **Step 3: 实现 CLI**

创建 `toolenv/toolenv`:

```bash
#!/usr/bin/env bash
# toolenv —— 计算化学工具的发现与激活层。
# 用法见 usage()。设计见 docs/superpowers/specs/2026-07-22-comp-chem-skills-design.md
set -u

TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$(readlink -f "$0")")}
export TOOLENV_HOME
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"
. "$TOOLENV_HOME/lib/cache.sh"
. "$TOOLENV_HOME/lib/resolve.sh"

usage() {
    cat >&2 <<'EOF'
usage: toolenv <command> [args]

  probe [--force]     探测全部工具并写缓存
  list                列出工具:状态 / 来源 / 路径
  which <tool>        打印工具根路径
  check <spec>...     检查依赖是否齐备(spec: <tool> 或 conda:<env>)
  env <tool>...       打印可 eval 的 export 行
EOF
    exit 2
}

# ---- 探测 ----------------------------------------------------------------
cmd_probe() {
    local force=0 tool
    [ "${1:-}" = "--force" ] && force=1
    toolenv_load_overrides
    if [ "$force" = 0 ] && toolenv_cache_load; then
        return 0
    fi
    toolenv_cache_clear
    while read -r tool; do
        [ -n "$tool" ] || continue
        if toolenv_resolve "$tool" 2>/dev/null; then
            toolenv_cache_put "$tool" found "$TOOLENV_HIT" "$TOOLENV_HIT_SOURCE" "$TOOLENV_HIT_ENV"
            printf 'found   %-14s %s  [%s]\n' "$tool" "$TOOLENV_HIT" "$TOOLENV_HIT_SOURCE"
        else
            toolenv_cache_put "$tool" missing "" "" ""
            printf 'missing %-14s\n' "$tool"
        fi
    done < <(toolenv_list_manifests)
    toolenv_cache_flush
}

_ensure_cache() {
    toolenv_load_overrides
    toolenv_cache_load 2>/dev/null || cmd_probe >/dev/null
    toolenv_cache_load 2>/dev/null
}

# ---- 查询 ----------------------------------------------------------------
cmd_list() {
    _ensure_cache
    printf '%-14s %-8s %-22s %s\n' TOOL STATUS SOURCE PATH
    local tool
    while read -r tool; do
        [ -n "$tool" ] || continue
        printf '%-14s %-8s %-22s %s\n' \
            "$tool" \
            "$(toolenv_cache_get "$tool" STATUS)" \
            "$(toolenv_cache_get "$tool" SOURCE)" \
            "$(toolenv_cache_get "$tool" PATH)"
    done < <(toolenv_cache_tools | sort)
}

_hint_for() {
    local tool=$1
    ( toolenv_load_manifest "$tool" >/dev/null 2>&1 && printf '%s' "$TOOL_HINT" )
}

cmd_which() {
    local tool=${1:-}
    [ -n "$tool" ] || usage
    _ensure_cache
    if [ "$(toolenv_cache_get "$tool" STATUS 2>/dev/null)" = "found" ]; then
        toolenv_cache_get "$tool" PATH
        return 0
    fi
    echo "toolenv: 没找到 $tool。$(_hint_for "$tool")" >&2
    echo "         纠正路径:在 ${TOOLENV_CONFIG_DIR:-\$HOME/.config/toolenv}/overrides.sh 里设 $(_te_override_var "$tool")=..." >&2
    return 1
}

# _check_spec SPEC —— 满足返回 0
_check_spec() {
    local spec=$1
    case "$spec" in
        conda:*) toolenv_conda_has_env "${spec#conda:}" ;;
        *)       [ "$(toolenv_cache_get "$spec" STATUS 2>/dev/null)" = "found" ] ;;
    esac
}

_spec_hint() {
    local spec=$1
    case "$spec" in
        conda:*) printf 'conda 环境 "%s" 不存在,用 `conda create -n %s` 建' "${spec#conda:}" "${spec#conda:}" ;;
        *)       _hint_for "$spec" ;;
    esac
}

cmd_check() {
    [ $# -gt 0 ] || usage
    _ensure_cache
    local spec rc=0
    for spec in "$@"; do
        if ! _check_spec "$spec"; then
            echo "missing: $spec — $(_spec_hint "$spec")" >&2
            rc=1
        fi
    done
    return "$rc"
}

cmd_env() {
    [ $# -gt 0 ] || usage
    _ensure_cache
    cmd_check "$@" || return 1
    local spec out=""
    for spec in "$@"; do
        case "$spec" in
            conda:*) continue ;;   # conda 环境由具体工具的 tool_activate 负责激活
        esac
        out="$out$(toolenv_activate_lines "$spec" \
                    "$(toolenv_cache_get "$spec" PATH)" \
                    "$(toolenv_cache_get "$spec" ENV)")
"
    done
    printf '%s' "$out"
}

# ---- 分发 ----------------------------------------------------------------
cmd=${1:-}
[ $# -gt 0 ] && shift
case "$cmd" in
    probe) cmd_probe "$@" ;;
    list)  cmd_list "$@" ;;
    which) cmd_which "$@" ;;
    check) cmd_check "$@" ;;
    env)   cmd_env "$@" ;;
    *)     usage ;;
esac
```

```bash
chmod +x toolenv/toolenv
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`

- [ ] **Step 5: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/toolenv toolenv/tests/test_cli.sh
git commit -m "feat(toolenv): CLI probe/list/which/check/env"
```

---

### Task 6: 脚本头元信息 + index / requires / run + activate.sh

**Files:**
- Create: `toolenv/lib/meta.sh`
- Create: `toolenv/activate.sh`
- Modify: `toolenv/toolenv`(加 `index` / `requires` / `run` 三个子命令与用法行)
- Create: `toolenv/tests/test_meta.sh`
- Create: `toolenv/tests/fixtures/scripts/demo.sh`

**Interfaces:**
- Consumes: Task 5 的 CLI 结构、Task 4 的 `toolenv_load_manifest`
- Produces:
  - `toolenv_meta_get FILE KEY` —— 打印脚本头 `# @KEY: value` 的值,无则空串返回 1
  - `toolenv_meta_requires FILE` —— 打印 `@requires` 逗号分隔值,规范化成空格分隔
  - `toolenv requires <script>` —— 同上,给 `activate.sh` 用
  - `toolenv index <dir>` —— markdown 表格:name / description / requires / usage
  - `toolenv run <script> [args...]` —— check + 激活 + exec
  - `activate.sh` —— 被脚本 `source`;解析调用方 `@requires` 并激活,失败时打印缺失项并 `exit 1`

- [ ] **Step 1: 写测试 fixture**

创建 `toolenv/tests/fixtures/scripts/demo.sh`:

```bash
#!/usr/bin/env bash
# @name: demo
# @description: 演示脚本,验证元信息解析
# @requires: faketool, conda:demoenv
# @usage: demo.sh <dir>...
set -u
echo "demo ran with FAKETOOL_HOME=${FAKETOOL_HOME:-unset}"
```

```bash
chmod +x toolenv/tests/fixtures/scripts/demo.sh
```

- [ ] **Step 2: 写失败的测试**

创建 `toolenv/tests/test_meta.sh`:

```bash
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
```

- [ ] **Step 3: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_meta.sh`
Expected: FAIL —— `lib/meta.sh: No such file or directory`

- [ ] **Step 4: 实现 meta.sh**

创建 `toolenv/lib/meta.sh`:

```bash
# meta.sh —— 解析脚本头的 "# @key: value" 元信息。
# 只扫描文件开头的注释区:遇到第一行既非注释也非空行即停止。

toolenv_meta_get() {
    local file=$1 key=$2 line val=""
    [ -f "$file" ] || return 1
    while IFS= read -r line; do
        case "$line" in
            '#!'*) continue ;;
            '#'*)  ;;
            '')    continue ;;
            *)     break ;;
        esac
        case "$line" in
            "# @$key:"*)
                val=${line#"# @$key:"}
                # 去掉首尾空白
                val=${val#"${val%%[![:space:]]*}"}
                val=${val%"${val##*[![:space:]]}"}
                printf '%s\n' "$val"
                return 0
                ;;
        esac
    done < "$file"
    return 1
}

# toolenv_meta_requires FILE —— 逗号分隔转空格分隔
toolenv_meta_requires() {
    local file=$1 raw
    raw=$(toolenv_meta_get "$file" requires) || return 0
    printf '%s\n' "$raw" | tr ',' ' ' | tr -s '[:space:]' ' ' \
        | sed 's/^ *//; s/ *$//'
}
```

- [ ] **Step 5: 实现 activate.sh**

创建 `toolenv/activate.sh`:

```bash
# activate.sh —— 被脚本 source 的一行入口:
#     source "$(dirname "$0")/../../toolenv/activate.sh"
# 读调用方脚本头的 @requires,检查并激活;缺依赖时打印缺什么并 exit 1。

_te_self=$(readlink -f "${BASH_SOURCE[0]}")
_te_home=$(dirname "$_te_self")
_te_caller=$(readlink -f "${BASH_SOURCE[1]:-$0}")

_te_reqs=$("$_te_home/toolenv" requires "$_te_caller")

if [ -n "$_te_reqs" ]; then
    # shellcheck disable=SC2086
    if ! "$_te_home/toolenv" check $_te_reqs; then
        echo "toolenv: $(basename "$_te_caller") 的依赖没装齐,已中止。" >&2
        echo "         看全貌:$_te_home/toolenv list" >&2
        exit 1
    fi
    # shellcheck disable=SC2086
    eval "$("$_te_home/toolenv" env $_te_reqs)"
fi

unset _te_self _te_home _te_caller _te_reqs
```

- [ ] **Step 6: 给 CLI 加 requires / index / run**

在 `toolenv/toolenv` 的 `. "$TOOLENV_HOME/lib/resolve.sh"` 之后加一行:

```bash
. "$TOOLENV_HOME/lib/meta.sh"
```

把 `usage()` 的 heredoc 改成:

```bash
    cat >&2 <<'EOF'
usage: toolenv <command> [args]

  probe [--force]     探测全部工具并写缓存
  list                列出工具:状态 / 来源 / 路径
  which <tool>        打印工具根路径
  check <spec>...     检查依赖是否齐备(spec: <tool> 或 conda:<env>)
  env <tool>...       打印可 eval 的 export 行
  requires <script>   打印脚本头声明的 @requires
  index <dir>         把目录里的脚本列成 markdown 表
  run <script> [args] 检查依赖、激活环境,然后执行脚本
EOF
```

在 `# ---- 分发 ----` 之前插入:

```bash
# ---- 脚本 ----------------------------------------------------------------
cmd_requires() {
    local f=${1:-}
    [ -n "$f" ] || usage
    toolenv_meta_requires "$f"
}

cmd_index() {
    local dir=${1:-.} f name desc reqs use
    [ -d "$dir" ] || { echo "toolenv: 不是目录: $dir" >&2; return 1; }
    echo '| 脚本 | 说明 | 依赖 | 用法 |'
    echo '|------|------|------|------|'
    for f in "$dir"/*; do
        [ -f "$f" ] || continue
        name=$(toolenv_meta_get "$f" name) || continue
        desc=$(toolenv_meta_get "$f" description) || desc=""
        reqs=$(toolenv_meta_requires "$f")
        use=$(toolenv_meta_get "$f" usage) || use=""
        printf '| %s | %s | %s | `%s` |\n' "$name" "$desc" "$reqs" "$use"
    done
}

cmd_run() {
    local script=${1:-}
    [ -n "$script" ] || usage
    shift
    local reqs
    reqs=$(toolenv_meta_requires "$script")
    if [ -n "$reqs" ]; then
        # shellcheck disable=SC2086
        cmd_check $reqs || return 1
        # shellcheck disable=SC2086
        eval "$(cmd_env $reqs)"
    fi
    exec "$script" "$@"
}
```

在 `case "$cmd" in` 里加三行:

```bash
    requires) cmd_requires "$@" ;;
    index)    cmd_index "$@" ;;
    run)      cmd_run "$@" ;;
```

- [ ] **Step 7: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`

注:`test_activate_fails_loudly_when_dep_missing` 依赖 `conda:demoenv` 不存在与 `envtool` 未找到,沙箱里两者都成立。

- [ ] **Step 8: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/lib/meta.sh toolenv/activate.sh toolenv/toolenv \
        toolenv/tests/test_meta.sh toolenv/tests/fixtures/scripts
git commit -m "feat(toolenv): 脚本头元信息解析 + index/run/activate.sh"
```

---

### Task 7: 真实工具 manifest(6 个)

**Files:**
- Create: `toolenv/tools.d/conda.sh`
- Create: `toolenv/tools.d/schrodinger.sh`
- Create: `toolenv/tools.d/automd.sh`
- Create: `toolenv/tools.d/plip.sh`
- Create: `toolenv/tools.d/ambertools.sh`
- Create: `toolenv/tools.d/rdkit.sh`
- Create: `toolenv/tests/test_manifests.sh`

**Interfaces:**
- Consumes: Task 4 的 manifest 契约、Task 1-2 的探测原语
- Produces:六个可用 manifest。`automd` 的 `tool_detect` 额外支持 `TOOLENV_AUTOMD_BUNDLED`(指向 skill 内置副本)——由 md-pipeline 在 Task 9 设置

- [ ] **Step 1: 写"每个 manifest 都合规"的测试**

创建 `toolenv/tests/test_manifests.sh`:

```bash
#!/usr/bin/env bash
# 这些测试不要求工具真的装了,只验证 manifest 本身合规、可加载、不崩。
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/lib/probe.sh"
. "$TOOLENV_HOME/lib/conda.sh"
. "$TOOLENV_HOME/lib/cache.sh"
. "$TOOLENV_HOME/lib/resolve.sh"

EXPECTED="ambertools automd conda plip rdkit schrodinger"

test_all_expected_manifests_present() {
    assert_eq "$(toolenv_list_manifests | tr '\n' ' ' | sed 's/ $//')" "$EXPECTED"
}

test_every_manifest_declares_required_fields() {
    local t
    for t in $EXPECTED; do
        toolenv_load_manifest "$t" || { fail "$t 加载失败"; continue; }
        [ -n "$TOOL_NAME" ] || fail "$t 缺 TOOL_NAME"
        [ -n "$TOOL_DESC" ] || fail "$t 缺 TOOL_DESC"
        [ -n "$TOOL_HINT" ] || fail "$t 缺 TOOL_HINT"
        declare -F tool_detect   >/dev/null || fail "$t 缺 tool_detect"
        declare -F tool_activate >/dev/null || fail "$t 缺 tool_activate"
    done
}

test_every_detect_runs_without_error_in_empty_sandbox() {
    # 干净沙箱里应当是"找不到",而不是报错或挂住
    local t
    for t in $EXPECTED; do
        TOOLENV_HIT=""; TOOLENV_HIT_SOURCE=""; TOOLENV_HIT_ENV=""
        toolenv_load_manifest "$t"
        tool_detect >/dev/null 2>&1
        # 不断言找不到(沙箱里 PATH 仍可能有真 conda),只断言没崩
        assert_eq "$?" "$?" "$t tool_detect 应当正常返回"
    done
}

test_every_activate_emits_only_assignments() {
    local t out line
    for t in $EXPECTED; do
        out=$(toolenv_activate_lines "$t" /fake/root fakeenv 2>/dev/null)
        while IFS= read -r line; do
            [ -n "$line" ] || continue
            case "$line" in
                export\ *|.\ *|conda\ activate*) ;;
                *) fail "$t 的 tool_activate 输出了非赋值行: $line" ;;
            esac
        done <<< "$out"
    done
}

run_all
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_manifests.sh`
Expected: FAIL —— manifest 列表为空,第一个断言不匹配

- [ ] **Step 3: 写 conda manifest**

创建 `toolenv/tools.d/conda.sh`:

```bash
TOOL_NAME="conda"
TOOL_DESC="conda / miniforge 安装根目录"
TOOL_HINT="装 miniforge:https://github.com/conda-forge/miniforge#install"
tool_detect() {
    [ -n "$TOOLENV_HIT" ] && return 0
    local root
    root=$(toolenv_conda_root) || return 1
    _te_hit "$root" "conda-root"
}
tool_activate() {
    local root=$1
    echo "export CONDA_ROOT=$root"
    echo ". \"$root/etc/profile.d/conda.sh\""
}
```

- [ ] **Step 4: 写 schrodinger manifest**

创建 `toolenv/tools.d/schrodinger.sh`:

```bash
TOOL_NAME="schrodinger"
TOOL_DESC="Schrödinger Suite(含 Desmond):\$SCHRODINGER/run、jobcontrol、prime_mmgbsa"
TOOL_HINT="需要已授权的 Schrödinger 安装;装好后 export SCHRODINGER=/path/to/Schrodinger/20XX-N"
tool_detect() {
    try_env SCHRODINGER
    try_cmd maestro --up 2
    try_glob "$HOME/software/Schrodinger/*" \
             "$HOME/Schrodinger/*" \
             "/opt/schrodinger/*" \
             "/opt/Schrodinger/*" \
             "/usr/local/schrodinger/*"
}
tool_activate() {
    local root=$1
    echo "export SCHRODINGER=$root"
    echo "export Desmond=$root"          # AutoTRJ 依赖 \$Desmond
    echo "export PATH=$root:\$PATH"
}
```

- [ ] **Step 5: 写 automd manifest**

创建 `toolenv/tools.d/automd.sh`:

```bash
TOOL_NAME="automd"
TOOL_DESC="AutoMD / AutoTRJ(第三方,GPLv3;md-pipeline skill 内置一份副本)"
TOOL_HINT="md-pipeline skill 已内置;或 git clone https://github.com/Wang-Lin-boop/AutoMD"
tool_detect() {
    try_env TOOLENV_AUTOMD_BUNDLED      # skill 内置副本,优先
    try_env AUTOMD_DIR
    try_cmd AutoTRJ --up 1
    try_glob "$HOME/software/AutoMD" "$HOME/AutoMD" "/opt/AutoMD"
}
tool_activate() {
    local root=$1
    echo "export AUTOMD_DIR=$root"
    echo "export PATH=$root:\$PATH"
}
```

- [ ] **Step 6: 写 plip / ambertools / rdkit manifest**

创建 `toolenv/tools.d/plip.sh`:

```bash
TOOL_NAME="plip"
TOOL_DESC="PLIP:蛋白-配体相互作用分析(python 包 + plip 命令)"
TOOL_HINT="在目标 conda 环境里:pip install plip"
tool_detect() {
    try_conda_env_bin plip
    try_conda_env_python "import plip"
    try_cmd plip --up 2
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_PLIP_ENV=$cenv"
    fi
}
```

创建 `toolenv/tools.d/ambertools.sh`:

```bash
TOOL_NAME="ambertools"
TOOL_DESC="AmberTools:antechamber / tleap / parmchk2 / cpptraj"
TOOL_HINT="conda create -n amber -c conda-forge ambertools"
tool_detect() {
    try_env AMBERHOME
    try_cmd antechamber --up 2
    try_conda_env_bin antechamber
    try_glob "$HOME/software/amber*" "$HOME/amber*" "/opt/amber*"
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export AMBERHOME=$root"
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_AMBERTOOLS_ENV=$cenv"
    fi
}
```

创建 `toolenv/tools.d/rdkit.sh`:

```bash
TOOL_NAME="rdkit"
TOOL_DESC="RDKit(python 包);TOOLENV_RDKIT_ENV 是能 import rdkit 的 conda 环境名"
TOOL_HINT="conda create -n chem -c conda-forge rdkit"
tool_detect() {
    try_conda_env_python "import rdkit"
}
tool_activate() {
    local root=$1 cenv=${2:-}
    echo "export PATH=$root/bin:\$PATH"
    if [ -n "$cenv" ]; then
        echo "export TOOLENV_RDKIT_ENV=$cenv"
    fi
}
```

- [ ] **Step 7: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh`
Expected: `ALL PASS`

- [ ] **Step 8: 在真机上验证探测结果**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/toolenv probe --force && ./toolenv/toolenv list`
Expected:`conda` 解析到 `/data1/home/huangshengjie/miniforge3`;`schrodinger` 解析到 `/data1/home/huangshengjie/software/Schrodinger/2023-4`;`automd` 解析到 `~/software/AutoMD`。`ambertools`/`rdkit`/`plip` 视实际安装为 found 或 missing —— 若 missing,确认 stderr 给出的安装提示可读。把这次的实际输出记录在提交信息里。

- [ ] **Step 9: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/tools.d toolenv/tests/test_manifests.sh
git commit -m "feat(toolenv): 首批 manifest —— conda/schrodinger/automd/plip/ambertools/rdkit"
```

---

### Task 8: selftest 与 install.sh

**Files:**
- Modify: `toolenv/toolenv`(加 `selftest` 子命令)
- Create: `install.sh`
- Create: `toolenv/tests/test_selftest.sh`

**Interfaces:**
- Consumes: Task 5-7 的 CLI
- Produces:
  - `toolenv selftest` —— 在 `env -i bash` 干净环境里跑 probe + list,验证不依赖交互式 `.bashrc`;有工具 missing 不算失败(只报告),CLI 自身报错才算失败
  - `install.sh` —— symlink 仓库里所有含 `SKILL.md` 的目录到 `~/.claude/skills/`,然后 probe 并打印 list

- [ ] **Step 1: 写失败的测试**

创建 `toolenv/tests/test_selftest.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"

TOOLENV="$TOOLENV_HOME/toolenv"
REPO=$(dirname "$TOOLENV_HOME")

test_selftest_passes_in_clean_env() {
    local out rc
    out=$("$TOOLENV" selftest 2>&1); rc=$?
    assert_eq "$rc" "0"
    assert_contains "$out" "clean-env"
}

test_install_creates_symlinks() {
    local out
    mkdir -p "$HOME/.claude/skills"
    out=$(HOME="$HOME" "$REPO/install.sh" 2>&1)
    assert_ok test -L "$HOME/.claude/skills/md-pipeline"
    assert_eq "$(readlink -f "$HOME/.claude/skills/md-pipeline")" "$REPO/md-pipeline"
}

test_install_is_idempotent() {
    mkdir -p "$HOME/.claude/skills"
    "$REPO/install.sh" >/dev/null 2>&1
    local out rc
    out=$("$REPO/install.sh" 2>&1); rc=$?
    assert_eq "$rc" "0"
    assert_ok test -L "$HOME/.claude/skills/md-pipeline"
}

run_all
```

注:`test_install_*` 依赖 Task 9 建出 `md-pipeline/SKILL.md`。本任务先让 `install.sh` 具备能力,这两项测试会在 Task 9 完成后转绿 —— 本任务只要求 `test_selftest_passes_in_clean_env` 通过,另两项此时预期失败,Task 9 的 Step 6 会复查。

- [ ] **Step 2: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_selftest.sh`
Expected: 3 项全 FAIL(`selftest` 未实现、`install.sh` 不存在)

- [ ] **Step 3: 实现 selftest**

在 `toolenv/toolenv` 的 `cmd_run` 之后加:

```bash
cmd_selftest() {
    echo "== clean-env probe(env -i,验证不依赖交互式 .bashrc)"
    env -i HOME="$HOME" PATH="/usr/bin:/bin" \
        TOOLENV_HOME="$TOOLENV_HOME" \
        ${TOOLENV_TOOLS_DIR:+TOOLENV_TOOLS_DIR="$TOOLENV_TOOLS_DIR"} \
        ${TOOLENV_CACHE_DIR:+TOOLENV_CACHE_DIR="$TOOLENV_CACHE_DIR"} \
        ${TOOLENV_CONFIG_DIR:+TOOLENV_CONFIG_DIR="$TOOLENV_CONFIG_DIR"} \
        bash "$TOOLENV_HOME/toolenv" probe --force || {
            echo "selftest: 干净环境里 probe 失败" >&2; return 1; }
    echo
    echo "== list"
    "$TOOLENV_HOME/toolenv" list || return 1
    echo
    echo "selftest OK(工具 missing 不算失败,照上表补装即可)"
}
```

在 `case "$cmd" in` 里加:

```bash
    selftest) cmd_selftest "$@" ;;
```

并在 `usage()` heredoc 末尾加一行:

```
  selftest            在干净环境里自检
```

- [ ] **Step 4: 实现 install.sh**

创建 `install.sh`:

```bash
#!/usr/bin/env bash
# install.sh —— 把本仓库里的 skill 挂到 ~/.claude/skills/,并做一次工具探测。
# 换机器:git clone && ./install.sh
set -u

REPO=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILLS_DIR="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}"

mkdir -p "$SKILLS_DIR" || exit 1

echo "== 挂载 skill 到 $SKILLS_DIR"
found=0
for d in "$REPO"/*/; do
    d=${d%/}
    [ -f "$d/SKILL.md" ] || continue
    found=1
    name=$(basename "$d")
    target="$SKILLS_DIR/$name"
    if [ -L "$target" ]; then
        if [ "$(readlink -f "$target")" = "$d" ]; then
            echo "  = $name(已挂载)"
        else
            ln -sfn "$d" "$target" && echo "  ~ $name(重指向)"
        fi
    elif [ -e "$target" ]; then
        echo "  ! $name:$target 已存在且不是 symlink,跳过。手工处理后重跑。" >&2
    else
        ln -s "$d" "$target" && echo "  + $name"
    fi
done
[ "$found" = 1 ] || echo "  (仓库里还没有含 SKILL.md 的目录)"

echo
echo "== 探测工具"
"$REPO/toolenv/toolenv" probe --force >/dev/null || exit 1
"$REPO/toolenv/toolenv" list

echo
echo "路径不对?写 ${XDG_CONFIG_HOME:-$HOME/.config}/toolenv/overrides.sh,例如:"
echo "  export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1"
echo "把 toolenv 加进 PATH(可选):export PATH=\"$REPO/toolenv:\$PATH\""
```

```bash
chmod +x install.sh
```

- [ ] **Step 5: 运行测试,确认 selftest 项通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh test_selftest.sh`
Expected: `test_selftest_passes_in_clean_env` ✓;另两项仍 ✗(md-pipeline 尚不存在,Task 9 修复)

- [ ] **Step 6: 手工验证干净环境自检**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/toolenv selftest`
Expected: 打印 clean-env probe 结果与 list 表,末行 `selftest OK`

- [ ] **Step 7: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add toolenv/toolenv install.sh toolenv/tests/test_selftest.sh
git commit -m "feat: toolenv selftest 与仓库 install.sh"
```

---

### Task 9: md-pipeline skill 迁移

**Files:**
- Move: `md_pipeline/*` → `md-pipeline/scripts/`(用 `git mv`)
- Create: `md-pipeline/SKILL.md`
- Create: `md-pipeline/references/troubleshooting.md`
- Modify: `md-pipeline/scripts/env.sh`(改成 toolenv 薄壳)
- Modify: `md-pipeline/scripts/run_serial_md.sh`、`run_analysis.sh`、`run_plip.sh`、`run_mmgbsa.sh`(加脚本头元信息)
- Create: `md-pipeline/tests/test_env_shim.sh`

**Interfaces:**
- Consumes: Task 6 的 `activate.sh`、Task 7 的 manifest(尤其 `TOOLENV_AUTOMD_BUNDLED`)
- Produces:
  - `md-pipeline/scripts/env.sh` —— 仍可被 `source`,仍提供 `md_env_check`,仍导出 `SCHRODINGER`/`Desmond`/`AUTOMD_DIR`,内部改由 toolenv 解析
  - 四个 `run_*.sh` 带 `@name`/`@description`/`@requires`/`@usage` 头
  - `SKILL.md` frontmatter:`name: md-pipeline`,`description` 说明触发场景

- [ ] **Step 1: 搬目录**

```bash
cd /data1/home/huangshengjie/workstations/skills
mkdir -p md-pipeline/scripts md-pipeline/references md-pipeline/tests
git mv md_pipeline/AutoMD md-pipeline/scripts/AutoMD
for f in env.sh run_serial_md.sh run_analysis.sh run_plip.sh run_mmgbsa.sh \
         plip_interaction_analysis.py summarize_analysis.py \
         md_conda_environment.yml md_pending_serial.list.template; do
    git mv "md_pipeline/$f" "md-pipeline/scripts/$f"
done
git mv md_pipeline/README.md md-pipeline/references/original-readme.md
rm -rf md_pipeline/__pycache__ md_pipeline/run_serial_md.log
rmdir md_pipeline 2>/dev/null || ls -la md_pipeline
```

Expected: `md_pipeline/` 被清空并删除;`git status` 显示的是 rename 而非 delete+add

- [ ] **Step 2: 拆出 troubleshooting.md**

把 `md-pipeline/references/original-readme.md` 里 `## ⚠️ 踩坑记录(重要经验)` 这一节(共 7 条,到 `## 备注` 之前)整段剪切到新文件 `md-pipeline/references/troubleshooting.md`,文件开头加:

```markdown
# md-pipeline 踩坑记录

来自实际跑 PEPX_P8P9 这批体系时踩到的坑。改动 `run_*.sh` 或调 ASL / 帧范围 /
并行度之前先读这里。

```

其余部分留在 `original-readme.md`。剪切后确认两文件都能正常渲染,且 7 条记录一条不少。

Run: `grep -c '^### ' md-pipeline/references/troubleshooting.md`
Expected: `7`

- [ ] **Step 3: 写 env.sh 薄壳的失败测试**

创建 `md-pipeline/tests/test_env_shim.sh`:

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$(dirname "$SKILL_DIR")
. "$REPO/toolenv/tests/helpers.sh"

ENV_SH="$SKILL_DIR/scripts/env.sh"

test_env_sh_exports_schrodinger_and_automd() {
    local out
    out=$(bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; echo "S=$SCHRODINGER"; echo "D=$Desmond"; echo "A=$AUTOMD_DIR"')
    assert_contains "$out" "S=/"
    assert_contains "$out" "D=/"
    assert_contains "$out" "A=/"
}

test_automd_resolves_to_bundled_copy() {
    local out
    out=$(bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; command -v AutoTRJ')
    assert_eq "$out" "$SKILL_DIR/scripts/AutoMD/AutoTRJ"
}

test_md_env_check_is_defined_and_runs() {
    assert_ok bash -c '. "'"$ENV_SH"'" >/dev/null 2>&1; declare -F md_env_check >/dev/null'
}

test_env_sh_works_in_clean_shell() {
    # 这是原 env.sh 的既有验收标准:不依赖交互式 .bashrc
    local out
    out=$(env -i HOME="$REAL_HOME" PATH=/usr/bin:/bin bash -c \
        '. "'"$ENV_SH"'" >/dev/null 2>&1; echo "S=$SCHRODINGER"')
    assert_contains "$out" "S=/"
}

REAL_HOME=$HOME
export REAL_HOME
run_all
```

注:`test_env_sh_works_in_clean_shell` 用真实 `$HOME`(沙箱 HOME 里没有 conda/Schrödinger),故在 `run_all` 前先存下 `REAL_HOME`。

- [ ] **Step 4: 运行测试,确认失败**

Run: `cd /data1/home/huangshengjie/workstations/skills && bash md-pipeline/tests/test_env_shim.sh`
Expected: `test_automd_resolves_to_bundled_copy` FAIL —— 旧 env.sh 的 `AUTOMD_DIR` 指向已不存在的旧路径

- [ ] **Step 5: 把 env.sh 改成 toolenv 薄壳**

用以下内容整体替换 `md-pipeline/scripts/env.sh`:

```bash
# env.sh —— md-pipeline 的环境入口(toolenv 薄壳)。
#
# 保持向后兼容:仍然可以 `source env.sh && md_env_check`,仍然导出
# SCHRODINGER / Desmond / AUTOMD_DIR。实际的"工具装在哪"交给 toolenv 解析,
# 换机器不需要改本文件。要纠正路径,写 ~/.config/toolenv/overrides.sh。
#
# 本文件被 source,不用 set -e;失败用 WARN/ERROR 提示,不中断调用方。

MD_PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_TOOLENV="$(cd "$MD_PIPELINE_DIR/../../toolenv" && pwd)/toolenv"

# skill 内置的 AutoMD 副本优先于系统安装
export TOOLENV_AUTOMD_BUNDLED="$MD_PIPELINE_DIR/AutoMD"

# conda 环境名:沿用旧变量名,允许覆盖
export MD_CONDA_ENV="${MD_CONDA_ENV:-md}"

if [ -x "$_TOOLENV" ]; then
    eval "$("$_TOOLENV" env conda schrodinger automd 2>/dev/null)"
else
    echo "WARN[env.sh]: 找不到 toolenv: $_TOOLENV" >&2
fi

# 激活隔离的 conda 环境(不污染调用方 base)
if [ -n "${CONDA_ROOT:-}" ] && [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
    # shellcheck disable=SC1091
    . "$CONDA_ROOT/etc/profile.d/conda.sh"
    conda activate "$MD_CONDA_ENV" 2>/dev/null \
        || echo "WARN[env.sh]: conda 环境 '$MD_CONDA_ENV' 激活失败,请确认已 create。" >&2
    # Schrödinger `run` 需要的动态库:隔离环境的 lib 优先
    export LD_LIBRARY_PATH="$CONDA_ROOT/envs/$MD_CONDA_ENV/lib:${LD_LIBRARY_PATH:-}"
fi

# 自检:调用方可在 source 后执行 `md_env_check || exit 1`
md_env_check() {
    "$_TOOLENV" check schrodinger automd conda "conda:$MD_CONDA_ENV" || return 1
    local ok=1
    command -v AutoMD  >/dev/null 2>&1 || { echo "ERROR: AutoMD 不在 PATH"  >&2; ok=0; }
    command -v AutoTRJ >/dev/null 2>&1 || { echo "ERROR: AutoTRJ 不在 PATH" >&2; ok=0; }
    [ "${CONDA_DEFAULT_ENV:-}" = "$MD_CONDA_ENV" ] \
        || echo "WARN: 当前 conda 环境=${CONDA_DEFAULT_ENV:-none}(期望 $MD_CONDA_ENV)" >&2
    [ "$ok" = 1 ]
}
```

- [ ] **Step 6: 运行测试,确认通过**

Run: `cd /data1/home/huangshengjie/workstations/skills && bash md-pipeline/tests/test_env_shim.sh && ./toolenv/tests/run_tests.sh`
Expected: 两者都 `0 failures` / `ALL PASS`(Task 8 里那两个 install 测试此时也应转绿)

- [ ] **Step 7: 给四个 run_*.sh 加元信息头**

在每个脚本的 shebang 之后、第一行代码之前插入对应的四行。`run_serial_md.sh`:

```bash
# @name: run_serial_md
# @description: 串行跑 Desmond MD:自动找空闲 GPU,逐个提交,跑完自动接分析
# @requires: schrodinger, automd, conda, conda:md
# @usage: run_serial_md.sh [--gpu N | --gpus 0,2] [--dry-run] [--list FILE]
```

`run_analysis.sh`:

```bash
# @name: run_analysis
# @description: 对已完成的 MD 目录重跑分析(AutoTRJ 聚类 + SID 交互报告)
# @requires: schrodinger, automd, conda, conda:md
# @usage: run_analysis.sh <md-dir>...
```

`run_plip.sh`:

```bash
# @name: run_plip
# @description: PLIP 肽-受体相互作用分析,逐帧算类型与残基对占据率(默认后 100ns)
# @requires: schrodinger, plip, conda, conda:md
# @usage: LAST_NS=100 JOBS=8 run_plip.sh <md-dir>...
```

`run_mmgbsa.sh`:

```bash
# @name: run_mmgbsa
# @description: Schrödinger Prime MMGBSA 逐帧结合自由能(默认后 100ns 每 20 帧)
# @requires: schrodinger, conda, conda:md
# @usage: START=1000 END=2000 STEP=20 NJOBS=4 run_mmgbsa.sh <md-dir>...
```

- [ ] **Step 8: 验证 index 能扫出四个脚本**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/toolenv index md-pipeline/scripts`
Expected: markdown 表格含 `run_serial_md` / `run_analysis` / `run_plip` / `run_mmgbsa` 四行,依赖列非空

- [ ] **Step 9: 写 SKILL.md**

创建 `md-pipeline/SKILL.md`:

```markdown
---
name: md-pipeline
description: Use when running or analyzing Desmond/Schrödinger molecular dynamics of protein-peptide or protein-ligand systems - serial MD on free GPUs, AutoTRJ clustering, PLIP interaction occupancy, Prime MMGBSA binding free energy, and merged summary tables. Also use when asked about MD trajectory analysis, 结合自由能, 相互作用占据率, or 跑 MD.
---

# MD 运行 + 轨迹分析流程

把「串行跑 Desmond MD → AutoTRJ 聚类 → SID 交互报告 → PLIP 相互作用 → Prime MMGBSA
→ 汇总成表」这条链做成可复用、跨机器、环境隔离的脚本集。AutoMD/AutoTRJ 已随包内置
(`scripts/AutoMD/`,GPLv3,上游 https://github.com/Wang-Lin-boop/AutoMD)。

## 先确认环境

```bash
SKILL=~/.claude/skills/md-pipeline
$SKILL/../../toolenv/toolenv check schrodinger automd conda conda:md
```

缺什么会直接说缺什么、怎么装。路径不对就写 `~/.config/toolenv/overrides.sh`
(例:`export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1`),不要改仓库里的文件。

## 有哪些脚本

不要凭记忆列举 —— 现场扫:

```bash
toolenv index $SKILL/scripts
```

输出是一张表,含每个脚本的说明、依赖、用法。脚本都在 `scripts/`,直接执行即可
(它们自己 source `activate.sh` 完成环境激活)。

## 典型流程

```bash
cd <工作目录>                     # 放 .mae 输入 + md_pending_serial.list
cp $SKILL/scripts/md_pending_serial.list.template md_pending_serial.list
$SKILL/scripts/run_serial_md.sh --dry-run          # 先干跑看命令
nohup $SKILL/scripts/run_serial_md.sh > run.log 2>&1 &
$SKILL/scripts/run_plip.sh    <md-dir>...          # 相互作用占据率
$SKILL/scripts/run_mmgbsa.sh  <md-dir>...          # 结合自由能
$SCHRODINGER/run $SKILL/scripts/summarize_analysis.py <md-dir>... --out-csv summary.csv
```

`run_serial_md.sh` 会把进度写进工作目录的 `md_completed_serial.list` /
`md_failed_serial.list`,已完成或已有 `*-md` 目录的体系自动跳过,可安全重启。

## 改参数或出问题之前

**必读** `references/troubleshooting.md` —— 7 条实测踩坑,包括:配体 ASL 必须用
`res.ptype UNK` 而不是 `ligand`;MMGBSA 的并行取决于 `-HOST localhost:N` 而非 `-NJOBS`;
PLIP 对「肽=单个 UNK 残基」体系必须走配体模式。这些都是花了时间才定位到的,别重踩。

更早的完整说明留在 `references/original-readme.md`。

## 加新脚本

丢进 `scripts/`,文件头写四行元信息即可,不用改本文件:

```bash
# @name: run_gmx
# @description: 一句话说明
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <dir>...
source "$(dirname "$0")/../../toolenv/activate.sh"
```

新工具则在 `toolenv/tools.d/` 加一个 manifest,契约见那个目录里现有的文件。
```

- [ ] **Step 10: 验证 skill 安装与端到端**

Run: `cd /data1/home/huangshengjie/workstations/skills && ./install.sh && ls -l ~/.claude/skills/`
Expected: `md-pipeline -> /data1/home/huangshengjie/workstations/skills/md-pipeline`,随后打印工具表

Run: `cd /data1/home/huangshengjie/workstations/skills && ./toolenv/tests/run_tests.sh && bash md-pipeline/tests/test_env_shim.sh`
Expected: 全部通过,包括 Task 8 里那两个 install 测试

Run: `cd /tmp && bash -c 'source ~/.claude/skills/md-pipeline/scripts/env.sh && md_env_check && echo READY'`
Expected: `READY`(经 symlink 访问也能正确定位 toolenv 与内置 AutoMD)

- [ ] **Step 11: 提交**

```bash
cd /data1/home/huangshengjie/workstations/skills
git add -A
git commit -m "feat(md-pipeline): 迁移为 skill,env.sh 改为 toolenv 薄壳,脚本加元信息头"
```

---

## 完成后的验收

跑一遍,四条都要绿:

```bash
cd /data1/home/huangshengjie/workstations/skills
./toolenv/tests/run_tests.sh          # 全部单元测试
bash md-pipeline/tests/test_env_shim.sh
./toolenv/toolenv selftest            # 干净环境自检
./toolenv/toolenv index md-pipeline/scripts   # 脚本清单
```

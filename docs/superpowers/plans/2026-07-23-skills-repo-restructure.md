# skills 仓库分层重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把扁平单域仓库重构为 `skills/<域>/<skill>/` 两级布局 + Claude Code plugin marketplace,并用一个「向上查找」的定位器替掉所有硬编码的 toolenv 相对路径,使目录移动与三种安装形态都能自动发现 toolenv。

**Architecture:** 新增 `toolenv/find-toolenv.sh` 提供 `te_find_toolenv` 函数(优先级:`$TOOLENV_BIN` → `$CLAUDE_PLUGIN_ROOT` → 从调用者向上逐级查找 → `PATH`)。`env.sh` 与 SKILL.md 里的脚本模板用一段固定 bootstrap 定位并 source 它。`git mv` 把 md-pipeline 移进 `skills/science/`,`.claude-plugin/marketplace.json` 用 `source:"./"` + 目录级 `skills` 声明把仓库切成 `science`/`office` 两个 plugin。`install.sh` 改扫两级目录并加 plugin 冲突检测。

**Tech Stack:** 纯 Bash(bash 4.4.20,无 jq/python/bats);零依赖测试骨架(`toolenv/tests/helpers.sh`);Claude Code plugin marketplace schema。

## Global Constraints

- 纯 Bash,兼容 bash 4.4;被 source 的文件不设 `set -e`(见 `probe.sh` 头注)。
- 路径规范化一律 `readlink -f`。
- toolenv 覆盖变量可用:`TOOLENV_HOME` / `TOOLENV_TOOLS_DIR` / `TOOLENV_CACHE_DIR` / `TOOLENV_CONFIG_DIR`;本计划新增 `TOOLENV_BIN`。
- 验收标准 = 干净环境(`env -i`)可复现,不依赖交互式 `.bashrc`。
- marketplace 名固定 `lazy-skills`;两个 plugin:`science`、`office`;每个 plugin 条目必须 `"source": "./"` 且 `"strict": false`(声明了组件却不设 str:false 会报 conflicting manifests)。
- 域划分:计算化学与 AIDD 合并进 `science`;`office` 先占位。
- 现有测试基线:toolenv 67 项 + env_shim 5 项,重构后必须全绿。
- 保留 `test_paths_come_from_toolenv_not_hardcoded` 这条判别性用例(证明路径来自 toolenv 而非写死)。
- 提交信息结尾附:`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## File Structure

- `toolenv/find-toolenv.sh` — **新增**。sourceable,定义 `te_find_toolenv START_DIR`,echo toolenv 可执行文件路径。唯一职责:定位 toolenv 自身。
- `toolenv/tests/test_find_toolenv.sh` — **新增**。4 条用例。
- `toolenv/tests/run_tests.sh` — **改**。把新测试文件纳入。
- `skills/science/md-pipeline/**` — **移动**(`git mv md-pipeline`)。
- `skills/science/md-pipeline/scripts/env.sh` — **改**。用 bootstrap + `te_find_toolenv` 取代硬编码 `../../toolenv`。
- `skills/science/md-pipeline/tests/test_env_shim.sh` — **改**。`REPO` 上溯层级修正;新增/保留判别用例。
- `skills/science/md-pipeline/SKILL.md` — **改**。「先确认环境」与「加新脚本」两处路径改用 bootstrap。
- `.claude-plugin/marketplace.json` — **新增**。
- `skills/office/.gitkeep` — **新增**(占位)。
- `install.sh` — **改**。扫 `skills/*/*/`,加 plugin 冲突检测。
- `plip_proa_prob_analysis.py`(仓库根)— **删**。
- `README.md` — **改**。两种安装方式 + 三个扩展接口。

各任务实现顺序有依赖:Task 1(定位器)→ Task 2(移动 + env.sh 接线)→ 其余。

---

## Task 1: toolenv 定位器 find-toolenv.sh

**Files:**
- Create: `toolenv/find-toolenv.sh`
- Create: `toolenv/tests/test_find_toolenv.sh`
- Modify: `toolenv/tests/run_tests.sh`

**Interfaces:**
- Produces: `te_find_toolenv START_DIR` — 成功时把 toolenv 可执行文件的绝对路径写到 stdout 并返回 0;失败时向 stderr 打印中文说明并返回 1。优先级:`$TOOLENV_BIN`(若可执行)→ `$CLAUDE_PLUGIN_ROOT/toolenv/toolenv`(若可执行)→ 从 `START_DIR` 起逐级向上(≤6 级)找 `<dir>/toolenv/toolenv` → `PATH` 上的 `toolenv`。

- [ ] **Step 1: 看现有测试骨架约定**

Run: `sed -n '36,66p' toolenv/tests/helpers.sh`
Expected: 看到 `_te_sandbox_setup` 会把 `HOME`/`TOOLENV_CACHE_DIR`/`TOOLENV_CONFIG_DIR` 重定向到 `$SANDBOX`,`run_all` 自动跑所有 `test_` 函数。测试里用 `$SANDBOX` 造隔离目录。

- [ ] **Step 2: 写失败测试 `toolenv/tests/test_find_toolenv.sh`**

```bash
#!/usr/bin/env bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
TOOLENV_HOME=${TOOLENV_HOME:-$(dirname "$TESTS_DIR")}
. "$TESTS_DIR/helpers.sh"
. "$TOOLENV_HOME/find-toolenv.sh"

# 造一个假的 toolenv 树:<root>/toolenv/toolenv(可执行)
_make_fake_toolenv() {
    local root=$1
    mkdir -p "$root/toolenv"
    printf '#!/bin/sh\n' > "$root/toolenv/toolenv"
    chmod +x "$root/toolenv/toolenv"
}

test_env_bin_wins_first() {
    _make_fake_toolenv "$SANDBOX/repo"
    mkdir -p "$SANDBOX/elsewhere"
    printf '#!/bin/sh\n' > "$SANDBOX/elsewhere/toolenv"
    chmod +x "$SANDBOX/elsewhere/toolenv"
    local out
    out=$(TOOLENV_BIN="$SANDBOX/elsewhere/toolenv" \
          te_find_toolenv "$SANDBOX/repo/skills/x/y/scripts")
    assert_eq "$out" "$SANDBOX/elsewhere/toolenv"
}

test_plugin_root_hits() {
    _make_fake_toolenv "$SANDBOX/plug"
    local out
    out=$(env -u TOOLENV_BIN CLAUDE_PLUGIN_ROOT="$SANDBOX/plug" \
          bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv /nonexistent')
    assert_eq "$out" "$SANDBOX/plug/toolenv/toolenv"
}

test_walks_up_from_caller() {
    _make_fake_toolenv "$SANDBOX/repo"
    mkdir -p "$SANDBOX/repo/skills/science/md-pipeline/scripts"
    local out
    out=$(env -u TOOLENV_BIN -u CLAUDE_PLUGIN_ROOT \
          bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv "'"$SANDBOX"'/repo/skills/science/md-pipeline/scripts"')
    assert_eq "$out" "$SANDBOX/repo/toolenv/toolenv"
}

test_fails_when_nothing_found() {
    assert_fail env -u TOOLENV_BIN -u CLAUDE_PLUGIN_ROOT PATH=/usr/bin:/bin \
        bash -c '. "'"$TOOLENV_HOME"'/find-toolenv.sh"; te_find_toolenv "'"$SANDBOX"'/empty/a/b" 2>/dev/null'
}

run_all
```

- [ ] **Step 3: 跑测试确认失败**

Run: `bash toolenv/tests/test_find_toolenv.sh`
Expected: FAIL —— `find-toolenv.sh: No such file` 或 `te_find_toolenv: command not found`。

- [ ] **Step 4: 写 `toolenv/find-toolenv.sh`**

```bash
# find-toolenv.sh —— 定位 toolenv 可执行文件本身。被脚本 source。
# 不设 set -e/-u(被各种环境 source)。
#
# te_find_toolenv START_DIR
#   优先级(首个命中即用):
#     1. $TOOLENV_BIN            显式覆盖,指向 toolenv 可执行文件
#     2. $CLAUDE_PLUGIN_ROOT/toolenv/toolenv   plugin 安装形态
#     3. 从 START_DIR 逐级向上(≤6 级)找 <dir>/toolenv/toolenv
#     4. PATH 上的 toolenv
#   成功:路径写 stdout,返回 0;失败:中文说明写 stderr,返回 1。
te_find_toolenv() {
    local start=${1:-} d i=0
    if [ -n "${TOOLENV_BIN:-}" ] && [ -x "$TOOLENV_BIN" ]; then
        printf '%s\n' "$TOOLENV_BIN"; return 0
    fi
    if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -x "$CLAUDE_PLUGIN_ROOT/toolenv/toolenv" ]; then
        printf '%s\n' "$CLAUDE_PLUGIN_ROOT/toolenv/toolenv"; return 0
    fi
    d=$start
    while [ -n "$d" ] && [ "$i" -lt 6 ]; do
        if [ -x "$d/toolenv/toolenv" ]; then
            printf '%s\n' "$(readlink -f "$d/toolenv/toolenv")"; return 0
        fi
        [ "$d" = "/" ] && break
        d=$(dirname "$d")
        i=$((i + 1))
    done
    if command -v toolenv >/dev/null 2>&1; then
        printf '%s\n' "$(command -v toolenv)"; return 0
    fi
    echo "toolenv: 找不到 toolenv 可执行文件。设 TOOLENV_BIN 指向它,或确认仓库完整。" >&2
    return 1
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `bash toolenv/tests/test_find_toolenv.sh`
Expected: `4 tests, 0 failures`。

- [ ] **Step 6: 把新测试纳入 run_tests.sh**

先看当前内容:`cat toolenv/tests/run_tests.sh`。它逐个跑 `test_*.sh`。若是显式列表,追加 `test_find_toolenv.sh`;若是 `for f in "$TESTS_DIR"/test_*.sh` 通配,则无需改(新文件自动纳入)。确认方式:

Run: `bash toolenv/tests/run_tests.sh 2>&1 | tail -5`
Expected: 汇总里包含 find_toolenv 的 4 项,总 failures = 0。

- [ ] **Step 7: 提交**

```bash
git add toolenv/find-toolenv.sh toolenv/tests/test_find_toolenv.sh toolenv/tests/run_tests.sh
git commit -m "feat(toolenv): find-toolenv.sh —— 向上查找定位 toolenv 自身(替代硬编码相对路径)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 移动 md-pipeline 到 skills/science/ 并给 env.sh 接线

**Files:**
- Move: `md-pipeline/` → `skills/science/md-pipeline/`(`git mv`)
- Modify: `skills/science/md-pipeline/scripts/env.sh:9-10`
- Modify: `skills/science/md-pipeline/tests/test_env_shim.sh:1-9`

**Interfaces:**
- Consumes: `te_find_toolenv`(Task 1)。
- Produces: env.sh 仍导出 `SCHRODINGER`/`Desmond`/`AUTOMD_DIR`,仍定义 `md_env_check`;`_TOOLENV` 现由定位器给出。

- [ ] **Step 1: 建目录并移动**

```bash
mkdir -p skills/science
git mv md-pipeline skills/science/md-pipeline
```

Run: `ls skills/science/md-pipeline/`
Expected: 看到 `SKILL.md scripts references tests`。

- [ ] **Step 2: 先跑一次 env_shim 测试,观察移动后的断裂(记录现状)**

Run: `bash skills/science/md-pipeline/tests/test_env_shim.sh 2>&1 | tail -8`
Expected: 报错或多条 FAIL —— 因为 `test_env_shim.sh` 里 `REPO=$(dirname "$SKILL_DIR")` 现在算出的是 `skills/science`,`. "$REPO/toolenv/tests/helpers.sh"` 找不到;且 env.sh 的 `../../toolenv` 现在指向 `skills/toolenv`(不存在)。这确认了两处都要改。

- [ ] **Step 3: 改 env.sh 的定位逻辑(替换第 9-10 行那段)**

把:
```bash
MD_PIPELINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_TOOLENV="$(cd "$MD_PIPELINE_DIR/../../toolenv" 2>/dev/null && pwd)/toolenv"
```
替换为:
```bash
MD_PIPELINE_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# 定位 toolenv:先找 find-toolenv.sh(bootstrap:CLAUDE_PLUGIN_ROOT 或向上找),
# 再用 te_find_toolenv 走完整优先级(含 TOOLENV_BIN 覆盖)。换机器/移动目录都不用改本文件。
_te_boot=${CLAUDE_PLUGIN_ROOT:-$MD_PIPELINE_DIR}
while [ "$_te_boot" != "/" ] && [ ! -f "$_te_boot/toolenv/find-toolenv.sh" ]; do
    _te_boot=$(dirname "$_te_boot")
done
if [ -f "$_te_boot/toolenv/find-toolenv.sh" ]; then
    . "$_te_boot/toolenv/find-toolenv.sh"
    _TOOLENV=$(te_find_toolenv "$MD_PIPELINE_DIR") || _TOOLENV=""
else
    _TOOLENV=""
fi
unset _te_boot
```

(下方 `if [ -x "$_TOOLENV" ]; then ... else echo "WARN[env.sh]: 找不到 toolenv: $_TOOLENV"` 一段保持不变,`$_TOOLENV` 为空时 `-x` 判假,自然走 WARN 分支。)

- [ ] **Step 4: 改 test_env_shim.sh 的 REPO 上溯(第 1-9 行)**

把:
```bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$(dirname "$SKILL_DIR")
```
替换为(向上找含 `toolenv/` 的仓库根,不写死层级):
```bash
TESTS_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
REPO=$SKILL_DIR
while [ "$REPO" != "/" ] && [ ! -d "$REPO/toolenv" ]; do REPO=$(dirname "$REPO"); done
```

- [ ] **Step 5: 跑 env_shim 测试确认全绿**

Run: `bash skills/science/md-pipeline/tests/test_env_shim.sh 2>&1 | tail -8`
Expected: `5 tests, 0 failures`(含 `test_paths_come_from_toolenv_not_hardcoded` 与 `test_automd_resolves_to_bundled_copy`)。

- [ ] **Step 6: 干净环境复验(不依赖 .bashrc)**

Run: `env -i HOME="$HOME" PATH=/usr/bin:/bin bash -c 'cd '"$PWD"' && . skills/science/md-pipeline/scripts/env.sh >/dev/null 2>&1; echo "S=$SCHRODINGER"'`
Expected: 输出 `S=/`(以 `/` 开头的真实路径),证明定位器在干净环境里也能找到 toolenv。

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "refactor: md-pipeline 移入 skills/science/,env.sh 改用 find-toolenv 定位

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 修 SKILL.md 里的两处硬编码路径

**Files:**
- Modify: `skills/science/md-pipeline/SKILL.md:14-18`(先确认环境)
- Modify: `skills/science/md-pipeline/SKILL.md:84-91`(加新脚本模板)

**Interfaces:**
- Consumes: `te_find_toolenv`(Task 1);`toolenv/activate.sh`(自定位,移动后无需改)。

- [ ] **Step 1: 改「先确认环境」代码块**

把:
```bash
SKILL=~/.claude/skills/md-pipeline
TOOLENV=$(readlink -f "$SKILL")/../toolenv/toolenv
$TOOLENV check schrodinger automd conda conda:md
```
替换为:
```bash
SKILL=~/.claude/skills/md-pipeline
REPO=$(readlink -f "$SKILL"); while [ "$REPO" != / ] && [ ! -x "$REPO/toolenv/toolenv" ]; do REPO=$(dirname "$REPO"); done
TOOLENV="$REPO/toolenv/toolenv"
"$TOOLENV" check schrodinger automd conda conda:md
```
(`$SKILL` 经 `install.sh` 是指向 `skills/science/md-pipeline` 的 symlink;`readlink -f` 解析进真实仓库树后向上找 `toolenv/toolenv`,dev 与 plugin 两态都成立。后续 `$TOOLENV index $SKILL/scripts` 等引用同一 `$TOOLENV` 变量,无需再改。)

- [ ] **Step 2: 改「加新脚本」模板块**

把:
```bash
source "$(dirname "$0")/../../toolenv/activate.sh"
```
替换为:
```bash
# 定位并激活依赖(向上找 toolenv,兼容 install.sh symlink 与 /plugin 安装)
_here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd); _r=${CLAUDE_PLUGIN_ROOT:-$_here}
while [ "$_r" != / ] && [ ! -f "$_r/toolenv/activate.sh" ]; do _r=$(dirname "$_r"); done
source "$_r/toolenv/activate.sh"
```

- [ ] **Step 3: 校验改后 SKILL.md 无残留旧路径**

Run: `grep -nE '\.\./toolenv|\.\./\.\./toolenv' skills/science/md-pipeline/SKILL.md`
Expected: 无输出(旧的 `../toolenv` / `../../toolenv` 已清除)。

- [ ] **Step 4: 冒烟验证「先确认环境」片段真能跑通**

先临时挂载:`./install.sh >/dev/null 2>&1 || true`,再:
```bash
SKILL=~/.claude/skills/md-pipeline
REPO=$(readlink -f "$SKILL"); while [ "$REPO" != / ] && [ ! -x "$REPO/toolenv/toolenv" ]; do REPO=$(dirname "$REPO"); done
"$REPO/toolenv/toolenv" list | head -3
```
Expected: 打印 toolenv 工具表表头与前几行(不报「找不到」)。

- [ ] **Step 5: 提交**

```bash
git add skills/science/md-pipeline/SKILL.md
git commit -m "docs(md-pipeline): SKILL.md 两处路径改用向上查找定位 toolenv

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: marketplace.json + office 占位

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Create: `skills/office/.gitkeep`

**Interfaces:**
- Produces: 一个可被 `/plugin marketplace add` 识别的 catalog,含 `science`/`office` 两 plugin。

- [ ] **Step 1: 建 office 占位目录**

```bash
mkdir -p skills/office
printf '# office 域占位:放办公类 skill(文档转换、报表、周报等)。\n' > skills/office/.gitkeep
```

- [ ] **Step 2: 写 `.claude-plugin/marketplace.json`**

```bash
mkdir -p .claude-plugin
```
文件内容:
```json
{
  "name": "lazy-skills",
  "owner": { "name": "huangshengjie" },
  "metadata": { "description": "计算化学 / AIDD / 办公 自用 skill 集" },
  "plugins": [
    {
      "name": "science",
      "source": "./",
      "strict": false,
      "description": "计算化学与 AIDD:Desmond/薛定谔 MD 模拟、轨迹分析、结合自由能等",
      "skills": ["./skills/science"]
    },
    {
      "name": "office",
      "source": "./",
      "strict": false,
      "description": "日常办公:文档转换、报表、周报等(占位,待填)",
      "skills": ["./skills/office"]
    }
  ]
}
```

- [ ] **Step 3: JSON 合法性校验**

Run: `python3 -c 'import json,sys; json.load(open(".claude-plugin/marketplace.json")); print("json ok")' 2>/dev/null || sed -n '1,30p' .claude-plugin/marketplace.json`
Expected: `json ok`(若无 python3,退而人工核对 sed 输出的括号/逗号成对)。

- [ ] **Step 4: 若有 claude CLI,做 plugin 校验(可选)**

Run: `command -v claude >/dev/null && claude plugin validate . --strict 2>&1 | tail -20 || echo "跳过:无 claude CLI"`
Expected: 校验通过,或明确「跳过」。若报 `conflicting manifests`,确认每个 plugin 条目都有 `"strict": false`。

- [ ] **Step 5: 提交**

```bash
git add .claude-plugin/marketplace.json skills/office/.gitkeep
git commit -m "feat: plugin marketplace(lazy-skills)—— science/office 两域

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: install.sh 改扫两级 + plugin 冲突检测,删游离旧脚本

**Files:**
- Modify: `install.sh:11-30`(挂载循环)
- Delete: `plip_proa_prob_analysis.py`

**Interfaces:**
- Consumes: `skills/*/*/SKILL.md` 布局(Task 2)。
- Produces: 幂等地把每个含 SKILL.md 的 skill 目录 symlink 到 `~/.claude/skills/<name>`;检测到同名 skill 已由 plugin 装上则告警。

- [ ] **Step 1: 删游离旧脚本(已验证是 md-pipeline 版的被取代旧版,无独有能力)**

```bash
git rm plip_proa_prob_analysis.py
```

- [ ] **Step 2: 改 install.sh 的挂载循环**

把原来的:
```bash
echo "== 挂载 skill 到 $SKILLS_DIR"
found=0
for d in "$REPO"/*/; do
    d=${d%/}
    [ -f "$d/SKILL.md" ] || continue
    ...
done
```
其中 `for d in "$REPO"/*/;` 一行改为扫两级:
```bash
for d in "$REPO"/skills/*/*/; do
```
并在 `[ -f "$d/SKILL.md" ] || continue` 之后、`name=$(basename "$d")` 之前插入 plugin 冲突检测:
```bash
    # 若同名 skill 已由 /plugin 装到 plugins cache,提示二选一,避免两份并存
    if compgen -G "$HOME/.claude/plugins/*/skills/$(basename "$d")" >/dev/null 2>&1; then
        echo "  ! $(basename "$d"):检测到已由某 plugin 安装,symlink 与 plugin 二选一(见 README)。" >&2
    fi
```

- [ ] **Step 3: 干净环境跑 install.sh,确认扫到 md-pipeline 且探测正常**

Run: `env -i HOME="$HOME" PATH=/usr/bin:/bin CLAUDE_SKILLS_DIR="$(mktemp -d)/skills" bash install.sh 2>&1 | tail -20`
Expected: 看到 `+ md-pipeline`(或 `= md-pipeline`),随后 `== 探测工具` 打印工具表;无「找不到 toolenv」。

- [ ] **Step 4: 确认游离脚本已删、根目录干净**

Run: `ls *.py 2>/dev/null; git status --short`
Expected: 根目录无 `.py` 文件;`git status` 显示 `plip_proa_prob_analysis.py` 已 staged 删除、`install.sh` 已改。

- [ ] **Step 5: 提交**

```bash
git add install.sh
git commit -m "refactor(install): 扫 skills/*/*/,加 plugin 冲突检测;删被取代的游离旧脚本

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 重写 README

**Files:**
- Modify: `README.md`(整体重写)

**Interfaces:**
- Consumes: 前述全部布局与安装形态。

- [ ] **Step 1: 看现有 README 保留哪些仍准确的段落**

Run: `sed -n '1,40p' README.md`
Expected: 记下仍准确的「加脚本 / 加工具 / 加 skill」模板措辞,重写时复用。

- [ ] **Step 2: 重写 README,至少覆盖以下小节**

- **两种安装方式**
  - 开发态(本机、改文件即时生效):`git clone … && ./install.sh`
  - 分发态(别人的机器):`/plugin marketplace add Hhuangsj/skills` → `/plugin install science@lazy-skills`
  - 二者不要同时装同一个 skill(install.sh 会检测并告警)。
- **目录布局**:`skills/<域>/<skill>/` + `toolenv/` + `.claude-plugin/marketplace.json`。
- **三个扩展接口**:
  - 加脚本:丢进某 skill 的 `scripts/`,写四行 `@name/@description/@requires/@usage` 头;`toolenv index` 自动收录。
  - 加 skill:在 `skills/science/` 或 `skills/office/` 下建目录放 `SKILL.md`;**不用改 marketplace.json**(目录级声明自动发现)。
  - 加工具:`toolenv/tools.d/` 加一个 manifest;tools.d 不分域。
  - 加域:`.claude-plugin/marketplace.json` 的 `plugins` 数组追加一个对象。
- **换机器路径不对**:写 `~/.config/toolenv/overrides.sh`(例 `export TOOLENV_SCHRODINGER=/opt/...`),或设 `TOOLENV_BIN` 指定 toolenv 本体。
- **测试**:`bash toolenv/tests/run_tests.sh` 与 `bash skills/science/md-pipeline/tests/test_env_shim.sh`。

- [ ] **Step 3: 校验 README 无残留旧路径**

Run: `grep -nE 'md_pipeline|/md-pipeline/|\.\./toolenv|67 项' README.md`
Expected: 无输出,或仅在明确正确的新语境中出现(逐条核对,`skills/science/md-pipeline` 是对的;裸 `md-pipeline/` 顶层路径是错的)。测试项数改为按实际(见 Task 7 汇总)。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: README 重写 —— 两种安装方式 + 三个扩展接口 + 新布局

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: 全量回归 + 收尾

**Files:**
- 无新增;仅运行验证。

- [ ] **Step 1: toolenv 全套测试**

Run: `bash toolenv/tests/run_tests.sh 2>&1 | tail -6`
Expected: 总 failures = 0(原 67 项 + find_toolenv 4 项 = 71 项)。

- [ ] **Step 2: env_shim 测试**

Run: `bash skills/science/md-pipeline/tests/test_env_shim.sh 2>&1 | tail -3`
Expected: `5 tests, 0 failures`。

- [ ] **Step 3: toolenv 干净环境自检**

Run: `toolenv/toolenv selftest 2>&1 | tail -8`
Expected: 末行 `selftest OK`;schrodinger/automd/conda 等在本机为 `found`(工具 missing 不算失败)。

- [ ] **Step 4: 确认实际测试项数,回填 README(如 Step 1 计数与 README 不符)**

若 README 里写的项数与 Step 1/2 实测不符,改成实测值并 `git commit --amend` 或追加一次 docs 提交。

- [ ] **Step 5: 全绿总结**

Run: `git log --oneline -7 && git status --short`
Expected: 7 个任务的提交在列,工作树干净。向用户报告:测试项数、本机 selftest 结果、以及「本机 `~/.claude/skills/md-pipeline` 旧 symlink 已因移动失效,已由 install.sh 重新指向」。

---

## Self-Review

**Spec coverage:**
- 目录两级布局 → Task 2/4 ✓
- marketplace.json(science/office,source ./,strict false)→ Task 4 ✓
- toolenv 定位器四级优先级 → Task 1 ✓
- 替换所有硬编码 `../../toolenv` / `../toolenv`:env.sh(Task 2)、SKILL.md ×2(Task 3)、install.sh(Task 5)✓
- 删游离旧脚本 → Task 5 ✓
- install.sh 扫两级 + plugin 冲突检测 → Task 5 ✓
- README 两安装方式 + 三接口 → Task 6 ✓
- 现有 67+5 测试全绿 + 新增 find-toolenv 4 条 + selftest → Task 7 ✓
- `test_paths_come_from_toolenv_not_hardcoded` 保留 → Task 2 Step 5 ✓
- office 占位 → Task 4 ✓
- 非目标(不写 office skill、不动业务逻辑、tools.d 不分域)→ 计划未触碰,符合 ✓

**Placeholder scan:** 无 TBD/TODO;每个 code step 均含完整命令或代码。

**Type consistency:** `te_find_toolenv` 的签名(`START_DIR` 入参、stdout 出路径、非零失败)在 Task 1 定义,Task 2 env.sh 按此调用一致;bootstrap 定位 `find-toolenv.sh` / `activate.sh` 的向上查找模式在 env.sh、SKILL.md、install.sh 中写法统一。

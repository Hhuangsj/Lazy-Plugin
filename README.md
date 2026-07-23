# 计算化学 Skill 仓库

Claude Code skill 的集合,底下垫一个工具发现层 `toolenv`。目标是:同一套脚本
放到任意机器上,自己找到 Schrödinger / AutoMD / conda / AmberTools / RDKit,
不用改仓库里任何文件。

## 装

```bash
git clone <repo> && cd skills && ./install.sh
```

`install.sh` 做两件事:把含 `SKILL.md` 的目录 symlink 到 `~/.claude/skills/`,
然后探测一遍工具并打印结果表。

路径不对就写 `~/.config/toolenv/overrides.sh`,别改仓库:

```bash
export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1
export TOOLENV_AMBERTOOLS=/opt/amber22
```

## 目录

| 路径 | 是什么 |
|------|--------|
| `toolenv/` | 工具发现与激活层(纯 bash,零依赖)。不是 skill,是被 skill 调用的包。 |
| `toolenv/tools.d/` | 每个工具一个 manifest:声明怎么找、怎么激活。 |
| `md-pipeline/` | skill:Desmond MD 运行 + 轨迹分析全流程。 |
| `docs/superpowers/` | 设计文档与实现计划。 |

## toolenv 常用命令

```bash
toolenv list                 # 工具 / 状态 / 来源 / 路径
toolenv which schrodinger    # 打印根路径
toolenv check plip conda:md  # 缺什么报什么 + 怎么装
toolenv index <dir>          # 把目录里的脚本列成表(读脚本头元信息)
toolenv probe --force        # 装了新东西后重新探测
toolenv selftest             # 干净环境自检(不依赖交互式 .bashrc)
```

解析优先级,首个命中即停:`overrides.sh` → 已有的同名环境变量(`SCHRODINGER`、
`AMBERHOME`…)→ `PATH` → 扫所有 conda 环境 → 常见目录 glob。结果按 hostname
缓存在 `~/.cache/toolenv/`。

## 加东西

**加脚本**:丢进某个 skill 的 `scripts/`,文件头写四行,别处零登记:

```bash
#!/usr/bin/env bash
# @name: run_gmx
# @description: 一句话
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <dir>...
source "$(dirname "$0")/../../toolenv/activate.sh"
```

**加工具**:`toolenv/tools.d/` 丢一个 manifest,四项:

```bash
TOOL_NAME="gromacs"
TOOL_DESC="GROMACS:gmx"
TOOL_HINT="装法或下载地址"
tool_detect() {                 # 用 try_* 原语,按优先级一行一个,命中即停
    try_env GMXDIR
    try_cmd gmx --up 2
    try_conda_env_bin gmx
    try_glob --require bin/gmx "$HOME/software/gromacs*"
}
tool_activate() {               # $1=根路径 $2=conda 环境名(如果是 conda 里找到的)
    local root=$1
    echo "export PATH=$root/bin:\$PATH"
}
```

原语:`try_env` / `try_cmd [--up N]` / `try_conda_env_bin` /
`try_conda_env_python` / `try_glob [--require RELPATH]`。
`--require` 用标记文件校验候选目录,挡掉安装包解压目录之类的赝品。

**加 skill**:新建目录 + `SKILL.md`,重跑 `./install.sh`。

## 测试

```bash
./toolenv/tests/run_tests.sh              # 67 项,零依赖 bash 测试
bash md-pipeline/tests/test_env_shim.sh   # env.sh 向后兼容
./toolenv/toolenv selftest                # 干净环境自检
```

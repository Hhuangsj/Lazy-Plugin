# 计算化学 / 办公 Skill 仓库(Lazy-Plugin)

Claude Code skill 的集合,按「域」分类:`skills/science/`(计算化学 / AIDD)、
`skills/office/`(日常办公,占位待填)。底下垫一个工具发现层 `toolenv`。目标是:
同一套脚本放到任意机器上,自己找到 Schrödinger / AutoMD / conda / AmberTools /
RDKit,不用改仓库里任何文件。

marketplace 名字是 `lazy-plugin`(见 `.claude-plugin/marketplace.json`),下面
挂两个 plugin:`science`、`office`,一个域一个 plugin。

## 装

有两种装法,**同一个 skill 不要两种都装**(会造成 `~/.claude/skills/` 的
symlink 和 plugin 缓存并存;`install.sh` 检测到会告警,让你二选一)。

### 开发态(本机开发这个仓库,改文件立即生效)

```bash
git clone git@github.com:Hhuangsj/Lazy-Plugin.git
cd Lazy-Plugin
./install.sh
```

`install.sh` 做两件事:把 `skills/<域>/<skill>/` 下每个含 `SKILL.md` 的目录
symlink 到 `~/.claude/skills/`,然后探测一遍工具并打印结果表。改仓库里的脚本
或 `SKILL.md`,因为是 symlink,不用重装就生效。

### 分发态(别人的机器,只想用,不改仓库)

```
/plugin marketplace add Hhuangsj/Lazy-Plugin
/plugin install science@lazy-plugin
```

办公域同理:`/plugin install office@lazy-plugin`。这种装法走 Claude Code 的
plugin 缓存,不感知本地仓库改动;要更新就重新 `/plugin install`。

路径不对就写 `~/.config/toolenv/overrides.sh`,别改仓库:

```bash
export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1
export TOOLENV_AMBERTOOLS=/opt/amber22
```

或者 toolenv 本体自己不在预期位置,设 `TOOLENV_BIN` 指过去。

## 目录布局

```
skills/<域>/<skill>/     # 一个 skill:SKILL.md + scripts/ + references/ + tests/
skills/science/md-pipeline/   # Desmond MD 运行 + 轨迹分析全流程
skills/office/weekly-work-report/ # 基于显式白名单生成隐私保护的 YAML 周报
toolenv/                       # 工具发现与激活层,纯 bash,零依赖,不是 skill
toolenv/tools.d/                # 每个工具一个 manifest,不分域(pandoc 和 schrodinger 平级)
.claude-plugin/marketplace.json # marketplace 声明:name + 每个域一个 plugin
docs/superpowers/               # 设计文档与实现计划
install.sh                      # 开发态安装脚本
```

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

## 四个扩展接口

这四件事互不干扰,加东西不用改中心注册表(marketplace.json 除外的「加域」)。

**加脚本**:丢进某个 skill 的 `scripts/`,文件头写四行,别处零登记:

```bash
#!/usr/bin/env bash
# @name: run_gmx
# @description: 一句话
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <dir>...
# 定位并激活依赖(向上找 toolenv,兼容 install.sh symlink 与 /plugin 安装)
_here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd); _r=${CLAUDE_PLUGIN_ROOT:-$_here}
while [ "$_r" != / ] && [ ! -f "$_r/toolenv/activate.sh" ]; do _r=$(dirname "$_r"); done
source "$_r/toolenv/activate.sh"
```

`toolenv index <dir>` 会读脚本头元信息自动收录成表,不用另外登记。

**加 skill**:在 `skills/science/` 或 `skills/office/` 下新建目录,放
`SKILL.md`,重跑 `./install.sh`(开发态)即可。**不用改 marketplace.json** ——
每个 plugin 在 `skills` 字段里声明的是域级目录(如 `./skills/science`),
Claude Code 会自动发现该目录下所有含 `SKILL.md` 的子目录。

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
`toolenv/tools.d/` 不分域,所有工具(schrodinger、conda、ambertools、
automd、plip、rdkit、以后加的 pandoc…)平级堆放。

**加域**:`.claude-plugin/marketplace.json` 的 `plugins` 数组里追加一个对象,
再在 `skills/` 下建同名目录:

```json
{
  "name": "<域名>",
  "source": "./",
  "strict": false,
  "description": "一句话",
  "skills": ["./skills/<域名>"]
}
```

## 换机器路径不对

写 `~/.config/toolenv/overrides.sh`,或设 `TOOLENV_BIN` 指定 toolenv 本体,
不要改仓库里的任何文件:

```bash
export TOOLENV_SCHRODINGER=/opt/schrodinger/2024-1
export TOOLENV_BIN=/some/other/place/toolenv
```

## 测试

```bash
bash toolenv/tests/run_tests.sh                            # 71 项,9 个文件,零依赖 bash 测试
bash skills/science/md-pipeline/tests/test_env_shim.sh      # 5 项,env.sh 向后兼容
./toolenv/toolenv selftest                                  # 干净环境自检
```

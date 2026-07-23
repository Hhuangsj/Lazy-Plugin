# 计算化学 Skill 仓库设计:共享环境层 `toolenv` + md-pipeline skill

日期:2026-07-22

## 背景与目标

`workstations/skills/md_pipeline` 已经沉淀了一套可用的 MD 流程(Desmond MD → AutoTRJ
聚类 → PLIP 相互作用 → MMGBSA → 汇总表),环境依赖集中在 `env.sh`。现在要:

1. 把它做成 Claude Code skill;
2. 让同一套机制覆盖其它软件(AmberTools、RDKit,后续 GROMACS/ORCA 等);
3. 加新脚本有稳定接口,不需要改动 skill 文档;
4. 换机器能复用 —— 自己识别工具位置再调用。

## 架构

共享环境层 + 多个小 skill。发现层 `toolenv` 独立于 skill,被各 skill 调用。

```
workstations/skills/            git repo
├── install.sh                  symlink 各 skill 到 ~/.claude/skills/ + 首次探测
├── toolenv/                    共享环境层(被 skill 调用的包,本身不是 skill)
│   ├── toolenv                 CLI
│   ├── activate.sh             脚本一行 source 的入口
│   ├── lib/probe.sh            探测原语
│   └── tools.d/                一个工具一个 manifest
│       ├── conda.sh  schrodinger.sh  automd.sh  plip.sh
│       └── ambertools.sh  rdkit.sh
└── md-pipeline/                skill #1
    ├── SKILL.md
    ├── scripts/                现有 run_*.sh / *.py / AutoMD/
    └── references/troubleshooting.md
```

发现层用纯 Bash 实现。理由:核心职责是在任意 shell / nohup / cron 下把环境变量设对,
这是 shell 的职责;且探测 conda 环境的工具自身不能先依赖某个 python 解释器。

## `toolenv` CLI 契约

| 命令 | 行为 |
|------|------|
| `toolenv probe [--force]` | 探测全部工具,结果写入 `~/.cache/toolenv/<hostname>.env` |
| `toolenv list` | 表格:工具 / 状态 / 解析路径 / 来源 |
| `toolenv which <tool>` | 打印根路径;未找到则非零退出 |
| `toolenv check <spec>...` | 缺失项报名字 + 安装提示,非零退出 |
| `toolenv env <tool>...` | 打印 `export` 行,供 `eval` / `source` |
| `toolenv run <script> [args]` | 读脚本头 `@requires` → check → 激活 → exec |
| `toolenv index <dir>` | 扫描目录内脚本头,输出 markdown 清单 |
| `toolenv selftest` | 在 `env -i` 干净环境跑 probe + check |

工具规格串:`<tool>` 或 `conda:<envname>`(要求某个 conda 环境存在)。

### 解析优先级

高到低,首个命中即停,来源记入缓存供 `list` 显示:

1. `~/.config/toolenv/overrides.sh` 中的 `TOOLENV_<TOOL>` 显式设置
2. 已存在的同名环境变量(`SCHRODINGER`、`AMBERHOME` 等),兼容现有用法
3. `PATH` 上的特征可执行文件
4. 扫描所有 conda 环境的 `bin/`,或 python import 探测
5. 常见目录 glob(`~/software/*`、`/opt/*`)

缓存按 hostname 分文件,`--force` 重新探测。缓存缺失时相关命令自动触发一次探测。
所有失败路径都必须报告"缺什么 + 怎么装",不静默降级。

## 加新工具的接口

`toolenv/tools.d/` 放一个 bash 文件,声明四项:

```bash
TOOL_NAME="ambertools"
TOOL_DESC="AmberTools: antechamber / tleap / parmchk2"
TOOL_HINT="conda create -n amber -c conda-forge ambertools"
tool_detect() {                      # 打印根路径,命中即返回 0
    try_env AMBERHOME
    try_cmd antechamber --up 2       # 从 bin/antechamber 上溯两级
    try_conda_env_bin antechamber
    try_glob "$HOME/software/amber*" "/opt/amber*"
}
tool_activate() {                    # $1 = 根路径,打印 export 行
    echo "export AMBERHOME=$1"
    echo "export PATH=\$AMBERHOME/bin:\$PATH"
}
```

纯 python 包(如 RDKit)用 `try_conda_env_python "import rdkit"` 探测,
`tool_activate` 导出激活对应 conda 环境所需的变量。

`lib/probe.sh` 提供的原语:`try_env`、`try_cmd`、`try_conda_env_bin`、
`try_conda_env_python`、`try_glob`。

## 加新脚本的接口

脚本头写元信息,别处零登记:

```bash
#!/usr/bin/env bash
# @name: run_gmx
# @description: GROMACS 平衡 + 成品 MD
# @requires: gromacs, conda:md
# @usage: run_gmx.sh <system-dir>...
source "$(dirname "$0")/../../toolenv/activate.sh"
```

`activate.sh` 解析调用方脚本头的 `@requires`,check 并激活,失败则非零退出。
丢进 `scripts/` 即生效。SKILL.md 不列举脚本,只指示运行 `toolenv index`,
因此加脚本不需要改 SKILL.md。

## 分发与换机器

仓库根 `install.sh`:

1. 把各 skill 目录 symlink 到 `~/.claude/skills/`(已存在则跳过并提示);
2. 跑一次 `toolenv probe`;
3. 打印 `toolenv list` 结果,缺失工具给安装提示。

新机器:`git clone && ./install.sh`。需要手工纠正路径时写
`~/.config/toolenv/overrides.sh`,不改仓库内任何文件。

脚本通过 `readlink -f "$0"` 定位真实仓库路径,故 symlink 后相对路径引用依然成立。

## 兼容与验证

- `md-pipeline/scripts/env.sh` 保留为薄壳,内部转调 `toolenv`,
  `md_env_check` 函数名与行为不变,现有命令行用法不受影响。
- `toolenv selftest` 在 `env -i bash` 干净环境跑 probe + check,
  确认不依赖交互式 `.bashrc` —— 这是现有 `env.sh` 的既有验收标准。
- 全部 shell 文件过 `shellcheck`。

## 首批工具

Schrödinger/Desmond、AutoMD/AutoTRJ、conda(含 `md` 环境)、PLIP、AmberTools、RDKit。
GROMACS、PLUMED、ORCA、Multiwfn、sobtop 后续按同一 manifest 接口追加。

## 明确不做

- 不做 Claude Code plugin / marketplace 打包。
- 不负责第三方软件本身的安装,只负责发现与激活。
- 不做集中式脚本注册表 —— 元信息只存在于脚本头。

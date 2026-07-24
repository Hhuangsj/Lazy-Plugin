# skills 仓库分层重构设计

日期:2026-07-23
状态:已批准,待实施

## 背景与目标

仓库当前是扁平单域布局:根下一个 `md-pipeline/` skill + 一个 `toolenv/` 共享发现层
+ `install.sh`。用户计划未来纳入两类新方向的工具:

- **AIDD 方向**(ADMET 预测、分子生成、数据清洗等)
- **日常办公方向**(Excel/PPT/周报、文档转换等)

因此要在外面包一层「分类」,并让仓库同时具备官方 Claude Code plugin 分发能力。
重构必须满足最初立项的三条硬要求:

1. 分类可扩展,容纳未来的 AIDD / 办公工具
2. 加新脚本、加新 skill、加新工具都要有**稳定接口**(不改中心文件)
3. 跨机器可复用,自动发现工具位置

## 参考:GitHub 同类做法(2026-07 调研)

- **`anthropics/skills`(官方)**:`skills/` 目录**扁平**平铺所有 skill;分类不靠目录,
  而在 `.claude-plugin/marketplace.json` 里切成多个 plugin,全部 `source: "./"`,
  各 plugin 用 `skills` 数组选取本仓库的子集。→ 「目录布局与分类解耦」是官方范式。
- **plugin marketplace 规范**:plugin 条目支持 `category` / `tags` / `strict` /
  `metadata.pluginRoot`。用户侧 `/plugin marketplace add <owner>/<repo>` 后按需装单个 plugin。
- **硬约束**:plugin 安装后,**跨出 plugin root 的相对路径(`../toolenv`)会失效**——
  外部文件不进 cache。官方解法是把依赖放在 plugin root 内,或用 root 内的相对 symlink。
  → 决定了 toolenv 必须位于 `source` 解析出的 plugin root 之内。
- **关键规则**(plugins-reference):marketplace 条目 `source` 解析到仓库根时,
  `skills` 里声明的目录**取代**默认 `skills/` 扫描;且 `skills` 项指向一个目录时,
  该目录下每个含 `SKILL.md` 的子目录都会被加载。→ 让「加 skill 不改 manifest」成立。

## 架构

### 目录布局

```
skills/
  science/
    md-pipeline/          SKILL.md  scripts/  references/  tests/
  office/                 (先建空目录 + .gitkeep 占位)
toolenv/                  通用工具发现层(位置不变,在 plugin root 内)
  find-toolenv.sh         新增:定位 toolenv 自身的入口
  ...
.claude-plugin/
  marketplace.json        新增
docs/superpowers/{specs,plans}/
install.sh                改
README.md                 改
```

采用 `skills/<域>/<skill>/` **两级**,而非官方的扁平布局。取舍:官方扁平要求每加一个
skill 就往 manifest 的 `skills` 数组补一行;两级 + 目录级声明(`skills: ["./skills/science"]`)
则让「域目录下新增 skill」被自动发现,**manifest 一字不改**——直接满足要求 2。

### marketplace.json

```json
{
  "name": "lazy-skills",
  "owner": { "name": "huangshengjie" },
  "metadata": { "description": "计算化学 / AIDD / 办公 自用 skill 集" },
  "plugins": [
    { "name": "science", "source": "./", "strict": false,
      "description": "计算化学与 AIDD:MD 模拟、轨迹分析、结合自由能等",
      "skills": ["./skills/science"] },
    { "name": "office", "source": "./", "strict": false,
      "description": "日常办公:文档转换、报表、周报等",
      "skills": ["./skills/office"] }
  ]
}
```

- 域划分:**science**(计算化学 + AIDD 合并,工具链重叠大,避免反复纠结归属)
  与 **office**。office 先占位,有实际 skill 再填。
- `strict: false` 必需:marketplace 条目声明了组件却不设它,会报 conflicting manifests。
- `source: "./"` 使 plugin root = 仓库根,`toolenv/` 天然在 root 内,合规。

### toolenv 定位(唯一真技术风险)

现状 `env.sh` 硬编码 `../../toolenv`,目录一移即断。三种安装形态下 toolenv 位置不同:

| 形态 | toolenv 路径 |
|---|---|
| `/plugin install` | `$CLAUDE_PLUGIN_ROOT/toolenv/toolenv` |
| `install.sh` symlink | symlink 解析成真实路径后所在仓库的 `toolenv/toolenv` |
| 仓库内直接跑脚本 | 同上 |

新增 `toolenv/find-toolenv.sh`,被脚本 source,按序解析(首个命中即用):

1. `$TOOLENV_BIN`(显式覆盖,若指向可执行文件)
2. `$CLAUDE_PLUGIN_ROOT/toolenv/toolenv`(若存在)
3. 从**调用者脚本的真实路径**(`readlink -f`)逐级向上(上限 6 级),
   找含 `toolenv/toolenv` 的目录
4. `PATH` 上的 `toolenv`

全找不到 → stderr 打印清晰缺失说明并返回非零。逻辑本质是把 toolenv 的「发现」
哲学用在它自己身上,单独测试覆盖。

## 数据流 / 契约(不变量)

- skill 脚本头四行元信息(`@name`/`@description`/`@requires`/`@usage`)+ `toolenv index`
  自动索引,不变。加脚本仍是「丢进 scripts/ 写四行头」。
- 加工具仍是「`toolenv/tools.d/` 加一个 manifest」。tools.d **不分域**——pandoc 与
  schrodinger 并列;toolenv 升为通用工具发现层,任何 skill 脚本头 `@requires` 写法一致。
- 加 skill = 丢进 `skills/<域>/`,不改 manifest。
- 加域 = marketplace.json 追加一个 plugin 对象。

## 迁移动作

1. `git mv md-pipeline skills/science/md-pipeline`
2. env.sh、SKILL.md 里的硬编码 toolenv 路径改用 find-toolenv
3. `install.sh`:`for d in "$REPO"/*/` 改为扫 `skills/*/*/`(含 SKILL.md 者);
   加「该 skill 已由 plugin 装上」的冲突检测
4. 删根目录游离旧脚本 `plip_proa_prob_analysis.py`(经比对是 `md-pipeline/scripts/
   plip_interaction_analysis.py` 的被取代旧版,无独有能力;git 历史保留可取回)
5. 建 `skills/office/.gitkeep`、`.claude-plugin/marketplace.json`
6. README 重写:两种安装方式(install.sh 开发态 / /plugin 分发态)+ 三个扩展接口
7. `docs/` 不动

用户本机 `~/.claude/skills/md-pipeline` symlink 会因目录移动而断,重跑 `./install.sh` 修复。

## 测试

- 现有 toolenv 67 项 + env_shim 5 项必须全绿;env_shim 因路径变化需相应调整,
  仍保留 `test_paths_come_from_toolenv_not_hardcoded` 这条判别性用例
- 新增 find-toolenv 4 条:伪造 `CLAUDE_PLUGIN_ROOT` 命中 / 伪造 symlink 安装向上找命中 /
  仓库内直跑命中 / 全不满足时报错返回非零
- `claude plugin validate`(若可用)校验 marketplace.json
- 干净环境跑 install.sh 一遍(env -i)

## 非目标

- 不写 office / AIDD 的实际 skill(只留空目录 + 接口)
- 不动 md-pipeline 的脚本业务逻辑
- 不提交到任何公共 marketplace 目录站
- tools.d 不按域分子目录(会使 probe 递归扫描与工具名→文件映射复杂化,现有 8 项测试受累)

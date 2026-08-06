# Ligand MM/GBSA 逐残基分解设计

**日期：** 2026-08-06  
**目标仓库：** Lazy-Plugin  
**协作依赖：** Synergy/Synergy-Fragment

## 1. 目标

为 `md-pipeline` 增加以 ligand 为中心的 Prime MM/GBSA 原子属性逐残基汇总能力，并支持两种输入形态：

1. 已完成 MD 的 ligand 是单个 `UNK` residue：分析时调用 Synergy-Fragment 重建肽残基边界，生成非破坏性的分析副本，不修改原始 CMS 和轨迹，也不重跑 MD。
2. 新体系在 MD 前已经把 ligand 建成逐个 residue：直接验证并使用现有 residue metadata，不再调用 Synergy-Fragment 拆分。

用户入口保持为现有 `run_mmgbsa.sh`，通过显式环境变量开启：

```bash
DECOMP=1 LIG_ASL='<ligand ASL>' run_mmgbsa.sh <md-dir>...
```

未设置 `DECOMP=1` 时，现有 thermal MM/GBSA 行为和输出保持不变。

## 2. 非目标与科学边界

- 第一版不要求 Synergy-Fragment 为每个非天然残基提供最终名称；结构位置编号是结果主键，名称只是可替换的显示信息。
- 第一版不把交联组件武断地分给某个氨基酸；交联原子使用独立 `XLINK_nnn` group。
- 不把大型 CMS、轨迹或 Prime 输出提交到 Lazy-Plugin 或 Synergy 仓库。
- 不把逐残基分解描述为严格可加的热力学自由能。输出是 Prime 写到原子上的 MM/GBSA property 按 ligand group 的近似汇总。
- ligand group 之和必须等于 ligand 原子 property 总和，但不要求等于整个复合物的总 `dG_Bind`，因为受体原子也可能承载分解项。

## 3. 总体架构

系统只有一个 MM/GBSA 调度入口，识别与计算职责分离：

- **Synergy-Fragment** 是 single-UNK 肽结构分段与重原子归属的唯一权威实现。
- **Lazy-Plugin/md-pipeline** 负责 ligand 模式检测、CMS/SDF 适配、Maestro 原子索引映射、显式氢归属、分析副本生成、thermal MM/GBSA 调度、property 汇总和结果说明。
- **Lazy-Plugin/SKILL.md** 负责 agent 路由：何时开启 `DECOMP=1`、如何设置 ligand ASL、如何识别两种模式、失败后查看哪些机器可读产物。

两个仓库通过版本化 JSON 契约协作，不让 Lazy-Plugin 复制 Synergy 的肽分段算法。

## 4. 数据流

### 4.1 公共入口

`run_mmgbsa.sh` 定位每个 MD 目录的主 `*-out.cms`，使用 `LIG_ASL` 解析 ligand。开启 `DECOMP=1` 后，它先运行 residue-map 准备步骤，再运行 thermal MM/GBSA 和 decomp 汇总。

入口必须验证 ligand ASL：

- 选中原子数大于零；
- 选中一个完整 ligand 分子；
- 不包含受体、水或离子；
- single-UNK 模式下，所选 ligand 只有一个 `UNK` residue；
- pre-resolved 模式下，所选 ligand 至少包含一个 residue，并具有可复现的 chain/resnum/pdbres 标识。

### 4.2 single-UNK 模式

1. Lazy-Plugin 从 CMS 中提取 ligand，生成仅含重原子的 `ligand_graph.sdf`。
2. 同时生成 `atom_index_map.json`，保存 SDF/RDKit atom index 到原始 Maestro atom index 的一一映射。
3. 导出后重新读取 SDF，并校验元素、形式电荷和键连接与 CMS ligand 重原子图一致。
4. Synergy-Fragment 读取 SDF，识别有序肽主链和所有非主链连通组件，输出版本化 residue-map JSON。
5. Lazy-Plugin 把 RDKit index 映射回 Maestro index，再把每个显式氢归属到其直接相连重原子的 group。
6. Lazy-Plugin 生成新的分析 CMS 副本，只更新 residue name、number、chain 等 metadata；原子顺序、坐标、键和力场参数保持不变。
7. thermal MM/GBSA 使用分析 CMS 副本和原始轨迹。

### 4.3 pre-resolved 模式

1. Lazy-Plugin 直接读取 ligand 的现有 residue metadata。
2. 每个 ligand residue 形成一个稳定 group；独立 cap residue 保持为独立 group。
3. 对 atom ownership 执行与 single-UNK 相同的完整性检查。
4. thermal MM/GBSA 使用现有 CMS；无需 Synergy-Fragment。

## 5. Group 身份与命名

结构位置是稳定主键：

```text
P000, P001, ... P999
N_CAP
C_CAP
XLINK_000, XLINK_001, ...
```

- `group_id` 不随后续名称修订而改变。
- `group_name` 可以是 Synergy 识别名称、原始 residue 名称或空字符串。
- unknown、立体匹配不完整和 crosslinked 状态保存在独立字段和 warnings 中，不编码进 `group_id`。
- 交联组件的所有原子只进入对应 `XLINK_nnn`，不同时计入被连接的 `Pxxx`。
- 每个显式氢跟随其唯一相连的重原子 group。

## 6. Synergy residue-map 契约

Synergy-Fragment 提供一个非交互 CLI，输入 `ligand_graph.sdf`，输出 schema version 1 JSON。顶层至少包含：

- `schema_version`
- `status`
- `source_atom_count`
- `groups`
- `unassigned_atom_indices`
- `duplicate_atom_indices`
- `warnings`
- `topology`
- `mapper_version`

每个 group 至少包含：

- `group_id`
- `group_type`：`residue`、`n_cap`、`c_cap` 或 `crosslink`
- `rdkit_atom_indices`
- `sequence_index`
- `display_name`
- `recognition_status`
- `residue_smiles`
- `connected_group_ids`

CLI 在结构名称 unknown 时仍返回 `status=ok`；只有结构无法分段、原子遗漏、原子重复或 schema 生成失败时返回非零退出码。

## 7. Lazy-Plugin 输出契约

每个 MD 目录的 MM/GBSA 输出子目录包含：

### 7.1 `residue_map.json`

记录：

- `schema_version`
- `mode`：`single_unk` 或 `pre_resolved`
- 原始 CMS、分析 CMS 和 ligand ASL
- 每个 group 的 Maestro atom indices
- 重原子和显式氢计数
- 名称、识别状态、连接关系和 warnings
- 未分配、重复分配和覆盖率
- Synergy mapper version；pre-resolved 模式记为 `null`

### 7.2 `ligand_decomp_frames.csv`

使用长表：

```text
frame,time_ps,group_id,group_name,property,value_kcal_mol
```

### 7.3 `ligand_decomp_summary.csv`

使用长表：

```text
group_id,group_name,property,n_frames,mean,sd,sem
```

### 7.4 `decomp_manifest.json`

无论成功或失败都尽力写出，至少记录：

- `schema_version`
- `status`：`running`、`success` 或 `failed`
- `mode`
- 输入和输出路径
- ligand ASL
- frame range 和 step
- Prime properties
- Schrodinger、Synergy mapper 和 Lazy-Plugin 版本
- 实际命令参数
- residue-map 覆盖统计
- warnings 和 error
- 产物路径

## 8. 默认 Prime properties

默认汇总下列原子 property；CLI 允许显式选择子集：

- `r_psp_MMGBSA_dG_Bind`
- `r_psp_MMGBSA_dG_Bind(NS)_Coulomb`
- `r_psp_MMGBSA_dG_Bind(NS)_Solv_GB`
- `r_psp_MMGBSA_dG_Bind(NS)_Covalent`
- `r_psp_MMGBSA_dG_Bind(NS)_vdW`
- `r_psp_MMGBSA_dG_Bind(NS)_Hbond`
- `r_psp_MMGBSA_dG_Bind(NS)_Lipo`
- `r_psp_MMGBSA_dG_Bind(NS)_Packing`
- `r_psp_MMGBSA_dG_Bind(NS)_SelfCont`
- `r_psp_Lig_Strain_Energy`

如果任何请求 property 在任一 snapshot 缺失，decomp 失败并报告具体 snapshot、atom 和 property；不产生被误认为完整结果的部分 summary。

## 9. 完整性和失败策略

以下条件必须返回非零退出码，并将 manifest 标记为 `failed`：

- ligand ASL 选不到原子或选中多个分子；
- SDF 与 CMS 重原子图不一致；
- group 原子并集不等于 ligand 全部原子；
- 任一原子未分配或分配到多个 group；
- Prime 输出缺少请求 property；
- snapshot 之间 ligand 原子身份或 group 映射不一致；
- 每个 snapshot、每个 property 的 group 求和与 ligand 原子 property 求和差值超过数值容差。

名称 unknown、立体化学未完全匹配和使用临时 `Pxxx/XLINK_xxx` 名称只产生 warning，不阻塞计算。

如果 single-UNK 模式找不到 Synergy-Fragment，命令必须给出可执行的安装或路径配置提示。pre-resolved 模式不依赖 Synergy，不能因此失败。

## 10. Agent 可理解性与工具发现

Lazy-Plugin 更新以下说明：

- `md-pipeline/SKILL.md` 增加 residue decomp 触发语句、模式决策表、调用示例、产物解释和科学边界。
- `run_mmgbsa.sh` 的元数据说明 `DECOMP=1`。
- 新脚本具有 `@name`、`@description`、`@requires`、`@usage` 头，能被 `toolenv index` 发现。
- `toolenv` 可选探测 Synergy-Fragment，但不把它列为所有 MM/GBSA 运行的无条件依赖。
- agent 优先读取 `decomp_manifest.json` 和 `ligand_decomp_summary.csv`，无需从日志猜测运行状态。

## 11. 测试策略

### 11.1 Synergy-Fragment 单元测试

- 线性标准肽；
- Ac/NH2 等封端；
- unknown 非天然残基；
- 头尾环肽；
- 二硫键和非二硫侧链交联；
- 交联原子进入独立 `XLINK_nnn`；
- 所有重原子完整且唯一归属；
- schema version 1 字段和稳定 group ordering；
- 错误结构返回非零状态和结构化错误。

### 11.2 Lazy-Plugin 单元与契约测试

- single-UNK 与 pre-resolved 模式自动判断；
- RDKit/Maestro atom-index remap；
- 显式氢归属；
- coverage、overlap 和 graph round-trip 校验；
- 每个 property 的逐帧求和；
- mean、population SD 和 SEM；
- unknown warning 不阻塞；
- 缺失 property、映射漂移和求和不一致严格失败；
- manifest 成功与失败状态；
- `DECOMP` 未开启时 `run_mmgbsa.sh` 旧行为不变；
- mock thermal MM/GBSA 调度参数正确。

### 11.3 本机 Schrödinger 冒烟测试

使用当前 NPR 数据但不提交大文件：

- `NPR1-SYN-007714-16473-md-out.cms` 的 ligand 应识别出 13 个主链位置、Ac、NH2 和一个交联组件；
- 124 个重原子和 133 个显式氢，共 257 个原子，必须全部且唯一归属；
- 使用 1–2 个 snapshot 验证分析 CMS、thermal MM/GBSA、Prime maegz 和全部默认 property 的端到端链路；
- 使用一个小型 pre-resolved peptide fixture 验证无需 Synergy 的第二条路径。

## 12. 兼容、隔离与实施顺序

- 现有 `run_mmgbsa.sh` 默认行为不变，residue decomp 只在 `DECOMP=1` 时启用。
- Lazy-Plugin 当前 `run_serial_md.sh` 有用户未提交修改，实施不得覆盖或夹带该 diff。
- Synergy 当前工作区存在大量用户修改和未跟踪文件，实施必须使用独立 worktree，并只触碰映射接口及其测试。
- 实施顺序为：Synergy 映射 JSON 契约 → Lazy-Plugin 映射适配与汇总核心 → `run_mmgbsa.sh` 可选集成 → 文档与本机冒烟测试。

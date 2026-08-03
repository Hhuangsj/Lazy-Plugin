# MD Align Trajectory Analysis Design

## Goal

让 md-pipeline 对已经生成的 Schrödinger Align 轨迹进行 event_analysis 和 PLIP 分析，同时保持现有 raw 轨迹流程完全兼容。

## Current behavior

- AutoTRJ 在传入 `-a` 时已经执行 `CLEAN → ALIGN → clustering`。
- `run_analysis.sh` 的 event_analysis 仍从主 `*-out.cms` 和主 `*_trj` 读取。
- `run_plip.sh` 也只选择主 `*-out.cms` 和匹配的 `*_trj`。
- Align 结果是一对匹配文件：`*_ALIGN-out.cms` 与 `*_ALIGN_trj`；Align CMS 通常已经是去水/去离子的清洁拓扑。

## Design

新增 `scripts/trajectory_source.sh`，作为 raw/align 轨迹选择的唯一实现：

- `TRAJECTORY_SOURCE=raw`：选择非 `PL_Analysis*`、非数字中间产物的主 `*-out.cms`，并要求匹配的 `*_trj`；这是默认值。
- `TRAJECTORY_SOURCE=align`：选择唯一的 `*_ALIGN-out.cms` 与匹配的 `*_ALIGN_trj`；多个候选时明确报错，并支持 `ALIGN_CMS` / `ALIGN_TRJ` 显式指定。
- 选择器输出统一的 CMS、轨迹目录和基名，调用方不再各自实现文件探测。

`run_analysis.sh` 的行为：

- raw 模式保持当前 AutoTRJ 参数（清洗并重新 Align），event_analysis 复用已有 raw EAF 或按原逻辑生成。
- align 模式读取已有 Align pair；AutoTRJ 直接在该 pair 上聚类，不再次清洗或 Align。
- align 模式始终根据 Align CMS 重新生成 EAF，并将报告写入 `analysis_align/`，避免复用不同拓扑的 raw EAF 或覆盖 raw 报告。

`run_plip.sh` 的行为：

- raw 模式保持当前输入和输出目录 `plip_last100ns/`。
- align 模式读取同一 Align pair，默认输出 `plip_last100ns_align/`；显式设置 `OUT_NAME` 时尊重调用方设置。

## Error handling

- 未知 `TRAJECTORY_SOURCE` 直接退出并给出允许值。
- Align 模式找不到 pair 或发现多个未指定的 pair 时直接退出，列出需要修正的路径。
- 显式 `ALIGN_CMS` 若未提供 `ALIGN_TRJ`，从 CMS 基名推导匹配的 `_trj`；若推导路径不存在则报错。

## Compatibility and scope

- 默认不改变已有 raw 结果、参数、文件名或输出目录。
- 不修改 MMGBSA；刚体 Align 不改变能量或分子内接触距离，MMGBSA 仍使用主轨迹。
- 不修改当前工作目录中的模拟结果；本次只改 Lazy-Plugin 源码、测试和说明文档。

## Verification

- 轨迹选择器 shell 回归测试覆盖 raw、唯一 Align、显式 Align、多个 Align 和缺失 pair。
- 对修改后的 shell 脚本运行 `bash -n`。
- 运行 md-pipeline env shim 与 toolenv 全套测试。
- 使用临时目录和 stub 命令做 dry-run 级别的 raw/align 输入选择检查，不运行实际 500 ns 分析。

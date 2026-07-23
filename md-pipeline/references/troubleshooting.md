# md-pipeline 踩坑记录

来自实际跑 PEPX_P8P9 这批体系时踩到的坑。改动 `run_*.sh` 或调 ASL / 帧范围 /
并行度之前先读这里。

### 1. 配体 ASL 必须用 `res.ptype UNK`,不能用默认 `ligand`
AutoMD 建模时配体是 `-L "res.ptype UNK"`(配体为 UNK 残基)。但 AutoTRJ 分析默认
`-L "ligand"` 靠 Schrödinger 自动识别配体,**对多肽/修饰氨基酸类配体识别不到**,
表现为:
```
No atoms selected by '-rmsd-asl "( ligand )"'          # 配体聚类失败
ASL expression ligand does not match any atoms         # MMGBSA 全帧失败
```
→ 本模板已统一改为 `-L "res.ptype UNK"`,与建模保持一致。

### 2. MMGBSA 默认关闭(CLEAN 后冻结集为空)
AutoTRJ 跑 MMGBSA 时硬加了
`-frozen -atom_asl "not ((protein) OR (res.ptype UNK))"`(冻结非蛋白非配体原子)。
但分析前 `-C "not solvent and not ions"` 已把水/离子删光,冻结集变成**空集**:
```
No such atoms present: not ((protein) OR (res.ptype UNK))   # 读第1帧即中止
```
→ 本模板默认 `-M` **不含 MMGBSA**。确需 MMGBSA 时:直接调
`$SCHRODINGER/run thermal_mmgbsa.py -lig_asl "res.ptype UNK" ...` 并**去掉
`-frozen`/`-atom_asl`**(体系里已无需冻结的组分),或保留水/离子不做 CLEAN。

### 3. AutoTRJ 的 `-a` 是异步提交
`AutoTRJ ... -a` 会把聚类/MMGBSA 作为**独立作业提交后立即返回**(日志里每个都有
`JobId:`),脚本可能显示 "DONE" 但子作业还在后台跑。判断真完成要看
`PL_Analysis_*-out.cms` 是否落地,或用 `$SCHRODINGER/jobcontrol -list`。

### 4. `Desmond.hosts: No such file` 警告无害
AutoTRJ 启动时会 `grep` Desmond.hosts,缺失只是警告,不影响运行。

### 5. PLIP 对「肽=单个 UNK 残基」体系必须走配体模式
AutoMD 建模的修饰肽是一整个 `UNK` 残基。PLIP 的 `--peptides <链>` 肽模式对它
**选不到原子(实测 0 相互作用、0 结合位点)**。而且这条肽在导出的 PDB 里落在
**空白链**,会被链检测忽略。`run_plip.sh` 的处理:
1. 导出帧用 `keep_asl = "protein or res.ptype UNK"`(否则整条肽被丢);
2. 把 UNK 肽重贴到独立链 `B`(`--ligand-chain B`);
3. `--no-plip-peptides` 让 PLIP 把 UNK 当**配体**自动识别(实测正常:疏水/氢键/盐桥等都能测到)。
→ 即 `--chain-a B`(肽/配体侧)、`--chain-b A`(受体)。

### 6. `run_analysis.sh` 传多个目录用绝对路径 / 已修复
`run_one` 内 `cd` 会改工作目录,老版本传多个**相对路径**时第二个起会报「目录无效」。
现已把每个目录放到子 shell 里跑(`( run_one "$d" )`)隔离 `cd`;仍建议传绝对路径。

### 7. MMGBSA 单体系并行:关键是 `-HOST localhost:N`,不是 `-NJOBS`,也不是 hosts 的 `processors`
`-NJOBS 8` 只是把帧切成 8 个子作业;它们**是否并发**由 `-HOST` 的处理器数决定:
- `-HOST localhost`(或不传 `-HOST`)=> 只申请 **1 个 slot** => 子作业**串行**,实际 1 核。
  日志里 `Running subjobs on hosts: localhost (Max: 1)`、子作业 `00001` 完才起 `00002` 即症状。
- `-HOST localhost:8` => 8 个 slot => 8 个子作业同时跑 = **8 核**。

`thermal_mmgbsa.py` 会把命令行 `-HOST` 原样透传给 `prime_mmgbsa`
(`get_command_line_host_list()`),所以**必须显式传 `-HOST localhost:N`**。
本包 `run_mmgbsa.sh` 已改为传 `-HOST "localhost:$NJOBS"`。

> 注意:光在 `schrodinger.hosts` 给 `localhost` 设 `processors: 8`**不够**——那只是声明
> "可用上限",`jobcontrol.get_host('localhost').processors` 查出来是 8,但 `-HOST localhost`
> 仍只要 1 slot。二者配合最稳:hosts 里 `processors: 8` + 命令行 `-HOST localhost:8`。
>
> 另一条路(不改单作业):**同时并行启动 N 个体系**,每个 thermal_mmgbsa 各占 1 核 = N 核
> (P8/P9 那批 4 体系并行 = 4 核就是这么来的)。体系数少、想单个跑满多核时,用 `-HOST localhost:N`。

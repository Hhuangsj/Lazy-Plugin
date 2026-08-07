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

---

来自 TSLP/From-Prasanna 这批体系(受体 TSLP 在链 A,肽是**正常氨基酸残基**、
不是 UNK)。这批和 PEPX 那批的建模方式不同,踩到的坑也不同。

### 8. 配体 ASL 不是万能默认值 —— 先看体系,别照抄 `res.ptype UNK`
`res.ptype UNK` 只对「肽被建成单个 UNK 残基」的 AutoMD 体系成立。TSLP 这批的肽是
ACE/NME 封端的**正常残基**(ASP/CYS/ILE...),落在**空白链**,于是:
```
ERROR: No atoms selected by '-rmsd-asl "( res.ptype UNK )"'    # 配体聚类静默失败
ERROR: Ligand ASL selection "..." returns zero atoms.           # thermal_mmgbsa 拒跑
```
而且 AutoTRJ 把这种失败写进 `PL_Analysis_Ligand*Cluster_*.log` 就**退出码 0**,
外层看不出错 —— 必须去 `grep -i error PL_Analysis_*.log` 才发现。

**跑分析前先查清配体是什么**:
```bash
$SCHRODINGER/run python3 -c "
from schrodinger.application.desmond.packages import topo
_, c = topo.read_cms('<体系>-out.cms')
ch = {}
for r in c.fsys_ct.residue: ch.setdefault(r.chain, []).append(r.pdbres.strip())
for k, v in ch.items(): print(repr(k), len(v), sorted(set(v))[:8])"
```
然后按体系选:
| 体系形态 | LIGAND_ASL |
|---|---|
| 肽 = 单个 UNK 残基(AutoMD 建模) | `res.ptype UNK` |
| 肽 = 正常残基、独立/空白链 | `not chain.name A and not water and not ions` |

**别用残基编号**(如 `res.num 63-88`)当通用配体 ASL:同一批里编号未必一致 ——
INT-001553 的肽编号 63–88,INT-001554 却是 0–16,同一条命令一个能跑一个报零原子。
按「链 + 非水非离子」选最稳。

### 9. 肽是正常残基链时,PLIP 要走**肽模式**而不是配体模式
踩坑 5 说的 `--no-plip-peptides`(把 UNK 当配体)只适用于 UNK 体系。正常残基肽走
配体模式识别不到,要用 PLIP 的 `--peptides`:
```bash
LIGAND_ASL='not chain.name A and not water and not ions' \
KEEP_ASL="chain.name A or ($LIGAND_ASL)" \
PEPTIDE_MODE=1 LIGAND_CHAIN=B CHAIN_A=B CHAIN_B=A run_plip.sh <dir>
```
`run_plip.sh` 的 `PEPTIDE_MODE=1` 就是「不传 `--no-plip-peptides`」。重贴链仍然必要
(空白链会被链检测忽略),但重贴哪些原子现在由 `LIGAND_ASL` 决定 ——
`plip_interaction_analysis.py` 新增 `--ligand-relabel-asl`,默认仍是 `res.ptype UNK`,
老体系行为不变。

### 10. `PL_Analysis_CLEAN_trj` 是中间产物,跑完可删
AutoTRJ 的链路是 `原始轨迹 → CLEAN(去水去离子) → ALIGN(叠合) → 聚类`。
聚类和后续分析都读 ALIGN,CLEAN 留着只是占地(每体系 ~55MB)。
`run_analysis.sh` 现在默认在**等 `$JOB_*` 作业全部离开 jobcontrol 队列后**删掉
`PL_Analysis_CLEAN_trj` / `PL_Analysis_CLEAN-out.cms`;`KEEP_CLEAN=1` 可保留。
> 等待是必须的:AutoTRJ `-a` 异步提交(踩坑 3),立刻删会抽掉聚类正在读的轨迹。

### 11. 重跑分析前先清掉上一轮的聚类产物
聚类输出文件名带成员数(`PL_Analysis_APCluster_5_0_56members-out.cms`),换了 ASL
重跑会生成**另一套名字**,和上一轮的并排躺在同一目录里,分不清谁是谁。重跑前:
```bash
rm -f <dir>/PL_Analysis_*Cluster_*-out.cms
```

### 12. 别在脚本运行中编辑它
bash 是按字节偏移边读边执行的。分析跑到一半时改 `drive.sh`,正在跑的那个进程会从
错位的偏移继续读,报 `syntax error near unexpected token` 然后死掉 —— 表现为
「明明语法没问题的脚本却挂了」。要改就先复制一份改副本,或等跑完再改。

### 13. Align 轨迹分析必须使用匹配的 CMS/trj pair
`TRAJECTORY_SOURCE=align` 只读取成对的 `*_ALIGN-out.cms` 与 `*_ALIGN_trj`。
目录里有多个 Align 产物时不会猜测，需显式指定：
```bash
TRAJECTORY_SOURCE=align \
ALIGN_CMS=/path/to/PL_Analysis_p2_18351_ALIGN-out.cms \
ALIGN_TRJ=/path/to/PL_Analysis_p2_18351_ALIGN_trj \
./run_plip.sh /path/to/XXX-md
```
event_analysis 会基于选中的 Align CMS 重新生成 EAF，并把报告放到 `analysis_align/`；
不要把 raw EAF 直接套到 CLEAN/ALIGN 后可能不同的拓扑上。Align 模式的 PLIP 默认写入
`plip_last100ns_align/`，raw 模式仍写入 `plip_last100ns/`。

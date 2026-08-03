# MD 运行 + 轨迹分析 流程模板

把「串行跑 Desmond MD → AutoTRJ 聚类分析 → event_analysis 交互报告 → PLIP 相互作用
→ 薛定谔 MMGBSA → 汇总成表」这套流程沉淀成可复用、可跨机器、环境隔离的模板。
**AutoMD/AutoTRJ 已随包内置**(`AutoMD/` 子目录),无需另外安装。

---

## 目录内容

| 文件 | 作用 |
|------|------|
| `env.sh` | **环境隔离核心**。集中所有环境设置(Schrödinger/Desmond/AutoMD/conda `md` 环境),被其他脚本 `source`。换机器只改这一个文件。 |
| `AutoMD/` | **内置的 AutoMD/AutoTRJ**(第三方,GPLv3,见 `AutoMD/LICENSE`)。上游 https://github.com/Wang-Lin-boop/AutoMD,内置版本 v0.1.6。env.sh 默认把它加入 PATH,无需单独 clone/安装。 |
| `run_serial_md.sh` | 串行 MD 运行器:自动找空闲 GPU,逐个跑 MD,跑完自动接分析。支持单卡/多卡/指定卡/dry-run。 |
| `run_analysis.sh` | **独立分析脚本**:对已完成的 MD 目录重跑分析(聚类 + SID 报告)。缺 eaf 时自动 `event_analysis.py analyze` 现场生成。用于补分析、换参数重分析。 |
| `run_plip.sh` | **PLIP 相互作用分析**(默认后 100ns):逐帧算肽–受体相互作用类型与残基对占据率。已适配「肽=单个 UNK 残基」体系(见踩坑记录 5)。 |
| `run_mmgbsa.sh` | **薛定谔 MMGBSA**(Prime,默认后 100ns 每 20 帧):逐帧结合自由能 ΔG_bind。配体用 `res.ptype UNK`,不加 `-frozen`。 |
| `plip_interaction_analysis.py` | `run_plip.sh` 调用的 PLIP 核心脚本(在原 PROA/PROB 脚本上扩展了 `--last-ns` / `--ligand-chain` / `--no-plip-peptides`)。 |
| `summarize_analysis.py` | 汇总多个体系的 MMGBSA ΔG + PLIP 占据率成一张合并表(CSV + PNG 表格图)。 |
| `md_pending_serial.list.template` | 待办清单模板。复制到工作目录改名为 `md_pending_serial.list` 使用。 |
| `md_conda_environment.yml` | AutoMD 标准 Python 依赖的 conda 定义(参考;numpy/scipy/matplotlib/pandas)。 |
| `README.md` | 本文件。 |

---

## 安装

> 前提:本机已装好 **Schrödinger / Desmond 套件**并有可用 license(本包直接用现有安装,
> 不负责它的安装;academic Desmond 亦可)。以下只装本包自己的东西。

**1. 取得本包**
```bash
# 把整个 md_pipeline 目录放到任意位置即可(AutoMD/AutoTRJ 已随包内置,无需另外 clone)。
cd /path/to/md_pipeline
chmod +x *.sh AutoMD/AutoMD AutoMD/AutoTRJ   # 确保可执行(拷贝后如丢失执行位)
```

**2. 建隔离的 conda 环境 `md`**(放 AutoMD/绘图/分析用的 Python 依赖)
```bash
conda env create -n md -f md_conda_environment.yml   # numpy/scipy/matplotlib/pandas
conda activate md
pip install plip                                     # PLIP 相互作用分析(run_plip.sh 需要)
```

**3. 配好路径:改 `env.sh` 顶部四个变量**(或用同名环境变量覆盖)
```bash
SCHRODINGER   # Schrödinger/Desmond 安装路径,如 /opt/schrodinger/2023-4
CONDA_ROOT    # conda/miniforge 根目录,如 ~/miniforge3
MD_CONDA_ENV  # 上一步建的环境名,默认 md
AUTOMD_DIR    # 默认 = 本包内置的 AutoMD/,一般不用改
```

**4. 自检**(一条命令确认环境就绪)
```bash
source env.sh && md_env_check && echo OK
# 通过则打印 OK;失败会指出缺哪个(SCHRODINGER/Desmond/AutoMD/AutoTRJ/conda 环境)。
```
自检通过后,`AutoMD`/`AutoTRJ` 应解析到 **本包内置** 的副本:
```bash
command -v AutoTRJ   # -> .../md_pipeline/AutoMD/AutoTRJ
```

> 说明:内置的 AutoMD/AutoTRJ 是 GPLv3 第三方项目(见 `AutoMD/LICENSE`)。若要升级,
> 从上游重新拉取覆盖 `AutoMD/` 即可;`AUTOMD_DIR` 也可指向外部安装以临时切换。
> AutoTRJ 靠 `$(dirname $0)` 定位同目录的 `xpm_plot.py`/`rmsd_plot.py`,故须整目录一起放。

---

## 环境隔离(重点)

所有环境依赖集中在 `env.sh`,脚本不依赖交互式 `~/.bashrc`,因此在
`nohup` / `cron` / 任意 shell 下都能稳定运行。

`env.sh` 负责:
1. 设 `SCHRODINGER` 和 `Desmond`(AutoTRJ 依赖 `$Desmond`)。
2. 激活隔离的 conda 环境 **`md`**(不污染 base)。
3. 把内置的 `AutoMD/`(即 `$AUTOMD_DIR`)加入 `PATH`(提供 `AutoMD`/`AutoTRJ`)。
4. 把 `md` 环境的 `lib` 前置到 `LD_LIBRARY_PATH`(解决 Schrödinger `run` 的库版本问题)。
5. 提供 `md_env_check` 自检函数。

**换机器 / 换路径**:只改 `env.sh` 顶部这几行,或用同名环境变量覆盖:

```bash
SCHRODINGER=/path/to/Schrodinger/2024-1 \
MD_CONDA_ENV=md \
AUTOMD_DIR=/path/to/AutoMD \
CONDA_ROOT=/path/to/miniforge3 \
    ./run_serial_md.sh
```

自检(干净 shell 也应通过):

```bash
env -i bash -c "source ./env.sh; md_env_check && echo OK"
```

### 前置条件
- 已安装并授权的 **Schrödinger Suite 2023-4**(含 Desmond)。
- 隔离的 conda 环境 **`md`** 已创建(实际在用的那个;`.bashrc` 会自动 activate)。
- `~/software/AutoMD` 下有可执行的 `AutoMD` / `AutoTRJ`。

---

## 用法

### 1. 串行跑 MD(+ 自动分析)

在包含 `.mae` 输入和 `md_pending_serial.list` 的**工作目录**里:

```bash
# 先干跑,只看会执行哪些命令,不真正提交
./run_serial_md.sh --dry-run

# 后台串行:自动找连续空闲的 GPU,一个跑完再跑下一个
nohup ./run_serial_md.sh > run_serial_md.nohup.log 2>&1 &

# 指定 GPU(只用 2 号卡)
nohup ./run_serial_md.sh --gpu 2 > run_serial_md.nohup.log 2>&1 &

# 多卡并行:0 和 2 各一个 worker,卡内仍串行,用锁认领任务不重复
nohup ./run_serial_md.sh --gpus 0,2 > run_serial_md.nohup.log 2>&1 &
```

**输入清单** `md_pending_serial.list`(每行一个 `.mae`,`#` 为注释):

```
# Run serially, one free GPU at a time.
RecA_PEPX-P9_Mod01.mae
RecB_PEPX-P8_Ac-_R_Xaa-Xaa.mae
```

进度自动记录到同目录:`md_completed_serial.list` / `md_failed_serial.list`。
已完成或已有 `*-md` 目录的结构会自动跳过(可安全重启)。

常用参数:`--list`/`--completed`/`--failed` 指定清单;`--sleep` 轮询间隔;
`--gpu-stable-checks`/`--gpu-check-interval` 调 GPU 空闲判定;`-h` 看全部。

### 2. 独立重跑分析(对已完成的 MD 目录)

```bash
# 对一个或多个 MD 目录跑分析(聚类 + 交互报告)
./run_analysis.sh /path/to/XXX-md /path/to/YYY-md

# 换参数(环境变量覆盖)
LIGAND_ASL='res.ptype UNK' FRAMES='1:2001:20' ./run_analysis.sh /path/to/XXX-md

# 分析已有 AutoTRJ Align 轨迹；报告写入 analysis_align/
TRAJECTORY_SOURCE=align ./run_analysis.sh /path/to/XXX-md
# 多个 Align 产物时显式指定
ALIGN_CMS=/path/to/XXX-md/PL_Analysis_ALIGN-out.cms \
TRAJECTORY_SOURCE=align ./run_analysis.sh /path/to/XXX-md
```

产出(在各 MD 目录内):
- `PL_Analysis_APCluster_5_*-out.cms` — 蛋白骨架聚类代表构象
- `PL_Analysis_LigandAPCluster_5_*-out.cms` — 配体聚类(AP)代表构象
- `PL_Analysis_LigandCHCluster_5_1.0_*-out.cms` — 配体聚类(化学哈希)代表构象
- `analysis/*.pdf` + PNG/SVG/DAT — RMSD / RMSF / 蛋白-配体接触 / 配体性质报告

> 注:`analysis/*.pdf` 需要 `.eaf`。老体系(如 P8/P9)MD 阶段已自带 eaf;新体系
> (如 P7)没有,脚本会自动用 `event_analysis.py analyze <cms> -lig "res.ptype UNK"`
> 现场生成再出报告。

### 3. PLIP 肽–受体相互作用分析(默认后 100ns)

```bash
# 逐帧算相互作用类型 + 残基对占据率,输出到各目录的 plip_last100ns/
./run_plip.sh /path/to/XXX-md /path/to/YYY-md
LAST_NS=100 JOBS=8 ./run_plip.sh /path/to/XXX-md      # 覆盖:窗口/并行数
# 对已有 Align 轨迹分析，默认输出到 plip_last100ns_align/
TRAJECTORY_SOURCE=align LAST_NS=100 JOBS=8 ./run_plip.sh /path/to/XXX-md
```
产出 `plip_last100ns/`:`interaction_type_summary.csv`(各类型占据率)、
`residue_pair_occupancy*.csv` 与 `plot_*` 图(含 `plot_residue_pair_occupancy_B_vs_A.png`)。

`TRAJECTORY_SOURCE=align` 要求 CMS 和轨迹目录成对存在：
`*_ALIGN-out.cms` 与 `*_ALIGN_trj`。Align 模式会根据 Align CMS 重新生成 event-analysis
EAF，不复用 raw 轨迹的 EAF；这是因为 CLEAN/ALIGN 可能改变拓扑中的原子集合。

### 4. 薛定谔 MMGBSA 结合自由能(默认后 100ns 每 20 帧)

```bash
# 逐帧 Prime MMGBSA,输出到各目录的 mmgbsa_last100ns/
./run_mmgbsa.sh /path/to/XXX-md /path/to/YYY-md
START=1000 END=2000 STEP=20 NJOBS=4 ./run_mmgbsa.sh /path/to/XXX-md   # 覆盖帧范围/并行
```
产出 `mmgbsa_last100ns/<name>-prime-out.csv`,逐帧列 `r_psp_MMGBSA_dG_Bind`(kcal/mol,越负越强)。

> **CPU 并行(重要,见踩坑记录 7):** `thermal_mmgbsa.py` 硬编码 `-HOST localhost`,单体系
> 内的真实并发由 `schrodinger.hosts` 里 `localhost` 的 `processors:` 决定,**不是** `-NJOBS`。
> 本机已设 `processors: 4` → 单体系用 4 核。`-NJOBS` 只控制切几片(≥ processors 即可)。
>
> PLIP(`run_plip.sh` 的 `--jobs`)是 Python 多进程,直接按设定的核数并行,不受此限制。

### 5. 汇总成表(MMGBSA ΔG + PLIP 占据率)

```bash
$SCHRODINGER/run summarize_analysis.py DIR1 DIR2 DIR3 DIR4 \
    --labels "RecA start,RecA P9-Mod01,RecB start,RecB P9-Mod01" \
    --out-csv summary.csv --out-png summary.png
```
输出合并表 `summary.csv` 与表格图片 `summary.png`(ΔG_bind 均值±SEM/SD + 各相互作用类型占据率,自动高亮最强结合)。

---

## ⚠️ 踩坑记录

已拆到独立文件:[troubleshooting.md](troubleshooting.md)(7 条实测经验)。

---

## 备注
- 修改 `md_pending_serial.list` 时:脚本正在 `while read < file` 逐行读它。若在运行中
  编辑,用「写临时文件再 `mv` 原子替换」的方式,避免打断已打开的文件描述符;新内容
  下次启动生效(运行中的结构靠 `has_existing_md_dir` 也会自动跳过)。
- 沉淀日期:2026-07-07。源流程来自 `workstations/RecA/PEPX_P8P9/`。

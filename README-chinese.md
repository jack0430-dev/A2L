# A2L-RAGAlign

**从二进制地址文件（AXF / ELF / MAP）自动重建 ASAM MCD-2A A2L 描述文件，并以检索增强的语义对齐完成标定与测量描述的更新。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)
![Tests](https://img.shields.io/badge/tests-360%20passed-brightgreen)
![CI](https://github.com/jack0430-dev/A2L-RAGAlign_2026/actions/workflows/ci.yml/badge.svg)

> **语言。** 本文件是中文版，英文版见 [`README.md`](README.md)。

---

## 1. 这个工具解决什么问题

汽车 ECU 使用 **INCA** / **CANape** 一类标定工具进行标定，而这些工具读取的是 **A2L** 描述文件。ECU 固件重新编译之后，A2L 中的符号名、地址、类型和枚举随即失效。手工重建不仅慢、容易出错，更重要的是——**静默失败非常普遍**：一个被改名的变量、一个变更的数据类型，或一个版本不对的地址文件，都会生成一个仍能*解析*、却向标定工程师报出垃圾数值的 A2L。

本工具依据地址文件重建 A2L，并且为每一个信号输出**证据链**（地址从何而来），使失败变得可见，而不是无声无息。

### 实测的问题规模

在本仓库随附发布的量产固件镜像上
（`data/TcuBsw.axf`，ELF32/ARM，带 DWARF 调试信息，6.3 MB）：

| 指标 | 数值 |
|---|---|
| 成功写入的信号数 | 28,812 |
| 精确名称匹配失败的信号数 | 2,504（**8.00 %**） |
| 输出 | 431,860 行 A2L / 17.4 MB |

借助随附镜像，这些数字可在全新检出的工作区中复现：

```
python experiments/production_axf_stats.py
```

该脚本会打印 31,316 个 DWARF 派生信号、28,812 个精确名称匹配与 2,504 个失败（8.00 %），并写出
`experiments/results/production_axf_stats.json`。

> **随附的固件镜像。** `data/TcuBsw.axf` 是本项工作所使用的量产变速箱控制单元镜像（ELF32 / ARM，HighTec clang + LLD，带 DWARF 调试信息，SHA-256
> `e5af27f9d968993f35245146f10640ebd55cb48316cd9bf24bde39aa7dd54fc3`）。随仓库分发，是为了让论文中的量产规模数字可以被独立验证，而不是仅仅被断言。该镜像只包含 ECU 的编译代码与符号名；发布前进行的扫描未发现任何凭据、源码或构建路径、邮箱地址，也未发现任何可识别车辆的信息。`data/TcuBsw.hex`（纯 flash 镜像）及其他所有构建产物均不进入本仓库。

这 8 % 的失败率，正是[第 7 节](#7-a2l-ragalign检索增强的语义对齐)所介绍的检索增强对齐的量化动机。

## 2. 功能一览

| 领域 | 能力 |
|---|---|
| **输入** | A2L 头文件（`.a2l` / `.A2L` / `.txt`）、地址文件（`.axf` / `.elf` / `.map` / `.bin`）、可选的信号筛选 XLSX、可选的上一版 A2L（作为差异比对基线） |
| **类型还原** | 从 DWARF（`.debug_info` / `.debug_abbrev`）还原标定量与测量量的对象类型、枚举、结构体成员与数组形状 |
| **嵌套类型** | 结构体数组与嵌套结构体数组（`Point pts[3]`、`Cfg tbl[2][4]`）按元素逐个展开，而不是压缩成一整块 |
| **输出** | 具备正确 `MATRIX_DIM`、带符号 DWARF 偏移、按字节寻址 `ECU_ADDRESS` 的 A2L |
| **报告** | 逐信号匹配报告（含证据链）、版本差异报告、地址区间导出、来源溯源边车文件（`.log`） |
| **安全** | 将新生成的 A2L 与 INCA 工程当前使用的 A2L 交叉校验，地址漂移时告警；地址文件与固件版本不匹配时拒绝继续 |
| **非破坏性** | 用户原始 A2L 按字节原样保留（缩进、注释、编码、换行符均不变），除非显式启用*自动修复* |
| **无界面运行** | 完整流水线均可从命令行调用，可在 CI 中运行、可在论文中复现 |
| **国际化** | 默认英文，可切换中文；运行时切换并持久化 |

## 3. 安装

```bash
py -m pip install -r requirements.txt
py A2L_Update_Tool_main.py
```

Python 3.10 – 3.12。在 Windows 10/11 上开发；测试套件也可在 Linux（CI）上运行。

### 构建独立可执行文件

```bash
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7.spec
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7_cli.spec
```

会产出两个变体：`dist/A2L_Update_Tool_V2.7/`（图形界面版）与
`dist/A2L_Update_Tool_V2.7_cli/`（同一工具的控制台版本，用于无界面运行与 CI）。
之所以提供控制台版本，是因为窗口程序没有可用的 stdout，用它跑 `--headless` 将完全没有输出。

第 5 节介绍了两个构建的运行方式、打包内容，以及源码改动后如何重新构建。

## 4. 使用方法

### 图形界面

1. 选择 **A2L 头文件**（即当前正在使用的描述文件）。
2. 选择 **地址文件**（由*同一份*固件编译出的 `.axf` / `.elf`）。
   *必须使用同一次构建——地址文件不匹配会悄无声息地让所有地址整体偏移。*
3. 可选：选择**信号参数 XLSX** 以控制输出哪些信号，并选择**上一版 A2L** 以启用版本差异报告。
4. 点击 **⚡ Update A2L**。

### 无界面运行（CI / 科研 / 复现）

```bash
py A2L_Update_Tool_main.py --headless \
    --a2l  data/sample/example.a2l \
    --addr data/sample/example.map \
    --out  out/updated.a2l \
    --report
```

| 参数 | 含义 |
|---|---|
| `--a2l PATH` | A2L 头文件（必填） |
| `--addr PATH` | 地址文件（必填） |
| `--out PATH` | 输出 A2L（默认 `<a2l>_updated.a2l`） |
| `--signals PATH` | 控制输出信号的信号参数 XLSX |
| `--prev-a2l PATH` | 上一版 A2L，作为版本差异报告的比对基线 |
| `--reference-a2l PATH` | INCA 工程所用的 A2L，用于地址交叉校验 |
| `--cal-ranges` / `--meas-ranges` | 地址区间，例如 `'0x1000-0x2000;0x3000-0x3100'` |
| `--insert-line N` | 插入到第 N 行（1 起算）；0 表示自动探测 |
| `--report` | 在输出文件旁写出逐信号匹配报告 |
| `--diff-report` | 写出版本差异报告（需配合 `--prev-a2l`） |
| `--repair-header` | 允许改写原始头文件（默认保持原样） |
| `--no-expand-struct-array` | 将结构体数组按原始字节流输出 |
| `--semantic-align` | 启用 A2L-RAGAlign：为未匹配信号给出语义候选，并在匹配报告中增加增量一致性检查一节 |
| `--language en\|zh` | 报告与消息的语言 |
| `--quiet` | 抑制进度输出 |

退出码：`0` 成功，`1` 更新失败，`2` 参数错误 / 输入缺失，`3` 无内容可写。

> 在 Windows 上控制台会自动切换到 UTF-8，因此进度日志中的 emoji 不会导致运行中断。

### 示例数据

`data/sample/` 随附一套**合成**的 A2L 头文件与 GCC-ld 风格的 MAP 文件，使演示与 CI 开箱即用：

```bash
py A2L_Update_Tool_main.py --headless \
    --a2l  data/sample/example.a2l \
    --addr data/sample/example.map \
    --out  out/updated.a2l --report
```

第 1 节测量所用的量产地址文件是真实的 ECU 构建产物，包含专有符号名与地址布局，因此**不在**本仓库中分发。请将工具指向你自己的 AXF/ELF 或 MAP 文件，即可在自己的构建上复现同样的数字。

## 5. 运行打包好的可执行文件

使用 PyInstaller 产出两个独立构建，因此无需安装 Python 即可分发与使用：

| 产物 | 入口 | 用途 |
|---|---|---|
| `dist/A2L_Update_Tool_V2.7/` | `A2L_Update_Tool_V2.7.exe` | 图形界面，窗口程序，双击即运行 |
| `dist/A2L_Update_Tool_V2.7_cli/` | `A2L_Update_Tool_V2.7_cli.exe` | 同一工具的控制台版本，用于无界面运行与 CI |

### 5.1 图形界面版本

双击 `dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe`，或使用仓库根目录下的启动脚本（以最小化、隐藏窗口方式启动）：

```
A2L_Update_Tool_V2.7.bat        （或 A2L_Update_Tool_V2.7.cmd）
```

当可执行文件不存在时，启动脚本会回退到 `py A2L_Update_Tool_main.py --autorun --hidden`，因此同一个入口在源码检出下同样可用。

排查问题时可用 `A2L_Update_Tool_V2.7_Debug.bat`：它以**前台**方式启动同一个工具（而非最小化），保留控制台、打印退出码并等待按键，启动诊断信息可直接阅读。

操作流程与第 4 节一致：选择 **A2L 头文件**、**地址文件**，可选的**信号 XLSX** 与**上一版 A2L**，然后点击 **⚡ Update A2L**。

### 5.2 控制台版本

```bat
dist\A2L_Update_Tool_V2.7_cli\A2L_Update_Tool_V2.7_cli.exe --headless ^
    --a2l  data\sample\example.a2l ^
    --addr data\sample\example.map ^
    --out  out\updated.a2l ^
    --report
```

命令行与 `py A2L_Update_Tool_main.py --headless` 完全一致；完整参数表见第 4 节，退出码为 `0` 成功、`1` 更新失败、`2` 参数错误、`3` 无内容可写。

> **脚本化运行请使用 `_cli` 版本。** 窗口程序没有可用的 stdout，用图形界面版本跑 `--headless` 将完全没有输出。

### 5.3 打包内容中包含什么、不包含什么

`sentence-transformers` **不**打包进发行版：它会把整个科学计算栈（torch，约 2 GB）拖进来。因此打包版本使用零依赖的字符 n-gram 检索器，这正是论文中已发表数字所采用的配置（`RAG_ALIGN_RETRIEVER=charngram`）。若需要语义向量嵌入，请从源码运行并自行安装 `sentence-transformers`——该模块在缺少此依赖时会自动回退到词法检索器。

### 5.4 重新构建可执行文件

```bash
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7.spec
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7_cli.spec
```

PyInstaller 会把中间文件写入 `build/`，该目录不属于本仓库；只有 `dist/` 会被分发。修改源码后请重新构建，以保持可执行文件与源码一致。

## 6. 架构

```
A2L_Update_Tool_main.py     GUI + 更新流水线（约 7.5k 行）
├── i18n.py                 翻译表（以英文为唯一事实来源，436 条）
├── rag_align.py            检索增强的语义对齐器（A2L-RAGAlign）
├── rag_bridge.py           轻量适配器：更新流水线 <-> rag_align
├── experiments/
│   ├── run_experiments.py  RQ1–RQ4 评测框架（带随机种子）
│   └── make_figures.py     论文图生成
├── paper/                  LaTeX 源文件（main.tex、tables/、figures/）
├── data/sample/            演示与 CI 使用的合成 A2L 头文件与 MAP
└── test_*.py               pytest 测试套件（360 个用例）
```

关键数据流：

```
地址文件 ──► ELF/DWARF 解析器 ──► 符号表 + 类型模型
                                     │
A2L 头文件 ──► 已有对象名 ─────────────┤
                                     ▼
              信号集合 ──► 精确匹配 ──► (未命中) ──► RAG 语义回退
                                     │                        │
                                     └──────────┬─────────────┘
                                                ▼
                                     A2L 写出 + 证据链
                                                ▼
                           匹配报告 / 版本差异报告
```

## 7. A2L-RAGAlign（检索增强的语义对齐）

项目源码中的信号名与地址文件符号表之间的精确字符串匹配，在两侧不一致时必然失效——改名、命名前后缀约定变化，或符号被截断（在参考数据集上有 8 %）。A2L-RAGAlign 提供一条检索增强的回退路径：

* 从项目源码编译出**领域知识库**（信号描述、单位、I/O 方向、C 类型、分组）；
* 对输入的信号名做嵌入，并在知识库中用混合的词法 + 稠密检索器匹配；
* 匹配结果按**标定过的阈值**分层为 `AUTO` / `SUGGEST` / `REJECT`，因此错误猜测绝不会被静默采纳。

同一套机制还提供针对已有 A2L 更新的**增量一致性检查**（重名、数据类型漂移、孤儿对象、候选改名）。

### 如何接入工具

`rag_bridge.py` 是更新流水线与 `rag_align` 之间的适配器。它是一个**软依赖**：如果检索模块缺失或抛出异常，更新流程会按原有行为继续，而不是整体失败。

| 位置 | 检索阶段的作用 |
|---|---|
| 未匹配信号 | 在匹配报告中，于确定性的词法候选旁追加基于检索的候选，每个都标注 `AUTO` / `SUGGEST` 及其分数 |
| 匹配报告第 5 节 | 追加增量一致性检查一节，把**实际写出**的对象与已有 A2L 头文件的对象集合进行比对 |

两点是有意为之：

* **它绝不改动输出。** 带与不带 `--semantic-align` 生成的 A2L 逐字节相同，只有报告会变。候选仅供人工复核，绝不会被自动采纳。
* **它针对写出的对象集合做检查。** 若去比对内部信号列表，正常的结构体数组合并会被误报成大量重名——因为展开后的成员共享基符号的 `Name`，而 A2L 对象名来自 `ShortName`。

完整方法、RQ1–RQ4 评测、安全性分析以及下游测量结果案例研究，见 [`paper/main.tex`](paper/main.tex)。论文中每一张表的复现方法见第 8 节。

## 8. 复现论文中的表格

论文中的每一个数字、每一张表都来自两个带随机种子的脚本加上一次 LaTeX 编译，没有任何一处是手工誊抄的。

### 8.1 一键复现

```bash
py -3.12 -m pip install -r requirements.txt

# 1. 评测框架 -> results.json + 全部七张 LaTeX 结果表
py -3.12 experiments/run_experiments.py

# 2. 守卫：任何已发表数字发生偏移即失败退出
py -3.12 check_results.py

# 3. 摘要与"问题规模"一节引用的量产规模统计
py -3.12 experiments/production_axf_stats.py

# 4. 编译论文本身
cd paper && mkdir _build && _tools\tectonic.exe -X compile main.tex --outdir _build
```

第 1 步是完全确定性的：框架固定 `SEED = 20261011`，并强制 `RAG_ALIGN_RETRIEVER=charngram`，因此已发表数字出自字符 n-gram 检索器，而非可选的稠密检索器。第 2 步断言已发表的数值（Top-1 99.0 %、AUTO 覆盖率 93.6 %、AUTO 精确率 99.3 %、单位补全准确率 97.8 %，以及量产规模的 31,316 / 28,812 / 2,504），一旦发生偏移即以非零码退出。CI 运行的正是这两条命令。

### 8.2 哪个脚本产出哪张表

| 论文表格 | 文件 | 产出方式 |
|---|---|---|
| 表 I — 错误分类 | `paper/tables/tab_contract.tex` | 手工编写（分析性分类，无实测数据）——评测框架不会改动它 |
| 表 II — 测试集准确率 | `paper/tables/tab_alignment.tex` | `run_experiments.py` 的 RQ1 段 |
| 表 III — 自动接受阈值下的安全性 | `paper/tables/tab_safety.tex` | `run_experiments.py` 的安全段（易混淆对 + 干扰项） |
| 消融实验 | `paper/tables/tab_ablation.tex` | `run_experiments.py` 的 RQ2 段 |
| 置信度分层 | `paper/tables/tab_tiers.tex` | `run_experiments.py` 的 RQ3 段 |
| 下游任务 | `paper/tables/tab_downstream.tex` | `run_experiments.py` 的 RQ4 段 |
| 鲁棒性 | `paper/tables/tab_robustness.tex` | `run_experiments.py`，10 组随机校准/测试划分 |
| 案例研究 | `paper/tables/tab_casestudy.tex` | `run_experiments.py`，逐通道证据导出 |

框架写出的七张表在每次运行时都会被覆盖；它们背后的原始指标保存在 `experiments/results/results.json` 中，这是论文引用的每一个数字的唯一事实来源。

表 I 是唯一不基于测量的表：它是一份漂移后的测量契约的解析性错误分类，需要手工维护，因为它描述的是失效的*类别*而非测量结果。

### 8.3 图

| 图 | 文件 | 重建方式 |
|---|---|---|
| 图 1 — 框架总览 | `paper/figures/overview.pdf` | `py -3.12 experiments/make_figures.py`（matplotlib） |
| 图 2 — 手工流程与工具流程对比 | `paper/figures/fig_workflow_compare.pdf` | `py -3.12 paper/figures/build_workflow_figure.py`（TikZ） |
| 图 3 — 工程走查 | `paper/figures/fig_signal_conversion.pdf` | `py -3.12 paper/figures/build_signal_conversion_figure.py`（TikZ） |

`experiments/make_figures.py` 还会额外写出 `results.pdf` 与 `thresholds.pdf`（逐变换准确率与阈值扫描），作为上述各表的补充材料。

有两道版式守卫保护插图——图被缩得过头时，即便 PDF 仍能编译，读者也已看不清：

```bash
py -3.12 -m pytest test_figure_overflow.py   # 图 1：标签溢出、最小字号、无 Type 3 字体
py -3.12 paper/figures/check_signal_conversion_figure.py   # 图 3：页面尺寸与字号集合
```

`check_figure_overflow.py`（第一条命令所调用的）从 `paper/figures/overview_boxes.json`
读取实测的框体几何数据。

## 9. 测试

```bash
py -m pytest -q
# 360 passed
```

| 文件 | 覆盖内容 |
|---|---|
| `test_a2l_update.py` | 更新流水线：匹配、类型、数组、枚举/结构体还原 |
| `test_a2l_header_preserved.py` | 原始 A2L 头文件按字节原样保留 |
| `test_a2l_header_compat.py` | 头文件兼容性检查与可选修复 |
| `test_a2l_struct_array.py` | 结构体数组合并展开，含嵌套数组 |
| `test_a2l_reports.py` | 匹配报告、版本差异报告、地址区间导出 |
| `test_i18n.py` | 翻译表完整性、占位符一致性、运行时切换 |
| `test_rag_align.py` | 检索对齐器、分层与阈值 |
| `test_rag_bridge.py` | 适配器：软依赖、逐候选分层、对象命名规则 |
| `test_rag_k2.py` | K2 历史配置来源 |
| `test_rag_vector_store.py` | 稠密检索器所用的向量存储 |
| `test_cli.py` | 无界面命令行：参数处理、退出码、端到端生成 |
| `test_figure_overflow.py` | 图 1 的排版约束（文字越框、最小字号、无 Type 3 字体） |

## 10. 引用

若在学术工作中使用本工具，请引用配套论文：

```bibtex
@inproceedings{a2l_ragalign,
  title     = {A2L-RAGAlign: Retrieval-Augmented Semantic Alignment for
               Automotive ECU Calibration Description Updates},
  booktitle = {Proc. IEEE International Symposium on Computer Architecture
               and Design (ISCAS)},
  year      = {2027},
  note      = {Preprint: paper/main.tex in this repository}
}
```

## 11. 许可证

MIT —— 见 [LICENSE](LICENSE)。

`data/sample/` 下的示例数据为合成数据，同样适用 MIT 许可证。你自行提供的地址文件仍受其自身条款约束。

## 12. 项目治理

| 文件 | 用途 |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 环境搭建、基本规则，以及如何保持 i18n 翻译表完整 |
| [`CITATION.cff`](CITATION.cff) | GitHub 引用元数据 |
| [`README.md`](README.md) | 本文档的英文版 |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | Windows + Linux 上的 pytest、带随机种子的实验框架，以及对已发表数字的守卫 |

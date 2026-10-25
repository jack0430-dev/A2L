# A2L-RAGAlign

**Automated regeneration of ASAM MCD-2A A2L description files from binary
address files (AXF / ELF / MAP), with retrieval-augmented semantic alignment
for calibration & measurement description updates.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-green)
![Tests](https://img.shields.io/badge/tests-360%20passed-brightgreen)
![CI](https://github.com/jack0430-dev/A2L-RAGAlign_2026/actions/workflows/ci.yml/badge.svg)

> **Language.** This file is the English version. The Chinese translation lives
> in [`README-chinese.md`](README-chinese.md).

---

## 1. Why this tool exists

Automotive ECUs are calibrated with tools such as **INCA** / **CANape** that
consume an **A2L** description file. When the ECU firmware is rebuilt, the
symbols, addresses, types and enumerations in the A2L go stale. Regenerating
it by hand is slow, error-prone, and — critically — **silent failures are
common**: a renamed variable, a changed data type, or a wrong address file
version produces an A2L that still *parses* but reports garbage to the
calibration engineer.

This tool regenerates the A2L from the address file and, for every signal,
emits **evidence** (where the address came from) so that failures are visible
rather than silent.

### Measured problem size

On the production firmware image released with this repository
(`data/TcuBsw.axf`, ELF32/ARM with DWARF debug information, 6.3 MB):

| Metric | Value |
|---|---|
| Signals written successfully | 28,812 |
| Signals that failed exact name matching | 2,504 (**8.00 %**) |
| Output | 431,860 A2L lines / 17.4 MB |

These numbers are reproducible from a clean checkout with the released image:

```
python experiments/production_axf_stats.py
```

The script prints 31,316 DWARF-derived signals, 28,812 exact name matches and
2,504 failures (8.00 %), and writes
`experiments/results/production_axf_stats.json`.

> **Released firmware image.** `data/TcuBsw.axf` is the production transmission
> control unit image this work was carried out on (ELF32 / ARM, HighTec clang +
> LLD, DWARF debug information, SHA-256
> `e5af27f9d968993f35245146f10640ebd55cb48316cd9bf24bde39aa7dd54fc3`). It is
> distributed here so that the production-scale numbers in the paper are
> independently verifiable instead of asserted. The image contains the ECU's
> compiled code and symbol names only; the scan performed before release found
> no credentials, no source or build paths, no e-mail addresses and no
> vehicle-identifying data. `data/TcuBsw.hex` (the plain flash image) and all
> other build artifacts stay out of the repository.

The 8 % failure rate is the quantitative motivation for the retrieval-augmented
alignment described in [§7](#7-a2l-ragalign-retrieval-augmented-semantic-alignment).

## 2. Features

| Area | Capability |
|---|---|
| **Input** | A2L header (`.a2l` / `.A2L` / `.txt`), address file (`.axf` / `.elf` / `.map` / `.bin`), optional signal-selection XLSX, optional previous A2L as diff baseline |
| **Type recovery** | Recovers calibration vs. measurement object types, enumerations, struct members and array shapes from DWARF (`.debug_info` / `.debug_abbrev`) |
| **Nested types** | Struct arrays and nested struct arrays (`Point pts[3]`, `Cfg tbl[2][4]`) are expanded per element instead of collapsing into one blob |
| **Output** | A2L with correct `MATRIX_DIM`, signed DWARF offsets, byte-addressed `ECU_ADDRESS` |
| **Reports** | Per-signal match report with evidence chains, version diff report, address-range export, provenance sidecar (`.log`) |
| **Safety** | Cross-checks the freshly generated A2L against the A2L currently used in the INCA project and warns on address drift; refuses to continue on a wrong-build address file |
| **Non-destructive** | The user's original A2L is preserved byte-for-byte (indentation, comments, encoding, line endings) unless *auto-repair* is explicitly enabled |
| **Headless** | Full pipeline available from the command line, so it can run in CI and be reproduced in a paper |
| **i18n** | English by default, Chinese available; switchable at runtime and persisted |

## 3. Installation

```bash
py -m pip install -r requirements.txt
py A2L_Update_Tool_main.py
```

Python 3.10 – 3.12. Developed on Windows 10/11; the test suite also runs on
Linux (CI).

### Build a standalone executable

```bash
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7.spec
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7_cli.spec
```

Two variants are produced: `dist/A2L_Update_Tool_V2.7/` (the GUI) and
`dist/A2L_Update_Tool_V2.7_cli/` (the same tool as a console program, for
headless runs and CI). The console variant exists because a windowed
executable has no usable stdout, so `--headless` would produce no output at
all.

§5 describes how to run both builds, what the bundle contains, and how to
rebuild it after a source change.

## 4. Usage

### GUI

1. Pick the **A2L header** (the description file currently in use).
2. Pick the **address file** (`.axf` / `.elf` built from the *same* firmware).
   *Use the same build — a mismatched address file silently shifts every address.*
3. Optionally pick a **signal parameter XLSX** to control which signals are
   emitted, and a **previous A2L** to enable the version diff report.
4. Click **⚡ Update A2L**.

### Headless (CI / research / reproduction)

```bash
py A2L_Update_Tool_main.py --headless \
    --a2l  data/sample/example.a2l \
    --addr data/sample/example.map \
    --out  out/updated.a2l \
    --report
```

| Option | Meaning |
|---|---|
| `--a2l PATH` | A2L header file (required) |
| `--addr PATH` | address file (required) |
| `--out PATH` | output A2L (default `<a2l>_updated.a2l`) |
| `--signals PATH` | signal parameter XLSX controlling which signals are emitted |
| `--prev-a2l PATH` | previous A2L, baseline of the version diff report |
| `--reference-a2l PATH` | A2L in use by the INCA project, for the address cross-check |
| `--cal-ranges` / `--meas-ranges` | address ranges, e.g. `'0x1000-0x2000;0x3000-0x3100'` |
| `--insert-line N` | insert at line N (1-based); 0 = auto-detect |
| `--report` | write the per-signal match report next to the output |
| `--diff-report` | write the version diff report (needs `--prev-a2l`) |
| `--repair-header` | allow rewriting the original header (default: keep it unchanged) |
| `--no-expand-struct-array` | emit struct arrays as a raw byte stream |
| `--semantic-align` | enable A2L-RAGAlign: semantic candidates for unmatched signals + the incremental-consistency section in the match report |
| `--language en\|zh` | language of the reports and messages |
| `--quiet` | suppress progress output |

Exit codes: `0` success, `1` update failed, `2` bad arguments / missing input,
`3` nothing to write.

> On Windows the console is switched to UTF-8 automatically, so the emoji in the
> progress log cannot break the run.

### Sample data

`data/sample/` ships a small **synthetic** A2L header and GCC-ld style MAP
file so that the demo and the CI run out of the box:

```bash
py A2L_Update_Tool_main.py --headless \
    --a2l  data/sample/example.a2l \
    --addr data/sample/example.map \
    --out  out/updated.a2l --report
```

The production address file used for the measurements in §1 is a real ECU
build. It contains proprietary symbol names and an address layout, so it is
**not** distributed with this repository. Point the tool at your own AXF/ELF
or MAP file to reproduce the same figures on your build.

## 5. Running the packaged executables

Two standalone builds are produced with PyInstaller, so the tool can be shipped
without a Python installation:

| Artefact | Entry point | Use |
|---|---|---|
| `dist/A2L_Update_Tool_V2.7/` | `A2L_Update_Tool_V2.7.exe` | GUI, windowed, double-click to run |
| `dist/A2L_Update_Tool_V2.7_cli/` | `A2L_Update_Tool_V2.7_cli.exe` | the same tool as a console program, for headless runs and CI |

### 5.1 GUI build

Double-click `dist\A2L_Update_Tool_V2.7\A2L_Update_Tool_V2.7.exe`, or use the
launcher in the repository root, which starts the tool minimised and hidden:

```
A2L_Update_Tool_V2.7.bat        (or A2L_Update_Tool_V2.7.cmd)
```

The launcher falls back to `py A2L_Update_Tool_main.py --autorun --hidden` when
the executable is absent, so the same entry point also works from a source
checkout.

For troubleshooting, `A2L_Update_Tool_V2.7_Debug.bat` starts the same tool in
the foreground instead of minimised: it keeps the console attached, prints the
exit code and waits for a key press, so the startup diagnostics are readable.

The workflow is the one of §4: pick the **A2L header**, the **address file**,
optionally a **signal XLSX** and a **previous A2L**, then press
**⚡ Update A2L**.

### 5.2 Console build

```bat
dist\A2L_Update_Tool_V2.7_cli\A2L_Update_Tool_V2.7_cli.exe --headless ^
    --a2l  data\sample\example.a2l ^
    --addr data\sample\example.map ^
    --out  out\updated.a2l ^
    --report
```

The command line is identical to `py A2L_Update_Tool_main.py --headless`; the
full option table is in §4, exit codes are `0` success, `1` update failed,
`2` bad arguments, `3` nothing to write.

> **Use the `_cli` variant for scripted runs.** A windowed executable has no
> usable stdout, so `--headless` would produce no output at all through the
> GUI build.

### 5.3 What the bundle contains — and what it does not

`sentence-transformers` is **not** bundled: it would drag the whole scientific
stack (torch, ~2 GB) into the distribution. The bundled build therefore uses
the zero-dependency character-n-gram retriever, which is the configuration the
published paper numbers were produced with (`RAG_ALIGN_RETRIEVER=charngram`).
To get semantic embeddings, run from source and install
`sentence-transformers` yourself — the module falls back to the lexical
retriever automatically when it is absent.

### 5.4 Rebuilding the executables

```bash
py -m pip install pyinstaller
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7.spec
py -m PyInstaller --noconfirm --clean A2L_Update_Tool_V2.7_cli.spec
```

PyInstaller writes its intermediate files to `build/`, which is not part of the
repository; only `dist/` is shipped. Rebuild after changing the sources, so
that the executable and the source stay in sync.

## 6. Architecture

```
A2L_Update_Tool_main.py     GUI + update pipeline (≈7.5k lines)
├── i18n.py                 translation table (English source of truth, 436 entries)
├── rag_align.py            retrieval-augmented semantic aligner (A2L-RAGAlign)
├── rag_bridge.py           thin adapter: update pipeline <-> rag_align
├── experiments/
│   ├── run_experiments.py  RQ1–RQ4 evaluation harness (seeded)
│   └── make_figures.py     paper figure generation
├── paper/                  LaTeX sources (main.tex, tables/, figures/)
├── data/sample/            synthetic A2L header + MAP used by the demo and CI
└── test_*.py               pytest suite (360 tests)
```

Key data flow:

```
address file ──► ELF/DWARF reader ──► symbol table + type model
                                        │
A2L header ──► existing object names ────┤
                                        ▼
                     signal set ──► exact match ──► (miss) ──► RAG semantic fallback
                                        │                            │
                                        └──────────┬─────────────────┘
                                                   ▼
                                        A2L writer + evidence chain
                                                   ▼
                              match report / version diff report
```

## 7. A2L-RAGAlign (retrieval-augmented semantic alignment)

Exact string matching between the project source's signal names and the address
file's symbol table fails whenever the two sides disagree — a rename, a
prefix/suffix convention change, or a truncated symbol (8 % of all signals on
the reference dataset). A2L-RAGAlign adds a retrieval-augmented fallback:

* a **domain knowledge base** (signal descriptions, units, I/O direction,
  C types, groups) is compiled from the project sources;
* an incoming signal name is embedded and matched against the KB with a
  hybrid lexical + dense retriever;
* matches are **stratified** into `AUTO` / `SUGGEST` / `REJECT` by calibrated
  thresholds, so a wrong guess is never applied silently.

The same machinery provides an **incremental-consistency check** (duplicate
names, data-type drift, orphaned objects, candidate renames) for updates
against an existing A2L.

### How it is wired into the tool

`rag_bridge.py` is the adapter between the update pipeline and `rag_align`.
It is a **soft dependency**: if the retrieval module is missing or raises,
the update continues with its original behaviour instead of failing.

| Where | What the retrieval stage does |
|---|---|
| Unmatched signals | Adds retrieval-based candidates next to the deterministic lexical ones in the match report, each labelled `AUTO` / `SUGGEST` with its score |
| Match report §5 | Adds the incremental-consistency section, comparing the objects **actually written** against the object set of the existing A2L header |

Two properties are deliberate:

* **It never mutates the output.** The generated A2L is byte-identical with
  and without `--semantic-align`; only the report changes. Candidates are
  for human review, never applied automatically.
* **It reports on the written object set.** Comparing the internal signal
  list would misreport a normal struct-array expansion as a mass of
  duplicate names, because the expanded members share the base symbol's
  `Name` while the A2L object name comes from `ShortName`.

See [`paper/main.tex`](paper/main.tex) for the full method, the RQ1–RQ4
evaluation, the safety analysis and the downstream measurement-results case
study. §8 describes how to regenerate every table of that paper.

## 8. Reproducing the paper's tables

Every number and every table in the paper comes from two seeded scripts plus
one LaTeX compilation. Nothing is transcribed by hand.

### 8.1 One-shot reproduction

```bash
py -3.12 -m pip install -r requirements.txt

# 1. evaluation harness -> results.json + all seven LaTeX result tables
py -3.12 experiments/run_experiments.py

# 2. guard: fails if any published number moved
py -3.12 check_results.py

# 3. production-scale statistics quoted in the abstract and in Sec. "Scale"
py -3.12 experiments/production_axf_stats.py

# 4. the paper itself
cd paper && mkdir _build && _tools\tectonic.exe -X compile main.tex --outdir _build
```

Step 1 is fully deterministic: the harness fixes `SEED = 20261011` and forces
`RAG_ALIGN_RETRIEVER=charngram`, so the character-n-gram retriever — not the
optional dense one — is what produced the published numbers. Step 2 asserts
the published values (Top-1 99.0 %, AUTO coverage 93.6 %, AUTO precision
99.3 %, unit completion 97.8 %, and the 31,316 / 28,812 / 2,504 production
counts) and exits non-zero on any drift. The CI runs exactly these commands.

### 8.2 Which script produces which table

| Paper table | File | Produced by |
|---|---|---|
| I — error taxonomy | `paper/tables/tab_contract.tex` | hand-written (analytical taxonomy, no measured data) — the harness does not touch it |
| II — test-split accuracy | `paper/tables/tab_alignment.tex` | `run_experiments.py`, RQ1 block |
| III — safety at the auto threshold | `paper/tables/tab_safety.tex` | `run_experiments.py`, safety block (confusable pairs + distractors) |
| ablation | `paper/tables/tab_ablation.tex` | `run_experiments.py`, RQ2 block |
| tiers | `paper/tables/tab_tiers.tex` | `run_experiments.py`, RQ3 block |
| downstream tasks | `paper/tables/tab_downstream.tex` | `run_experiments.py`, RQ4 block |
| robustness | `paper/tables/tab_robustness.tex` | `run_experiments.py`, 10 random calibration/test splits |
| case study | `paper/tables/tab_casestudy.tex` | `run_experiments.py`, per-channel evidence dump |

The seven harness-written tables are overwritten on every run; the raw metrics
behind all of them live in `experiments/results/results.json`, which is the
single source of truth for every number quoted in the paper.

Table I is the only table that is not measured: it is the analytical error
taxonomy of a drifted measurement contract, and it is maintained by hand
because it describes *categories* of failure rather than measurements.

### 8.3 Figures

| Figure | File | Rebuilt with |
|---|---|---|
| 1 — framework overview | `paper/figures/overview.pdf` | `py -3.12 experiments/make_figures.py` (matplotlib) |
| 2 — manual vs. tool workflow | `paper/figures/fig_workflow_compare.pdf` | `py -3.12 paper/figures/build_workflow_figure.py` (TikZ) |
| 3 — engineering walk-through | `paper/figures/fig_signal_conversion.pdf` | `py -3.12 paper/figures/build_signal_conversion_figure.py` (TikZ) |

`experiments/make_figures.py` additionally writes `results.pdf` and
`thresholds.pdf` (per-transformation accuracy and the threshold sweep) as
supplementary material for the tables above.

Two layout guards protect the figures, because an over-shrunk figure is
unreadable even when the PDF still compiles:

```bash
py -3.12 -m pytest test_figure_overflow.py   # Fig. 1: label overflow, min. font size, no Type 3
py -3.12 paper/figures/check_signal_conversion_figure.py   # Fig. 3: page size and type sizes
```

`check_figure_overflow.py` (used by the first) reads the measured box geometry
from `paper/figures/overview_boxes.json`.

## 9. Tests

```bash
py -m pytest -q
# 360 passed
```

| File | Covers |
|---|---|
| `test_a2l_update.py` | the update pipeline: matching, types, arrays, enum/struct recovery |
| `test_a2l_header_preserved.py` | the original A2L header is kept byte-for-byte |
| `test_a2l_header_compat.py` | header compatibility checks and optional repair |
| `test_a2l_struct_array.py` | struct-array expansion, including nested arrays |
| `test_a2l_reports.py` | match report, version diff report, address-range export |
| `test_i18n.py` | translation table completeness, placeholder consistency, runtime switching |
| `test_rag_align.py` | the retrieval aligner, tiering and thresholds |
| `test_rag_bridge.py` | the adapter: soft dependency, per-candidate tiering, object-name rules |
| `test_rag_k2.py` | the K2 historical-configuration source |
| `test_rag_vector_store.py` | the vector store used by the dense retriever |
| `test_cli.py` | the headless CLI: argument handling, exit codes, end-to-end generation |
| `test_figure_overflow.py` | the layout constraints of Fig. 1 (label overflow, minimum font size, no Type 3 fonts) |

## 10. Citation

If you use this tool in academic work, please cite the accompanying paper:

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

## 11. License

MIT — see [LICENSE](LICENSE).

The sample data under `data/sample/` is synthetic and is covered by the MIT
license. Any address file you supply yourself remains under your own terms.

## 12. Project governance

| File | Purpose |
|---|---|
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | how to set up, the ground rules, and how to keep the i18n table complete |
| [`CITATION.cff`](CITATION.cff) | citation metadata for GitHub |
| [`README-chinese.md`](README-chinese.md) | the Chinese translation of this document |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | pytest on Windows + Linux, the seeded experiment harness, and a guard on the published numbers |

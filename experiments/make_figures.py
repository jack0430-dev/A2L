"""
make_figures.py - generate the paper figures (matplotlib) and write vector
PDFs that paper/main.tex includes:

  paper/figures/overview.pdf    Fig. 1: system architecture
  paper/figures/results.pdf     Fig. 2: top-1 accuracy per transformation
  paper/figures/thresholds.pdf  Fig. 3: AUTO threshold sensitivity

The fourth figure (the workflow comparison, Fig. 4) is drawn in TikZ and
built from paper/figures/fig_workflow_compare.tex by
paper/figures/build_workflow_figure.py.

Usage: py -3.12 experiments/make_figures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIG_DIR = ROOT / "paper" / "figures"
RESULTS = HERE / "results" / "results.json"

# Fig. 1 canvas size (inches); also recorded in overview_boxes.json so that
# check_figure_overflow.py validates the labels against the real geometry.
FIG_W, FIG_H = 7.0, 3.2

plt.rcParams.update({
    "font.size": 9, "font.family": "serif",
    "axes.linewidth": 0.6, "text.usetex": False,
    # ISCAS 2027 requires all fonts to be embedded and Type 3 fonts to be
    # avoided.  Matplotlib defaults to pdf.fonttype=3, which emits Type 3
    # character procedures (not embedded font programs) and fails the check;
    # 42 embeds the glyphs as subsetted TrueType instead.
    "pdf.fonttype": 42,
})

C_INPUT = "#dce7f5"
C_KB = "#fde9d9"
C_CORE = "#e2f0d9"
C_OUT = "#f2dcdb"


def _box(ax, x, y, w, h, text, fc, fs=8.0, bold=False):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle="round,pad=0.02,rounding_size=0.06",
        fc=fc, ec="#404040", lw=0.7))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, fontweight="bold" if bold else "normal", linespacing=1.25)


def _arrow(ax, x1, y1, x2, y2, style="-|>", lw=0.9):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                                 mutation_scale=9, lw=lw, color="#404040"))


def make_overview() -> None:
    fig, ax = plt.subplots(figsize=(7.0, 3.2))
    # The axes must fill the whole canvas: matplotlib's default subplot
    # margins (left=0.125, right=0.9, ...) would shrink the data area to
    # ~77% of the figure, i.e. every box would be ~23% narrower than its
    # nominal size and the labels would no longer fit.
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
    ax.set_xlim(0.02, 9.98)
    ax.set_ylim(0.02, 3.58)
    ax.axis("off")

    # Box labels are deliberately short.  This figure is placed in a single
    # column, so the 7.0 in canvas is scaled by ~0.43 in the paper: a 13 pt
    # canvas size lands at ~5.6 pt, while the previous 8 pt landed at 3.2 pt
    # and was unreadable.  Long labels would either overflow their box or have
    # to be set below the legibility floor; the removed detail (what K1/K2/K3
    # hold, which two channels are fused) is spelled out in the caption and
    # in Sec. III-B.
    FS = 14.0

    # 行坐标: 顶 2.35-3.20 / 中 1.25-2.10 / 底 0.15-1.00
    # KB 箱体 0.90-2.60, 顶部留 y=2.77 走廊给 ELF->decomposition 箭头
    # ── 输入层 ──
    _box(ax, 0.15, 2.35, 1.55, 0.85, "ELF / AXF\n+ DWARF", C_INPUT, fs=FS)
    _box(ax, 0.15, 1.25, 1.55, 0.85, "xlsx\nconfig", C_INPUT, fs=FS)
    _box(ax, 0.15, 0.15, 1.55, 0.85, "A2L\nheader", C_INPUT, fs=FS)

    # ── 知识库层（下缘与 A2L 输入行对齐，使 A2L->KB 箭头为水平线） ──
    _box(ax, 2.15, 0.45, 1.85, 2.15,
         "Domain\nKnowledge\nBase\n(K1–K3)", C_KB, fs=FS, bold=True)

    # ── 核心层 ──
    _box(ax, 4.45, 2.35, 2.15, 0.85,
         "Name\ndecomposition", C_CORE, fs=FS)
    _box(ax, 4.45, 1.25, 2.15, 0.85,
         "Embedding\n+ retrieval", C_CORE, fs=FS)
    _box(ax, 4.45, 0.15, 2.15, 0.85,
         "Fusion\n(2 channels)", C_CORE, fs=FS)

    # ── 输出层（自下而上串联: 对齐 -> 补全 -> 一致性） ──
    _box(ax, 7.05, 2.35, 2.8, 0.85,
         "Consistency\nreport", C_OUT, fs=FS)
    _box(ax, 7.05, 1.25, 2.8, 0.85,
         "Metadata\ncompletion", C_OUT, fs=FS)
    _box(ax, 7.05, 0.15, 2.8, 0.85,
         "Alignment\nAUTO / SUGGEST /\nMANUAL", C_OUT, fs=FS)

    # ── 箭头（水平/垂直为主，无交叉、无穿越箱体） ──
    _arrow(ax, 1.7, 2.77, 4.45, 2.77)          # ELF/MAP -> decomposition (KB 顶走廊)
    _arrow(ax, 1.7, 1.68, 2.15, 1.68)          # xlsx -> KB (水平)
    _arrow(ax, 1.7, 0.575, 2.15, 0.575)        # A2L existing -> KB (水平, K2b)
    _arrow(ax, 4.0, 1.68, 4.45, 1.68)          # KB -> retrieval (水平)
    _arrow(ax, 5.5, 2.35, 5.5, 2.10)           # decomposition -> retrieval
    _arrow(ax, 5.5, 1.25, 5.5, 1.00)           # retrieval -> fusion
    _arrow(ax, 6.6, 0.575, 7.05, 0.575)        # fusion -> alignment (水平)
    _arrow(ax, 8.45, 1.00, 8.45, 1.25)         # alignment -> metadata completion
    _arrow(ax, 8.45, 2.10, 8.45, 2.35)         # metadata completion -> consistency

    ax.text(5.0, 3.32, "A2L-RAGAlign: retrieval-augmented semantic alignment pipeline",
            ha="center", fontsize=13, fontweight="bold")

    # ── 导出各框的真实 PDF 坐标，供 check_figure_overflow.py 校验文字未越框 ──
    fig.canvas.draw()
    dpi = fig.dpi

    def _pdf_rect(x, y, w, h):
        p0 = ax.transData.transform((x, y))
        p1 = ax.transData.transform((x + w, y + h))
        return [round(p0[0] / dpi * 72.0, 2),
                round(FIG_H * 72.0 - p1[1] / dpi * 72.0, 2),
                round(p1[0] / dpi * 72.0, 2),
                round(FIG_H * 72.0 - p0[1] / dpi * 72.0, 2)]

    boxes = {
        "ELF/AXF": (0.15, 2.35, 1.55, 0.85), "xlsx": (0.15, 1.25, 1.55, 0.85),
        "A2L header": (0.15, 0.15, 1.55, 0.85), "KB": (2.15, 0.45, 1.85, 2.15),
        "decomp": (4.45, 2.35, 2.15, 0.85), "retrieval": (4.45, 1.25, 2.15, 0.85),
        "fusion": (4.45, 0.15, 2.15, 0.85), "consistency": (7.05, 2.35, 2.8, 0.85),
        "metadata": (7.05, 1.25, 2.8, 0.85), "alignment": (7.05, 0.15, 2.8, 0.85),
    }
    meta = {
        "canvas_in": [FIG_W, FIG_H],
        "boxes": {k: _pdf_rect(*v) for k, v in boxes.items()},
        "title": _pdf_rect(0, 3.32 - 0.13, 10, 0.26),
    }
    (FIG_DIR / "overview_boxes.json").write_text(
        json.dumps(meta, indent=1), encoding="utf-8")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    # No bbox_inches="tight": with the larger labels the tight box widens the
    # canvas to ~7.8 in, which shrinks the figure more in the paper and cancels
    # the point of the bigger type.  A fixed 7.0 x 3.2 in canvas keeps the
    # single-column scale at ~0.43, i.e. 14 pt -> 6 pt.
    fig.savefig(FIG_DIR / "overview.pdf")
    fig.savefig(FIG_DIR / "overview.png", dpi=160)
    plt.close(fig)


def make_results(results: dict) -> None:
    bt = results["by_transform"]
    kinds = [k for k in
             ["lower", "camel", "abbrev", "expand", "suffix_drop",
              "struct_path", "io_token", "camel_expand", "camel_abbrev"] if k in bt]
    hard = set(results["meta"]["hard_transforms"])
    labels = [k.replace("_", "\n") for k in kinds]
    ours = [100 * bt[k]["ours_top1"] for k in kinds]
    lev = [100 * bt[k]["lev_top1"] for k in kinds]
    exact = [100 * bt[k]["exact_top1"] for k in kinds]

    x = range(len(kinds))
    w = 0.27
    fig, ax = plt.subplots(figsize=(7.0, 2.4))
    ax.bar([i - w for i in x], exact, w, label="Exact", color="#b0b0b0", edgecolor="#606060", lw=0.4)
    ax.bar(list(x), lev, w, label="Levenshtein", color="#f4b183", edgecolor="#606060", lw=0.4)
    ax.bar([i + w for i in x], ours, w, label="Ours", color="#7c6af7", edgecolor="#404040", lw=0.4)
    for i, (o, l) in enumerate(zip(ours, lev)):
        ax.text(i + w, o + 1.5, f"{o:.0f}", ha="center", fontsize=6.5)
        if l < o - 3:
            ax.text(i, l + 1.5, f"{l:.0f}", ha="center", fontsize=6.5, color="#a05000")
    for i, k in enumerate(kinds):
        if k in hard:
            ax.text(i, -14, "*", ha="center", fontsize=9)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=7.5)
    ax.set_ylabel("Top-1 accuracy (%)")
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.legend(ncol=3, fontsize=8, frameon=False, loc="upper center",
              bbox_to_anchor=(0.5, 1.18))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "results.pdf", bbox_inches="tight")
    fig.savefig(FIG_DIR / "results.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def make_threshold_sweep(results: dict) -> None:
    """阈值敏感性: AUTO 覆盖/精度 与 混淆对/负样本误报 随 tau_auto 变化（RQ3）。"""
    sw = results["rq3_threshold_sweep"]
    g = sw["grid"]
    tau = results["meta"]["calibration"]["tau_auto"]
    fig, ax = plt.subplots(figsize=(3.5, 2.5))
    ax.plot(g, [100 * x for x in sw["coverage"]], "-o", ms=2.5, lw=1.2,
            color="#7c6af7", label="Valid AUTO coverage")
    ax.plot(g, [100 * x for x in sw["precision"]], "-s", ms=2.5, lw=1.2,
            color="#2e8b57", label="Valid AUTO precision")
    ax.plot(g, [100 * x for x in sw["confusable_fp"]], "-^", ms=2.5, lw=1.2,
            color="#c0392b", label="Confusable false-AUTO")
    ax.plot(g, [100 * x for x in sw["distractor_fp"]], "-v", ms=2.5, lw=1.2,
            color="#e67e22", label="Distractor false-AUTO")
    ax.axvline(tau, color="#555555", lw=0.8, ls="--")
    ax.annotate(rf"$\tau_{{auto}}$={tau}", (tau, 55), fontsize=7.5,
                ha="right", rotation=90, va="center", color="#555555")
    ax.set_xlabel(r"$\tau_{auto}$")
    ax.set_ylabel("Rate (%)")
    ax.set_ylim(-3, 108)
    ax.legend(fontsize=6.2, frameon=False, loc="center right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "thresholds.pdf", bbox_inches="tight")
    fig.savefig(FIG_DIR / "thresholds.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    make_overview()
    make_results(results)
    make_threshold_sweep(results)
    print(f"figures written to {FIG_DIR}: overview.pdf, results.pdf, thresholds.pdf")

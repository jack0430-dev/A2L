# -*- coding: utf-8 -*-
r"""论文 Fig. 1（框架总览图）的排版约束测试。

Fig. 1 以单栏宽度插入正文，画布 7.0 x 3.2 in 在论文中被缩放到约 0.43，
因此图内字号必须足够大才可读；同时每个标签必须完整落在自己的框内，
否则审稿人看到的是越框的碎字。这两条用几何方式自动校验：

  1. 框的真实 PDF 坐标由 experiments/make_figures.py 导出到
     paper/figures/overview_boxes.json（由 matplotlib 的数据变换算出，
     不靠手工估算），这里用 fitz 量出实际文字外框逐个比对；
  2. 论文中的最终字号 = 画布字号 x 0.86\linewidth / 7.0 in，
     要求不低于 5.5 pt（ISCAS 印刷后可读的下限）；
  3. 画布 PDF 不得含 Type 3 字体（ISCAS 明文禁项）。
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "experiments"))

FIG_PDF = os.path.join(ROOT, "paper", "figures", "overview.pdf")
FIG_META = os.path.join(ROOT, "paper", "figures", "overview_boxes.json")

INSERT_WIDTH = 0.83      # \includegraphics[width=0.83\linewidth]
COLUMN_WIDTH_IN = 3.5   # IEEEtran conference, US Letter
MIN_BOX_PT = 5.5        # 框内标签的最终字号下限
MIN_TITLE_PT = 5.3      # 图标题的最终字号下限（标题只出现一次）


@pytest.fixture(scope="module")
def fig_meta():
    if not (os.path.isfile(FIG_PDF) and os.path.isfile(FIG_META)):
        pytest.skip("Fig. 1 未生成，请运行: py -3.12 experiments/make_figures.py")
    with open(FIG_META, encoding="utf-8") as fh:
        return json.load(fh)


class TestFig1Layout:
    def test_labels_stay_inside_their_boxes(self, fig_meta):
        """每个标签必须完整落在自己的框内。"""
        fitz = pytest.importorskip("fitz")
        from check_figure_overflow import main as check_overflow

        rc = check_overflow()
        assert rc == 0, "Fig. 1 有标签越出方框或画布，见上方 OVERFLOW 明细"

    def test_paper_font_size_is_readable(self, fig_meta):
        """缩到单栏后，图中文字仍须达到 5.5 pt。"""
        fitz = pytest.importorskip("fitz")
        doc = fitz.open(FIG_PDF)
        try:
            sizes = {round(s["size"], 2)
                     for b in doc[0].get_text("dict")["blocks"]
                     for l in b.get("lines", []) for s in l["spans"]}
        finally:
            doc.close()
        assert sizes, "Fig. 1 中未找到文字"

        fig_w = fig_meta["canvas_in"][0]
        scale = INSERT_WIDTH * COLUMN_WIDTH_IN / fig_w
        # The largest canvas size belongs to the box labels, the smallest to
        # the single figure title.
        box_pt = max(sizes) * scale
        title_pt = min(sizes) * scale
        assert box_pt >= MIN_BOX_PT, (
            f"Fig. 1 的框内标签缩到单栏后只有 {box_pt:.2f} pt "
            f"(要求 >= {MIN_BOX_PT} pt)，读者会看不清")
        assert title_pt >= MIN_TITLE_PT, (
            f"Fig. 1 的标题缩到单栏后只有 {title_pt:.2f} pt "
            f"(要求 >= {MIN_TITLE_PT} pt)")

    def test_no_type3_fonts(self):
        """matplotlib 默认会输出 Type 3 字体，ISCAS 明确禁止。"""
        fitz = pytest.importorskip("fitz")
        doc = fitz.open(FIG_PDF)
        try:
            for page in doc:
                for font in page.get_fonts(full=True):
                    assert font[2] != "Type3", (
                        f"Fig. 1 含 Type 3 字体 {font[3]}，ISCAS 禁止；"
                        "请确认 make_figures.py 仍设置 pdf.fonttype=42")
                    assert font[1] not in ("", "n/a"), (
                        f"Fig. 1 字体 {font[3]} 未嵌入")
        finally:
            doc.close()

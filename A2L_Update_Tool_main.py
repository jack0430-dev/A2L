"""
A2L Update Tool  V2.7  ww_20261024  –  PyQt5 上位机
功能：
  1. 导入 A2L 头文件（.a2l / .A2L / .txt）
    2. 导入地址文件（.map / .axf / .elf 等），自动梳理地址区间
    3. 优先从 AXF/ELF 的 DWARF 中识别标定量、观测量、枚举、结构体和数组
  4. 一键更新 A2L，成功/失败弹窗提示

V2.7 更新（国际化 / i18n）：
  - 界面文案全面英文化，英文为默认语言（缺译文自动回落，不会中英混排）。
  - 标题栏右上角（日期下方）新增语言下拉框，支持中文 / English 一键切换，
    切换后按钮、标签、提示、占位符、分组标题、Tab 标题、表格表头即时刷新。
  - 语言选择通过 QSettings 持久化，重启后沿用上次选择。
  - 报告 / 日志 / 弹窗等运行时输出文案同样随语言变化。
  - 开发者 docstring 中的中文保留不译。

V2.6 更新：
  - 嵌套类型修复：结构体中出现「结构体数组成员」「嵌套结构体数组」
    （如 Point pts[3]、Cfg tbl[2][4]）时，按元素逐项展开为独立成员，
    不再整块退化为单变量，观测/标定类型与地址偏移均正确。
  - A2L 生成修复：数组型成员按元素字节数推导维度并输出 MATRIX_DIM；
    标量型 struct_* 成员不再被误判为数组（消除幽灵 MATRIX_DIM 1 2）。
  - 负偏移修复：DW_OP_consts 有符号解析，位域跨容器时保留符号偏移，
    避免成员地址被静默截断为 0。

V2.6 修订（保持原始头文件）：
  - 默认不再改写用户提供的 A2L 头文件：不再重命名 MEMORY_SEGMENT、
    不再重排 XCP SEGMENT 序号 / PAG，也不再替换旧版 XCP PAGE token，
    原文件的缩进、注释、编码与换行符全部原样保留。
  - 兼容性检查结果只作为提示输出；如需自动改写，可在「输出设置」中
    勾选「自动修复头文件兼容性问题」。
"""

import sys, os, re, json, time, traceback
from collections import Counter
from datetime import datetime
from pathlib import Path

from i18n import tr, tf, set_language, get_language

# ── PyQt5 ────────────────────────────────────────────────────────────────────
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QPushButton, QLabel, QLineEdit, QTextEdit, QFileDialog,
    QMessageBox, QProgressBar, QSplitter, QFrame, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QStatusBar,
    QSizePolicy, QScrollArea, QGridLayout, QRadioButton, QAbstractItemView,
    QComboBox, QCheckBox, QProxyStyle, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect, QPointF
from PyQt5.QtGui import QFont, QColor, QTextCursor, QPen, QPainter

# ── openpyxl ─────────────────────────────────────────────────────────────────
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# ── pyelftools ────────────────────────────────────────────────────────────────
try:
    from elftools.elf.elffile import ELFFile
    HAS_ELF = True
except ImportError:
    HAS_ELF = False


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                              版本信息                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
APP_VERSION  = "V2.7"
APP_DATE     = "20261024"
APP_AUTHOR   = "wangwei"
APP_TITLE    = f"A2L_Update_Tool_{APP_VERSION}"
ENABLE_A2L_TEXT_PREVIEW = False
SIGNAL_CONFIG_FILENAME = "signal_keep_config.xlsx"
PATH_HISTORY_LIMIT = 12

# —— 报告相关常量（放在此处：信号打来源标记时需要用到）——
# 匹配报告 / 版本差异报告的明细上限（超出截断并在报告中注明）。
MATCH_REPORT_MAX_DETAIL = 60000
DIFF_REPORT_MAX_DETAIL = 60000

# 证据来源标签：既是报告里的统计口径，也是逐条证据链的标题。
EVID_ADDR_SYMBOL = "Address source: address file symbol table"
EVID_OFFSET_DWARF = "Offset source: member offset expanded from DWARF (AddrOffset column)"
EVID_OFFSET_XLSX = "Offset source: XLSX AddrOffset filled in manually"
EVID_OFFSET_MEMBERINFO = "Offset source: DWARF member offset (member info table)"
EVID_OFFSET_NONE = "Offset source: no member offset found (counted from base address)"
EVID_BIT_DWARF = "Bitfield source: member bitfield expanded from DWARF (BitOffset/BitSize columns)"
EVID_BIT_XLSX = "Bitfield source: XLSX BitOffset/BitSize filled in manually"
EVID_BIT_MEMBERINFO = "Bitfield source: DWARF member bitfield (member info table mask)"
EVID_TYPE_DWARF = "Type source: DWARF debug information"
EVID_TYPE_SYMBOL = "Type source: AXF/ELF symbol table (no DWARF type information)"
EVID_SUPP_XLSX = "Supplement columns: merged from the signal parameter XLSX"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                              样式常量                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
DARK_BG   = "#1e1e2e"
PANEL_BG  = "#2a2a3e"
ACCENT    = "#7c6af7"
ACCENT2   = "#56cfb2"
BTN_GREEN = "#27ae60"
BTN_RED   = "#e74c3c"
TEXT_CLR  = "#cdd6f4"
BORDER    = "#44475a"

STYLE_SHEET = f"""
QMainWindow, QWidget {{
    background-color: {DARK_BG};
    color: {TEXT_CLR};
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 6px;
    background: {PANEL_BG};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    color: {ACCENT};
    font-weight: bold;
    font-size: 13px;
}}
QLineEdit {{
    background: #12121f;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 8px;
    color: {TEXT_CLR};
}}
QLineEdit:focus {{
    border: 1px solid {ACCENT};
}}
QPushButton {{
    background: {ACCENT};
    color: #ffffff;
    border: none;
    border-radius: 5px;
    padding: 6px 16px;
    font-weight: bold;
}}
QPushButton:hover {{
    background: #9b8dff;
}}
QPushButton:pressed {{
    background: #5a4ec0;
}}
QPushButton#btn_update {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {BTN_GREEN}, stop:1 #2ecc71);
    font-size: 15px;
    padding: 10px 32px;
    border-radius: 8px;
}}
QPushButton#btn_update:hover {{
    background: #2ecc71;
}}
QPushButton#btn_update:disabled {{
    background: #444;
    color: #888;
}}
QPushButton#btn_gen_demo {{
    background: {ACCENT2};
    color: #12121f;
    font-weight: bold;
}}
QPushButton#btn_gen_demo:hover {{
    background: #7adfcc;
}}
QPushButton#btn_clear {{
    background: #555577;
    color: {TEXT_CLR};
}}
QPushButton#btn_clear:hover {{
    background: #7777aa;
}}
QTextEdit {{
    background: #12121f;
    border: 1px solid {BORDER};
    border-radius: 4px;
    color: {TEXT_CLR};
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
}}
QTableWidget {{
    background: #12121f;
    gridline-color: {BORDER};
    color: {TEXT_CLR};
    border: 1px solid {BORDER};
    border-radius: 4px;
}}
QTableWidget::item:selected {{
    background: {ACCENT};
    color: #fff;
}}
QHeaderView::section {{
    background: {PANEL_BG};
    color: {ACCENT};
    border: 1px solid {BORDER};
    padding: 4px;
    font-weight: bold;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {PANEL_BG};
}}
QTabBar::tab {{
    background: {PANEL_BG};
    color: {TEXT_CLR};
    padding: 6px 18px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-radius: 4px 4px 0 0;
}}
QTabBar::tab:selected {{
    background: {ACCENT};
    color: #fff;
}}
QProgressBar {{
    border: 1px solid {BORDER};
    border-radius: 4px;
    background: #12121f;
    text-align: center;
    color: {TEXT_CLR};
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 {ACCENT2});
    border-radius: 3px;
}}
QStatusBar {{
    background: {PANEL_BG};
    color: {ACCENT2};
    border-top: 1px solid {BORDER};
}}
QScrollBar:vertical {{
    background: {PANEL_BG};
    width: 8px;
}}
QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 4px;
    min-height: 20px;
}}
QScrollBar:horizontal {{
    background: {PANEL_BG};
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 4px;
    min-width: 20px;
}}
QSplitter::handle {{
    background: {BORDER};
}}
QCheckBox {{
    spacing: 8px;
    color: {TEXT_CLR};
    background: transparent;
}}
QCheckBox:hover {{
    color: #ffffff;
}}
QCheckBox:disabled {{
    color: #6f6f86;
}}
"""


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                  高对比度勾选框绘制（深色主题下清晰可见）                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
# 背景问题：QSS 未定义 ::indicator，Fusion 默认指示器是浅灰描边 + 浅灰对勾，
# 落在深色面板（#2a2a3e）以及表格的深蓝/深绿行底色上几乎不可见。
# 解决：QCheckBox 走 QProxyStyle 重绘 PE_IndicatorCheckBox；
#       表格 Keep 列的勾选框由 item delegate 绘制（表格项不走 QSS）。
CHK_FILL_ON    = QColor(ACCENT)        # 勾选态填充
CHK_FILL_ON_H  = QColor("#8f80ff")     # 勾选态 + 悬停
CHK_EDGE_ON    = QColor("#b3a6ff")     # 勾选态描边
CHK_FILL_OFF   = QColor("#10101c")     # 未勾选填充
CHK_EDGE_OFF   = QColor("#9aa8c7")     # 未勾选描边
CHK_EDGE_OFF_H = QColor(ACCENT2)      # 未勾选 + 悬停
CHK_MARK       = QColor("#ffffff")     # 对勾
CHK_DIM        = QColor("#5a5a70")     # 禁用态


def _paint_check_indicator(painter: "QPainter", rect: "QRect", checked: bool,
                           enabled: bool = True, hovered: bool = False) -> None:
    """绘制高对比度勾选框：圆角方框 + 白色粗对勾（面板与表格共用）。"""
    if rect.width() <= 0 or rect.height() <= 0:
        return
    # 保持正方形并居中
    if rect.width() > rect.height():
        s = rect.height()
        rect = QRect(rect.x() + (rect.width() - s) // 2, rect.y(), s, s)
    else:
        s = rect.width()
        rect = QRect(rect.x(), rect.y() + (rect.height() - s) // 2, s, s)

    if not enabled:
        fill, edge, mark = CHK_FILL_OFF, CHK_DIM, CHK_DIM
    elif checked:
        fill = CHK_FILL_ON_H if hovered else CHK_FILL_ON
        edge, mark = CHK_EDGE_ON, CHK_MARK
    else:
        fill = QColor("#191927")
        edge = CHK_EDGE_OFF_H if hovered else CHK_EDGE_OFF
        mark = CHK_MARK

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(edge, 2))
    painter.setBrush(fill)
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)

    if checked:
        pen = QPen(mark, max(2.0, rect.width() * 0.15))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        x0 = rect.left() + rect.width() * 0.26
        y0 = rect.top() + rect.height() * 0.52
        x1 = rect.left() + rect.width() * 0.43
        y1 = rect.top() + rect.height() * 0.70
        x2 = rect.left() + rect.width() * 0.76
        y2 = rect.top() + rect.height() * 0.30
        painter.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    painter.restore()


class HighContrastCheckStyle(QProxyStyle):
    """让 QCheckBox 的指示器在深色主题下清晰可见。"""

    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PE_IndicatorCheckBox:
            state = option.state
            checked = bool(state & QStyle.State_On) or bool(state & QStyle.State_NoChange)
            rect = self.subElementRect(QStyle.SE_CheckBoxIndicator, option, widget)
            # QSS 里不写 ::indicator（否则 Qt 会接管绘制、绕过本代理样式），
            # 指示器尺寸在这里统一放大。
            side = max(16, rect.height())
            rect = QRect(rect.x() + (rect.width() - side) // 2,
                         rect.y() + (rect.height() - side) // 2, side, side)
            _paint_check_indicator(
                painter,
                rect,
                checked,
                bool(state & QStyle.State_Enabled),
                bool(state & QStyle.State_MouseOver),
            )
            return
        super().drawPrimitive(element, option, painter, widget)


class CheckIndicatorDelegate(QStyledItemDelegate):
    """表格 Keep 列的勾选框：表格项不走 QSS，需单独委托绘制。"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        # 抑制默认指示器绘制，避免与自绘叠加
        opt.features &= ~QStyleOptionViewItem.HasCheckIndicator
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        checked = index.data(Qt.CheckStateRole) == Qt.Checked
        cell = option.rect
        size = max(16, min(cell.height() - 6, 20))
        rect = QRect(cell.x() + (cell.width() - size) // 2,
                    cell.y() + (cell.height() - size) // 2, size, size)
        _paint_check_indicator(painter, rect, checked, True, False)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        Excel 列定义 & 数据类型映射                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
XLSX_COLS = [
    "Group", "Type", "Name", "ShortName", "LongName",
    "Unit", "Min", "Max", "Factor", "Offset",
    "DataType", "ObjectType", "BitMask", "ArraySize", "AddrOffset", "BitOffset", "BitSize", "Description"
]

SIGNAL_IO_CHECK_COL = "Checked"
SIGNAL_IO_KEEP_COL = "Keep"
SIGNAL_IO_HEADER_ALIASES = {SIGNAL_IO_KEEP_COL, SIGNAL_IO_CHECK_COL}
SIGNAL_CONFIG_HEADER_ALIASES = {
    "Unit": {"Unit", "unit", "UNIT", "uint", "UINT"},
}
SIGNAL_CORE_COLS = ["Group", "Type", "Name", "ShortName", "DataType", "ObjectType", "ArraySize"]
SIGNAL_CONFIG_USER_COLS = [
    "LongName", "Unit", "Min", "Max", "Factor", "Offset", "Description"
]
SIGNAL_CONFIG_DERIVED_COLS = ["BitMask", "AddrOffset", "BitOffset", "BitSize"]
SIGNAL_CONFIG_MERGE_COLS = SIGNAL_CONFIG_USER_COLS
SIGNAL_CONFIG_EDITABLE_COLS = [SIGNAL_IO_KEEP_COL] + SIGNAL_CONFIG_USER_COLS
SIGNAL_CONFIG_EXPORT_COLS = SIGNAL_CORE_COLS + SIGNAL_CONFIG_USER_COLS + SIGNAL_CONFIG_DERIVED_COLS
SIGNAL_PREVIEW_COLS = [
    "Group", "Type", "Name", "ShortName", "DataType",
    "Unit", "Min", "Max", "Factor", "Offset",
    "ObjectType", "ArraySize", "LongName", "Description",
    "BitMask", "AddrOffset", "BitOffset", "BitSize"
]
PREVIEW_READONLY_COLS = {
    "Group", "Type", "Name", "ShortName", "DataType",
    "ObjectType", "ArraySize", "AddrOffset", "BitOffset", "BitSize"
}
PREVIEW_EDITABLE_COLS = [col for col in XLSX_COLS if col not in PREVIEW_READONLY_COLS]
ADDRESS_RANGE_XLSX_COLS = ["ObjectType", "Start", "End"]

# C 数据类型 → A2L DATATYPE（支持标准C/嵌入式固定宽度类型/枚举/数组/结构体成员）
C_DTYPE_MAP = {
    # uint8
    "uint8_t":  "UBYTE",  "uint8":   "UBYTE",  "UBYTE":  "UBYTE",
    "unsigned char": "UBYTE",
    # int8
    "int8_t":   "SBYTE",  "int8":    "SBYTE",  "SBYTE":  "SBYTE",
    "signed char":   "SBYTE",  "char": "SBYTE",
    # uint16
    "uint16_t": "UWORD",  "uint16":  "UWORD",  "UWORD":  "UWORD",
    "unsigned short": "UWORD",
    # int16
    "int16_t":  "SWORD",  "int16":   "SWORD",  "SWORD":  "SWORD",
    "short": "SWORD",  "signed short": "SWORD",
    # uint32
    "uint32_t": "ULONG",  "uint32":  "ULONG",  "ULONG":  "ULONG",
    "unsigned int":  "ULONG",  "unsigned long": "ULONG",
    # int32
    "int32_t":  "SLONG",  "int32":   "SLONG",  "SLONG":  "SLONG",
    "int": "SLONG",  "signed int": "SLONG",  "long": "SLONG",
    # uint64  → A2L 用 A_UINT64（部分工具支持）
    "uint64_t": "A_UINT64", "uint64": "A_UINT64", "A_UINT64": "A_UINT64",
    "unsigned long long": "A_UINT64",
    # int64
    "int64_t":  "A_INT64",  "int64":  "A_INT64",  "A_INT64": "A_INT64",
    "long long": "A_INT64",  "signed long long": "A_INT64",
    # float
    "float":    "FLOAT32_IEEE", "FLOAT32_IEEE": "FLOAT32_IEEE",
    # double
    "double":   "FLOAT64_IEEE", "FLOAT64_IEEE": "FLOAT64_IEEE",
    # bool
    "bool":     "UBYTE",  "_Bool": "UBYTE",
    # 枚举（enum）→ 底层按声明大小映射，默认 uint8_t → UBYTE
    "enum_u8":  "UBYTE",   "enum_u16": "UWORD",  "enum_u32": "ULONG",
    "enum":     "UBYTE",
    # 数组元素（array element）→ 按元素类型映射
    "array_u8": "UBYTE",   "array_i8":  "SBYTE",
    "array_u16":"UWORD",   "array_i16": "SWORD",
    "array_u32":"ULONG",   "array_i32": "SLONG",
    "array_f32":"FLOAT32_IEEE", "array_f64": "FLOAT64_IEEE",
    # 结构体成员（struct member）→ 按成员类型映射，用 struct_<type> 约定
    "struct_u8": "UBYTE",  "struct_i8":  "SBYTE",
    "struct_u16":"UWORD",  "struct_i16": "SWORD",
    "struct_u32":"ULONG",  "struct_i32": "SLONG",
    "struct_f32":"FLOAT32_IEEE", "struct_f64": "FLOAT64_IEEE",
}

# Excel DataType 列下拉可选项（按类别分组显示顺序）
DTYPE_DROPDOWN_OPTIONS = [
    # 基础整数
    "uint8_t", "int8_t", "uint16_t", "int16_t",
    "uint32_t", "int32_t", "uint64_t", "int64_t",
    # 浮点
    "float", "double",
    # 布尔
    "bool",
    # 枚举
    "enum_u8", "enum_u16", "enum_u32",
    # 数组元素
    "array_u8", "array_i8", "array_u16", "array_i16",
    "array_u32", "array_i32", "array_f32", "array_f64",
    # 结构体成员
    "struct_u8", "struct_i8", "struct_u16", "struct_i16",
    "struct_u32", "struct_i32", "struct_f32", "struct_f64",
]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          Excel 信号表解析                                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def parse_xlsx(path: str) -> list:
    """解析 signal_demo.xlsx，返回信号字典列表。

    支持单 Sheet 或多个 Sheet 的信号表。忽略以 "_" 开头的隐藏辅助 Sheet。
    """
    if not HAS_OPENPYXL:
        raise RuntimeError(
            tr("openpyxl is not installed, cannot read xlsx files!\nPlease run in the terminal:  py -m pip install openpyxl")
        )
    wb = openpyxl.load_workbook(path, data_only=True)
    signals = []

    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue

        header_map = {}
        for row in ws.iter_rows(values_only=True):
            if not row or row[0] is None:
                continue

            first = str(row[0]).strip()
            if first == "Group" and len(row) > 1 and row[1] is not None:
                for ci, h in enumerate(row):
                    if h in XLSX_COLS:
                        header_map[h] = ci
                continue

            if first.startswith(("▶", "[", "  ▶")):
                continue
            if not header_map:
                continue

            sig = {}
            for col in XLSX_COLS:
                ci = header_map.get(col)
                if ci is not None and ci < len(row) and row[ci] is not None:
                    sig[col] = str(row[ci]).strip()
                else:
                    sig[col] = ""
            if sig.get("Name"):
                # 旧 Excel 信号源里 AddrOffset/BitOffset/BitSize 可能是人工填的，
                # 与 DWARF 展开出的同名列来源不同，报告里要区分开。
                if str(sig.get("AddrOffset") or "").strip():
                    sig["_offset_source"] = "xlsx"
                if str(sig.get("BitSize") or "").strip():
                    sig["_bit_source"] = "xlsx"
                signals.append(sig)

    return signals


def _parse_checked_cell(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in ("1", "true", "yes", "y", "checked", "check", "x", "v", "√")


def _format_keep_cell(value: bool) -> str:
    return "yes" if value else "no"


def _canonical_signal_config_header(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in SIGNAL_IO_HEADER_ALIASES or text in XLSX_COLS:
        return text
    for canonical, aliases in SIGNAL_CONFIG_HEADER_ALIASES.items():
        if text in aliases:
            return canonical
    lower_text = text.lower()
    if lower_text == SIGNAL_IO_KEEP_COL.lower():
        return SIGNAL_IO_KEEP_COL
    if lower_text == SIGNAL_IO_CHECK_COL.lower():
        return SIGNAL_IO_CHECK_COL
    return ""


def parse_signal_config_xlsx(path: str) -> list[dict]:
    """解析信号勾选与补充信息 xlsx。"""
    if not HAS_OPENPYXL:
        raise RuntimeError(
            tr("openpyxl is not installed, cannot read xlsx files!\n"
               "Please run in the terminal:  py -m pip install openpyxl")
        )
    wb = openpyxl.load_workbook(path, data_only=True)
    rows_out = []

    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue

        header_map = {}
        for row in ws.iter_rows(values_only=True):
            if not row:
                continue
            first = "" if row[0] is None else str(row[0]).strip()
            canonical_row = [_canonical_signal_config_header(cell) for cell in row]
            if any(cell in SIGNAL_IO_HEADER_ALIASES or cell in XLSX_COLS for cell in canonical_row):
                for ci, h in enumerate(row):
                    h_text = _canonical_signal_config_header(h)
                    if h_text in SIGNAL_IO_HEADER_ALIASES or h_text in XLSX_COLS:
                        header_map[h_text] = ci
                continue
            if first.startswith(("▶", "[", "  ▶")):
                continue
            if not header_map:
                continue

            sig = {col: "" for col in XLSX_COLS}
            for col in XLSX_COLS:
                ci = header_map.get(col)
                if ci is not None and ci < len(row) and row[ci] is not None:
                    sig[col] = str(row[ci]).strip()
            check_ci = header_map.get(SIGNAL_IO_KEEP_COL)
            if check_ci is None:
                check_ci = header_map.get(SIGNAL_IO_CHECK_COL)
            checked = False
            if check_ci is not None and check_ci < len(row):
                checked = _parse_checked_cell(row[check_ci])
            if sig.get("Name") or sig.get("ShortName"):
                sig[SIGNAL_IO_KEEP_COL] = checked
                rows_out.append(sig)

    return rows_out


def parse_address_ranges_xlsx(path: str) -> dict[str, list[tuple[int, int]]]:
    if not HAS_OPENPYXL:
        raise RuntimeError(
            tr("openpyxl is not installed, cannot read xlsx files!\n"
               "Please run in the terminal:  py -m pip install openpyxl")
        )
    wb = openpyxl.load_workbook(path, data_only=True)
    ranges = {"CALIBRATION": [], "MEASUREMENT": []}
    for ws in wb.worksheets:
        if ws.title.startswith("_"):
            continue
        header_map = {}
        for row in ws.iter_rows(values_only=True):
            if not row:
                continue
            first = "" if row[0] is None else str(row[0]).strip()
            if first == "ObjectType":
                for ci, h in enumerate(row):
                    h_text = str(h).strip() if h is not None else ""
                    if h_text in ADDRESS_RANGE_XLSX_COLS:
                        header_map[h_text] = ci
                continue
            if not header_map:
                continue
            otype = str(row[header_map.get("ObjectType", 0)] or "").strip().upper()
            if otype in ("CAL", "CALIBRATION"):
                otype = "CALIBRATION"
            elif otype in ("MEAS", "MEASUREMENT"):
                otype = "MEASUREMENT"
            else:
                continue
            start = _safe_int_auto(row[header_map.get("Start", 1)], None)
            end = _safe_int_auto(row[header_map.get("End", 2)], None)
            if start is None or end is None:
                continue
            if end < start:
                start, end = end, start
            ranges[otype].append((int(start), int(end)))
    ranges["CALIBRATION"] = _merge_ranges(ranges["CALIBRATION"])
    ranges["MEASUREMENT"] = _merge_ranges(ranges["MEASUREMENT"])
    return ranges


def parse_address_ranges_txt_config(text: str) -> dict[str, list[tuple[int, int]]]:
    ranges = {"CALIBRATION": [], "MEASUREMENT": []}
    current = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";", "//")):
            continue
        upper = line.upper()
        if upper in ("[CALIBRATION]", "CALIBRATION", "CALIBRATION:", "[CAL]", "CAL"):
            current = "CALIBRATION"
            continue
        if upper in ("[MEASUREMENT]", "MEASUREMENT", "MEASUREMENT:", "[MEAS]", "MEAS"):
            current = "MEASUREMENT"
            continue
        if "=" in line or ":" in line:
            sep = "=" if "=" in line else ":"
            head, tail = line.split(sep, 1)
            head_upper = head.strip().upper()
            if head_upper in ("CAL", "CALIBRATION"):
                current = "CALIBRATION"
                line = tail.strip()
            elif head_upper in ("MEAS", "MEASUREMENT"):
                current = "MEASUREMENT"
                line = tail.strip()
        if not line or current not in ranges:
            continue
        ranges[current].extend(parse_address_ranges_text(line))
    ranges["CALIBRATION"] = _merge_ranges(ranges["CALIBRATION"])
    ranges["MEASUREMENT"] = _merge_ranges(ranges["MEASUREMENT"])
    return ranges


def parse_address_ranges_txt_file(path: str) -> dict[str, list[tuple[int, int]]]:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return parse_address_ranges_txt_config(fh.read())


def format_address_ranges_txt(config: dict[str, list[tuple[int, int]]]) -> str:
    lines = ["[CALIBRATION]"]
    cal_ranges = list((config or {}).get("CALIBRATION") or [])
    meas_ranges = list((config or {}).get("MEASUREMENT") or [])
    if cal_ranges:
        lines.extend(f"0x{start:08X}-0x{end:08X}" for start, end in cal_ranges)
    lines.append("")
    lines.append("[MEASUREMENT]")
    if meas_ranges:
        lines.extend(f"0x{start:08X}-0x{end:08X}" for start, end in meas_ranges)
    lines.append("")
    return "\n".join(lines)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          地址文件解析                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def parse_map_file(path: str) -> dict:
    """
    解析 .map 文件，提取符号→(地址, 大小)映射。
    兼容 GCC ld / IAR / Keil / ArmCC 常见格式。
    返回: {symbol_name: (address, size)}
    """
    sym_map = {}
    # 模式1: GCC ld   => 0x0000000020001234   symbol_name
    pat_gcc  = re.compile(r'^\s+0x([0-9a-fA-F]{5,16})\s+(\w+)\s*$')
    # 模式2: Keil     => 0x20001234    symbol_name
    pat_keil = re.compile(r'\b0x([0-9a-fA-F]{6,8})\s+(\w+)')
    # 模式3: IAR      => symbol_name    0x20001234
    pat_iar  = re.compile(r'^(\w+)\s+0x([0-9a-fA-F]{4,16})\b')
    # 模式4: nm 输出  => 20001234 B symbol_name
    pat_nm   = re.compile(r'^([0-9a-fA-F]{8,16})\s+[A-Za-z]\s+(\w+)')
    
    # 首先收集所有符号的地址
    addr_map = {}
    with open(path, "r", errors="replace") as f:
        for line in f:
            line_s = line.rstrip()
            for pat, grp_addr, grp_name in [
                (pat_gcc,  1, 2),
                (pat_keil, 1, 2),
                (pat_iar,  2, 1),
                (pat_nm,   1, 2),
            ]:
                m = pat.search(line_s)
                if m:
                    try:
                        addr = int(m.group(grp_addr), 16)
                        name = m.group(grp_name)
                        if name and addr:
                            addr_map[name] = addr
                    except ValueError:
                        pass
                    break
    
    # 再次遍历文件，查找大小信息
    # GCC格式: addr1 addr2 size align section:symbol
    size_pattern = re.compile(r'^\s*([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+\d+\s+.*:(\w+)\s*$')
    
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = size_pattern.search(line)
            if m:
                addr_hex, size_hex, name = m.group(1), m.group(2), m.group(3)
                if name in addr_map:
                    try:
                        size = int(size_hex, 16)
                        sym_map[name] = (addr_map[name], size)
                    except ValueError:
                        pass
    
    # 对于没有找到大小信息的符号，假设大小为数据类型的默认大小
    for name, addr in addr_map.items():
        if name not in sym_map:
            sym_map[name] = (addr, 0)  # 0表示未知大小，需要从Excel中获取
    
    return sym_map


def parse_elf_axf(path: str) -> dict:
    """解析 ELF/AXF 文件提取符号地址（需要 pyelftools）。"""
    if not HAS_ELF:
        raise RuntimeError(
            tr("pyelftools is not installed, cannot parse ELF/AXF files!\nPlease run in the terminal:  py -m pip install pyelftools")
        )
    sym_map = {}
    with open(path, "rb") as f:
        elf = ELFFile(f)
        for section in elf.iter_sections():
            if section["sh_type"] in ("SHT_SYMTAB", "SHT_DYNSYM"):
                for sym in section.iter_symbols():
                    if sym.name and sym["st_value"]:
                        size = sym["st_size"] if sym["st_size"] else 0
                        sym_map[sym.name] = (sym["st_value"], size)
    return sym_map


def load_address_file(path: str) -> dict[str, tuple[int, int]]:
    """根据扩展名自动选择解析方式，兼容旧接口。"""
    details = load_address_file_details(path)
    return {
        name: (int(meta.get("address") or 0), int(meta.get("size") or 0))
        for name, meta in details.items()
    }


_DWARF_MEMBER_INFO_CACHE: dict[tuple[str, int, int], dict[str, dict[str, int | None]]] = {}
_ADDRESS_FILE_CACHE: dict[tuple[str, int, int], dict[str, dict[str, object]]] = {}


def _cache_key_for_file(path: str) -> tuple[str, int, int]:
    abs_path = os.path.abspath(path)
    stat = os.stat(abs_path)
    return abs_path, int(stat.st_mtime_ns), int(stat.st_size)


def _safe_int_auto(val, default=0):
    """支持十进制/0x 十六进制/浮点文本的安全整数解析。"""
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    try:
        return int(s, 0)
    except Exception:
        try:
            return int(float(s))
        except Exception:
            return default


def _decode_dwarf_name(attr) -> str:
    if not attr:
        return ""
    value = attr.value
    if isinstance(value, bytes):
        return value.decode(errors="ignore")
    return str(value)


def _resolve_type_die(die):
    """展开 typedef / const / volatile，拿到最终类型 DIE。"""
    seen = set()
    while die is not None and die.offset not in seen:
        seen.add(die.offset)
        if die.tag not in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type"):
            break
        if "DW_AT_type" not in die.attributes:
            break
        die = die.get_DIE_from_attribute("DW_AT_type")
    return die


def _get_dwarf_type_size_bytes(type_die) -> int:
    type_die = _resolve_type_die(type_die)
    if type_die is None:
        return 0
    size_attr = type_die.attributes.get("DW_AT_byte_size")
    if size_attr is not None:
        try:
            return int(size_attr.value)
        except Exception:
            pass
    if type_die.tag == "DW_TAG_pointer_type":
        return 4
    if "DW_AT_type" in type_die.attributes:
        return _get_dwarf_type_size_bytes(type_die.get_DIE_from_attribute("DW_AT_type"))
    return 0


def _read_uleb128(data: bytes, start: int = 0) -> tuple[int | None, int]:
    result = 0
    shift = 0
    pos = start
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if (byte & 0x80) == 0:
            return result, pos
        shift += 7
    return None, pos


def _parse_dwarf_member_location(attr) -> int | None:
    """解析 DW_AT_data_member_location，返回字节偏移。"""
    if not attr:
        return None
    value = attr.value
    if isinstance(value, int):
        return value
    if isinstance(value, (bytes, bytearray)):
        data = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            data = bytes(value)
        except Exception:
            return None
    else:
        return None

    if not data:
        return 0

    op = data[0]
    if op == 0x23:  # DW_OP_plus_uconst
        parsed, _ = _read_uleb128(data, 1)
        return parsed
    if op == 0x10:  # DW_OP_constu
        parsed, _ = _read_uleb128(data, 1)
        return parsed
    if op == 0x11:  # DW_OP_consts
        signed, _ = _read_sleb128(data, 1)
        return signed
    return None


def _read_sleb128(data: bytes, start: int = 0) -> tuple[int | None, int]:
    """读取有符号 LEB128（DW_OP_consts 可能是负数，如继承成员相对于派生基址）。"""
    result = 0
    shift = 0
    pos = start
    while pos < len(data):
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        shift += 7
        pos += 1
        if (byte & 0x80) == 0:
            if shift < 64 and (byte & 0x40):
                result -= 1 << shift
            return result, pos
    return None, pos


def _collect_dwarf_member_infos(type_die, base_name: str, base_offset: int,
                                out_map: dict[str, dict[str, int | None]], active_types: set[int]) -> None:
    type_die = _resolve_type_die(type_die)
    if type_die is None or type_die.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type"):
        return
    if type_die.offset in active_types:
        return

    active_types.add(type_die.offset)
    try:
        for child in type_die.iter_children():
            if child.tag != "DW_TAG_member":
                continue

            member_name = _decode_dwarf_name(child.attributes.get("DW_AT_name"))
            if not member_name:
                continue

            member_off = _parse_dwarf_member_location(child.attributes.get("DW_AT_data_member_location"))
            if member_off is None:
                member_off = 0
            total_offset = base_offset + member_off
            full_name = f"{base_name}.{member_name}"

            info: dict[str, int | None] = {
                "byte_offset": total_offset,
                "bit_offset": None,
                "bit_size": None,
                "bit_mask": None,
            }

            bit_size_attr = child.attributes.get("DW_AT_bit_size")
            if bit_size_attr is not None:
                bit_size = int(bit_size_attr.value)
                info["bit_size"] = bit_size

                data_bit_attr = child.attributes.get("DW_AT_data_bit_offset")
                if data_bit_attr is not None:
                    total_data_bits = base_offset * 8 + int(data_bit_attr.value)
                    info["byte_offset"] = total_data_bits // 8
                    info["bit_offset"] = total_data_bits % 8
                else:
                    bit_off_attr = child.attributes.get("DW_AT_bit_offset")
                    container_bits = _get_dwarf_type_size_bytes(
                        child.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in child.attributes else None
                    ) * 8
                    if container_bits <= 0:
                        container_bits = max(8, bit_size)
                    if bit_off_attr is not None:
                        lsb_shift = container_bits - int(bit_off_attr.value) - bit_size
                        # 同时返回字节偏移与容器内位偏移，避免负数被截断后地址静默错位。
                        if lsb_shift < 0:
                            info["byte_offset"] = total_offset + (lsb_shift // 8)
                            info["bit_offset"] = lsb_shift % 8
                        else:
                            info["bit_offset"] = lsb_shift
                    else:
                        info["bit_offset"] = 0

                if info["bit_offset"] is not None and bit_size > 0:
                    info["bit_mask"] = ((1 << bit_size) - 1) << int(info["bit_offset"])

            out_map[full_name] = info

            if "DW_AT_type" in child.attributes:
                child_type = child.get_DIE_from_attribute("DW_AT_type")
                _collect_dwarf_member_infos(child_type, full_name, int(info["byte_offset"] or 0), out_map, active_types)
    finally:
        active_types.remove(type_die.offset)


def load_dwarf_member_infos(path: str) -> dict[str, dict[str, int | None]]:
    """从 ELF/AXF 的 DWARF 中提取 `变量.成员` 的偏移/位域信息。"""
    if not HAS_ELF:
        return {}

    cache_key = _cache_key_for_file(path)
    abs_path = cache_key[0]
    cached = _DWARF_MEMBER_INFO_CACHE.get(cache_key)
    if cached is not None:
        return {name: dict(meta) for name, meta in cached.items()}

    member_map: dict[str, dict[str, int | None]] = {}
    try:
        with open(abs_path, "rb") as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                _DWARF_MEMBER_INFO_CACHE.clear()
                _DWARF_MEMBER_INFO_CACHE[cache_key] = member_map
                return member_map

            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                top = cu.get_top_DIE()
                for die in top.iter_children():
                    if die.tag != "DW_TAG_variable":
                        continue
                    var_name = _decode_dwarf_name(die.attributes.get("DW_AT_name"))
                    if not var_name or "DW_AT_type" not in die.attributes:
                        continue
                    type_die = die.get_DIE_from_attribute("DW_AT_type")
                    _collect_dwarf_member_infos(type_die, var_name, 0, member_map, set())
    except Exception:
        member_map = {}

    _DWARF_MEMBER_INFO_CACHE.clear()
    _DWARF_MEMBER_INFO_CACHE[cache_key] = {name: dict(meta) for name, meta in member_map.items()}
    return {name: dict(meta) for name, meta in member_map.items()}


def load_dwarf_member_offsets(path: str) -> dict[str, int]:
    """从 ELF/AXF 的 DWARF 调试信息中提取 `变量.成员` → 字节偏移映射。"""
    infos = load_dwarf_member_infos(path)
    return {name: int(info.get("byte_offset") or 0) for name, info in infos.items()}


def _get_dwarf_array_dims(type_die) -> list[int]:
    type_die = _resolve_type_die(type_die)
    if type_die is None or type_die.tag != "DW_TAG_array_type":
        return []
    dims = []
    for child in type_die.iter_children():
        if child.tag != "DW_TAG_subrange_type":
            continue
        count_attr = child.attributes.get("DW_AT_count")
        upper_attr = child.attributes.get("DW_AT_upper_bound")
        if count_attr is not None:
            dims.append(max(1, int(count_attr.value)))
        elif upper_attr is not None:
            dims.append(max(1, int(upper_attr.value) + 1))
        else:
            dims.append(1)
    return dims


def _get_dwarf_enum_items(type_die) -> list[tuple[str, int]]:
    type_die = _resolve_type_die(type_die)
    if type_die is None or type_die.tag != "DW_TAG_enumeration_type":
        return []
    items = []
    for child in type_die.iter_children():
        if child.tag != "DW_TAG_enumerator":
            continue
        name = _decode_dwarf_name(child.attributes.get("DW_AT_name"))
        value_attr = child.attributes.get("DW_AT_const_value")
        if not name or value_attr is None:
            continue
        try:
            value = int(value_attr.value)
        except Exception:
            continue
        items.append((name, value))
    return items


def _get_dwarf_type_display_name(type_die) -> str:
    seen = set()
    current = type_die
    fallback = ""
    while current is not None and current.offset not in seen:
        seen.add(current.offset)
        name = _decode_dwarf_name(current.attributes.get("DW_AT_name"))
        if name and not fallback:
            fallback = name
        if current.tag not in ("DW_TAG_typedef", "DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type"):
            return name or fallback
        if "DW_AT_type" not in current.attributes:
            break
        current = current.get_DIE_from_attribute("DW_AT_type")
    return fallback


def _collect_dwarf_member_signals(type_die, base_name: str, prefix_name: str, base_offset: int,
                                  object_type: str, out_list: list[dict], active_types: set[int]) -> None:
    resolved_type = _resolve_type_die(type_die)
    if resolved_type is None or resolved_type.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type"):
        return
    if resolved_type.offset in active_types:
        return

    active_types.add(resolved_type.offset)
    try:
        struct_type_name = _decode_dwarf_name(resolved_type.attributes.get("DW_AT_name"))
        for child in resolved_type.iter_children():
            if child.tag != "DW_TAG_member":
                continue
            member_name = _decode_dwarf_name(child.attributes.get("DW_AT_name"))
            if not member_name:
                continue

            member_type_die = child.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in child.attributes else None
            declared_type = _get_dwarf_type_display_name(member_type_die)
            resolved_member_type = _resolve_type_die(member_type_die)
            dims = _get_dwarf_array_dims(resolved_member_type)
            elem_type_die = resolved_member_type
            if resolved_member_type is not None and resolved_member_type.tag == "DW_TAG_array_type":
                elem_type_die = resolved_member_type.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in resolved_member_type.attributes else None
                elem_type_die = _resolve_type_die(elem_type_die)

            member_off = _parse_dwarf_member_location(child.attributes.get("DW_AT_data_member_location"))
            if member_off is None:
                member_off = 0
            total_offset = int(base_offset) + int(member_off)
            full_name = f"{prefix_name}.{member_name}" if prefix_name else f"{base_name}.{member_name}"

            # 结构体/联合体数组：按元素逐个展开，否则整个数组会被误当成单变量。
            if (dims and dims != [1]
                    and elem_type_die is not None
                    and elem_type_die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type")):
                elem_size = max(1, _get_dwarf_type_size_bytes(
                    resolved_member_type.get_DIE_from_attribute("DW_AT_type")
                    if resolved_member_type is not None and "DW_AT_type" in resolved_member_type.attributes
                    else elem_type_die
                ))
                element_count = _dims_product(dims)
                for flat_index in range(element_count):
                    indexed_name = f"{full_name}{_index_suffix_from_dims(flat_index, dims)}"
                    _collect_dwarf_member_signals(
                        elem_type_die, base_name, indexed_name,
                        total_offset + flat_index * elem_size,
                        object_type, out_list, active_types,
                    )
                continue

            bit_size = ""
            bit_offset = ""
            if child.attributes.get("DW_AT_bit_size") is not None:
                bit_size = str(int(child.attributes["DW_AT_bit_size"].value))
                data_bit_attr = child.attributes.get("DW_AT_data_bit_offset")
                if data_bit_attr is not None:
                    bit_offset = str(int(data_bit_attr.value) % 8)
                    total_offset = int(base_offset) + (int(data_bit_attr.value) // 8)
                elif child.attributes.get("DW_AT_bit_offset") is not None:
                    container_bits = _get_dwarf_type_size_bytes(member_type_die) * 8
                    if container_bits <= 0:
                        container_bits = max(8, int(bit_size or 0))
                    lsb_shift = container_bits - int(child.attributes["DW_AT_bit_offset"].value) - int(bit_size or 0)
                    # 负数表示位域跨出当前容器（例如继承成员的负偏移），折算到字节偏移而非截断为 0。
                    if lsb_shift < 0:
                        total_offset += lsb_shift // 8
                        bit_offset = str(lsb_shift % 8)
                    else:
                        bit_offset = str(lsb_shift)

            if elem_type_die is not None and elem_type_die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type") and not dims:
                _collect_dwarf_member_signals(elem_type_die, base_name, full_name, total_offset, object_type, out_list, active_types)
                continue

            resolved_label = _decode_dwarf_name((elem_type_die or resolved_member_type or member_type_die).attributes.get("DW_AT_name")) if (elem_type_die or resolved_member_type or member_type_die) is not None else ""
            size_hint = _get_dwarf_type_size_bytes(member_type_die)
            enum_items = _get_dwarf_enum_items(elem_type_die or resolved_member_type)
            enum_type_name = _decode_dwarf_name((elem_type_die or resolved_member_type).attributes.get("DW_AT_name")) if enum_items and (elem_type_die or resolved_member_type) is not None else ""

            # 成员自身占用的字节数 = 元素大小 × 元素个数。
            # 结构体成员信号的 size 参数是「整个结构体」的大小，绝不能拿它推导成员维度。
            member_elem_bytes = _get_dwarf_type_size_bytes(elem_type_die) if elem_type_die is not None else 0
            member_bytes = member_elem_bytes * _dims_product(dims) if dims else member_elem_bytes

            # dims 全为 1 时实质是标量：部分编译器（HighTec）会把标量成员描述成
            # array_type + subrange count=1（且不带 byte_size）。若当成数组，
            # 后续会用整个结构体大小反推出荒谬的维度并越界读取。
            is_real_array = bool(dims) and any(int(d) > 1 for d in dims)

            if enum_items:
                dtype_raw = _enum_dtype_from_items(enum_items, size_hint)
                bitmask_text = _enum_items_to_text(enum_items)
            else:
                kind_source = resolved_label or declared_type
                kind = _infer_kind_from_c_type(kind_source, size_hint)
                dtype_raw = _kind_to_dtype_raw(kind, "array" if is_real_array else "struct")
                bitmask_text = ""

            decl_suffix = _dims_to_decl_suffix(dims)
            desc_type = declared_type or resolved_label or "uint32_t"
            description = f"{desc_type} {full_name}{decl_suffix}".strip()
            out_list.append(_make_auto_signal(
                base_name=base_name,
                short_name=full_name,
                object_type=object_type,
                dtype_raw=dtype_raw,
                array_dims=dims or [1],
                addr_offset=total_offset,
                bit_offset=bit_offset,
                bit_size=bit_size,
                bitmask_text=bitmask_text,
                description=description,
                declared_c_type=declared_type,
                resolved_c_type=resolved_label or declared_type,
                enum_type_name=enum_type_name,
                struct_type_name=struct_type_name,
                member_bytes=member_bytes,
            ))
    finally:
        active_types.remove(resolved_type.offset)


def load_dwarf_struct_signals(path: str, sym_details: dict[str, dict[str, object]] | None = None) -> dict[str, list[dict]]:
    if not HAS_ELF:
        return {}

    struct_signal_map: dict[str, list[dict]] = {}
    abs_path = os.path.abspath(path)
    try:
        with open(abs_path, "rb") as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                return struct_signal_map
            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                top = cu.get_top_DIE()
                for die in top.iter_children():
                    if die.tag != "DW_TAG_variable":
                        continue
                    var_name = _decode_dwarf_name(die.attributes.get("DW_AT_name"))
                    if not var_name or not _is_user_symbol(var_name) or "DW_AT_type" not in die.attributes:
                        continue
                    type_die = die.get_DIE_from_attribute("DW_AT_type")
                    resolved_type = _resolve_type_die(type_die)
                    if resolved_type is None or resolved_type.tag not in ("DW_TAG_structure_type", "DW_TAG_union_type"):
                        continue
                    object_type = _normalize_object_type_label((sym_details or {}).get(var_name, {}).get("object_type") or "MEASUREMENT")
                    out_list: list[dict] = []
                    _collect_dwarf_member_signals(type_die, var_name, var_name, 0, object_type, out_list, set())
                    if out_list:
                        struct_signal_map[var_name] = out_list
    except Exception:
        return {}
    return struct_signal_map


def _build_signal_from_dwarf_variable(var_name: str, type_die, object_type: str,
                                      size_hint: int = 0,
                                      expand_struct_array: bool = True) -> list[dict]:
    signals: list[dict] = []
    declared_type = _get_dwarf_type_display_name(type_die)
    resolved_type = _resolve_type_die(type_die)
    if resolved_type is None:
        return signals

    if resolved_type.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
        _collect_dwarf_member_signals(type_die, var_name, var_name, 0, object_type, signals, set())
        return signals

    dims = _get_dwarf_array_dims(resolved_type)
    elem_type_die = resolved_type
    if resolved_type.tag == "DW_TAG_array_type":
        elem_type_die = resolved_type.get_DIE_from_attribute("DW_AT_type") if "DW_AT_type" in resolved_type.attributes else None
        elem_type_die = _resolve_type_die(elem_type_die)

    enum_items = _get_dwarf_enum_items(elem_type_die or resolved_type)
    enum_type_name = _decode_dwarf_name((elem_type_die or resolved_type).attributes.get("DW_AT_name")) if enum_items and (elem_type_die or resolved_type) is not None else ""
    resolved_label = _decode_dwarf_name((elem_type_die or resolved_type).attributes.get("DW_AT_name")) if (elem_type_die or resolved_type) is not None else ""
    effective_size = size_hint or _get_dwarf_type_size_bytes(type_die)

    # 数组元素类型是结构体/联合体时，元素宽度 = 结构体大小（供方案 B 做字节数对齐）
    elem_bytes = _get_dwarf_type_size_bytes(elem_type_die) if elem_type_die is not None else 0
    is_struct_array = bool(dims and not enum_items and elem_type_die is not None
                            and elem_type_die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"))

    if is_struct_array and expand_struct_array:
        # 【方案 A】顶层变量本身是「结构体/联合体数组」：逐元素展开成 名[i].成员。
        # 若把整个数组降级成字节数组，元素宽度（结构体大小）会被丢弃，
        # A2L 里只声明元素个数个字节，INCA 只能读到数组头部一小段数据。
        elem_size = max(1, elem_bytes)
        element_count = _dims_product(dims)
        if element_count <= MAX_STRUCT_ARRAY_EXPAND_ELEMENTS:
            expanded: list[dict] = []
            for flat_index in range(element_count):
                indexed_name = f"{var_name}{_dot_index_suffix_from_dims(flat_index, dims)}"
                _collect_dwarf_member_signals(
                    elem_type_die, var_name, indexed_name,
                    flat_index * elem_size, object_type, expanded, set(),
                )
            if expanded:
                # 展开出的「名.i.成员」是新增信号，但**原顶层信号必须保留**：
                # 如果用展开结果直接替换原信号，INCA 里按原名引用的观测量
                # 会因为对象在 A2L 中消失而变成无源状态——显示恒为 0 且不再刷新。
                # 这里把原顶层信号保留为「整段字节流」，地址/长度与符号完全一致。
                total_bytes = max(1, effective_size or elem_size * element_count)
                signals.extend(expanded)
                signals.append(_make_auto_signal(
                    base_name=var_name,
                    short_name=var_name,
                    object_type=object_type,
                    dtype_raw="array_u8",
                    array_dims=[total_bytes],
                    bitmask_text="",
                    description=f"{declared_type or resolved_label or 'uint32_t'} {var_name}[{element_count}]".strip(),
                    declared_c_type=declared_type,
                    resolved_c_type=resolved_label or declared_type,
                    enum_type_name="",
                    elem_bytes=elem_size,
                    member_bytes=total_bytes,
                ))
                return signals
        # 元素过多或成员无法解析：退化为字节数组，并把真实元素字节数带出去（方案 B）
        dtype_raw = "array_u8"
        elem_bytes = elem_size
        bitmask_text = ""
    elif enum_items:
        dtype_raw = _enum_dtype_from_items(enum_items, effective_size)
        bitmask_text = _enum_items_to_text(enum_items)
    else:
        if elem_type_die is not None and elem_type_die.tag in ("DW_TAG_structure_type", "DW_TAG_union_type"):
            dtype_raw = "array_u8"
            elem_bytes = max(1, elem_bytes)
        else:
            kind = _infer_kind_from_c_type(resolved_label or declared_type, _get_dwarf_type_size_bytes(elem_type_die or resolved_type) or effective_size)
            dtype_raw = _kind_to_dtype_raw(kind, "array" if dims else "scalar")
        bitmask_text = ""

    decl_suffix = _dims_to_decl_suffix(dims)
    desc_type = declared_type or resolved_label or "uint32_t"
    description = f"{desc_type} {var_name}{decl_suffix}".strip()
    signals.append(_make_auto_signal(
        base_name=var_name,
        short_name=var_name,
        object_type=object_type,
        dtype_raw=dtype_raw,
        array_dims=dims or [1],
        bitmask_text=bitmask_text,
        description=description,
        declared_c_type=declared_type,
        resolved_c_type=resolved_label or declared_type,
        enum_type_name=enum_type_name,
        elem_bytes=elem_bytes,
    ))
    return signals


def build_signals_from_dwarf(path: str,
                             sym_details: dict[str, dict[str, object]] | None = None,
                             log_cb=None,
                             expand_struct_array: bool = True) -> list[dict]:
    if not HAS_ELF:
        return []

    signals: dict[tuple[str, str], dict] = {}
    abs_path = os.path.abspath(path)
    try:
        with open(abs_path, "rb") as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                return []
            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                top = cu.get_top_DIE()
                for die in top.iter_children():
                    if die.tag != "DW_TAG_variable":
                        continue
                    var_name = _decode_dwarf_name(die.attributes.get("DW_AT_name"))
                    if not var_name or not _is_user_symbol(var_name) or "DW_AT_type" not in die.attributes:
                        continue
                    meta = (sym_details or {}).get(var_name, {})
                    object_type = _normalize_object_type_label(meta.get("object_type") or "MEASUREMENT")
                    size_hint = int(meta.get("size") or 0)
                    for sig in _build_signal_from_dwarf_variable(
                            var_name, die.get_DIE_from_attribute("DW_AT_type"), object_type,
                            size_hint, expand_struct_array=expand_struct_array):
                        signals[(sig["Name"], sig["ShortName"])] = sig
    except Exception:
        return []

    out = list(signals.values())
    out.sort(key=lambda s: (s.get('Group', 'Other'), s.get('ObjectType', 'MEASUREMENT'), s.get('ShortName') or s.get('Name')))
    if log_cb and out:
        n_enum = sum(1 for s in out if str(s.get("DataType", "")).startswith("enum_"))
        n_struct = sum(1 for s in out if "." in str(s.get("ShortName", "")))
        n_array = sum(1 for s in out if str(s.get("ArraySize", "1")).strip() not in ("", "1"))
        log_cb(tf("   🧬 {n1} candidate signals identified from DWARF", n1=len(out)))
        log_cb(tf("   ├─ Enum: {n1}", n1=n_enum))
        log_cb(tf("   ├─ Struct members: {n1}", n1=n_struct))
        log_cb(tf("   └─ Arrays/Matrices: {n1}", n1=n_array))
    return out


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                    自动信号识别 / 工程扫描辅助逻辑                           ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
_C_SCAN_EXCLUDE_DIRS = {".git", ".svn", ".hg", "build", "dist", "Debug", "Release", "out", "venv", ".venv", "__pycache__"}
_C_TYPE_SIZE_MAP = {
    "bool": 1, "_Bool": 1,
    "char": 1, "signed char": 1, "unsigned char": 1,
    "int8_t": 1, "uint8_t": 1,
    "short": 2, "short int": 2, "signed short": 2, "signed short int": 2,
    "unsigned short": 2, "unsigned short int": 2,
    "int16_t": 2, "uint16_t": 2,
    "int": 4, "signed int": 4, "unsigned int": 4,
    "long": 4, "signed long": 4, "signed long int": 4,
    "unsigned long": 4, "unsigned long int": 4,
    "int32_t": 4, "uint32_t": 4,
    "long long": 8, "signed long long": 8, "unsigned long long": 8,
    "int64_t": 8, "uint64_t": 8,
    "float": 4, "double": 8,
}
_C_DECL_QUALIFIERS = {
    "extern", "static", "volatile", "const", "register", "auto", "signed", "unsigned",
    "__io", "__i", "__o", "__weak", "__root", "inline"
}

# INCA 简化兼容开关：当前默认关闭，恢复结构体/枚举；多维数组仍会单独屏蔽
INCA_SAFE_SIMPLE_ONLY = False

# 结构体数组逐元素展开的上限：元素个数超过该值时不再展开成成员信号，
# 改为按符号实际字节数输出字节流（避免信号数量爆炸拖垮 A2L 体积与 INCA 加载速度）。
MAX_STRUCT_ARRAY_EXPAND_ELEMENTS = 64

_NON_SIGNAL_SYMBOLS = {
    "RAM", "FLASH", "ROM", "ITCM", "DTCM", "SRAM", "DRAM",
    "MEMORY", "CONFIGURATION", "NAME", "ORIGIN", "LENGTH", "ATTRIBUTES", "LOAD"
}


def _safe_identifier(name: str, default: str = "AUTO") -> str:
    cleaned = re.sub(r"\W+", "_", str(name or "").strip()).strip("_")
    return cleaned or default


def _normalize_type_label(c_type: str) -> str:
    return _collapse_ws(str(c_type or "")).replace(" *", "*")


def _extract_existing_a2l_object_names(a2l_content: str) -> set[str]:
    pattern = re.compile(
        r"/begin\s+(?:MEASUREMENT|CHARACTERISTIC)\s*\n\s*/\*\s*Name.*?\*/\s*([^\s\r\n]+)",
        re.I,
    )
    return {m.group(1).strip() for m in pattern.finditer(str(a2l_content or "")) if m.group(1).strip()}


def _find_sibling_address_files(addr_path: str, max_up: int = 4) -> list[tuple[str, datetime, int]]:
    """在工程目录里找出所有与 addr_path 同名的地址文件（AXF/ELF/MAP/OUT）。

    「选错版本」是本工具最容易犯也最难自查的错误：同一工程名（如 TcuBsw.axf）
    在磁盘上常同时存在好几份不同编译时间的副本，地址文件一选错，全部观测量
    地址会整体错位，INCA 侧表现为恒为 0 且不刷新，肉眼完全看不出来。
    这里把同名的其它版本列出来，用户一眼就能确认自己用的是哪一份。
    """
    try:
        ap = os.path.abspath(addr_path)
    except Exception:
        return []
    base = os.path.splitext(os.path.basename(ap))[0]
    exts = (".axf", ".elf", ".map", ".out")
    found: list[tuple[str, datetime, int]] = []
    deadline = time.monotonic() + 2.0   # 时间预算：避免大目录扫描拖慢界面

    def _collect(folder: str, depth: int = 0):
        if depth > 5 or len(found) > 200:
            return
        if time.monotonic() > deadline:
            return
        try:
            entries = os.listdir(folder)
        except OSError:
            return
        subdirs = []
        for fn in entries:
            fp = os.path.join(folder, fn)
            if os.path.isdir(fp):
                subdirs.append(fp)
                continue
            stem, ext = os.path.splitext(fn)
            if stem == base and ext.lower() in exts:
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                found.append((fp, datetime.fromtimestamp(st.st_mtime), st.st_size))
        # 再往下 5 层：工程里旧版本常放在 Release/、Tools/File/、或另一个
        # 并列的工程根目录下
        for sub in subdirs[:60]:
            if time.monotonic() > deadline:
                return
            _collect(sub, depth + 1)

    cur = os.path.dirname(ap)
    for _ in range(max_up):
        if not cur or not os.path.isdir(cur):
            break
        _collect(cur)
        cur = os.path.dirname(cur)
    return sorted(set(found), key=lambda x: x[1], reverse=True)


def collect_a2l_object_addresses(a2l_path: str) -> dict[str, int]:
    """解析 A2L 中的 MEASUREMENT / CALIBRATION / CHARACTERISTIC，返回 名称 -> ECU_ADDRESS。

    兼容两种 Name 写法：
      1. 注释式  /* Name */   XXX
      2. 位置式  块内第一行就是一个裸标识符（旧版工具生成的那种）
    """
    try:
        text, _enc, _nl = read_a2l_text_preserving_format(a2l_path)
    except Exception:
        return {}
    result: dict[str, int] = {}
    for kind in ("MEASUREMENT", "CALIBRATION", "CHARACTERISTIC"):
        for m in re.finditer(r"/begin\s+%s\b(.*?)/end\s+%s" % (kind, kind), text, flags=re.S | re.I):
            body = m.group(1)
            ad = re.search(r"ECU_ADDRESS\s+(0x[0-9A-Fa-f]+)", body)
            if not ad:
                continue
            nm = re.search(r"/\*\s*Name\s*\*/\s*([^\s\r\n]+)", body)
            if not nm:
                # 位置参数写法：块内第一个非空行是裸标识符
                for line in body.splitlines():
                    s = line.strip()
                    if not s or s.startswith(("/*", '"', "/")):
                        continue
                    if re.match(r"^[A-Za-z_]\w*$", s):
                        nm = re.match(r"([A-Za-z_]\w*)", s)
                        break
            if nm:
                result[nm.group(1).strip()] = int(ad.group(1), 0)
    return result


def _first_divergent_dir_name(dir_a: str, dir_b: str) -> tuple[str, str]:
    """返回两个目录路径中第一个不同的目录名（用于判断是否同一工程）。"""
    a = [p for p in os.path.abspath(dir_a).split(os.sep) if p]
    b = [p for p in os.path.abspath(dir_b).split(os.sep) if p]
    for x, y in zip(a, b):
        if x.lower() != y.lower():
            return x, y
    return "", ""


def detect_address_file_conflicts(addr_path: str, out_path: str) -> list[str]:
    """检测“地址文件与输出文件不属于同一固件/工程”的情况。

    观测量在 INCA 里全为 0 的最常见原因：生成了 A2L 的地址文件与实际刷入 ECU 的
    固件不是同一份（内存布局不同），此时 INCA 读到的是别的内存区域。
    """
    warnings: list[str] = []
    if not addr_path or not out_path:
        return warnings
    addr_abs = os.path.abspath(addr_path)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        return warnings

    # 1) 输出目录里存在同名固件，但当前用的不是它
    stem = os.path.splitext(os.path.basename(addr_path))[0]
    try:
        entries = os.listdir(out_dir)
    except OSError:
        entries = []
    for fn in entries:
        full = os.path.join(out_dir, fn)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(fn)[1].lower()
        if ext not in (".axf", ".elf", ".map"):
            continue
        if os.path.splitext(fn)[0] != stem:
            continue
        if os.path.abspath(full) == addr_abs:
            continue
        warnings.append(
            tf("Another firmware with the same name exists in the output directory, but the current address file is not it:\n    Currently using: {n1}\n    Another one in the directory: {n2}\n  If the two firmwares are not from the same build, INCA will read at wrong addresses (measurements often show all 0).", n1=addr_abs, n2=full)
        )

    # 2) 地址文件与输出文件看起来属于不同工程目录
    addr_dir = os.path.dirname(addr_abs)
    try:
        same_tree = os.path.commonpath([addr_dir, out_dir]).lower() == addr_dir.lower()
    except ValueError:
        same_tree = False
    if not same_tree:
        a_name, b_name = _first_divergent_dir_name(addr_dir, out_dir)
        if a_name and b_name:
            warnings.append(
                tf("The address file and the output file are not in the same directory tree:\n    Address file directory: {n1}\n    Output file directory: {n2}\n  Please make sure the address file and the firmware actually flashed to the ECU are from the same build.", n1=a_name, n2=b_name)
            )
    return warnings


def compare_a2l_addresses(ref_map: dict[str, int], gen_map: dict[str, int]) -> dict:
    """对比参考 A2L 与本次生成结果的同名信号地址。"""
    common = [k for k in gen_map if k in ref_map]
    same = [k for k in common if gen_map[k] == ref_map[k]]
    diff = [(k, ref_map[k], gen_map[k], gen_map[k] - ref_map[k]) for k in common if gen_map[k] != ref_map[k]]
    only_ref = [k for k in ref_map if k not in gen_map]
    only_gen = [k for k in gen_map if k not in ref_map]
    deltas = Counter(d[3] for d in diff)
    return {
        "ref_total": len(ref_map),
        "gen_total": len(gen_map),
        "common": len(common),
        "same": len(same),
        "diff": diff,
        "only_ref": only_ref,
        "only_gen": only_gen,
        "delta_hist": deltas.most_common(8),
    }


def derive_group_from_name(signal_name: str) -> str:
    """按“前 5 个字符内识别 '_'，否则归类到 Other”规则生成 Group。"""
    raw = str(signal_name or "").strip()
    if not raw:
        return "Other"
    head = raw[:5]
    pos = head.find("_")
    if pos <= 0:
        return "Other"
    grp = re.sub(r"[^A-Za-z0-9]", "", raw[:pos]).strip()
    return grp or "Other"


def _is_user_symbol(name: str) -> bool:
    if not name or not re.match(r"^[A-Za-z_]\w*$", str(name)):
        return False
    if name.startswith("__") or name.startswith("_"):
        return False
    if str(name).upper() in _NON_SIGNAL_SYMBOLS:
        return False
    return True


def _make_symbol_meta(name: str, address: int, size: int = 0,
                      section: str = "", source: str = "") -> dict[str, object]:
    return {
        "name": name,
        "address": int(address),
        "size": int(size or 0),
        "section": str(section or ""),
        "source": str(source or ""),
    }


def _classify_object_type(name: str, section: str = "", qualifiers: str = "") -> str:
    """基于命名、段名和限定符，粗分标定量/观测量。"""
    name_l = str(name or "").lower()
    sec_l = str(section or "").lower()
    qual_l = str(qualifiers or "").lower()

    if re.search(r"(?:^|_)(?:c|cal|cfg|k)(?:$|_)", name_l):
        return "CALIBRATION"
    if re.search(r"(?:^|_)(?:m|meas|mon|obs)(?:$|_)", name_l):
        return "MEASUREMENT"

    if any(key in sec_l for key in ("cal", "const", "rodata", "flash", "rom")):
        return "CALIBRATION"
    if any(key in sec_l for key in ("bss", "data", "ram", "noinit")):
        return "MEASUREMENT"

    if "const" in qual_l and "volatile" not in qual_l:
        return "CALIBRATION"
    if "volatile" in qual_l:
        return "MEASUREMENT"

    return "MEASUREMENT"


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    merged = []
    for start, end in sorted(ranges):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(int(s), int(e)) for s, e in merged]


def summarize_address_ranges(sym_details: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    summary = {
        "CALIBRATION": {"count": 0, "ranges": [], "names": []},
        "MEASUREMENT": {"count": 0, "ranges": [], "names": []},
    }
    for name, meta in sym_details.items():
        if not _is_user_symbol(name):
            continue
        otype = str(meta.get("object_type") or "MEASUREMENT")
        if otype not in summary:
            continue
        addr = int(meta.get("address") or 0)
        size = max(1, int(meta.get("size") or 0))
        summary[otype]["count"] += 1
        summary[otype]["ranges"].append((addr, addr + size - 1))
        summary[otype]["names"].append(name)

    for otype in summary:
        summary[otype]["ranges"] = _merge_ranges(summary[otype]["ranges"])
    return summary


def parse_address_ranges_text(text: str) -> list[tuple[int, int]]:
    ranges = []
    raw = str(text or "").strip()
    if not raw:
        return ranges
    tokens = [tok.strip() for tok in re.split(r"[;\r\n]+", raw) if tok.strip()]
    for token in tokens:
        match = re.match(r"^(.+?)(?:\s*[-~]\s*)(.+)$", token)
        if not match:
            raise ValueError(tf("Invalid address range format: {n1}", n1=token))
        start = _safe_int_auto(match.group(1), None)
        end = _safe_int_auto(match.group(2), None)
        if start is None or end is None:
            raise ValueError(tf("Cannot parse address range: {n1}", n1=token))
        if end < start:
            start, end = end, start
        ranges.append((int(start), int(end)))
    return _merge_ranges(ranges)


def format_address_ranges_text(ranges: list[tuple[int, int]]) -> str:
    return "; ".join(f"0x{start:08X}-0x{end:08X}" for start, end in (ranges or []))


def _normalize_object_type_label(value) -> str:
    text = str(value or "").upper().strip()
    return "CALIBRATION" if text in ("CAL", "CALIBRATION") else "MEASUREMENT"


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """把地址区间列表排序并合并相邻/重叠区间。"""
    if not ranges:
        return []
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if out and start <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _parse_header_memory_segment_ranges(a2l_text: str) -> dict[str, list[tuple[int, int]]]:
    """从 A2L 头文件的 MEMORY_SEGMENT 声明里读出内存区间。

    头文件里的段定义是「这份 A2L 所描述的内存空间」的权威说明。工具按用户
    手工填的地址区间过滤符号时，只要漏掉其中一段（本工程就漏了
    0x30504000 起的 MEARAM 段），该段里的信号就不会被生成——INCA 侧按名字
    引用它们时表现为恒为 0 且不再刷新。

    这里**只读取**头文件信息用于过滤，不改写头文件的任何内容。
    """
    result: dict[str, list[tuple[int, int]]] = {"CALIBRATION": [], "MEASUREMENT": []}
    for m in re.finditer(
            r"/begin\s+MEMORY_SEGMENT\s+[^\s]+\s+\"[^\"]*\"\s+DATA\s+(\w+)\s+"
            r"(?:INTERN\s+|EXTERN\s+)?(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)",
            a2l_text or "", flags=re.S):
        kind = m.group(1).upper()
        start = int(m.group(2), 16)
        size = int(m.group(3), 16)
        if size <= 0:
            continue
        key = "CALIBRATION" if kind in ("FLASH", "ROM", "EEPROM") else "MEASUREMENT"
        result[key].append((start, start + size - 1))
    return {key: _merge_ranges(val) for key, val in result.items()}


def apply_address_range_overrides(sym_details: dict[str, dict[str, object]],
                                  address_ranges: dict[str, list[tuple[int, int]]] | None,
                                  filter_unmatched: bool = True) -> dict[str, dict[str, object]]:
    if not address_ranges:
        return sym_details
    cal_ranges = list(address_ranges.get("CALIBRATION") or [])
    meas_ranges = list(address_ranges.get("MEASUREMENT") or [])
    if not cal_ranges and not meas_ranges:
        return sym_details

    updated = {name: dict(meta) for name, meta in sym_details.items()}
    result: dict[str, dict[str, object]] = {}
    for name, meta in updated.items():
        addr = int(meta.get("address") or 0)
        in_cal = any(start <= addr <= end for start, end in cal_ranges)
        in_meas = any(start <= addr <= end for start, end in meas_ranges)
        if not in_cal and not in_meas:
            if filter_unmatched:
                continue
            meta["object_type"] = _normalize_object_type_label(meta.get("object_type"))
            result[name] = meta
            continue
        if in_cal and not in_meas:
            meta["object_type"] = "CALIBRATION"
        elif in_meas and not in_cal:
            meta["object_type"] = "MEASUREMENT"
        else:
            meta["object_type"] = _normalize_object_type_label(meta.get("object_type"))
        result[name] = meta
    return result


def _signal_key(sig: dict) -> str:
    return str(sig.get("ShortName") or sig.get("Name") or "").strip()


def merge_signal_config_rows_into_signals(signals: list[dict], imported_rows: list[dict]) -> tuple[list[dict], list[str], int]:
    signal_map = {_signal_key(sig): sig for sig in signals if _signal_key(sig)}
    kept_names = {key for key in signal_map}
    matched = 0
    keep_seen = False
    for row in imported_rows or []:
        key = str(row.get("ShortName") or row.get("Name") or "").strip()
        sig = signal_map.get(key)
        if not sig:
            continue
        matched += 1
        for col in SIGNAL_CONFIG_MERGE_COLS:
            sig[col] = str(row.get(col, "")).strip()
        # 标记补充列来源：匹配报告要能说明「Unit/Min/Max 等是从 XLSX 合入的」。
        sig["_xlsx_merged"] = True
        if SIGNAL_IO_KEEP_COL in row:
            keep_seen = True
            if _parse_checked_cell(row.get(SIGNAL_IO_KEEP_COL)):
                kept_names.add(key)
            else:
                kept_names.discard(key)
    if not keep_seen:
        kept_names = list(signal_map.keys())
    else:
        kept_names = [name for name in signal_map.keys() if name in kept_names]
    return signals, kept_names, matched


def write_signal_config_xlsx(path: str | Path,
                             signals: list[dict],
                             keep_names: list[str] | set[str] | None = None) -> None:
    if not HAS_OPENPYXL:
        raise RuntimeError(
            tr("openpyxl is not installed, cannot write xlsx files!\nPlease run in the terminal:  py -m pip install openpyxl")
        )

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    save_path = Path(path)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    keep_name_set = {str(name).strip() for name in (keep_names or []) if str(name).strip()}

    wb = Workbook()
    ws = wb.active
    ws.title = "Signals"
    headers = [SIGNAL_IO_KEEP_COL] + SIGNAL_CONFIG_EXPORT_COLS
    header_fill = PatternFill(fill_type="solid", fgColor="4F81BD")
    editable_fill = PatternFill(fill_type="solid", fgColor="FFF2CC")
    editable_header_fill = PatternFill(fill_type="solid", fgColor="D9EAD3")
    for ci, header in enumerate(headers, 1):
        cell = ws.cell(1, ci, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = editable_header_fill if header in SIGNAL_CONFIG_EDITABLE_COLS else header_fill
        if header in SIGNAL_CONFIG_EDITABLE_COLS:
            cell.font = Font(bold=True, color="000000")
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(headers))}1"
    ws.column_dimensions["A"].width = 8
    for idx, col in enumerate(SIGNAL_CONFIG_EXPORT_COLS, start=2):
        ws.column_dimensions[openpyxl.utils.get_column_letter(idx)].width = max(14, len(col) + 2)

    for row_idx, sig in enumerate(signals or [], start=2):
        sig_key = _signal_key(sig)
        keep_cell = ws.cell(row_idx, 1, _format_keep_cell(sig_key in keep_name_set))
        keep_cell.fill = editable_fill
        for ci, col in enumerate(SIGNAL_CONFIG_EXPORT_COLS, start=2):
            value_cell = ws.cell(row_idx, ci, str(sig.get(col, "")))
            if col in SIGNAL_CONFIG_MERGE_COLS:
                value_cell.fill = editable_fill

    tmp_path = save_path.with_suffix(save_path.suffix + ".tmp")
    wb.save(tmp_path)
    os.replace(tmp_path, save_path)


def summarize_signal_config_changes(signals: list[dict], existing_rows: list[dict] | None = None) -> dict[str, int]:
    rows = list(existing_rows or [])
    old_row_map = {
        str(row.get("ShortName") or row.get("Name") or "").strip(): row
        for row in rows
        if str(row.get("ShortName") or row.get("Name") or "").strip()
    }
    current_signals = [dict(sig) for sig in (signals or [])]
    merged_signals, _, matched = merge_signal_config_rows_into_signals(current_signals, rows)
    current_keys = [_signal_key(sig) for sig in merged_signals if _signal_key(sig)]
    current_key_set = set(current_keys)
    old_key_set = set(old_row_map)

    core_updated = 0
    for sig in merged_signals:
        key = _signal_key(sig)
        old_row = old_row_map.get(key)
        if not old_row:
            continue
        if any(str(old_row.get(col, "")).strip() != str(sig.get(col, "")).strip() for col in SIGNAL_CORE_COLS):
            core_updated += 1

    return {
        "matched": matched,
        "added": len(current_key_set - old_key_set),
        "removed": len(old_key_set - current_key_set),
        "updated": core_updated,
        "total": len(current_keys),
    }


def sync_signal_config_xlsx(path: str | Path,
                            signals: list[dict],
                            existing_rows: list[dict] | None = None,
                            keep_names: list[str] | set[str] | None = None) -> tuple[list[dict], list[str], dict[str, object]]:
    save_path = Path(path)
    rows = list(existing_rows or [])
    old_row_map = {
        str(row.get("ShortName") or row.get("Name") or "").strip(): row
        for row in rows
        if str(row.get("ShortName") or row.get("Name") or "").strip()
    }
    merged_signals = [dict(sig) for sig in (signals or [])]
    merged_signals, preserved_keep_names, matched = merge_signal_config_rows_into_signals(merged_signals, rows)
    current_keys = [_signal_key(sig) for sig in merged_signals if _signal_key(sig)]
    current_key_set = set(current_keys)
    old_key_set = set(old_row_map)

    if keep_names is None:
        effective_keep_names = [name for name in preserved_keep_names if name in current_key_set]
    else:
        effective_keep_names = [
            str(name).strip() for name in keep_names
            if str(name).strip() in current_key_set
        ]

    core_updated = 0
    for sig in merged_signals:
        key = _signal_key(sig)
        old_row = old_row_map.get(key)
        if not old_row:
            continue
        if any(str(old_row.get(col, "")).strip() != str(sig.get(col, "")).strip() for col in SIGNAL_CORE_COLS):
            core_updated += 1

    write_signal_config_xlsx(save_path, merged_signals, effective_keep_names)
    summary = {
        "path": str(save_path),
        "matched": matched,
        "added": len(current_key_set - old_key_set),
        "removed": len(old_key_set - current_key_set),
        "updated": core_updated,
        "total": len(current_keys),
    }
    return merged_signals, effective_keep_names, summary


def _normalize_path_history(paths, current_path: str = "", limit: int = PATH_HISTORY_LIMIT) -> list[str]:
    seen = set()
    normalized = []
    for raw_path in [current_path] + list(paths or []):
        path = str(raw_path or "").strip()
        if not path:
            continue
        try:
            key = os.path.normcase(os.path.abspath(path))
        except Exception:
            key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
        if len(normalized) >= max(1, int(limit or PATH_HISTORY_LIMIT)):
            break
    return normalized


def _expand_struct_array_flag(owner) -> bool:
    """读取「结构体数组逐元素展开为成员信号」开关；控件不存在时默认开启。"""
    chk = getattr(owner, "chk_expand_struct_array", None)
    if chk is None:
        return True
    try:
        return bool(chk.isChecked())
    except Exception:
        return True


def build_axf_signal_preview(addr_path: str,
                             address_range_config: dict[str, list[tuple[int, int]]] | None = None,
                             signal_config_rows: list[dict] | None = None,
                             log_cb=None,
                             expand_struct_array: bool = True,
                             a2l_path: str | None = None) -> tuple[list[dict], list[str], dict[str, dict[str, object]], int]:
    sym_details = load_address_file_details(addr_path)
    effective_ranges = address_range_config
    if a2l_path and os.path.isfile(a2l_path):
        # 与 do_update_a2l 保持一致：把头文件 MEMORY_SEGMENT 声明的区间并入过滤范围
        # （用户未配置区间时视为「全都要」，不过滤）
        try:
            _hdr, _enc, _nl = read_a2l_text_preserving_format(a2l_path)
            _hr = _parse_header_memory_segment_ranges(_hdr)
            _user = {k: _merge_ranges(list((address_range_config or {}).get(k) or []))
                     for k in ("CALIBRATION", "MEASUREMENT")}
            if any(_user.values()):
                effective_ranges = {k: _merge_ranges(_user[k] + _hr.get(k, []))
                                    for k in ("CALIBRATION", "MEASUREMENT")}
        except Exception:
            effective_ranges = address_range_config
    sym_details = apply_address_range_overrides(sym_details, effective_ranges, filter_unmatched=True)
    signals = build_signals_from_dwarf(addr_path, sym_details, log_cb,
                                       expand_struct_array=expand_struct_array)
    if signals:
        # 标记类型来源：DWARF 识别出的信号，其类型/数组维度可信度最高。
        for _s in signals:
            _s["_type_source"] = EVID_TYPE_DWARF
    else:
        signals = []
        for name, meta in sym_details.items():
            if not _is_user_symbol(name):
                continue
            size = int(meta.get("size") or 0)
            if size > 8:
                dtype_raw = "array_u8"
                dims = [size]
            else:
                dims = [1]
                dtype_raw = _kind_to_dtype_raw({1: "u8", 2: "u16", 4: "u32", 8: "u64"}.get(size, "u8"), "scalar")
            object_type = _normalize_object_type_label(meta.get("object_type") or "MEASUREMENT")
            sig = _make_auto_signal(name, name, object_type, dtype_raw, dims,
                                    description=f"auto-detected from {meta.get('source') or 'ADDR'}")
            sig["_type_source"] = EVID_TYPE_SYMBOL
            signals.append(sig)

    for sig in signals:
        ref_name = _signal_key(sig)
        if not str(sig.get("Group", "")).strip():
            sig["Group"] = derive_group_from_name(ref_name)
        meta = sym_details.get(str(sig.get("Name") or "").strip(), {})
        obj = meta.get("object_type") or sig.get("ObjectType", "")
        sig["ObjectType"] = _normalize_object_type_label(obj)
        sig["Type"] = "CAL" if sig["ObjectType"] == "CALIBRATION" else "MEAS"

    kept_names = [_signal_key(sig) for sig in signals if _signal_key(sig)]
    matched = 0
    if signal_config_rows:
        signals, kept_names, matched = merge_signal_config_rows_into_signals(signals, signal_config_rows)
    return signals, kept_names, sym_details, matched


def parse_map_file_detailed(path: str) -> dict[str, dict[str, object]]:
    """解析 MAP，提取更丰富的符号信息：地址 / 大小 / 段名。"""
    sym_map: dict[str, dict[str, object]] = {}
    current_section = ""
    section_pat = re.compile(r'^\s*(\.[\w.$]+)\s*$')
    pat_with_size = re.compile(r'^\s*(?:0x)?([0-9a-fA-F]{6,16})\s+(?:0x)?([0-9a-fA-F]{1,16})\s+([A-Za-z_]\w*)\b')
    pat_addr_name = re.compile(r'^\s*(?:0x)?([0-9a-fA-F]{6,16})\s+([A-Za-z_]\w*)\b')
    pat_name_addr = re.compile(r'^\s*([A-Za-z_]\w*)\s+(?:0x)?([0-9a-fA-F]{6,16})\b')
    pat_nm = re.compile(r'^\s*([0-9a-fA-F]{6,16})\s+[A-Za-z]\s+([A-Za-z_]\w*)\b')

    with open(path, "r", errors="replace") as f:
        for line in f:
            raw = line.rstrip()
            m_sec = section_pat.match(raw)
            if m_sec:
                current_section = m_sec.group(1)
                continue

            matched = False
            for pat, has_size, reverse_name in [
                (pat_with_size, True, False),
                (pat_addr_name, False, False),
                (pat_name_addr, False, True),
                (pat_nm, False, False),
            ]:
                m = pat.match(raw)
                if not m:
                    continue
                try:
                    if pat is pat_name_addr:
                        name, addr_hex = m.group(1), m.group(2)
                        size_hex = "0"
                    else:
                        addr_hex = m.group(1)
                        if has_size:
                            size_hex = m.group(2)
                            name = m.group(3)
                        else:
                            size_hex = "0"
                            name = m.group(2)
                    if not _is_user_symbol(name):
                        matched = True
                        break
                    sym_map[name] = _make_symbol_meta(
                        name=name,
                        address=int(addr_hex, 16),
                        size=int(size_hex, 16),
                        section=current_section,
                        source="MAP",
                    )
                    matched = True
                    break
                except Exception:
                    matched = True
                    break
            if matched:
                continue

    for meta in sym_map.values():
        meta["object_type"] = _classify_object_type(
            str(meta.get("name") or ""),
            str(meta.get("section") or ""),
            "",
        )
    return sym_map


def parse_elf_axf_detailed(path: str) -> dict[str, dict[str, object]]:
    """解析 ELF/AXF，提取对象符号地址、大小和所属段名。"""
    if not HAS_ELF:
        raise RuntimeError(
            tr("pyelftools is not installed, cannot parse ELF/AXF files!\n"
               "Please run in the terminal:  py -m pip install pyelftools")
        )

    sym_map: dict[str, dict[str, object]] = {}
    with open(path, "rb") as f:
        elf = ELFFile(f)
        for section in elf.iter_sections():
            if section["sh_type"] not in ("SHT_SYMTAB", "SHT_DYNSYM"):
                continue
            for sym in section.iter_symbols():
                name = sym.name
                if not name or not sym["st_value"] or not _is_user_symbol(name):
                    continue
                sym_type = str(sym.entry["st_info"]["type"])
                if sym_type not in ("STT_OBJECT", "STT_COMMON", "STT_NOTYPE"):
                    continue
                sec_name = ""
                shndx = sym["st_shndx"]
                if isinstance(shndx, int) and 0 <= shndx < elf.num_sections():
                    try:
                        sec_name = elf.get_section(shndx).name or ""
                    except Exception:
                        sec_name = ""
                sym_map[name] = _make_symbol_meta(
                    name=name,
                    address=int(sym["st_value"]),
                    size=int(sym["st_size"] or 0),
                    section=sec_name,
                    source="ELF",
                )

    for meta in sym_map.values():
        meta["object_type"] = _classify_object_type(
            str(meta.get("name") or ""),
            str(meta.get("section") or ""),
            "",
        )
    return sym_map


def load_address_file_details(path: str) -> dict[str, dict[str, object]]:
    """根据扩展名自动返回带段信息的符号详情。"""
    cache_key = _cache_key_for_file(path)
    abs_path = cache_key[0]
    cached = _ADDRESS_FILE_CACHE.get(cache_key)
    if cached is not None:
        return {name: dict(meta) for name, meta in cached.items()}

    ext = Path(path).suffix.lower()
    if ext == ".map":
        details = parse_map_file_detailed(path)
    elif ext in (".elf", ".axf"):
        try:
            details = parse_elf_axf_detailed(path)
        except RuntimeError:
            raise
        except Exception:
            details = parse_map_file_detailed(path)
    else:
        try:
            details = parse_elf_axf_detailed(path)
        except Exception:
            details = parse_map_file_detailed(path)

    _ADDRESS_FILE_CACHE.clear()
    _ADDRESS_FILE_CACHE[cache_key] = {name: dict(meta) for name, meta in details.items()}
    return {name: dict(meta) for name, meta in details.items()}


def _strip_c_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    return text


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _safe_eval_c_expr(expr: str, names: dict[str, int] | None = None) -> int | None:
    expr = str(expr or "").strip()
    if not expr:
        return None
    expr = re.sub(r'(?<=\d)[uUlL]+\b', '', expr)
    expr = re.sub(r'\bsizeof\s*\([^)]*\)', '4', expr)
    lookup = names or {}

    def repl(match):
        token = match.group(0)
        if token in lookup:
            return str(lookup[token])
        return token

    expr = re.sub(r'\b[A-Za-z_]\w*\b', repl, expr)
    if re.search(r'[^0-9xXa-fA-F_\s\+\-\*/%()<>|&~]', expr):
        return None
    try:
        return int(eval(expr, {"__builtins__": {}}, {}))
    except Exception:
        return None


def _parse_array_dims_text(array_text: str, macros: dict[str, int] | None = None) -> list[int]:
    dims = []
    for raw in re.findall(r'\[([^\]]*)\]', str(array_text or '')):
        raw = raw.strip()
        if not raw:
            dims.append(1)
            continue
        val = _safe_eval_c_expr(raw, macros)
        dims.append(int(val) if val and int(val) > 0 else 1)
    return dims


def _dims_to_text(dims: list[int]) -> str:
    return "x".join(str(max(1, int(x))) for x in dims) if dims else "1"


def _dims_to_decl_suffix(dims: list[int]) -> str:
    if not dims:
        return ""
    return "".join(f"[{max(1, int(dim))}]" for dim in dims)


def _dims_product(dims: list[int] | None) -> int:
    total = 1
    for dim in dims or [1]:
        total *= max(1, int(dim))
    return max(1, total)


def _index_suffix_from_dims(index: int, dims: list[int]) -> str:
    """把扁平下标还原成 C 多维下标后缀，例如 (5, [2,3]) -> '[1][2]'。"""
    tail: list[int] = []
    rest = int(index)
    for dim in reversed(list(dims)):
        size = max(1, int(dim))
        tail.append(rest % size)
        rest //= size
    return "".join(f"[{i}]" for i in reversed(tail))


def _dot_index_suffix_from_dims(index: int, dims: list[int]) -> str:
    """把扁平下标还原成点号下标后缀，例如 (5, [2,3]) -> '.1.2'。

    结构体数组逐元素展开时使用：点号既符合 ASAP2 标识符规范，
    又能让「名.下标.成员」沿用结构体成员别名的地址换算逻辑。
    """
    tail: list[int] = []
    rest = int(index)
    for dim in reversed(list(dims)):
        size = max(1, int(dim))
        tail.append(rest % size)
        rest //= size
    return "".join(f".{i}" for i in reversed(tail))


def _matrix_dims_for_a2l(dims: list[int]) -> list[int]:
    dims = [max(1, int(x)) for x in (dims or [1])]
    if len(dims) == 1:
        return [1, dims[0]]
    return dims[:3]


def _effective_array_dims(sig: dict) -> list[int]:
    array_dims = _parse_array_dims_value(sig.get("ArraySize", "1"))
    return [int(dim) for dim in array_dims if int(dim) > 1]


def _is_multidim_array_signal(sig: dict) -> bool:
    """只要存在两个及以上有效维度，就视为多维数组。"""
    dims = _effective_array_dims(sig)
    return len(dims) > 1


def _is_unsupported_highdim_array_signal(sig: dict) -> bool:
    """当前仅支持到二维数组；三维及以上仍跳过。"""
    dims = _effective_array_dims(sig)
    return len(dims) > 2


def _should_skip_for_inca(sig: dict) -> bool:
    """INCA 临时兼容模式：跳过三维及以上数组、结构体成员、枚举；保留一维/二维数组。"""
    dtype_raw = str(sig.get("DataType", "")).strip()
    shortname = str(sig.get("ShortName", "")).strip()
    is_struct = dtype_raw.startswith("struct_") or (shortname and "." in shortname)
    is_enum = dtype_raw.startswith("enum_")
    return _is_unsupported_highdim_array_signal(sig) or is_struct or is_enum


def _parse_array_dims_value(value) -> list[int]:
    if value is None:
        return [1]
    if isinstance(value, (list, tuple)):
        dims = [max(1, int(x)) for x in value if str(x).strip()]
        return dims or [1]
    s = str(value).strip()
    if not s:
        return [1]
    parts = [p for p in re.split(r'[xX,;\s]+', s) if p]
    dims = []
    for part in parts:
        try:
            dims.append(max(1, int(part, 0)))
        except Exception:
            pass
    return dims or [1]


def _infer_kind_from_c_type(c_type: str, size_hint: int = 0) -> str:
    norm = _collapse_ws(c_type).replace(" *", "*")
    if "*" in norm:
        return "ptr"
    mapping = {
        "uint8_t": "u8", "uint8": "u8", "unsigned char": "u8",
        "int8_t": "i8", "int8": "i8", "signed char": "i8", "char": "i8",
        "uint16_t": "u16", "uint16": "u16", "unsigned short": "u16", "unsigned short int": "u16",
        "int16_t": "i16", "int16": "i16", "short": "i16", "short int": "i16", "signed short": "i16", "signed short int": "i16",
        "uint32_t": "u32", "uint32": "u32", "unsigned int": "u32", "unsigned long": "u32", "unsigned long int": "u32",
        "int32_t": "i32", "int32": "i32", "int": "i32", "signed int": "i32", "long": "i32", "long int": "i32", "signed long": "i32",
        "uint64_t": "u64", "uint64": "u64", "unsigned long long": "u64",
        "int64_t": "i64", "int64": "i64", "long long": "i64", "signed long long": "i64",
        "float": "f32",
        "double": "f64",
        "bool": "bool", "_Bool": "bool",
    }
    if norm in mapping:
        return mapping[norm]
    if norm.startswith("enum "):
        if size_hint == 1:
            return "enum_u8"
        if size_hint == 2:
            return "enum_u16"
        return "enum_u32"
    if size_hint == 1:
        return "u8"
    if size_hint == 2:
        return "u16"
    if size_hint == 8:
        return "u64"
    if size_hint == 4:
        return "u32"
    return "u32"


def _kind_to_dtype_raw(kind: str, context: str = "scalar") -> str:
    scalar_map = {
        "u8": "uint8_t", "i8": "int8_t",
        "u16": "uint16_t", "i16": "int16_t",
        "u32": "uint32_t", "i32": "int32_t",
        "u64": "uint64_t", "i64": "int64_t",
        "f32": "float", "f64": "double", "bool": "bool",
        "enum_u8": "enum_u8", "enum_u16": "enum_u16", "enum_u32": "enum_u32",
    }
    if context == "array":
        return {
            "u8": "array_u8", "i8": "array_i8",
            "u16": "array_u16", "i16": "array_i16",
            "u32": "array_u32", "i32": "array_i32",
            "u64": "array_u32", "i64": "array_i32",
            "f32": "array_f32", "f64": "array_f64", "bool": "array_u8",
        }.get(kind, "array_u32")
    if context == "struct":
        return {
            "u8": "struct_u8", "i8": "struct_i8",
            "u16": "struct_u16", "i16": "struct_i16",
            "u32": "struct_u32", "i32": "struct_i32",
            "u64": "struct_u32", "i64": "struct_i32",
            "f32": "struct_f32", "f64": "struct_f64", "bool": "struct_u8",
            "enum_u8": "enum_u8", "enum_u16": "enum_u16", "enum_u32": "enum_u32",
        }.get(kind, "struct_u32")
    return scalar_map.get(kind, "uint32_t")


def _sizeof_c_type(c_type: str, struct_defs: dict[str, dict] | None = None) -> int:
    norm = _collapse_ws(c_type).replace(" *", "*")
    if not norm:
        return 4
    if "*" in norm:
        return 4
    if norm in _C_TYPE_SIZE_MAP:
        return _C_TYPE_SIZE_MAP[norm]
    if norm.startswith("struct "):
        key = norm.split(None, 1)[1]
        if struct_defs and key in struct_defs:
            return int(struct_defs[key].get("size") or 4)
    if norm.startswith("enum "):
        return 4
    if struct_defs and norm in struct_defs:
        return int(struct_defs[norm].get("size") or 4)
    return 4


def _parse_enum_body(body: str, macros: dict[str, int]) -> list[tuple[str, int]]:
    items = []
    current = -1
    local_names = dict(macros)
    for raw_item in body.split(','):
        item = _collapse_ws(raw_item)
        if not item:
            continue
        if '=' in item:
            name, expr = item.split('=', 1)
            name = name.strip()
            parsed = _safe_eval_c_expr(expr, local_names)
            if parsed is None:
                parsed = current + 1
            current = int(parsed)
        else:
            name = item.strip()
            current += 1
        if name:
            items.append((name, current))
            local_names[name] = current
    return items


def _enum_items_to_text(items: list[tuple[str, int]]) -> str:
    return " ".join(f'{int(val)}="{name}"' for name, val in items)


def _enum_dtype_from_items(items: list[tuple[str, int]], size_hint: int = 0) -> str:
    if size_hint == 1:
        return "enum_u8"
    if size_hint == 2:
        return "enum_u16"
    if size_hint == 4:
        return "enum_u32"
    max_val = max((abs(int(v)) for _, v in items), default=0)
    if max_val <= 0xFF:
        return "enum_u8"
    if max_val <= 0xFFFF:
        return "enum_u16"
    return "enum_u32"


def _collect_type_definitions(file_paths: list[Path]) -> tuple[dict[str, int], dict[str, list[tuple[str, int]]], dict[str, dict], dict[str, str]]:
    macros: dict[str, int] = {}
    enum_defs: dict[str, list[tuple[str, int]]] = {}
    struct_defs: dict[str, dict] = {}
    type_aliases: dict[str, str] = {}
    raw_struct_defs: dict[str, str] = {}

    enum_pat = re.compile(r'typedef\s+enum(?:\s+(\w+))?\s*\{(.*?)\}\s*(\w+)\s*;', re.S)
    enum_pat2 = re.compile(r'enum\s+(\w+)\s*\{(.*?)\}\s*;', re.S)
    struct_pat = re.compile(r'typedef\s+struct(?:\s+(\w+))?\s*\{(.*?)\}\s*(\w+)\s*;', re.S)
    struct_pat2 = re.compile(r'struct\s+(\w+)\s*\{(.*?)\}\s*;', re.S)

    for file_path in file_paths:
        try:
            raw_text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        clean = _strip_c_comments(raw_text)

        for m in re.finditer(r'^\s*#\s*define\s+(\w+)\s+([^\r\n]+)', clean, re.M):
            name, expr = m.group(1), m.group(2).strip()
            val = _safe_eval_c_expr(expr, macros)
            if val is not None:
                macros[name] = val

        for pat in (enum_pat, enum_pat2):
            for m in pat.finditer(clean):
                tag = (m.group(1) or "").strip()
                body = m.group(2)
                alias = (m.group(3) or "").strip() if pat is enum_pat else ""
                items = _parse_enum_body(body, macros)
                if tag:
                    enum_defs[tag] = items
                if alias:
                    enum_defs[alias] = items
                    if tag:
                        type_aliases[alias] = f"enum {tag}"

        for pat in (struct_pat, struct_pat2):
            for m in pat.finditer(clean):
                tag = (m.group(1) or "").strip()
                body = m.group(2)
                alias = (m.group(3) or "").strip() if pat is struct_pat else ""
                names = [n for n in (tag, alias) if n]
                if not names:
                    continue
                for nm in names:
                    raw_struct_defs[nm] = body
                if tag and alias:
                    type_aliases[alias] = f"struct {tag}"

        for stmt in _iter_top_level_statements(clean):
            stmt_s = _collapse_ws(stmt)
            if not stmt_s.startswith('typedef '):
                continue
            if '{' in stmt_s or '}' in stmt_s or '(' in stmt_s:
                continue
            tail = _collapse_ws(stmt_s[len('typedef '):])
            mm = re.match(r'^(.*?)\b([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)$', tail)
            if not mm:
                continue
            type_part = _collapse_ws(mm.group(1))
            alias = (mm.group(2) or '').strip()
            if not type_part or not alias or '*' in type_part:
                continue
            if alias != type_part:
                type_aliases[alias] = type_part

    building: set[str] = set()

    def _ensure_struct_def(struct_name: str) -> dict | None:
        resolved_name = _resolve_typedef_type(struct_name, type_aliases) or struct_name
        key = resolved_name.split(None, 1)[1].strip() if resolved_name.startswith('struct ') else resolved_name
        if key in struct_defs:
            return struct_defs[key]
        body = raw_struct_defs.get(key)
        if body is None:
            body = raw_struct_defs.get(struct_name)
            key = struct_name if body is not None else key
        if body is None:
            return None
        if key in building:
            return {"name": key, "members": [], "size": 4}

        building.add(key)
        members = []
        offset = 0
        bit_cursor = None

        for raw_stmt in body.split(';'):
            stmt = _collapse_ws(raw_stmt)
            if not stmt or '(' in stmt or '{' in stmt or '}' in stmt:
                continue
            mm = re.match(r'^(.*?)\b([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)\s*(?::\s*(\d+))?$', stmt)
            if not mm:
                continue

            raw_type = _collapse_ws(mm.group(1))
            member_name = mm.group(2)
            array_text = mm.group(3)
            bit_size = int(mm.group(4) or 0)
            dims = _parse_array_dims_text(array_text, macros)
            resolved_type = _resolve_typedef_type(raw_type, type_aliases) or raw_type

            enum_key = _detect_enum_key(resolved_type, enum_defs)
            struct_ref = _detect_struct_key(resolved_type, struct_defs)
            if not struct_ref:
                if resolved_type.startswith('struct '):
                    nested_key = resolved_type.split(None, 1)[1].strip()
                else:
                    nested_key = resolved_type
                if nested_key in raw_struct_defs:
                    _ensure_struct_def(nested_key)
                    struct_ref = _detect_struct_key(nested_key, struct_defs)

            if struct_ref:
                nested_def = _ensure_struct_def(struct_ref)
                elem_size = int((nested_def or {}).get('size') or 4)
            else:
                elem_size = _sizeof_c_type(resolved_type, struct_defs)

            align = 1 if bit_size else min(max(elem_size, 1), 8)
            total_count = 1
            for dim in dims:
                total_count *= max(1, int(dim))
            total_size = max(1, elem_size) * max(1, total_count)

            bit_offset = ""
            if bit_size:
                container_bits = max(8, elem_size * 8)
                if bit_cursor is None or bit_cursor["type"] != resolved_type or bit_cursor["used"] + bit_size > container_bits:
                    if offset % align:
                        offset += align - (offset % align)
                    bit_cursor = {"type": resolved_type, "base_offset": offset, "used": 0, "container_bits": container_bits}
                    offset += elem_size
                member_offset = int(bit_cursor["base_offset"])
                bit_offset = str(int(bit_cursor["used"]))
                bit_cursor["used"] += bit_size
            else:
                bit_cursor = None
                if offset % align:
                    offset += align - (offset % align)
                member_offset = int(offset)
                offset += total_size

            size_hint = max(elem_size, 1)
            if enum_key:
                items = enum_defs.get(enum_key, [])
                dtype_raw = _enum_dtype_from_items(items, size_hint)
                enum_text = _enum_items_to_text(items)
            elif dims:
                kind = _infer_kind_from_c_type(resolved_type, size_hint)
                dtype_raw = _kind_to_dtype_raw(kind, "array")
                enum_text = ""
            else:
                kind = _infer_kind_from_c_type(resolved_type, size_hint)
                dtype_raw = _kind_to_dtype_raw(kind, "struct")
                enum_text = ""

            members.append({
                "name": member_name,
                "declared_c_type": raw_type,
                "resolved_c_type": resolved_type,
                "c_type": resolved_type,
                "struct_ref": struct_ref,
                "enum_key": enum_key,
                "offset": member_offset,
                "bit_size": str(bit_size) if bit_size else "",
                "bit_offset": bit_offset,
                "array_dims": dims,
                "dtype_raw": dtype_raw,
                "enum_text": enum_text,
                "elem_size": int(elem_size),
            })

        struct_size = int(offset or 0)
        struct_def = {"name": key, "members": members, "size": struct_size}
        struct_defs[key] = struct_def
        building.discard(key)
        return struct_def

    for struct_name in list(raw_struct_defs.keys()):
        struct_def = _ensure_struct_def(struct_name)
        if struct_def is not None:
            struct_defs[struct_name] = struct_def

    return macros, enum_defs, struct_defs, type_aliases


def _iter_top_level_statements(text: str):
    buf = []
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth = max(0, depth - 1)
        if ch == ';' and depth == 0:
            stmt = ''.join(buf).strip()
            if stmt:
                yield stmt
            buf = []
        else:
            buf.append(ch)


def _extract_decl_type(prefix: str) -> tuple[str, str]:
    prefix = _collapse_ws(prefix)
    if not prefix:
        return "", ""
    tokens = prefix.split()
    qualifiers = []
    base_tokens = []
    for tok in tokens:
        if tok.lower() in _C_DECL_QUALIFIERS:
            qualifiers.append(tok)
        else:
            base_tokens.append(tok)
    return _collapse_ws(' '.join(base_tokens)), _collapse_ws(' '.join(qualifiers))


def _resolve_typedef_type(c_type: str, type_aliases: dict[str, str] | None = None) -> str:
    """沿着 typedef 链一路展开，尽量拿到最原始的类型声明。"""
    current = _collapse_ws(c_type).replace(" *", "*")
    if not current or not type_aliases:
        return current
    seen = set()
    while current and current not in seen:
        seen.add(current)
        nxt = type_aliases.get(current)
        if not nxt:
            break
        current = _collapse_ws(nxt).replace(" *", "*")
    return current


def _detect_struct_key(c_type: str, struct_defs: dict[str, dict]) -> str:
    norm = _collapse_ws(c_type)
    if not norm:
        return ""
    if norm.startswith("struct "):
        key = norm.split(None, 1)[1].strip()
        if key in struct_defs:
            return key
    if norm in struct_defs:
        return norm
    return ""


def _detect_enum_key(c_type: str, enum_defs: dict[str, list[tuple[str, int]]]) -> str:
    norm = _collapse_ws(c_type)
    if not norm:
        return ""
    if norm.startswith("enum "):
        key = norm.split(None, 1)[1].strip()
        if key in enum_defs:
            return key
    if norm in enum_defs:
        return norm
    return ""


def _make_auto_signal(base_name: str, short_name: str, object_type: str,
                      dtype_raw: str, array_dims: list[int] | None = None,
                      addr_offset: int = 0, bit_offset: str = "", bit_size: str = "",
                      bitmask_text: str = "", description: str = "",
                      declared_c_type: str = "", resolved_c_type: str = "",
                      enum_type_name: str = "", struct_type_name: str = "",
                      elem_bytes: int = 0, member_bytes: int = 0) -> dict[str, str]:
    group = derive_group_from_name(short_name or base_name)
    obj = "CAL" if object_type == "CALIBRATION" else "MEAS"
    sig = {col: "" for col in XLSX_COLS}
    sig["Group"] = group
    sig["Type"] = obj
    sig["Name"] = base_name
    sig["ShortName"] = short_name
    sig["LongName"] = short_name or base_name
    sig["DataType"] = dtype_raw
    sig["ObjectType"] = object_type
    sig["ArraySize"] = _dims_to_text(array_dims or [1])
    sig["AddrOffset"] = str(int(addr_offset or 0)) if int(addr_offset or 0) else ""
    sig["BitOffset"] = str(bit_offset).strip()
    sig["BitSize"] = str(bit_size).strip()
    sig["BitMask"] = bitmask_text or ""
    sig["Description"] = description or f"auto-scanned: {dtype_raw}"
    sig["_declared_c_type"] = _normalize_type_label(declared_c_type)
    sig["_resolved_c_type"] = _normalize_type_label(resolved_c_type)
    # 记录偏移/位域的来源：DWARF 展开 vs 后续被 XLSX 手工填写。
    # 报告要据此区分「自动推导」与「人工指定」，避免把两者混为一谈。
    sig["_offset_source"] = "dwarf" if int(addr_offset or 0) else ""
    sig["_bit_source"] = "dwarf" if str(bit_size).strip() else ""
    sig["_enum_type_name"] = str(enum_type_name or "").strip()
    sig["_struct_type_name"] = str(struct_type_name or "").strip()
    # 数组元素的真实字节宽度（DWARF 元素为结构体/联合体时 > 1），
    # A2L 生成阶段据此判断是否需要按符号实际字节数对齐（方案 B）。
    sig["_elem_bytes"] = str(int(elem_bytes or 0))
    # 成员自身占用的字节数（元素大小 × 元素个数）。
    # 结构体成员信号的 size 参数是整个结构体的大小，不能拿它推导成员维度。
    sig["_member_bytes"] = str(int(member_bytes or 0))
    return sig


def _append_struct_member_signals(out_list: list[dict], base_name: str, prefix_name: str,
                                  struct_name: str, struct_defs: dict[str, dict],
                                  object_type: str, base_offset: int = 0,
                                  source_desc: str = "") -> None:
    struct_def = struct_defs.get(struct_name)
    if not struct_def:
        return
    for member in struct_def.get("members", []):
        member_name = f"{prefix_name}.{member['name']}" if prefix_name else f"{base_name}.{member['name']}"
        member_offset = int(base_offset) + int(member.get("offset") or 0)
        nested_ref = member.get("struct_ref")
        dims = member.get("array_dims") or []
        if nested_ref and not dims:
            _append_struct_member_signals(
                out_list, base_name, member_name, str(nested_ref), struct_defs,
                object_type, member_offset, source_desc
            )
            continue
        # 结构体/联合体数组：逐元素展开成员，否则整个数组会被误当成单变量。
        if nested_ref and dims and dims != [1]:
            element_count = _dims_product(dims)
            for flat_index in range(element_count):
                indexed_name = f"{member_name}{_index_suffix_from_dims(flat_index, dims)}"
                _append_struct_member_signals(
                    out_list, base_name, indexed_name, str(nested_ref), struct_defs,
                    object_type, member_offset + flat_index * int(member.get("elem_size") or 0),
                    source_desc
                )
            continue
        decl_type_text = member.get("declared_c_type") or member.get("c_type") or "uint32_t"
        decl_text = f"{decl_type_text} {member_name}{_dims_to_decl_suffix(list(dims))}"
        out_list.append(_make_auto_signal(
            base_name=base_name,
            short_name=member_name,
            object_type=object_type,
            dtype_raw=str(member.get("dtype_raw") or "struct_u32"),
            array_dims=list(dims),
            addr_offset=member_offset,
            bit_offset=str(member.get("bit_offset") or ""),
            bit_size=str(member.get("bit_size") or ""),
            bitmask_text=str(member.get("enum_text") or ""),
            description=decl_text,
            declared_c_type=str(member.get("declared_c_type") or member.get("c_type") or ""),
            resolved_c_type=str(member.get("resolved_c_type") or member.get("c_type") or ""),
            enum_type_name=str(member.get("enum_key") or ""),
            struct_type_name=str(struct_name or ""),
        ))


def build_signals_from_project(root_path: str,
                               sym_details: dict[str, dict[str, object]] | None = None,
                               log_cb=None) -> list[dict]:
    """从工程根目录递归扫描 .c/.h，自动识别变量、枚举、结构体、数组。"""
    root = Path(root_path)
    if not root.is_dir():
        raise ValueError(tf("Project root directory does not exist: {n1}", n1=root_path))

    file_paths = [
        p for p in root.rglob('*')
        if p.is_file()
        and p.suffix.lower() in ('.c', '.h')
        and not any(part in _C_SCAN_EXCLUDE_DIRS for part in p.parts)
    ]
    if not file_paths:
        raise ValueError(tr("No .c / .h files found under the project root directory"))

    macros, enum_defs, struct_defs, type_aliases = _collect_type_definitions(file_paths)
    known_symbols = set(sym_details.keys()) if sym_details else set()
    signal_map: dict[tuple[str, str], dict] = {}
    source_hits = set()

    for file_path in file_paths:
        try:
            raw_text = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        clean = _strip_c_comments(raw_text)
        rel_path = str(file_path.relative_to(root)).replace('\\', '/')

        for stmt in _iter_top_level_statements(clean):
            stmt_s = _collapse_ws(stmt)
            if not stmt_s or stmt_s.startswith('#') or stmt_s.startswith('typedef'):
                continue
            if '(' in stmt_s or '{' in stmt_s or '}' in stmt_s:
                continue

            identifiers = set(re.findall(r'\b[A-Za-z_]\w*\b', stmt_s))
            if known_symbols:
                candidates = sorted(identifiers & known_symbols, key=len, reverse=True)
            else:
                candidates = re.findall(r'\b[A-Za-z_]\w*\b', stmt_s)
                candidates = [candidates[-1]] if candidates else []

            for symbol_name in candidates:
                if not _is_user_symbol(symbol_name):
                    continue
                try:
                    prefix, suffix = stmt_s.split(symbol_name, 1)
                except ValueError:
                    continue
                base_type, qualifiers = _extract_decl_type(prefix)
                if not base_type or '*' in base_type:
                    continue
                resolved_type = _resolve_typedef_type(base_type, type_aliases) or base_type
                array_dims = _parse_array_dims_text(suffix, macros)
                meta = sym_details.get(symbol_name, {}) if sym_details else {}
                if meta.get("object_type"):
                    object_type = _normalize_object_type_label(meta.get("object_type"))
                else:
                    object_type = _classify_object_type(
                        symbol_name,
                        str(meta.get('section') or ''),
                        qualifiers,
                    )
                decl_suffix = _dims_to_decl_suffix(array_dims)
                decl_type_text = base_type or resolved_type
                decl_text = f"{decl_type_text} {symbol_name}{decl_suffix}".strip()
                desc = decl_text
                source_hits.add(symbol_name)

                struct_key = _detect_struct_key(resolved_type, struct_defs)
                enum_key = _detect_enum_key(resolved_type, enum_defs)

                if struct_key and not array_dims:
                    members = []
                    _append_struct_member_signals(
                        members, symbol_name, symbol_name, struct_key,
                        struct_defs, object_type, 0, desc
                    )
                    for sig in members:
                        signal_map[(sig['Name'], sig['ShortName'])] = sig
                    continue

                # 结构体数组：逐元素展开成员，避免把整个数组当成一个标量信号。
                if struct_key and array_dims and array_dims != [1]:
                    struct_def = struct_defs.get(struct_key) or {}
                    element_size = max(1, int(struct_def.get('size') or 0))
                    element_count = _dims_product(array_dims)
                    for flat_index in range(element_count):
                        index_suffix = _index_suffix_from_dims(flat_index, array_dims)
                        element_name = f"{symbol_name}{index_suffix}"
                        members = []
                        _append_struct_member_signals(
                            members, element_name, element_name, struct_key,
                            struct_defs, object_type, flat_index * element_size, desc
                        )
                        for sig in members:
                            signal_map[(sig['Name'], sig['ShortName'])] = sig
                    continue

                size_hint = int(meta.get('size') or 0)
                if array_dims:
                    total_count = 1
                    for dim in array_dims:
                        total_count *= max(1, int(dim))
                    elem_size = max(1, size_hint // total_count) if size_hint and total_count else _sizeof_c_type(resolved_type, struct_defs)
                    kind = _infer_kind_from_c_type(resolved_type, elem_size)
                    dtype_raw = _kind_to_dtype_raw(kind, 'array')
                    sig = _make_auto_signal(
                        symbol_name, symbol_name, object_type, dtype_raw, array_dims,
                        description=desc,
                        declared_c_type=base_type,
                        resolved_c_type=resolved_type,
                        struct_type_name=struct_key,
                    )
                    signal_map[(sig['Name'], sig['ShortName'])] = sig
                    continue

                if enum_key:
                    items = enum_defs.get(enum_key, [])
                    dtype_raw = _enum_dtype_from_items(items, size_hint)
                    sig = _make_auto_signal(
                        symbol_name, symbol_name, object_type, dtype_raw,
                        [1], bitmask_text=_enum_items_to_text(items), description=desc,
                        declared_c_type=base_type,
                        resolved_c_type=resolved_type,
                        enum_type_name=enum_key,
                    )
                    signal_map[(sig['Name'], sig['ShortName'])] = sig
                    continue

                kind = _infer_kind_from_c_type(resolved_type, size_hint or _sizeof_c_type(resolved_type, struct_defs))
                if kind == 'ptr':
                    continue
                dtype_raw = _kind_to_dtype_raw(kind, 'scalar')
                sig = _make_auto_signal(
                    symbol_name, symbol_name, object_type, dtype_raw, [1], description=desc,
                    declared_c_type=base_type,
                    resolved_c_type=resolved_type,
                    struct_type_name=struct_key,
                )
                signal_map[(sig['Name'], sig['ShortName'])] = sig

    if sym_details:
        for name, meta in sym_details.items():
            if not _is_user_symbol(name):
                continue
            if name in source_hits:
                continue
            size = int(meta.get('size') or 0)
            if size in (2, 4, 8):
                kind = {2: 'u16', 4: 'u32', 8: 'u64'}[size]
            else:
                kind = 'u8'
            if size > 8:
                dtype_raw = 'array_u8'
                dims = [size]
            else:
                dtype_raw = _kind_to_dtype_raw(kind, 'scalar')
                dims = [1]
            object_type = _normalize_object_type_label(meta.get('object_type') or 'MEASUREMENT')
            desc = f"auto-detected from {meta.get('source') or 'ADDR'} section={meta.get('section') or '-'}"
            sig = _make_auto_signal(name, name, object_type, dtype_raw, dims, description=desc)
            signal_map[(sig['Name'], sig['ShortName'])] = sig

    signals = list(signal_map.values())
    base_names_with_members = {
        str(s.get('Name') or '').strip()
        for s in signals
        if str(s.get('ShortName') or '').strip().startswith(f"{str(s.get('Name') or '').strip()}.")
    }
    if base_names_with_members:
        signals = [
            s for s in signals
            if not (
                str(s.get('Name') or '').strip() in base_names_with_members
                and str(s.get('ShortName') or '').strip() == str(s.get('Name') or '').strip()
            )
        ]
    signals.sort(key=lambda s: (s.get('Group', 'Other'), s.get('ObjectType', 'MEASUREMENT'), s.get('ShortName') or s.get('Name')))

    if log_cb:
        n_enum = sum(1 for s in signals if str(s.get('DataType', '')).startswith('enum_'))
        n_struct = sum(1 for s in signals if '.' in str(s.get('ShortName', '')))
        n_array = sum(1 for s in signals if str(s.get('ArraySize', '1')).strip() not in ('', '1'))
        log_cb(tf("   📁 Scanned {n1} source files, identified {n2} candidate signals", n1=len(file_paths), n2=len(signals)))
        log_cb(tf("   ├─ Enum: {n1}", n1=n_enum))
        log_cb(tf("   ├─ Struct members: {n1}", n1=n_struct))
        log_cb(tf("   └─ Arrays/Matrices: {n1}", n1=n_array))

    return signals


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          A2L 块生成                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except Exception:
        return default


def _safe_limit(val, default=None) -> float | None:
    """安全解析限值，空白字符串返回 None。"""
    if val is None:
        return None
    if isinstance(val, str) and not val.strip():
        return None
    try:
        return float(val)
    except Exception:
        return default


def _get_dtype_limits(dtype: str) -> tuple[float, float]:
    """返回指定数据类型的最小和最大允许值。"""
    if dtype == "UBYTE":
        return 0.0, 255.0
    if dtype == "SBYTE":
        return -128.0, 127.0
    if dtype == "UWORD":
        return 0.0, 65535.0
    if dtype == "SWORD":
        return -32768.0, 32767.0
    if dtype == "ULONG":
        return 0.0, 4294967295.0
    if dtype == "SLONG":
        return -2147483648.0, 2147483647.0
    if dtype == "A_UINT64":
        return 0.0, 18446744073709551615.0
    if dtype == "A_INT64":
        return -9223372036854775808.0, 9223372036854775807.0
    if dtype == "FLOAT32_IEEE":
        return -3.4028235E+38, 3.4028235E+38
    if dtype == "FLOAT64_IEEE":
        return -1.7976931348623157E+308, 1.7976931348623157E+308
    return 0.0, 65535.0


def _format_limit(value: float, dtype: str) -> str:
    """
    根据数据类型格式化限值。
    整型：无小数点格式（如 100）
    浮点型：科学计数法（如 1.0E+02）
    """
    # 整型数据类型
    integer_types = ("UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "A_UINT64", "A_INT64")
    
    if dtype in integer_types:
        # 整型：格式化为整数，无小数点
        return str(int(round(value)))
    else:
        # 浮点型：使用科学计数法，保留1位小数
        return f"{value:.1E}"


# A2L数据类型 → RECORD_LAYOUT 和 COMPU_METHOD映射
DTYPE_TO_RECORD_LAYOUT = {
    "UBYTE":         "Scalar_UBYTE",
    "SBYTE":         "Scalar_SBYTE",
    "UWORD":         "Scalar_UWORD",
    "SWORD":         "Scalar_SWORD",
    "ULONG":         "Scalar_ULONG",
    "SLONG":         "Scalar_SLONG",
    "FLOAT32_IEEE":  "Scalar_FLOAT32_IEEE",
    "FLOAT64_IEEE":  "Scalar_FLOAT64_IEEE",
    "A_UINT64":      "Scalar_ULONG",  # 回退到 ULONG
    "A_INT64":       "Scalar_SLONG",  # 回退到 SLONG
}

# 数据类型 → COMPU_METHOD名称映射
# 注意：这里刻意**不复用头文件里已有的 CM_uint8 / CM_uint16 ...**。
# 头文件的 COMPU_METHOD 是 CANape 的旧式写法（RAT_FUNC 且缺 COMPU_TAB_REF），
# 按 ASAM-2MC 1.6 RAT_FUNC 必须带 COMPU_TAB_REF，缺了 INCA 会判定该转换方法
# 无效，引用它的 measurement 整体不可用——表现为观测量恒为 0 且不刷新。
# 新生成的信号一律使用工具自建的标准 LINEAR 转换（COEFFS_LINEAR 1 0，语义等价
# 于恒等），名字加 CM_A2L_ 前缀以免与头文件已有定义重名。
DTYPE_TO_COMPU = {
    "UBYTE":         "CM_A2L_uint8",
    "SBYTE":         "CM_A2L_int8",
    "UWORD":         "CM_A2L_uint16",
    "SWORD":         "CM_A2L_int16",
    "ULONG":         "CM_A2L_uint32",
    "SLONG":         "CM_A2L_int32",
    "FLOAT32_IEEE":  "CM_A2L_single",
    "FLOAT64_IEEE":  "CM_A2L_double",
    "A_UINT64":      "CM_A2L_uint32",
    "A_INT64":       "CM_A2L_int32",
}

DTYPE_TO_FORMAT = {
    "UBYTE": "%3d",
    "SBYTE": "%3d",
    "UWORD": "%5d",
    "SWORD": "%5d",
    "ULONG": "%10d",
    "SLONG": "%10d",
    "A_UINT64": "%10d",
    "A_INT64": "%10d",
    "FLOAT32_IEEE": "%6.3f",
    "FLOAT64_IEEE": "%10.6f",
}

LEGACY_XCP_PAGE_ACCESS_TOKENS = {
    "ECU_ACCESS_DONT_CARE": "ECU_ACCESS_ALLOWED_DONT_CARE",
    "XCP_READ_ACCESS_DONT_CARE": "XCP_READ_ACCESS_ALLOWED_DONT_CARE",
    "XCP_WRITE_ACCESS_DONT_CARE": "XCP_WRITE_ACCESS_ALLOWED_DONT_CARE",
}


def _detect_a2l_encoding(raw: bytes) -> str:
    """探测 A2L 文件编码，保证读入后能按原编码写回，不破坏原文件内容。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "gbk", "cp1252"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"      # 兜底：逐字节可还原，绝不丢字符


def read_a2l_text_preserving_format(path: str) -> tuple[str, str, str]:
    """读取 A2L 文本，返回 (文本, 编码, 换行符)。

    不使用 errors="replace"，避免把原文件里的非法字节替换成 U+FFFD；
    换行符按原文件保持（CRLF 或 LF），保证输出时原始行不被改写。
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    encoding = _detect_a2l_encoding(raw)
    text = raw.decode(encoding, errors="strict")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, encoding, newline


def write_a2l_text_preserving_format(path: str, text: str, encoding: str, newline: str) -> None:
    """按原编码/换行符写回 A2L 文本。"""
    with open(path, "w", encoding=encoding, errors="replace", newline="") as fh:
        fh.write(text.replace("\r\n", "\n").replace("\n", newline))


def normalize_newlines(text: str, newline: str) -> str:
    """把新生成的内容统一成原文件的换行风格。"""
    if newline == "\n":
        return text.replace("\r\n", "\n")
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def build_minimal_a2l_header(insert_body: str = "", project_name: str = "A2L_Project",
                             module_name: str = "MODULE_001") -> str:
    """构造一个尽量通用于 INCA/CANape 的最小头文件。

    该模板刻意不携带厂商专有 A2ML/IF_DATA，避免导入工具对
    IF_DATA 枚举、传输层描述、内存页权限等扩展语法的严格校验差异。
    """
    safe_project = _safe_identifier(project_name, "A2L_Project")
    safe_module = _safe_identifier(module_name, "MODULE_001")
    body = (insert_body or "").strip("\n")
    body_block = f"\n{body}\n" if body else "\n"
    return (
        f'ASAP2_VERSION 1 60\n'
        f'/begin PROJECT {safe_project} ""\n'
        '  /begin HEADER\n'
        '    "Portable A2L header"\n'
        '    VERSION "1.0"\n'
        '    PROJECT_NO "AUTO"\n'
        '  /end HEADER\n'
        '\n'
        f'  /begin MODULE {safe_module} ""\n'
        '    /begin MOD_COMMON ""\n'
        '      BYTE_ORDER MSB_LAST\n'
        '      ALIGNMENT_BYTE 1\n'
        '      ALIGNMENT_WORD 2\n'
        '      ALIGNMENT_LONG 4\n'
        '      ALIGNMENT_FLOAT32_IEEE 4\n'
        '      ALIGNMENT_FLOAT64_IEEE 8\n'
        '    /end MOD_COMMON\n'
        '\n'
        '    /* ============== RECORD_LAYOUT definitions ============== */\n'
        '    /begin RECORD_LAYOUT Scalar_UBYTE\n'
        '      FNC_VALUES 1 UBYTE ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_SBYTE\n'
        '      FNC_VALUES 1 SBYTE ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_UWORD\n'
        '      FNC_VALUES 1 UWORD ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_SWORD\n'
        '      FNC_VALUES 1 SWORD ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_ULONG\n'
        '      FNC_VALUES 1 ULONG ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_SLONG\n'
        '      FNC_VALUES 1 SLONG ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_FLOAT32_IEEE\n'
        '      FNC_VALUES 1 FLOAT32_IEEE ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /begin RECORD_LAYOUT Scalar_FLOAT64_IEEE\n'
        '      FNC_VALUES 1 FLOAT64_IEEE ROW_DIR DIRECT\n'
        '    /end RECORD_LAYOUT\n'
        '\n'
        '    /* ============== COMPU_METHOD definitions ============== */\n'
        '    /begin COMPU_METHOD CM_uint8\n'
        '      "uint8 conversion"\n'
        '      LINEAR\n'
        '      "%3d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_int8\n'
        '      "int8 conversion"\n'
        '      LINEAR\n'
        '      "%3d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_uint16\n'
        '      "uint16 conversion"\n'
        '      LINEAR\n'
        '      "%5d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_int16\n'
        '      "int16 conversion"\n'
        '      LINEAR\n'
        '      "%5d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_uint32\n'
        '      "uint32 conversion"\n'
        '      LINEAR\n'
        '      "%10d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_int32\n'
        '      "int32 conversion"\n'
        '      LINEAR\n'
        '      "%10d"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_single\n'
        '      "float32 IEEE conversion"\n'
        '      LINEAR\n'
        '      "%6.3f"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        '\n'
        '    /begin COMPU_METHOD CM_double\n'
        '      "float64 IEEE conversion"\n'
        '      LINEAR\n'
        '      "%10.6f"\n'
        '      ""\n'
        '      COEFFS_LINEAR 1 0\n'
        '    /end COMPU_METHOD\n'
        f'{body_block}'
        '  /end MODULE\n'
        '/end PROJECT\n'
    )


def validate_canape_header_compatibility(a2l_text: str) -> list[str]:
    """对头文件做静态兼容性检查，优先捕获 CANape 更敏感的问题。"""
    text = str(a2l_text or "")
    findings = []

    for legacy_token, expected_token in LEGACY_XCP_PAGE_ACCESS_TOKENS.items():
        if legacy_token in text:
            findings.append(
                f'Legacy XCP PAGE token "{legacy_token}" should be "{expected_token}".'
            )

    for required_marker in ("/end MODULE", "/end PROJECT"):
        if required_marker not in text:
            findings.append(f'Missing required closing marker: {required_marker}')

    stack = []
    for match in re.finditer(r'/\s*(begin|end)\s+([A-Za-z_][A-Za-z0-9_]*)', text):
        action = match.group(1).lower()
        block_name = match.group(2).upper()
        if action == "begin":
            stack.append(block_name)
            continue
        if not stack:
            findings.append(f'Unexpected closing block: /end {block_name}')
            continue
        last_block = stack.pop()
        if last_block != block_name:
            findings.append(f'Block mismatch: /begin {last_block} closed by /end {block_name}')
            break

    if stack:
        findings.append(f'Unclosed A2L block(s): {", ".join(stack[:5])}')

    mem_ids = re.findall(r'^\s*/begin MEMORY_SEGMENT\s+([A-Za-z_][A-Za-z0-9_]*)', text, re.M)
    if mem_ids:
        duplicates = [name for name, count in Counter(mem_ids).items() if count > 1]
        for name in duplicates[:10]:
            findings.append(f'Duplicate MEMORY_SEGMENT identifier: {name}')

    xcp_segment_numbers = []
    for match in re.finditer(r'/begin IF_DATA XCP\s*[\r\n]+\s*/begin SEGMENT\s*[\r\n]+\s*(0x[0-9A-Fa-f]+|\d+)', text):
        raw_num = match.group(1)
        try:
            xcp_segment_numbers.append(int(raw_num, 0))
        except Exception:
            findings.append(f'Invalid XCP SEGMENT number: {raw_num}')
    if xcp_segment_numbers:
        dup_nums = [num for num, count in Counter(xcp_segment_numbers).items() if count > 1]
        for num in dup_nums[:10]:
            findings.append(f'Duplicate XCP SEGMENT number: 0x{num:02X}')

        pag_match = re.search(r'(^\s*/begin PAG\s*$\s*^\s*)(0x[0-9A-Fa-f]+|\d+)', text, re.M)
        if pag_match:
            try:
                pag_segments = int(pag_match.group(2), 0)
                if pag_segments != len(xcp_segment_numbers):
                    findings.append(
                        f'PAG MAX_SEGMENTS mismatch: declared {pag_match.group(2)}, actual {len(xcp_segment_numbers)}'
                    )
            except Exception:
                findings.append(f'Invalid PAG MAX_SEGMENTS value: {pag_match.group(2)}')

    return findings


def normalize_legacy_canape_header_tokens(a2l_text: str) -> tuple[str, list[str]]:
    """修正旧头文件中常见的 CANape 不兼容 XCP token。"""
    text = str(a2l_text or "")
    replacements = []
    for legacy_token, expected_token in LEGACY_XCP_PAGE_ACCESS_TOKENS.items():
        if legacy_token in text:
            text = text.replace(legacy_token, expected_token)
            replacements.append(f"{legacy_token} -> {expected_token}")
    return text, replacements


def repair_a2l_header_for_tool_compatibility(a2l_text: str) -> tuple[str, list[str]]:
    """基于已有 A2L 头文件做保守修复，使其更容易被 INCA/CANape 接受。"""
    text, repair_log = normalize_legacy_canape_header_tokens(a2l_text)

    memory_segment_index = 0
    xcp_segment_index = 0
    used_memory_ids: set[str] = set()

    def _format_segment_no(value: int) -> str:
        return f"0x{value:02X}" if value <= 0xFF else f"0x{value:04X}"

    def _repair_memory_segment(match: re.Match) -> str:
        nonlocal memory_segment_index, xcp_segment_index

        indent = match.group("indent")
        old_id = match.group("id")
        body = match.group("body")

        addr_match = re.search(r'\b(?:INTERN|EXTERN)\s+(0x[0-9A-Fa-f]+)\s+(0x[0-9A-Fa-f]+)', body)
        addr_suffix = None
        if addr_match:
            addr_suffix = addr_match.group(1).upper().replace("0X", "")

        candidate = old_id if addr_suffix is None else f"{old_id}_{addr_suffix}"
        candidate = _safe_identifier(candidate, old_id)
        if candidate in used_memory_ids:
            candidate = f"{candidate}_{memory_segment_index:03d}"
        used_memory_ids.add(candidate)
        memory_segment_index += 1
        if candidate != old_id:
            repair_log.append(f"MEMORY_SEGMENT {old_id} -> {candidate}")

        if re.search(r'/begin IF_DATA XCP\s*[\r\n]+\s*/begin SEGMENT\s*[\r\n]+\s*(0x[0-9A-Fa-f]+|\d+)', body):
            new_segment_no = _format_segment_no(xcp_segment_index)
            body, replaced = re.subn(
                r'(/begin IF_DATA XCP\s*[\r\n]+\s*/begin SEGMENT\s*[\r\n]+\s*)(0x[0-9A-Fa-f]+|\d+)',
                lambda m: f"{m.group(1)}{new_segment_no}",
                body,
                count=1,
            )
            if replaced:
                repair_log.append(f"XCP SEGMENT index -> {new_segment_no} for {candidate}")
                xcp_segment_index += 1

        return f"{indent}/begin MEMORY_SEGMENT {candidate}{body}{indent}/end MEMORY_SEGMENT"

    text = re.sub(
        r'(?ms)^(?P<indent>\s*)/begin MEMORY_SEGMENT\s+(?P<id>[A-Za-z_][A-Za-z0-9_]*)(?P<body>[\s\S]*?)^\s*/end MEMORY_SEGMENT',
        _repair_memory_segment,
        text,
    )

    if xcp_segment_index:
        new_pag_value = _format_segment_no(xcp_segment_index)
        text, pag_replaced = re.subn(
            r'(^\s*/begin PAG\s*$\s*^\s*)(0x[0-9A-Fa-f]+|\d+)',
            lambda m: f"{m.group(1)}{new_pag_value}",
            text,
            count=1,
            flags=re.M,
        )
        if pag_replaced:
            repair_log.append(f"PAG MAX_SEGMENTS -> {new_pag_value}")

    return text, repair_log

INTEGER_A2L_TYPES = {"UBYTE", "SBYTE", "UWORD", "SWORD", "ULONG", "SLONG", "A_UINT64", "A_INT64"}


def _format_coeff_value(value: float) -> str:
    if abs(value - round(value)) < 1e-12:
        return str(int(round(value)))
    return f"{value:.12g}"


def _fractional_digits_hint(raw: str) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    if "e" in text.lower():
        return 6
    if "." not in text:
        return 0
    return max(0, len(text.split(".", 1)[1].rstrip("0")))


def _build_linear_format(dtype: str, factor: float, offset: float,
                         factor_raw: str, offset_raw: str) -> str:
    default_fmt = DTYPE_TO_FORMAT.get(dtype, "%10d")
    if dtype not in INTEGER_A2L_TYPES:
        return default_fmt

    has_fractional_scaling = (
        abs(factor - round(factor)) > 1e-12 or
        abs(offset - round(offset)) > 1e-12
    )
    width_match = re.match(r"%(\d+)d$", default_fmt)
    base_width = int(width_match.group(1)) if width_match else 10
    if not has_fractional_scaling:
        return f"%{base_width + 2}.0f"

    decimals = max(1, min(6, max(_fractional_digits_hint(factor_raw), _fractional_digits_hint(offset_raw), 3)))
    return f"%{base_width + decimals + 1}.{decimals}f"


def _extract_compu_method_name(compu_text: str) -> str:
    """从 COMPU_METHOD 块文本里取出方法名（兼容新式注释字段与旧式首行标识）。"""
    if not compu_text:
        return ""
    m = re.search(r"Name of CompuMethod\s*\*/\s*([^\s\r\n]+)", compu_text)
    if m:
        return m.group(1)
    for line in compu_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("/begin") or line.startswith("/end"):
            continue
        tokens = line.split()
        if tokens:
            return tokens[0]
    return ""


def _extract_defined_compu_method_names(a2l_text: str) -> set[str]:
    """提取 A2L 文本中已定义的 COMPU_METHOD 名（兼容新旧两种写法）。"""
    names: set[str] = set()
    for body in re.findall(r"/begin\s+COMPU_METHOD\b(.*?)/end\s+COMPU_METHOD",
                            a2l_text or "", flags=re.S):
        name = _extract_compu_method_name(body)
        if name:
            names.add(name)
    return names


def _build_linear_compu_method(sig: dict, name: str, longname: str,
                               dtype: str, default_method: str) -> tuple[str, str]:
    unit = str(sig.get("Unit", "") or "").strip().replace('"', "'")
    factor_raw = str(sig.get("Factor", "") or "").strip()
    offset_raw = str(sig.get("Offset", "") or "").strip()
    has_custom = bool(unit or factor_raw or offset_raw)
    if not has_custom:
        return default_method, ""

    factor = _safe_float(factor_raw, 1.0) if factor_raw else 1.0
    offset = _safe_float(offset_raw, 0.0) if offset_raw else 0.0
    compu_method = f"{_safe_identifier(name, 'SIG')}_Method"
    method_title = (longname or name or compu_method).replace('"', "'")
    fmt = _build_linear_format(dtype, factor, offset, factor_raw, offset_raw)
    method_lines = [
        f"    /begin COMPU_METHOD",
        f"        /* Name of CompuMethod    */      {compu_method}",
        f"        /* Long identifier        */      \"{method_title}\"",
        f"        /* Conversion Type        */      LINEAR",
        f"        /* Format                 */      \"{fmt}\"",
        f"        /* Units                  */      \"{unit}\"",
        f"        COEFFS_LINEAR {_format_coeff_value(factor)} {_format_coeff_value(offset)}",
        f"    /end COMPU_METHOD\n",
    ]
    return compu_method, "\n".join(method_lines)


def _parse_enum_items(enum_text: str) -> list[tuple[str, str]]:
    """解析 Excel 中的枚举映射，例如：0="OFF" 1="RUN"。"""
    items = []
    if not enum_text:
        return items

    pattern = re.compile(r'([^\s=]+)\s*=\s*(?:"([^"]*)"|([^\s]+))')
    for match in pattern.finditer(str(enum_text)):
        raw_val = match.group(1).strip()
        raw_text = (match.group(2) if match.group(2) is not None else (match.group(3) or "")).strip()
        if raw_val:
            items.append((raw_val, raw_text))
    return items


def generate_a2l_block(sig: dict, address: int, size: int = 0) -> tuple[str, str]:
    """
    根据信号信息和地址生成 A2L MEASUREMENT 或 CHARACTERISTIC 块。
    格式符合 ASAP2 1.60 规范，与INCA兼容。
    支持数组；优先使用 ShortName 作为 A2L 显示名，适用于结构体成员和枚举成员。
    """
    symbol_name = str(sig.get("Name", "UNKNOWN")).strip() or "UNKNOWN"
    dtype_raw   = str(sig.get("DataType", "UWORD")).strip()
    is_struct_signal = dtype_raw.startswith("struct_")
    shortname   = str(sig.get("ShortName", "")).strip()
    is_member_alias = bool(shortname and shortname != symbol_name)
    name        = shortname or symbol_name
    longname    = str(sig.get("LongName", "")).strip()
    unit        = str(sig.get("Unit", "") or "").strip().replace('"', "'")
    vmin        = _safe_limit(sig.get("Min"))
    vmax        = _safe_limit(sig.get("Max"))
    dtype       = C_DTYPE_MAP.get(dtype_raw, "UWORD")
    otype       = _normalize_object_type_label(sig.get("ObjectType", "MEASUREMENT"))
    desc        = str(sig.get("Description", "")).strip()
    declared_type = _normalize_type_label(sig.get("_declared_c_type", ""))
    resolved_type = _normalize_type_label(sig.get("_resolved_c_type", ""))
    enum_type_name = str(sig.get("_enum_type_name", "")).strip()
    struct_type_name = str(sig.get("_struct_type_name", "")).strip()

    if not longname and desc:
        longname = desc
    elif (is_struct_signal or is_member_alias) and desc and desc not in longname:
        longname = f"{longname} | {desc}" if longname else desc
    type_notes = []
    if enum_type_name:
        type_notes.append(f"enum: {enum_type_name}")
    elif struct_type_name and is_member_alias:
        type_notes.append(f"struct: {struct_type_name}")
    if declared_type and resolved_type and declared_type != resolved_type:
        type_notes.append(f"type: {declared_type} -> {resolved_type}")
    elif resolved_type and resolved_type not in (declared_type, desc):
        type_notes.append(f"type: {resolved_type}")
    for note in type_notes:
        if note not in longname:
            longname = f"{longname} | {note}" if longname else note
    longname = longname.replace('"', "'")

    bit_size_hint = _safe_int_auto(sig.get("_bit_size", sig.get("BitSize", "")), 0)
    bit_shift = max(0, _safe_int_auto(sig.get("_bit_shift", sig.get("BitOffset", "")), 0))
    bit_mask_val = None
    raw_auto_mask = str(sig.get("_bit_mask", "")).strip()
    if raw_auto_mask:
        parsed_mask = _safe_int_auto(raw_auto_mask, None)
        bit_mask_val = parsed_mask if parsed_mask not in (None, 0) else None
    elif not str(dtype_raw).startswith("enum_"):
        raw_excel_mask = str(sig.get("BitMask", "")).strip()
        if raw_excel_mask and re.fullmatch(r"0[xX][0-9a-fA-F]+|\d+", raw_excel_mask):
            parsed_mask = _safe_int_auto(raw_excel_mask, None)
            bit_mask_val = parsed_mask if parsed_mask not in (None, 0) else None
    if bit_mask_val is None and bit_size_hint > 0:
        bit_mask_val = ((1 << bit_size_hint) - 1) << bit_shift
    
    # 获取数组维度/数组大小（支持 8、4x8、4 8 等写法）
    array_dims = _parse_array_dims_value(sig.get("ArraySize", "1"))
    array_size = 1
    for dim in array_dims:
        array_size *= max(1, int(dim))
    
    # 如果 map/elf 中有大小信息且未指定维度，则仅对“真实对象行”推导数组长度。
    # 对结构体成员别名（例如 Name=g_imu_config, ShortName=g_imu_config.accRange）默认保持标量，
    # 避免把整个结构体尺寸误判成单个成员的 MATRIX_DIM。
    _dtype_size_table = {
        "UBYTE": 1, "SBYTE": 1,
        "UWORD": 2, "SWORD": 2,
        "ULONG": 4, "SLONG": 4, "A_UINT64": 8, "A_INT64": 8,
        "FLOAT32_IEEE": 4, "FLOAT64_IEEE": 8
    }
    _element_bytes = _dtype_size_table.get(dtype, 0)

    # 【方案 C】符号实际占用小于 DWARF 声明的类型宽度（例如符号只有 1 字节，
    # DWARF 却声明为 unsigned long）：以符号实际大小为准降级为 UBYTE，
    # 避免越界读到相邻内存拼出假值。
    if size > 0 and 0 < size < _element_bytes and not is_member_alias:
        dtype = "UBYTE"
        dtype_raw = "uint8_t"
        _element_bytes = 1
        longname = f"{longname} | size-conflict: symbol={size}B".strip()

    # 【方案 B】DWARF 数组的元素是结构体/联合体时，元素宽度（结构体大小）大于 1。
    # 若仍按「元素个数」输出，A2L 声明的字节数会远小于符号实际占用，
    # INCA 只能读到数组头部（实测 576 字节的数组只声明了 16 字节）。
    # 此时改为按符号实际字节数输出字节流，保证读到的数据完整。
    _elem_bytes_hint = _safe_int_auto(sig.get("_elem_bytes", ""), 0)
    if (size > 0 and not is_member_alias
            and _elem_bytes_hint > _element_bytes):
        dtype = "UBYTE"
        dtype_raw = "array_u8"
        _element_bytes = 1
        array_dims = [size]
        array_size = size
        longname = f"{longname} | raw-bytes: {size}B".strip()

    if size > 0 and array_size == 1 and not is_member_alias:
        # 根据数据类型大小计算数组元素数量
        dtype_size = _element_bytes or 4  # 默认4字节
        if dtype_size > 0:
            inferred_count = size // dtype_size
            if inferred_count > 1:
                array_dims = [inferred_count]
                array_size = inferred_count
    elif size > 0 and array_size == 1 and is_member_alias:
        # 数组型 DataType（array_*）表示该成员本身是数组：依据元素字节数推导维度。
        # 注意：标量型的 struct_* 只说明它位于结构体内，并不代表是数组，必须保持单变量，
        # 否则会把成员的字节宽度误当成元素个数（如 struct_u16 成员的 4 字节 -> MATRIX_DIM 1 2）。
        # 关键：这里必须用「成员自身字节数」推导。size 是整个结构体的大小，
        # 用它反推会得到远大于成员实际占用的维度（如 48 字节结构体里的 4 字节成员
        # 被推成 12 维），读数时越界到结构体之外。
        if str(dtype_raw).startswith("array_") and _element_bytes > 0:
            _member_bytes = _safe_int_auto(sig.get("_member_bytes", ""), 0)
            # 成员自身字节数已知时以它为准（DWARF 维度缺失时也能推准）；
            # 未知时才退回按符号大小推导——此时靠下面的「结构体剩余空间」
            # 裁剪兜底，保证不会越界到结构体之外。
            _infer_bytes = _member_bytes if _member_bytes > 0 else size
            inferred_count = _infer_bytes // _element_bytes
            if inferred_count > 1:
                array_dims = [inferred_count]
                array_size = inferred_count

    is_array_signal = array_size > 1

    # 维度乘积不得超过该符号实际占用的元素容量（防止把未初始化的结构体尾部
    # 或对齐填充误当成额外的数组元素）。
    if size > 0 and is_array_signal and _element_bytes > 0:
        total_count = 1
        for dim in array_dims:
            total_count *= max(1, int(dim))
        # 成员信号的可读上限是「所属结构体末尾 - 成员偏移」；
        # 顶层信号则是符号本身的总容量。
        _cap_bytes = size
        if is_member_alias:
            _member_offset = _safe_int_auto(sig.get("AddrOffset", ""), 0)
            if _member_offset > 0:
                _cap_bytes = max(0, size - _member_offset)
        max_count = _cap_bytes // _element_bytes
        if max_count <= 0:
            array_dims = [1]
            array_size = 1
            is_array_signal = False
        elif total_count > max_count:
            array_dims = [max_count]
            array_size = max_count
    effective_dims = [max(1, int(dim)) for dim in array_dims if max(1, int(dim)) > 1]
    array_rank = len(effective_dims) if effective_dims else (1 if is_array_signal else 0)
    is_one_dim_array = is_array_signal and array_rank <= 1
    is_two_dim_array = is_array_signal and array_rank == 2
    is_curve_calibration = otype == "CALIBRATION" and is_one_dim_array
    is_map_calibration = otype == "CALIBRATION" and is_two_dim_array

    # 如果 Excel 没有填写限值，则使用数据类型的允许区间
    dtype_min, dtype_max = _get_dtype_limits(dtype)
    if vmin is None:
        vmin = dtype_min
    if vmax is None:
        vmax = dtype_max
    if bit_size_hint > 0:
        if str(sig.get("Min", "")).strip() == "":
            vmin = 0.0
        if str(sig.get("Max", "")).strip() == "":
            vmax = min(dtype_max, float((1 << bit_size_hint) - 1))
    
    # 获取对应的RECORD_LAYOUT和COMPU_METHOD
    record_layout = DTYPE_TO_RECORD_LAYOUT.get(dtype, "Scalar_UWORD")

    # 处理枚举：从 BitMask 中读取 0="OFF" 1="RUN" 这类映射，并生成 COMPU_METHOD + COMPU_VTAB
    compu_block = ""
    compu_method = DTYPE_TO_COMPU.get(dtype, "NO_COMPU_METHOD")
    if str(dtype_raw).startswith("enum_"):
        enum_items = _parse_enum_items(sig.get("BitMask", ""))
        if enum_items:
            enum_ref_name = enum_type_name or resolved_type or declared_type or name
            safe_name = _safe_identifier(enum_ref_name, "ENUM")
            compu_method = f"{safe_name}_Enum_Method"
            compu_vtab = f"{safe_name}_Vtab"
            # 同一个枚举类型在整个 A2L 里只允许有一份 COMPU_METHOD / COMPU_VTAB 定义，
            # 描述文本因此不能带信号名——否则同一枚举的不同信号会生成内容不同、
            # 但名字相同的块，INCA 的 ASAM-2MC 解析器会因重名而卡在“读入 ASAM-2MC”。
            long_text = f"Enumerated data type: {enum_ref_name}"
            long_text = long_text.replace('"', "'")

            method_lines = [
                f"    /begin COMPU_METHOD",
                f"        /* Name of CompuMethod    */      {compu_method}",
                f"        /* Long identifier        */      \"{long_text}\"",
                f"        /* Conversion Type        */      TAB_VERB",
                f"        /* Format                 */      \"%s\"",
                f"        /* Units                  */      \"{unit}\"",
                f"        /* Conversion Table       */      COMPU_TAB_REF {compu_vtab}",
                f"    /end COMPU_METHOD",
                "",
                f"    /begin COMPU_VTAB",
                f"        /* Name of Table          */      {compu_vtab}",
                f"        /* Long identifier        */      \"{long_text}\"",
                f"        /* Conversion Type        */      TAB_VERB",
                f"        /* Number of Elements     */      {len(enum_items)}",
            ]
            for val, text in enum_items:
                text_safe = str(text).replace('"', "'")
                method_lines.append(f"        /* Table Element          */      {val} \"{text_safe}\"")
            method_lines.append(f"    /end COMPU_VTAB\n")
            compu_block = "\n".join(method_lines)
        else:
            compu_method = DTYPE_TO_COMPU.get(dtype, "NO_COMPU_METHOD")
    else:
        compu_method, compu_block = _build_linear_compu_method(
            sig, name, longname, dtype, DTYPE_TO_COMPU.get(dtype, "NO_COMPU_METHOD")
        )
    
    # 根据数据类型格式化限值
    vmin_str = _format_limit(vmin, dtype)
    vmax_str = _format_limit(vmax, dtype)
    bit_mask_line = f"\t\tBIT_MASK 0x{int(bit_mask_val):X}\n" if bit_mask_val not in (None, 0) else ""
    
    blocks = []
    
    # 计算数据类型的大小
    dtype_size = {
        "UBYTE": 1, "SBYTE": 1,
        "UWORD": 2, "SWORD": 2,
        "ULONG": 4, "SLONG": 4, "A_UINT64": 8, "A_INT64": 8,
        "FLOAT32_IEEE": 4, "FLOAT64_IEEE": 8
    }.get(dtype, 4)
    
    if array_size == 1:
        # 标量变量：单个 MEASUREMENT 块
        addr_str = f"0x{address:08X}"
        
        if otype in ("MEASUREMENT", "MEAS"):
            # 观测量格式
            block = (
                f"    /begin MEASUREMENT\n"
                f"\t\t/* Name                  */\t\t{name}\n"
                f"\t\t/* Long identifier       */\t\t\"{longname}\"\n"
                f"\t\t/* Data type             */\t\t{dtype}\n"
                f"\t\t/* Conversion method     */\t\t{compu_method}\n"
                f"\t\t/* Resolution (Not used) */\t\t0\n"
                f"\t\t/* Accuracy (Not used)   */\t\t0\n"
                f"\t\t/* Lower limit           */\t\t{vmin_str}\n"
                f"\t\t/* Upper limit           */\t\t{vmax_str}\n"
                f"{bit_mask_line}"
                f"\t\tECU_ADDRESS {addr_str}\n"
                f"    /end MEASUREMENT\n"
            )
        else:
            # 标定量格式（CHARACTERISTIC）
            block = (
                f"    /begin CHARACTERISTIC\n"
                f"\t\t/* Name               */\t\t{name}\n"
                f"\t\t/* Long Identifier    */\t\t\"{longname}\"\n"
                f"\t\t/* Type               */\t\tVALUE\n"
                f"\t\t/* ECU Address        */\t\t{addr_str}\n"
                f"\t\t/* Record Layout      */\t\t{record_layout}\n"
                f"\t\t/* Maximum Difference */\t\t0\n"
                f"\t\t/* Conversion Method  */\t\t{compu_method}\n"
                f"\t\t/* Lower Limit        */\t\t{vmin_str}\n"
                f"\t\t/* Upper Limit        */\t\t{vmax_str}\n"
                f"{bit_mask_line}"
                f"    /end CHARACTERISTIC\n"
            )
        
        blocks.append(block)
    
    else:
        addr_str = f"0x{address:08X}"

        if is_curve_calibration:
            axis_points = effective_dims[0] if effective_dims else array_size
            axis_upper = max(0, axis_points - 1)
            block = (
                f"    /begin CHARACTERISTIC\n"
                f"\t\t/* Name               */\t\t{name}\n"
                f"\t\t/* Long Identifier    */\t\t\"{longname}\"\n"
                f"\t\t/* Type               */\t\tCURVE\n"
                f"\t\t/* ECU Address        */\t\t{addr_str}\n"
                f"\t\t/* Record Layout      */\t\t{record_layout}\n"
                f"\t\t/* Maximum Difference */\t\t0\n"
                f"\t\t/* Conversion Method  */\t\t{compu_method}\n"
                f"\t\t/* Lower Limit        */\t\t{vmin_str}\n"
                f"\t\t/* Upper Limit        */\t\t{vmax_str}\n"
                f"{bit_mask_line}"
                f"\t\t/begin AXIS_DESCR\n"
                f"\t\t\t/* Attribute         */\t\tFIX_AXIS\n"
                f"\t\t\t/* Input Quantity    */\t\tNO_INPUT_QUANTITY\n"
                f"\t\t\t/* Conversion Method */\t\tNO_COMPU_METHOD\n"
                f"\t\t\t/* Max Axis Points   */\t\t{axis_points}\n"
                f"\t\t\t/* Lower Limit       */\t\t0\n"
                f"\t\t\t/* Upper Limit       */\t\t{axis_upper}\n"
                f"\t\t\tFIX_AXIS_PAR_DIST 0 1 {axis_points}\n"
                f"\t\t/end AXIS_DESCR\n"
                f"    /end CHARACTERISTIC\n"
            )
        elif is_map_calibration:
            x_points = effective_dims[0]
            y_points = effective_dims[1]
            x_upper = max(0, x_points - 1)
            y_upper = max(0, y_points - 1)
            matrix_dim_line = f"\t\tMATRIX_DIM {x_points} {y_points}\n"
            block = (
                f"    /begin CHARACTERISTIC\n"
                f"\t\t/* Name               */\t\t{name}\n"
                f"\t\t/* Long Identifier    */\t\t\"{longname}\"\n"
                f"\t\t/* Type               */\t\tMAP\n"
                f"\t\t/* ECU Address        */\t\t{addr_str}\n"
                f"\t\t/* Record Layout      */\t\t{record_layout}\n"
                f"\t\t/* Maximum Difference */\t\t0\n"
                f"\t\t/* Conversion Method  */\t\t{compu_method}\n"
                f"\t\t/* Lower Limit        */\t\t{vmin_str}\n"
                f"\t\t/* Upper Limit        */\t\t{vmax_str}\n"
                f"{bit_mask_line}"
                f"{matrix_dim_line}"
                f"\t\t/begin AXIS_DESCR\n"
                f"\t\t\t/* Attribute         */\t\tFIX_AXIS\n"
                f"\t\t\t/* Input Quantity    */\t\tNO_INPUT_QUANTITY\n"
                f"\t\t\t/* Conversion Method */\t\tNO_COMPU_METHOD\n"
                f"\t\t\t/* Max Axis Points   */\t\t{x_points}\n"
                f"\t\t\t/* Lower Limit       */\t\t0\n"
                f"\t\t\t/* Upper Limit       */\t\t{x_upper}\n"
                f"\t\t\tFIX_AXIS_PAR_DIST 0 1 {x_points}\n"
                f"\t\t/end AXIS_DESCR\n"
                f"\t\t/begin AXIS_DESCR\n"
                f"\t\t\t/* Attribute         */\t\tFIX_AXIS\n"
                f"\t\t\t/* Input Quantity    */\t\tNO_INPUT_QUANTITY\n"
                f"\t\t\t/* Conversion Method */\t\tNO_COMPU_METHOD\n"
                f"\t\t\t/* Max Axis Points   */\t\t{y_points}\n"
                f"\t\t\t/* Lower Limit       */\t\t0\n"
                f"\t\t\t/* Upper Limit       */\t\t{y_upper}\n"
                f"\t\t\tFIX_AXIS_PAR_DIST 0 1 {y_points}\n"
                f"\t\t/end AXIS_DESCR\n"
                f"    /end CHARACTERISTIC\n"
            )
        else:
            matrix_dims = _matrix_dims_for_a2l(array_dims)
            matrix_dim_line = f"\t\tMATRIX_DIM {' '.join(str(x) for x in matrix_dims)}\n"

            block = (
                f"    /begin MEASUREMENT\n"
                f"\t\t/* Name                  */\t\t{name}\n"
                f"\t\t/* Long identifier       */\t\t\"{longname}\"\n"
                f"\t\t/* Data type             */\t\t{dtype}\n"
                f"\t\t/* Conversion method     */\t\t{compu_method}\n"
                f"\t\t/* Resolution (Not used) */\t\t0\n"
                f"\t\t/* Accuracy (Not used)   */\t\t0\n"
                f"\t\t/* Lower limit           */\t\t{vmin_str}\n"
                f"\t\t/* Upper limit           */\t\t{vmax_str}\n"
                f"{bit_mask_line}"
                f"{matrix_dim_line}"
                f"\t\tECU_ADDRESS {addr_str}\n"
                f"    /end MEASUREMENT\n"
            )

        blocks.append(block)
    
    return "\n".join(blocks), compu_block


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║        报告生成：A2L 匹配证据报告 / A2L 版本差异报告                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
# 两份报告解决的是同一类现场问题：「INCA 里这个观测量为什么是 0 / 为什么会变」。
#   · 匹配报告：逐信号回答「地址、类型、位域到底从哪来」；匹配失败时给出可操作的
#     排查线索（大小写、前导下划线、成员基符号、同前缀符号）。
#   · 差异报告：逐对象回答「两版 A2L 之间新增/删除了什么、地址与类型改了哪些」。
# 均为事后可审计产物，默认关闭，由 GUI 开关启用。
# 常量 MATCH_REPORT_MAX_DETAIL / DIFF_REPORT_MAX_DETAIL / EVID_* 定义在文件前部常量区。


def _report_file_stamp(path: str) -> str:
    """返回文件的「修改时间 / 大小」描述，失败时返回空串。"""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return tf("{n1} / {n2} bytes", n1=format(datetime.fromtimestamp(st.st_mtime), "%Y-%m-%d %H:%M:%S"), n2=format(st.st_size, ","))


def _report_read_text(path: str) -> str:
    """按常见编码读取 A2L 文本（报告只需内容，不需保持原格式）。"""
    for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError):
            continue
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def collect_a2l_object_details(a2l_path: str) -> dict[str, dict[str, object]]:
    """解析 A2L 中的观测量/标定量，返回 名称 -> 详细属性。

    相比 collect_a2l_object_addresses 额外解析了数据类型、转换方法与位域，
    差异报告需要这些字段才能说清「同名对象除了地址还变了什么」。
    """
    text = _report_read_text(a2l_path)
    if not text:
        return {}
    result: dict[str, dict[str, object]] = {}
    for kind in ("MEASUREMENT", "CHARACTERISTIC", "CALIBRATION"):
        for m in re.finditer(r"/begin\s+%s\b(.*?)/end\s+%s" % (kind, kind), text, flags=re.S | re.I):
            body = m.group(1)
            ad = re.search(r"ECU_ADDRESS\s+(0x[0-9A-Fa-f]+)", body)
            if not ad:
                continue
            nm = re.search(r"/\*\s*Name\s*\*/\s*([^\s\r\n]+)", body)
            if not nm:
                for line in body.splitlines():
                    s = line.strip()
                    if not s or s.startswith(("/*", '"', "/")):
                        continue
                    if re.match(r"^[A-Za-z_]\w*$", s):
                        nm = re.match(r"([A-Za-z_]\w*)", s)
                        break
            if not nm:
                continue
            name = nm.group(1).strip()
            if name in result:
                continue
            dtype = re.search(r"/\*\s*Data type\s*\*/\s*([^\s\r\n]+)", body, re.I)
            method = re.search(r"/\*\s*Conversion method\s*\*/\s*([^\s\r\n]+)", body, re.I)
            bit_off = re.search(r"/\*\s*Bit\s*offset\s*\*/\s*([^\s\r\n]+)", body, re.I)
            bit_size = re.search(r"/\*\s*Bit\s*size\s*\*/\s*([^\s\r\n]+)", body, re.I)
            result[name] = {
                "name": name,
                "address": int(ad.group(1), 0),
                "object_type": ("CALIBRATION" if kind.upper() in ("CHARACTERISTIC", "CALIBRATION")
                                else "MEASUREMENT"),
                "data_type": dtype.group(1).strip() if dtype else "",
                "conversion": method.group(1).strip() if method else "",
                "bit_offset": bit_off.group(1).strip() if bit_off else "",
                "bit_size": bit_size.group(1).strip() if bit_size else "",
            }
    return result


def suggest_symbol_candidates(name: str, sym_names: list[str], limit: int = 6) -> list[str]:
    """为未匹配的信号名推荐「最可能本该用的」符号。

    现场排查「观测量全 0」时最常见的原因就是符号名对不上：大小写不同、
    带了前导下划线、或者把结构体成员当成了独立符号。这里给出可直接核对的线索。
    """
    target = str(name or "").strip()
    if not target or not sym_names:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def _add(cand: str, why: str) -> None:
        if not cand or cand == target or cand in seen:
            return
        seen.add(cand)
        out.append(f"{cand}   ← {why}")

    lower = target.lower()
    for cand in sym_names:
        if cand != target and cand.lower() == lower:
            _add(cand, tr("differs only in letter case"))
            break

    stripped = target.lstrip("_")
    if stripped != target:
        for cand in sym_names:
            if cand.lstrip("_") == stripped:
                _add(cand, tr("leading underscore difference"))
                break

    base = target.split(".")[0].split("[")[0]
    if base and base != target:
        for cand in sym_names:
            if cand == base:
                _add(cand, tr("base symbol that owns this member"))
                break

    prefix = target[:max(4, len(target) // 2)]
    near = [c for c in sym_names if c != target and c.startswith(prefix)]
    for cand in near[:3]:
        _add(cand, tr("similar name prefix"))

    # 兜底：名字完全不像时也要给点线索 —— 列出最接近的几个符号。
    # 现场排查时「符号表里到底有什么」比「精确匹配」更有用。
    try:
        import difflib
        pool = sym_names
        if len(pool) > 4000:
            # 符号表很大时先按长度粗筛，避免 O(n*m) 拖慢报告生成
            pool = [c for c in pool
                    if abs(len(c) - len(target)) <= max(3, len(target) // 3)] or sym_names
        for cand in difflib.get_close_matches(target, pool, n=3, cutoff=0.3):
            _add(cand, tr("name is fairly close (difference may be large; for manual cross-check only)"))
    except Exception:
        pass

    if not out:
        # 没给候选时也要说清楚，避免用户以为是工具出错了。
        out.append(tr("(no similar symbol found in the symbol table; first verify whether the address file and the current firmware are from the same build)"))

    return out[:limit]


def _short_evidence_label(label: str) -> str:
    """把长证据标签压缩成单行表格里能用的短标签。"""
    # 这里在调用时才翻译：模块级字典若在导入时求值，切换语言后不会更新。
    return tr(_EVIDENCE_SHORT.get(str(label), str(label)))


_EVIDENCE_SHORT = {
    EVID_ADDR_SYMBOL: "addr:symbol table",
    EVID_OFFSET_DWARF: "offset:DWARF expanded",
    EVID_OFFSET_XLSX: "offset:XLSX manual",
    EVID_OFFSET_MEMBERINFO: "offset:member info table",
    EVID_OFFSET_NONE: "offset:not found (base addr)",
    EVID_BIT_DWARF: "bitfield:DWARF expanded",
    EVID_BIT_XLSX: "bitfield:XLSX manual",
    EVID_BIT_MEMBERINFO: "bitfield:member info table",
    EVID_TYPE_DWARF: "type:DWARF",
    EVID_TYPE_SYMBOL: "type:symbol table",
    EVID_SUPP_XLSX: "supplement:XLSX",
}


def _report_meta_block(meta: dict[str, object], items: list[tuple[str, str]]) -> list[str]:
    """渲染「生成信息」区块。"""
    out = [tr("[1. Generation info]")]
    for label, key in items:
        val = str(meta.get(key) or "").strip()
        if val:
            out.append(f"  {label}：{val}")
    out.append(tf("  Header handling: ")
               + (tr("auto repair (rewrites the original header)") if meta.get("repair_header")
                  else tr("keep the original content unchanged (only append the newly generated signal blocks)")))
    out.append(tf("  Struct array handling: ")
               + (tr("expand into per-element member signals") if meta.get("expand_struct_array")
                  else tr("output as a raw byte stream (data complete, no field semantics)")))
    return out


def build_match_report(records: list[dict], meta: dict[str, object] | None = None,
                       consistency: dict | None = None) -> str:
    """生成逐信号匹配证据报告（纯文本）。

    records 每条形如：
        {"status": "OK"/"FAIL", "name", "shortname", "object_type", "data_type",
         "address", "size", "section", "evidence": [(标签, 明细), ...],
         "reason", "candidates": [...], "semantic_candidates": [...]}
    consistency: 可选，增量一致性检查结果（见 rag_bridge.consistency_check）。
    """
    meta = dict(meta or {})
    recs = list(records or [])
    ok = [r for r in recs if str(r.get("status")) == "OK"]
    fail = [r for r in recs if str(r.get("status")) != "OK"]
    total = len(recs)
    rate = (len(ok) / total * 100.0) if total else 0.0

    bar = "=" * 96
    out: list[str] = [bar, tr("                  A2L Match Report (per-signal match evidence)"), bar, ""]

    out += _report_meta_block(meta, [
        (tr("Generated at"), "generated_at"),
        (tr("A2L header"), "a2l_header"),
        (tr("Address file"), "addr_file"),
        (tr("  └ File state"), "addr_stamp"),
        (tr("Signal parameter XLSX"), "signal_xlsx"),
        (tr("Output A2L"), "out_a2l"),
        (tr("This report"), "report_path"),
    ])
    out.append("")

    out.append(tr("[2. Match result overview]"))
    out.append(tf("  Total candidate signals: {n1}", n1=total))
    out.append(tf("  Matched: {n1}", n1=len(ok)))
    out.append(tf("  Failed: {n1}", n1=len(fail)))
    out.append(tf("  Success rate: {n1} %", n1=format(rate, ".2f")))
    out.append("")
    out.append(tr("  -- Evidence source distribution (counted over the matched signals; one signal may carry several evidences) --"))
    cnt: Counter = Counter()
    for r in ok:
        for label, _detail in (r.get("evidence") or []):
            cnt[str(label)] += 1
    if cnt:
        for k, v in cnt.most_common():
            out.append(f"    {tr(k)}: {v}")
    else:
        out.append(tr("    (none)"))
    out.append("")

    out.append(tf("[3. Match failure details] ({n1} signals)", n1=len(fail)))
    out.append(tr("  Meaning of failure: the AXF symbol name of this signal was not found in the address file symbol table,"))
    out.append(tr("            so no ECU address could be determined and nothing was written to the A2L. In INCA this measurement will be missing or keep the old address."))
    out.append(tr("  Common root causes: 1) the variable is local/static and produced no global symbol; 2) symbols or debug info were stripped at build time;"))
    out.append(tr("             3) the address file is not from the same build as the current project; 4) the variable name differs in letter case."))
    out.append("")
    # 同一基符号的多个成员信号会一起失败，按基符号聚合后根因一目了然。
    fail_groups: dict[str, dict] = {}
    for r in fail:
        _key = str(r.get("name") or "")
        _g = fail_groups.get(_key)
        if _g is None:
            _g = fail_groups[_key] = {
                "name": _key,
                "count": 0,
                "samples": [],
                "reason": r.get("reason") or "",
                "candidates": r.get("candidates") or [],
                "semantic_candidates": r.get("semantic_candidates") or [],
                "object_type": r.get("object_type") or "",
                "data_type": r.get("data_type") or "",
            }
        _g["count"] += 1
        if len(_g["samples"]) < 3:
            _g["samples"].append(str(r.get("shortname") or ""))
    _groups = sorted(fail_groups.values(), key=lambda g: (-g["count"], g["name"]))
    out.append(tf("  Aggregated into {n1} groups by base symbol (the member signals in one group failed together because the same base symbol is missing):", n1=len(_groups)))
    out.append("")
    out.append(tr("  Format: base symbol | affected members | object type | data type | failure reason | suggested similar symbol | sample members"))
    if not fail:
        out.append(tr("  (none, all candidate signals matched successfully)"))
    else:
        _has_sem = any(g.get("semantic_candidates") for g in _groups)
        if _has_sem:
            out.append(tr("  Format: base symbol | affected members | object type | data type | failure reason | suggested similar symbol | semantic candidate (A2L-RAGAlign) | sample members"))
        for idx, g in enumerate(_groups[:MATCH_REPORT_MAX_DETAIL], 1):
            cand_txt = (" ; ".join(str(c) for c in g["candidates"]) if g["candidates"]
                        else tr("(no similar symbol)"))
            if _has_sem:
                sem = g.get("semantic_candidates") or []
                sem_txt = (" ; ".join(
                    "{n1} ({n2}, {n3})".format(n1=str(c.get("name")), n2=c.get("tier"),
                                              n3=format(float(c.get("score") or 0.0), ".3f"))
                    for c in sem) if sem else tr("(no semantic candidate)"))
                out.append(f"  {idx} | {g['name']} | {g['count']} | {g['object_type']}"
                           f" | {g['data_type']} | {g['reason']} | {cand_txt} | {sem_txt}"
                           f" | {', '.join(g['samples'])}")
            else:
                out.append(f"  {idx} | {g['name']} | {g['count']} | {g['object_type']}"
                           f" | {g['data_type']} | {g['reason']} | {cand_txt}"
                           f" | {', '.join(g['samples'])}")
        if len(_groups) > MATCH_REPORT_MAX_DETAIL:
            out.append(tf("  ... {n1} more groups not listed (limit {n2} groups)", n1=len(_groups) - MATCH_REPORT_MAX_DETAIL, n2=MATCH_REPORT_MAX_DETAIL))
        if len(_groups) < len(fail):
            out.append("")
            out.append(tf("  Full list of failed signal names ({n1} in total, one per line):", n1=len(fail)))
            for r in fail[:MATCH_REPORT_MAX_DETAIL]:
                out.append(f"    {r.get('shortname') or r.get('name') or ''}")
            if len(fail) > MATCH_REPORT_MAX_DETAIL:
                out.append(tf("    ... {n1} more not listed", n1=len(fail) - MATCH_REPORT_MAX_DETAIL))
    out.append("")

    out.append(tf("[4. Matched details] ({n1} signals)", n1=len(ok)))
    out.append(tr("  Format: no. | signal name | short name | object type | data type | final address | bytes | section | evidence sources"))
    if not ok:
        out.append(tr("  (none)"))
    else:
        shown = ok[:MATCH_REPORT_MAX_DETAIL]
        for idx, r in enumerate(shown, 1):
            addr = r.get("address")
            addr_txt = f"0x{int(addr):08X}" if addr is not None else tr("(undetermined)")
            # 证据压缩成短标签，便于单行阅读；完整链条见每条的证据统计。
            ev_short = "; ".join(_short_evidence_label(str(lb)) for lb, _d in (r.get("evidence") or []))
            out.append(f"  {idx} | {r.get('name') or ''} | {r.get('shortname') or ''}"
                       f" | {r.get('object_type') or ''} | {r.get('data_type') or ''}"
                       f" | {addr_txt} | {r.get('size') or 0} | {r.get('section') or ''}"
                       f" | {ev_short}")
        if len(ok) > len(shown):
            out.append(tf("  ... {n1} more matched details not listed (limit {n2} entries)", n1=len(ok) - len(shown), n2=MATCH_REPORT_MAX_DETAIL))
    out.append("")

    # 增量一致性检查（A2L-RAGAlign）：对照已有 A2L 对象集合检查本轮写入的信号。
    # 这是「提取了已有对象名却从未使用」这一缺口的补齐：重名 / 类型漂移 / 孤儿对象
    # / 候选改名，都是工程师最容易漏掉、却直接影响 INCA 读数的问题。
    if consistency:
        out.append(tr("[5. Incremental consistency check]"))
        if not consistency.get("available"):
            out.append(tr("  (semantic alignment is not available in this run, skipped)"))
        else:
            _dups = list(consistency.get("duplicates") or [])
            _tc = list(consistency.get("type_conflicts") or [])
            _orph = list(consistency.get("removed_orphans") or [])
            _ren = list(consistency.get("aligned_renames") or [])
            out.append(tf("  Duplicate names (a new signal reuses an existing A2L object name): {n1}", n1=len(_dups)))
            for n in _dups[:MATCH_REPORT_MAX_DETAIL]:
                out.append(f"    - {n}")
            if len(_dups) > MATCH_REPORT_MAX_DETAIL:
                out.append(tf("    ... {n1} more not listed", n1=len(_dups) - MATCH_REPORT_MAX_DETAIL))
            out.append(tf("  Data type drift (the same name carries a different data type than before): {n1}", n1=len(_tc)))
            for c in _tc[:MATCH_REPORT_MAX_DETAIL]:
                out.append(tf("    - {n1}: {n2} -> {n3}", n1=c.get("name"), n2=c.get("old"), n3=c.get("new")))
            if len(_tc) > MATCH_REPORT_MAX_DETAIL:
                out.append(tf("    ... {n1} more not listed", n1=len(_tc) - MATCH_REPORT_MAX_DETAIL))
            out.append(tf("  Orphan objects (present in the old A2L but not in this run; confirm whether to delete): {n1}", n1=len(_orph)))
            for n in _orph[:MATCH_REPORT_MAX_DETAIL]:
                out.append(f"    - {n}")
            if len(_orph) > MATCH_REPORT_MAX_DETAIL:
                out.append(tf("    ... {n1} more not listed", n1=len(_orph) - MATCH_REPORT_MAX_DETAIL))
            out.append(tf("  Candidate renames (a new name is semantically close to an old object; manual confirmation required): {n1}", n1=len(_ren)))
            for c in _ren[:MATCH_REPORT_MAX_DETAIL]:
                out.append(tf("    - {n1} -> {n2}  (score {n3})", n1=c.get("old"), n2=c.get("new"),
                              n3=format(float(c.get("score") or 0.0), ".3f")))
            if len(_ren) > MATCH_REPORT_MAX_DETAIL:
                out.append(tf("    ... {n1} more not listed", n1=len(_ren) - MATCH_REPORT_MAX_DETAIL))
            if not (_dups or _tc or _orph or _ren):
                out.append(tr("    (no inconsistency found)"))
        out.append("")

    out.append(bar)
    out.append(tr("  Note: this report only reflects the tool's decisions during this generation run."))
    out.append(tr("        If a signal is shown as matched here but INCA still reads 0, the problem is usually not in the matching step,"))
    out.append(tr("        but in the address file not being from the same build as the firmware on the ECU"))
    out.append(tr("        (compare this report with the address delta distribution in the version diff report)."))
    out.append(bar)
    return "\n".join(out) + "\n"


def build_a2l_diff_report(prev_path: str, cur_path: str,
                           meta: dict[str, object] | None = None) -> str:
    """生成两版 A2L 的差异报告（纯文本）。"""
    meta = dict(meta or {})
    prev = collect_a2l_object_details(prev_path)
    cur = collect_a2l_object_details(cur_path)

    added = [k for k in cur if k not in prev]
    removed = [k for k in prev if k not in cur]
    common = [k for k in cur if k in prev]

    addr_changed: list[tuple[str, int, int]] = []
    type_changed: list[tuple[str, str, str]] = []
    conv_changed: list[tuple[str, str, str]] = []
    bit_changed: list[tuple[str, str, str, str, str, str]] = []
    unchanged = 0
    for k in common:
        p, c = prev[k], cur[k]
        if p.get("address") != c.get("address"):
            addr_changed.append((k, int(p.get("address") or 0), int(c.get("address") or 0)))
        if str(p.get("data_type") or "") != str(c.get("data_type") or ""):
            type_changed.append((k, str(p.get("data_type") or ""), str(c.get("data_type") or "")))
        if str(p.get("conversion") or "") != str(c.get("conversion") or ""):
            conv_changed.append((k, str(p.get("conversion") or ""), str(c.get("conversion") or "")))
        if (str(p.get("bit_offset") or ""), str(p.get("bit_size") or "")) != \
           (str(c.get("bit_offset") or ""), str(c.get("bit_size") or "")):
            bit_changed.append((k,
                                str(p.get("bit_offset") or ""), str(p.get("bit_size") or ""),
                                str(c.get("bit_offset") or ""), str(c.get("bit_size") or "")))

    bar = "=" * 96
    out: list[str] = [bar, tr("                     A2L Version Diff Report"), bar, ""]

    out.append(tr("[1. Comparison info]"))
    out.append(tf("  Previous A2L: {n1}", n1=os.path.abspath(prev_path)))
    stamp = _report_file_stamp(prev_path)
    if stamp:
        out.append(tf("    └ File state: {n1}", n1=stamp))
    out.append(tf("  Current A2L: {n1}", n1=os.path.abspath(cur_path)))
    stamp = _report_file_stamp(cur_path)
    if stamp:
        out.append(tf("    └ File state: {n1}", n1=stamp))
    for label, key in (("A2L header", "a2l_header"), ("Address file", "addr_file"),
                       ("  └ File state", "addr_stamp"), ("Generated at", "generated_at")):
        val = str(meta.get(key) or "").strip()
        if val:
            out.append(tf("  {n1}: {n2}", n1=tr(label), n2=val))
    out.append("")

    out.append(tr("[2. Overview]"))
    out.append(tf("  Objects in the previous version: {n1}", n1=len(prev)))
    out.append(tf("  Objects in the current version: {n1}", n1=len(cur)))
    out.append(tf("  Common objects: {n1}", n1=len(common)))
    out.append(tf("    ├─ Address changes: {n1}", n1=len(addr_changed)))
    out.append(tf("    ├─ Data type changes: {n1}", n1=len(type_changed)))
    out.append(tf("    ├─ Conversion method changes: {n1}", n1=len(conv_changed)))
    out.append(tf("    └─ Bitfield setting changes: {n1}", n1=len(bit_changed)))
    out.append(tf("  Added in this version: {n1}", n1=len(added)))
    out.append(tf("  Removed in this version: {n1}", n1=len(removed)))
    out.append("")

    out.append(tf("[3. Objects added in this version] ({n1})", n1=len(added)))
    if not added:
        out.append(tr("  (none)"))
    else:
        for k in sorted(added)[:DIFF_REPORT_MAX_DETAIL]:
            c = cur[k]
            out.append(f"  {k}  |  {c.get('data_type') or '-'}  |  {c.get('object_type') or '-'}"
                       f"  |  0x{int(c.get('address') or 0):08X}")
    out.append("")

    out.append(tf("[4. Objects removed in this version] ({n1})", n1=len(removed)))
    out.append(tr("  Note: removals usually come from \"the current rules filtered this signal out\", not from a firmware removal."))
    if not removed:
        out.append(tr("  (none)"))
    else:
        for k in sorted(removed)[:DIFF_REPORT_MAX_DETAIL]:
            p = prev[k]
            out.append(f"  {k}  |  {p.get('data_type') or '-'}  |  {p.get('object_type') or '-'}"
                       f"  |  0x{int(p.get('address') or 0):08X}")
    out.append("")

    out.append(tf("[5. Address changes] ({n1})", n1=len(addr_changed)))
    out.append(tr("  Note: a uniform address shift of the same-named objects usually means \"the two A2L versions correspond to different firmware builds\";"))
    out.append(tr("        if the delta is consistent, the addresses of this version can be used as they are."))
    if not addr_changed:
        out.append(tr("  (none)"))
    else:
        for k, pa, ca in sorted(addr_changed)[:DIFF_REPORT_MAX_DETAIL]:
            out.append(tf("  {n1}  |  previous 0x{n2}  |  current 0x{n3}  |  delta {n4}", n1=k, n2=format(pa, "08X"), n3=format(ca, "08X"), n4=format(ca - pa, "+d")))
        hist = Counter(ca - pa for _k, pa, ca in addr_changed)
        out.append("")
        out.append(tr("  Delta distribution: ") + ", ".join(f"{d:+d}×{n}" for d, n in hist.most_common(12)))
    out.append("")

    out.append(tf("[6. Type / conversion method / bitfield changes] ({n1} items)", n1=len(type_changed) + len(conv_changed) + len(bit_changed)))
    rows: list[tuple[str, str, str, str]] = []
    rows += [(k, tr("Data type"), a, b) for k, a, b in type_changed]
    rows += [(k, tr("Conversion method"), a, b) for k, a, b in conv_changed]
    rows += [(k, tr("Bitfield (off/size)"), f"{a}/{b}", f"{c}/{d}") for k, a, b, c, d in bit_changed]
    if not rows:
        out.append(tr("  (none)"))
    else:
        for k, field, a, b in sorted(rows)[:DIFF_REPORT_MAX_DETAIL]:
            out.append(tf("  {n1}  |  {n2}  |  previous {n3}  |  current {n4}", n1=k, n2=field, n3=a or '-', n4=b or '-'))
    out.append("")
    out.append(bar)
    out.append(tr("  Note: this report only compares the content differences of the two A2L files; it does not decide which version is correct."))
    out.append(tr("        To decide whether the version matches the current firmware, refer to the address source evidence in the match report."))
    out.append(bar)
    return "\n".join(out) + "\n"


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          A2L 更新主逻辑                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def do_update_a2l(a2l_path: str, signal_source_path: str, addr_path: str,
                  out_path: str, insert_line: int, log_cb,
                  selected_signal_names: list[str] | set[str] | None = None,
                  preview_signals: list[dict] | None = None,
                  address_range_config: dict[str, list[tuple[int, int]]] | None = None,
                  repair_header: bool = False,
                  expand_struct_array: bool = True,
                  reference_a2l_path: str | None = None,
                  match_report_path: str | None = None,
                  diff_report_path: str | None = None,
                  prev_a2l_path: str | None = None,
                  semantic_align: bool = False) -> None:
    """
    执行 A2L 更新主逻辑。
    signal_source_path: 可选，兼容 Excel 文件或工程根目录；留空时直接依赖地址文件/DWARF。
    insert_line: 插入行号（从1开始），0表示追加到末尾。
    repair_header: 默认 False —— 保持用户提供的 A2L 头文件内容原样不动，仅追加新生成的块。
    expand_struct_array: 结构体数组是否逐元素展开为成员信号（方案 A）。
    reference_a2l_path: 可选，INCA 工程里正在使用的 A2L；用于交叉校验地址文件版本。
    match_report_path: 可选，匹配证据报告的输出路径；非空即启用（逐信号记录匹配依据）。
    diff_report_path: 可选，版本差异报告的输出路径；需同时提供 prev_a2l_path。
    prev_a2l_path: 可选，上一版 A2L 文件路径（差异报告的比对基准）。
    semantic_align: 默认 False —— 关闭时行为与原来完全一致；打开后额外启用
                    A2L-RAGAlign 语义候选与增量一致性检查（只报告，不自动采纳）。
    """
    # 报告开关：仅在指定了输出路径时才收集证据，避免默认流程额外开销。
    want_match_report = bool(str(match_report_path or "").strip())
    want_diff_report = bool(str(diff_report_path or "").strip()) and bool(str(prev_a2l_path or "").strip())
    match_records: list[dict] = []
    _reports_written = False
    _consistency: dict | None = None     # 增量一致性检查结果

    def _emit_reports() -> None:
        """把匹配报告 / 版本差异报告写到磁盘。

        正常流程与异常路径共用：报告的价值恰恰体现在「一个信号都没匹配上」
        这种场景下，若先抛异常就永远看不到报告了。报告自身写盘失败不影响主流程。
        """
        nonlocal _reports_written
        if _reports_written:
            return
        _reports_written = True

        if want_match_report:
            try:
                _meta = {
                    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "a2l_header": os.path.abspath(a2l_path),
                    "addr_file": os.path.abspath(addr_path),
                    "addr_stamp": _report_file_stamp(addr_path),
                    "signal_xlsx": os.path.abspath(signal_source_path)
                                   if signal_source_path and os.path.isfile(signal_source_path) else "",
                    "out_a2l": os.path.abspath(out_path),
                    "report_path": os.path.abspath(str(match_report_path)),
                    "repair_header": bool(repair_header),
                    "expand_struct_array": bool(expand_struct_array),
                }
                _text = build_match_report(match_records, _meta, _consistency)
                with open(str(match_report_path), "w", encoding="utf-8", newline="\r\n") as _fh:
                    _fh.write(_text)
                _n_ok = sum(1 for _r in match_records if _r.get("status") == "OK")
                _n_fail = len(match_records) - _n_ok
                log_cb(tf("   🔍 Match report generated: {n1} (succeeded {n2} / failed {n3})", n1=os.path.basename(str(match_report_path)), n2=_n_ok, n3=_n_fail))
            except Exception as _e:
                log_cb(tf("   (failed to generate the match report: {n1})", n1=_e))

        if want_diff_report:
            try:
                if not os.path.isfile(str(out_path)):
                    log_cb(tr("   ⚠ Version diff report skipped: the current A2L was not generated successfully, nothing to compare"))
                elif not os.path.isfile(str(prev_a2l_path)):
                    log_cb(tf("   ⚠ Version diff report skipped: the previous A2L does not exist → {n1}", n1=prev_a2l_path))
                else:
                    _meta = {
                        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "a2l_header": os.path.abspath(a2l_path),
                        "addr_file": os.path.abspath(addr_path),
                        "addr_stamp": _report_file_stamp(addr_path),
                    }
                    _text = build_a2l_diff_report(str(prev_a2l_path), str(out_path), _meta)
                    with open(str(diff_report_path), "w", encoding="utf-8", newline="\r\n") as _fh:
                        _fh.write(_text)
                    log_cb(tf("   🔀 Version diff report generated: {n1}", n1=os.path.basename(str(diff_report_path))))
            except Exception as _e:
                log_cb(tf("   (failed to generate the version diff report: {n1})", n1=_e))

    # 1. 读 A2L 头文件（保持原编码与换行符，内容不改写）
    log_cb(tr("📄 Reading the A2L header ..."))
    a2l_content, a2l_encoding, a2l_newline = read_a2l_text_preserving_format(a2l_path)
    if repair_header:
        a2l_content, repaired_items = repair_a2l_header_for_tool_compatibility(a2l_content)
        if repaired_items:
            log_cb(tr("   🔧 Header compatibility issues were auto repaired:"))
            for item in repaired_items[:20]:
                log_cb(f"      - {item}")
            if len(repaired_items) > 20:
                log_cb(tf("      ... {n1} in total", n1=len(repaired_items)))
    else:
        log_cb(tr("   🔒 The original header content is kept unchanged (only the newly generated signal blocks are appended)"))
        header_findings = validate_canape_header_compatibility(a2l_content)
        if header_findings:
            log_cb(tr("   ⚠ The original header has the following compatibility issues (duplicate section names are auto repaired, the rest are only reported):"))
            for item in header_findings[:10]:
                log_cb(f"      - {item}")
            if len(header_findings) > 10:
                log_cb(tf("      ... {n1} in total", n1=len(header_findings)))

    # 【原则】原始头文件内容一字不改：不改名段、不补块、不重排。
    # 头文件的既有问题（段名重名、旧式 COMPU_METHOD 等）只通过「新生成的信号
    # 自身写法规范」来规避，绝不改动头文件已有字节。
    existing_a2l_names = _extract_existing_a2l_object_names(a2l_content)
    log_cb(tf("   OK, {n1} lines in total", n1=len(a2l_content.splitlines())))

    # ── A2L-RAGAlign（可选，默认关闭）──────────────────────────────────
    # 打开后：精确匹配失败的信号额外给出检索增强的语义候选；同时对照已有 A2L
    # 对象集合做一次增量一致性检查（重名 / 类型漂移 / 孤儿对象 / 候选改名）。
    # 语义候选只写进报告供人工确认，绝不自动采纳。
    _rag_aligner = None
    if semantic_align:
        try:
            import rag_bridge
            _rag_aligner = rag_bridge.build_aligner()
            if _rag_aligner is None:
                log_cb(tr("   ⚠ Semantic alignment is unavailable (rag_align could not be loaded); the run continues without it"))
            else:
                log_cb(tr("   🧠 Semantic alignment (A2L-RAGAlign) enabled: semantic candidates and the incremental consistency check will be reported"))
        except Exception as _e:
            _rag_aligner = None
            log_cb(tf("   ⚠ Semantic alignment is unavailable: {n1}", n1=_e))

    # 2. 解析地址文件，并先梳理标定量/观测量地址区间
    log_cb(tf("🗺  Parsing the address file: {n1} ...", n1=Path(addr_path).suffix.upper()))
    try:
        _axf_stat = os.stat(addr_path)
        _axf_mtime = datetime.fromtimestamp(_axf_stat.st_mtime)
        log_cb(tf("   📄 Full path of the address file: {n1}", n1=os.path.abspath(addr_path)))
        log_cb(tf("   🕒 File modified  : {n1}   size {n2} bytes", n1=format(_axf_mtime, "%Y-%m-%d %H:%M:%S"), n2=format(_axf_stat.st_size, ",")))
    except OSError:
        _axf_stat = None
        log_cb(tf("   📄 Full path of the address file: {n1}", n1=os.path.abspath(addr_path)))
    sym_details = load_address_file_details(addr_path)

    # 同一工程下常存在多份同名地址文件（不同编译时间的副本）。选错版本会让
    # 全部观测量地址整体错位，这里把候选版本列出来供用户核对。
    try:
        _sibs = _find_sibling_address_files(addr_path)
        if len(_sibs) > 1:
            log_cb(tf("   🔎 {n1} address files with the same name were found in this project; please confirm you picked the one matching the current firmware:", n1=len(_sibs)))
            _cur = os.path.abspath(addr_path)
            for _p, _mt, _sz in _sibs:
                _mark = tr("  ← currently used") if os.path.abspath(_p) == _cur else ""
                log_cb(tf("      {n1}  {n2}  ({n3} bytes){n4}", n1=format(_mt, "%Y-%m-%d %H:%M"), n2=_p, n3=format(_sz, ","), n4=_mark))
    except Exception as _e:
        log_cb(tf("   (skipped scanning for address files with the same name: {n1})", n1=_e))

    # 地址过滤范围 = 用户手工配置 ∪ 头文件 MEMORY_SEGMENT 声明的区间。
    # 头文件是这份 A2L 所描述内存空间的权威说明，手工配置漏掉某一段时，
    # 该段的信号会凭空消失（INCA 侧表现为恒为 0 且不刷新）。此处只读不改。
    # 用户未配置任何区间时视为「全都要」，保持不过滤的原有行为。
    _user_ranges = {k: _merge_ranges(list((address_range_config or {}).get(k) or []))
                    for k in ("CALIBRATION", "MEASUREMENT")}
    _header_ranges = _parse_header_memory_segment_ranges(a2l_content)
    if any(_user_ranges.values()):
        _effective_ranges = {
            k: _merge_ranges(_user_ranges[k] + _header_ranges.get(k, []))
            for k in ("CALIBRATION", "MEASUREMENT")
        }
        for _k, _label in (("CALIBRATION", tr("Calibration")), ("MEASUREMENT", tr("Measurement"))):
            _added = [r for r in _effective_ranges[_k] if r not in _user_ranges[_k]]
            if _added:
                _txt = ", ".join(f"0x{s:08X}-0x{e:08X}" for s, e in _added[:6])
                log_cb(tf("   🗺 Ranges declared in the header ({n1}) merged: {n2}", n1=_label, n2=_txt)
                       + (tf(" and {n1} more segments", n1=len(_added)) if len(_added) > 6 else ""))
    else:
        _effective_ranges = None

    _all_syms = dict(sym_details)
    sym_details = apply_address_range_overrides(sym_details, _effective_ranges)
    _dropped = [n for n in _all_syms if n not in sym_details]
    if _dropped:
        log_cb(tf("   ⚠ {n1} symbols have addresses outside every range, so no A2L object was generated for them (e.g. {n2})", n1=len(_dropped), n2=', '.join(_dropped[:3])))
    sym_map = {name: (int(meta.get('address') or 0), int(meta.get('size') or 0)) for name, meta in sym_details.items()}
    if not sym_map:
        raise ValueError(
            tr("No symbols could be extracted from the address file!\nPossible causes:\n  1. The file format is not a standard .map/.elf/.axf\n  2. The ELF/AXF file contains no symbol table (debug info is required)\n  3. The MAP file format does not match any known pattern")
        )
    log_cb(tf("   OK, {n1} candidate symbol addresses extracted in total", n1=len(sym_map)))

    # ── 语义候选检索器（可选）──────────────────────────────────────────
    # 候选池用「地址范围内的符号 + 范围外被丢弃的符号」的并集：信号名落不进
    # 地址范围时，语义上相近的符号仍是有价值的线索，只是地址无法确定。
    # 池只索引一次并复用，否则每个信号都重建一次索引会退化到 O(信号数 × 池大小)。
    _pool_aligner = None
    _sem_cache: dict[str, list] = {}     # 基符号 -> 语义候选（避免重复检索）
    if _rag_aligner is not None:
        try:
            import rag_bridge
            _pool_aligner = rag_bridge.build_pool_aligner(
                list(_all_syms.keys()), base_aligner=_rag_aligner)
            if _pool_aligner is None:
                log_cb(tr("   ⚠ The semantic candidate retriever could not be built; the run continues without it"))
        except Exception as _e:
            _pool_aligner = None
            log_cb(tf("   (the semantic candidate retriever could not be built: {n1})", n1=_e))

    range_summary = summarize_address_ranges(sym_details)
    for otype, label in (("CALIBRATION", tr("Calibration CAL")), ("MEASUREMENT", tr("Measurement MEAS"))):
        info = range_summary.get(otype, {})
        ranges = info.get("ranges", [])
        count = int(info.get("count") or 0)
        if ranges:
            range_text = ", ".join(f"0x{s:08X}-0x{e:08X}" for s, e in ranges[:4])
            if len(ranges) > 4:
                range_text += " ..."
        else:
            range_text = tr("unrecognized")
        log_cb(tf("   📍 {n1}: {n2} in total, address ranges {n3}", n1=label, n2=count, n3=range_text))

    # 3. 获取信号清单：优先使用预览页已编辑数据；否则兼容旧流程或直接依赖地址文件/DWARF
    source_path = str(signal_source_path or "").strip()
    if preview_signals:
        log_cb(tr("📝 Generating the A2L from the current signal table of the preview page (including manually added columns) ..."))
        allowed_names = set(sym_details.keys())
        signals = []
        filtered_preview: list[dict] = []
        for _sig in preview_signals:
            _nm = str(_sig.get("Name") or "").strip()
            if _nm in allowed_names:
                signals.append(dict(_sig))
            else:
                filtered_preview.append(_sig)
        skipped_preview = len(filtered_preview)
        if skipped_preview > 0:
            log_cb(tf("   ✂ {n1} preview signals outside the address ranges were filtered out", n1=skipped_preview))
        # 这些信号在进入地址匹配环节前就被丢弃了（名字对不上符号表）。若不在报告里
        # 体现，用户会以为「勾了但没生成」是无声的。统一记为匹配失败并给出线索。
        if want_match_report:
            for _sig in filtered_preview:
                _nm = str(_sig.get("Name") or "").strip()
                # 语义候选（可选）：这批信号是在地址匹配之前就被过滤掉的，
                # 名字多半是被截断或改了前缀的，给出语义上最接近的符号供人工判断。
                _sem_cands = []
                if _pool_aligner is not None:
                    if _nm in _sem_cache:
                        _sem_cands = _sem_cache[_nm]
                    else:
                        try:
                            import rag_bridge
                            _sem_cands = rag_bridge.semantic_candidates(
                                _nm, aligner=_rag_aligner, pool_aligner=_pool_aligner)
                        except Exception:
                            _sem_cands = []
                        _sem_cache[_nm] = _sem_cands
                match_records.append({
                    "status": "FAIL",
                    "name": _nm,
                    "shortname": str(_sig.get("ShortName") or "").strip(),
                    "object_type": str(_sig.get("ObjectType") or ""),
                    "data_type": str(_sig.get("DataType") or ""),
                    "unit": str(_sig.get("Unit") or ""),
                    "address": None,
                    "size": 0,
                    "section": "",
                    "evidence": [],
                    "reason": tr("the signal name is not in the address file symbol table (it was filtered by the symbol table before generation and never reached the address matching step)"),
                    "candidates": suggest_symbol_candidates(_nm, list(sym_map.keys())),
                    "semantic_candidates": _sem_cands,
                })
    elif source_path and os.path.isdir(source_path):
        log_cb(tr("📁 Scanning the project root to auto-detect signal types in .c/.h ..."))
        signals = build_signals_from_project(source_path, sym_details, log_cb)
    elif source_path and os.path.isfile(source_path):
        log_cb(tr("📊 Parsing the Excel signal table (for the legacy workflow) ..."))
        signals = parse_xlsx(source_path)
        for sig in signals:
            ref_name = str(sig.get("ShortName") or sig.get("Name") or "").strip()
            sig["Group"] = derive_group_from_name(ref_name)
            meta = sym_details.get(sig.get("Name", ""), {})
            guessed = _classify_object_type(sig.get("Name", ""), str(meta.get("section") or ""))
            obj = meta.get("object_type") or sig.get("ObjectType", "") or guessed
            sig["ObjectType"] = _normalize_object_type_label(obj)
            sig["Type"] = "CAL" if sig["ObjectType"] == "CALIBRATION" else "MEAS"
    else:
        log_cb(tr("🧬 No project directory given, building the signal list from the DWARF of the address file ..."))
        signals = build_signals_from_dwarf(addr_path, sym_details, log_cb,
                                           expand_struct_array=expand_struct_array)
        if signals:
            for _s in signals:
                _s["_type_source"] = EVID_TYPE_DWARF
        if not signals:
            log_cb(tr("📦 DWARF does not provide complete type information, falling back to the basic signal list of the address file ..."))
            signals = []
            for name, meta in sym_details.items():
                if not _is_user_symbol(name):
                    continue
                size = int(meta.get("size") or 0)
                if size > 8:
                    dtype_raw = "array_u8"
                    dims = [size]
                else:
                    dims = [1]
                    dtype_raw = _kind_to_dtype_raw({1: "u8", 2: "u16", 4: "u32", 8: "u64"}.get(size, "u8"), "scalar")
                object_type = _normalize_object_type_label(meta.get("object_type") or "MEASUREMENT")
                sig = _make_auto_signal(name, name, object_type, dtype_raw, dims,
                                        description=f"auto-detected from {meta.get('source') or 'ADDR'}")
                sig["_type_source"] = EVID_TYPE_SYMBOL
                signals.append(sig)

    if not signals:
        # 一个信号都不可用时，匹配报告恰恰是最有价值的产出：它直接说明
        # 「哪些信号名对不上符号表」以及「有哪些近似符号可以试」。
        _emit_reports()
        raise ValueError(
            tr("No valid signal was recognized!\nPlease check whether the address file contains a symbol table / DWARF debug info, or whether the Excel file contains a Name column.")
            + (tf("\n(a match report was generated this time, see {n1})", n1=match_report_path)
               if want_match_report else "")
        )

    selected_names = {str(name).strip() for name in (selected_signal_names or []) if str(name).strip()}
    if selected_names:
        signals = [
            sig for sig in signals
            if str(sig.get("ShortName") or sig.get("Name") or "").strip() in selected_names
        ]
        log_cb(tf("   🎯 Filtered by the check boxes, {n1} target signals kept", n1=len(signals)))

    dwarf_struct_signals = load_dwarf_struct_signals(addr_path, sym_details)
    if dwarf_struct_signals and not preview_signals:
        merged_signal_map: dict[tuple[str, str], dict] = {}
        replaced_structs = 0
        for sig in signals:
            name = str(sig.get("Name", "")).strip()
            shortname = str(sig.get("ShortName", "")).strip()
            if (
                name in dwarf_struct_signals
                and shortname == name
                and str(sig.get("DataType", "")).strip() == "array_u8"
                and str(sig.get("Description", "")).startswith("auto-detected from ELF")
            ):
                replaced_structs += 1
                for member_sig in dwarf_struct_signals[name]:
                    merged_signal_map[(member_sig["Name"], member_sig["ShortName"])] = member_sig
                continue
            merged_signal_map[(name, shortname)] = sig
        if replaced_structs:
            signals = list(merged_signal_map.values())
            log_cb(tf("   🧬 {n1} degraded struct-level signals were replaced using DWARF member information", n1=replaced_structs))

    for sig in signals:
        ref_name = str(sig.get("ShortName") or sig.get("Name") or "").strip()
        if not str(sig.get("Group", "")).strip():
            sig["Group"] = derive_group_from_name(ref_name)
        meta = sym_details.get(str(sig.get("Name") or "").strip(), {})
        obj = meta.get("object_type") or sig.get("ObjectType", "")
        sig["ObjectType"] = _normalize_object_type_label(obj)
        sig["Type"] = "CAL" if sig["ObjectType"] == "CALIBRATION" else "MEAS"

    highdim_signals = [s for s in signals if _is_unsupported_highdim_array_signal(s)]
    if highdim_signals:
        signals = [s for s in signals if not _is_unsupported_highdim_array_signal(s)]
        log_cb(tf("   ⚠ {n1} array signals of dimension 3 or higher were skipped; 1-D CURVE and 2-D MAP are supported", n1=len(highdim_signals)))

    if INCA_SAFE_SIMPLE_ONLY:
        skipped_signals = [s for s in signals if _should_skip_for_inca(s)]
        if skipped_signals:
            skip_enum = sum(1 for s in skipped_signals if str(s.get("DataType", "")).startswith("enum_"))
            skip_struct = sum(1 for s in skipped_signals if str(s.get("DataType", "")).startswith("struct_") or "." in str(s.get("ShortName", "")))
            skip_array = sum(1 for s in skipped_signals if _is_unsupported_highdim_array_signal(s))
            signals = [s for s in signals if not _should_skip_for_inca(s)]
            log_cb(tf("   ⚠ INCA simplified compatibility mode is on: {n1} complex signals were skipped", n1=len(skipped_signals)))
            log_cb(tf("   ├─ Arrays of dimension 3+: {n1}", n1=skip_array))
            log_cb(tf("   ├─ Structs: {n1}", n1=skip_struct))
            log_cb(tf("   └─ Enums: {n1}", n1=skip_enum))

    if not signals:
        raise ValueError(tr("No signals remain after the current rules were applied; please check whether they are all multi-dimensional arrays / structs / enums."))

    n_cal = sum(1 for s in signals if s.get("ObjectType") == "CALIBRATION")
    n_meas = len(signals) - n_cal
    n_enum = sum(1 for s in signals if str(s.get("DataType", "")).strip().startswith("enum_"))
    n_struct = sum(1 for s in signals if "." in str(s.get("ShortName", "")))
    n_array = sum(1 for s in signals if str(s.get("ArraySize", "1")).strip() not in ("", "1"))
    log_cb(tf("   OK, {n1} signals obtained in total  (CAL: {n2}, MEAS: {n3})", n1=len(signals), n2=n_cal, n3=n_meas))
    log_cb(tf("   🧩 {n1} enums, 🧱 {n2} struct members, 🧮 {n3} arrays/matrices", n1=n_enum, n2=n_struct, n3=n_array))
    n_expanded = sum(1 for s in signals
                      if re.match(r"^.+\.\d+\.", str(s.get("ShortName") or "").strip()))
    if n_expanded:
        log_cb(tf("   🧱 Struct arrays were expanded per element: {n1} element-level member signals (exact addresses)", n1=n_expanded))

    # 4. 匹配信号地址
    log_cb(tr("🔗 Matching signal addresses ..."))
    matched, missing = [], []
    dwarf_member_info = load_dwarf_member_infos(addr_path)
    struct_alias_count = 0
    struct_offset_auto = 0
    struct_offset_manual = 0
    struct_bitfield_auto = 0
    struct_bitfield_manual = 0
    struct_offset_missing = []

    for sig in signals:
        name = str(sig.get("Name", "")).strip()
        shortname = str(sig.get("ShortName", "")).strip()
        # 逐信号证据链：报告里要能回答「这个地址/类型/位域到底从哪来」。
        evidence: list[tuple[str, str]] = []
        if name in sym_map:
            base_addr, size = sym_map[name]
            final_addr = base_addr
            sig_eff = dict(sig)
            member_info = dwarf_member_info.get(shortname, {}) if shortname else {}
            meta = sym_details.get(name, {})
            if not sig_eff.get("Description"):
                sec = str(meta.get("section") or "")
                sig_eff["Description"] = f"auto-detected symbol from {sec or 'ADDR'}"

            evidence.append((
                EVID_ADDR_SYMBOL,
                tf("symbol {n1} → 0x{n2} (size {n3} bytes, section {n4}, source {n5})", n1=name, n2=format(int(base_addr), "08X"), n3=int(size), n4=str(meta.get('section') or tr("unknown")), n5=str(meta.get('source') or tr("unknown"))),
            ))
            type_src = str(sig.get("_type_source") or "").strip()
            if type_src == EVID_TYPE_DWARF:
                evidence.append((EVID_TYPE_DWARF, tr("Data type and array dimensions come from the DWARF debug information of the address file")))
            elif type_src:
                evidence.append((EVID_TYPE_DWARF, type_src))
            if sig.get("_xlsx_merged"):
                evidence.append((EVID_SUPP_XLSX, tr("Supplement columns such as Unit / Min / Max / Description come from the signal parameter XLSX")))

            manual_offset = None
            for col in ("AddrOffset", "ByteOffset", "MemberOffset"):
                raw_val = sig.get(col, "")
                if str(raw_val).strip() != "":
                    manual_offset = _safe_int_auto(raw_val, 0)
                    break

            # 成员别名：名.成员（结构体成员）与 名[i].成员（结构体数组逐元素展开）都算，
            # 这类信号的地址 = 符号基址 + AddrOffset 记录的成员偏移。
            is_struct_alias = bool(shortname and (
                shortname.startswith(name + ".") or shortname.startswith(name + "[")))
            if is_struct_alias:
                struct_alias_count += 1
                if manual_offset is not None:
                    final_addr = base_addr + manual_offset
                    struct_offset_manual += 1
                    _off_from_xlsx = str(sig.get("_offset_source") or "") == "xlsx"
                    if _off_from_xlsx:
                        _off_label = EVID_OFFSET_XLSX
                        _off_detail = (tf("AddrOffset column of the XLSX = {n1} (filled in manually), final address = 0x{n2} + {n3} = 0x{n4}", n1=format(manual_offset, "+d"), n2=format(int(base_addr), "08X"), n3=format(manual_offset, "+d"), n4=format(int(final_addr), "08X")))
                    else:
                        _off_label = EVID_OFFSET_DWARF
                        _off_detail = (tf("member offset AddrOffset expanded from DWARF = {n1}, final address = 0x{n2} + {n3} = 0x{n4}", n1=format(manual_offset, "+d"), n2=format(int(base_addr), "08X"), n3=format(manual_offset, "+d"), n4=format(int(final_addr), "08X")))
                    evidence.append((_off_label, _off_detail))
                elif member_info:
                    _mo = int(member_info.get("byte_offset") or 0)
                    final_addr = base_addr + _mo
                    struct_offset_auto += 1
                    evidence.append((
                        EVID_OFFSET_MEMBERINFO,
                        tf("offset of member {n1} within the struct = {n2} bytes, final address = 0x{n3} + {n4} = 0x{n5}", n1=shortname, n2=format(_mo, "+d"), n3=format(int(base_addr), "08X"), n4=format(_mo, "+d"), n5=format(int(final_addr), "08X")),
                    ))
                else:
                    struct_offset_missing.append(shortname)
                    evidence.append((
                        EVID_OFFSET_NONE,
                        tf("no offset information for member {n1} in DWARF, the address is computed from the base address 0x{n2} (may be inaccurate)", n1=shortname, n2=format(int(base_addr), "08X")),
                    ))

            manual_bit_size = str(sig.get("BitSize", "")).strip()
            manual_bit_offset = str(sig.get("BitOffset", "")).strip()
            if manual_bit_size:
                bit_size = max(1, _safe_int_auto(manual_bit_size, 0))
                bit_offset = max(0, _safe_int_auto(manual_bit_offset, 0))
                sig_eff["_bit_size"] = bit_size
                sig_eff["_bit_shift"] = bit_offset
                sig_eff["_bit_mask"] = ((1 << bit_size) - 1) << bit_offset
                struct_bitfield_manual += 1
                if str(sig.get("_bit_source") or "") == "xlsx":
                    _bit_label = EVID_BIT_XLSX
                    _bit_detail = (tf("BitOffset / BitSize of the XLSX = {n1} / {n2} (filled in manually), mask = 0x{n3}", n1=bit_offset, n2=bit_size, n3=format(sig_eff['_bit_mask'], "X")))
                else:
                    _bit_label = EVID_BIT_DWARF
                    _bit_detail = (tf("member bitfield BitOffset / BitSize expanded from DWARF = {n1} / {n2}, mask = 0x{n3}", n1=bit_offset, n2=bit_size, n3=format(sig_eff['_bit_mask'], "X")))
                evidence.append((_bit_label, _bit_detail))
            elif member_info.get("bit_mask") is not None:
                sig_eff["_bit_size"] = int(member_info.get("bit_size") or 0)
                sig_eff["_bit_shift"] = int(member_info.get("bit_offset") or 0)
                sig_eff["_bit_mask"] = int(member_info.get("bit_mask") or 0)
                struct_bitfield_auto += 1
                evidence.append((
                    EVID_BIT_MEMBERINFO,
                    tf("member bitfield mask = 0x{n1} (offset {n2}, size {n3})", n1=format(int(member_info.get('bit_mask') or 0), "X"), n2=int(member_info.get('bit_offset') or 0), n3=int(member_info.get('bit_size') or 0)),
                ))

            block, compu = generate_a2l_block(sig_eff, final_addr, size)
            matched.append((sig_eff, final_addr, size, compu, block))
            if want_match_report:
                match_records.append({
                    "status": "OK",
                    "name": name,
                    "shortname": shortname,
                    "object_type": str(sig.get("ObjectType") or ""),
                    "data_type": str(sig.get("DataType") or ""),
                    "unit": str(sig.get("Unit") or ""),
                    "address": int(final_addr),
                    "size": int(size),
                    "section": str(meta.get("section") or ""),
                    "evidence": evidence,
                    "reason": "",
                    "candidates": [],
                })
        else:
            missing.append(name)
            if want_match_report:
                # 失败原因要区分两类：整个信号没有符号 vs. 结构体基符号没有符号
                # （后者会导致其全部成员信号一起失败，是很常见的一类根因）。
                if shortname.startswith(name + "."):
                    _reason = (tf("the base symbol \"{n1}\" is not in the address file symbol table, so its member signal ({n2}) cannot be located", n1=name, n2=shortname))
                elif shortname.startswith(name + "["):
                    _reason = (tf("the base symbol \"{n1}\" is not in the address file symbol table, so its array element signal ({n2}) cannot be located", n1=name, n2=shortname))
                else:
                    _reason = tr("no symbol with the same name exists in the address file symbol table, so the ECU address cannot be determined")
                # 语义候选（可选）：按基符号缓存，避免同一结构体的多个成员重复检索
                _semantic_cands = []
                if _pool_aligner is not None:
                    if name in _sem_cache:
                        _semantic_cands = _sem_cache[name]
                    else:
                        try:
                            import rag_bridge
                            _semantic_cands = rag_bridge.semantic_candidates(
                                name, aligner=_rag_aligner, pool_aligner=_pool_aligner)
                        except Exception:
                            _semantic_cands = []
                        _sem_cache[name] = _semantic_cands
                match_records.append({
                    "status": "FAIL",
                    "name": name,
                    "shortname": shortname,
                    "object_type": str(sig.get("ObjectType") or ""),
                    "data_type": str(sig.get("DataType") or ""),
                    "unit": str(sig.get("Unit") or ""),
                    "address": None,
                    "size": 0,
                    "section": "",
                    "evidence": [],
                    "reason": _reason,
                    "candidates": suggest_symbol_candidates(name, list(sym_map.keys())),
                    "semantic_candidates": _semantic_cands,
                })

    if missing:
        uniq_missing = []
        seen = set()
        for item in missing:
            if item not in seen:
                uniq_missing.append(item)
                seen.add(item)
        log_cb(tf("   ⚠ The following {n1} signals were not found in the address file:", n1=len(uniq_missing)))
        for m in uniq_missing[:30]:
            log_cb(f"      - {m}")
        if len(uniq_missing) > 30:
            log_cb(tf("      ... {n1} unmatched in total", n1=len(uniq_missing)))

    if not matched:
        # 全部匹配失败是最需要报告的场景（多半是地址文件选错了版本），
        # 因此先落盘报告再抛错，避免用户拿不到任何线索。
        _emit_reports()
        raise ValueError(
            tf("None of the {n1} signals matched an address!\nPossible causes:\n  1. The variable names in the project source differ from the symbols in the address file (letter case)\n  2. Global symbols / debug info were not preserved at build time\n  3. The address file does not correspond to the current project version", n1=len(signals))
            + (tr("\n(a match report was generated this time, containing the per-signal failure reasons and similar symbol suggestions:")
               + str(match_report_path) + "）" if want_match_report else "")
        )
    log_cb(tf("   ✔ {n1} of {n2} signals matched successfully", n1=len(matched), n2=len(signals)))

    # 4.05 增量一致性检查（A2L-RAGAlign，可选）：对照已有 A2L 对象集合检查本轮信号。
    #      此前 existing_a2l_names 被提取出来却从未使用；这里补上重名 / 类型漂移 /
    #      孤儿对象 / 候选改名的检查。确定性结论直接给出，RAG 结论仅作候选提示。
    if _rag_aligner is not None and (want_match_report or want_diff_report):
        try:
            import rag_bridge
            # 必须用「实际落盘的对象集合」而不是内部信号列表：内部列表里同一名字
            # 可能出现多次（基信号 + 展开成员 + 自动识别信号），直接传进去会把
            # 正常重生成误判成「名称重复」。
            _written = [m[0] for m in matched]
            _consistency = rag_bridge.consistency_check(
                _written, existing_a2l_names, aligner=_rag_aligner)
            if _consistency.get("available"):
                _n_issue = (len(_consistency.get("duplicates") or [])
                            + len(_consistency.get("type_conflicts") or [])
                            + len(_consistency.get("removed_orphans") or [])
                            + len(_consistency.get("aligned_renames") or []))
                log_cb(tf("   🔍 Incremental consistency check: {n1} item(s) need attention", n1=_n_issue))
        except Exception as _e:
            _consistency = None
            log_cb(tf("   (the incremental consistency check was skipped: {n1})", n1=_e))

    # 4.1 交叉校验：与 INCA 工程里正在使用的 A2L 比对同名信号的地址。
    #     同一工程名在磁盘上常有多份不同版本的 AXF，选错版本会让全部观测量
    #     地址整体错位，INCA 侧表现为恒为 0 且不刷新，且肉眼看不出来。
    if reference_a2l_path and os.path.isfile(str(reference_a2l_path)):
        try:
            ref_addrs = collect_a2l_object_addresses(str(reference_a2l_path))
            gen_addrs = {str(sig.get("ShortName") or sig.get("Name") or "").strip(): addr
                         for sig, addr, _s, _c, _b in matched}
            common = [k for k in gen_addrs if k in ref_addrs]
            same = [k for k in common if gen_addrs[k] == ref_addrs[k]]
            log_cb(tr("🔀 Cross-checking addresses against the reference A2L ..."))
            log_cb(tf("   Reference A2L: {n1}", n1=os.path.abspath(str(reference_a2l_path))))
            if not common:
                log_cb(tr("   ⚠ The reference A2L has no signal with the same name, cannot verify address consistency"))
            else:
                ratio = len(same) / len(common)
                log_cb(tf("   {n1} signals with the same name, {n2} with identical addresses (match rate {n3}%)", n1=len(common), n2=len(same), n3=format(ratio * 100, ".1f")))
                if ratio < 0.9:
                    diff_samples = [(k, ref_addrs[k], gen_addrs[k]) for k in common
                                    if gen_addrs[k] != ref_addrs[k]][:5]
                    log_cb(tr("   ℹ The addresses of the reference A2L and this run differ on a large scale — they correspond to different firmware builds (the reference A2L may also be an older version); use the address file of the current firmware as the source of truth:"))
                    for k, ra, ga in diff_samples:
                        log_cb(tf("      {n1}: reference 0x{n2} / current 0x{n3}  (delta {n4})", n1=k, n2=format(ra, "08X"), n3=format(ga, "08X"), n4=format(ga - ra, "+d")))
        except Exception as _e:
            log_cb(tf("   (skipped the reference A2L cross-check: {n1})", n1=_e))

    if struct_alias_count:
        if struct_offset_auto:
            log_cb(tf("   🧱 {n1} struct member offsets were resolved automatically (using the real alignment in AXF/ELF)", n1=struct_offset_auto))
        if struct_offset_manual:            log_cb(tf("   🔧 {n1} struct member offsets were overridden by source/manual offsets", n1=struct_offset_manual))
        if struct_bitfield_auto:
            log_cb(tf("   🪄 {n1} bitfield masks were recognized automatically", n1=struct_bitfield_auto))
        if struct_bitfield_manual:
            log_cb(tf("   🔧 {n1} bitfield settings were overridden by BitOffset/BitSize in the source", n1=struct_bitfield_manual))
        if struct_offset_missing:
            log_cb(tf("   ⚠ {n1} struct members have no resolved offset, the struct base address is still used:", n1=len(struct_offset_missing)))
            for item in struct_offset_missing[:10]:
                log_cb(f"      - {item}")
            if len(struct_offset_missing) > 10:
                log_cb(tf("      ... {n1} in total", n1=len(struct_offset_missing)))

    # 5. 按新 Group 逻辑分组，并生成 GROUP 块
    log_cb(tr("✏  Generating the A2L content ..."))
    groups: dict[str, list] = {}
    compu_blocks = []
    compu_seen = set()
    for sig, addr, size, compu, block in matched:
        ref_name = str(sig.get("ShortName") or sig.get("Name") or "").strip()
        grp = derive_group_from_name(ref_name)
        sig["Group"] = grp
        groups.setdefault(grp, []).append((sig, addr, size, compu, block))
        if compu:
            # 去重键必须是 COMPU_METHOD 的名字，而不是整块文本：
            # 同一枚举类型被多个信号引用时，块内容一致（描述里不含信号名），
            # 按名字去重可保证 A2L 中每个 COMPU_METHOD / COMPU_VTAB 只定义一次。
            cm_key = _extract_compu_method_name(compu) or compu
            if cm_key not in compu_seen:
                compu_blocks.append(compu)
                compu_seen.add(cm_key)

    insert_lines = []
    if compu_blocks:
        log_cb(tf("   🧩 {n1} conversion blocks generated (COMPU_METHOD / COMPU_VTAB)", n1=len(compu_blocks)))
        if insert_line > 0:
            log_cb(tf("   ⚠ A manual insert line number {n1} was detected; to keep the conversion blocks correctly placed, the mode was switched to append at the end automatically", n1=insert_line))
            insert_line = 0
        insert_lines.append("\n    /* ===== COMPU_METHOD and COMPU_VTAB ===== */\n")
        insert_lines.extend(compu_blocks)

    # 基础数据类型的标准转换方法：主动生成，不依赖「缺失检测」。
    # 新生成的信号一律引用这些自建的标准 LINEAR 转换（COEFFS_LINEAR 1 0，恒等），
    # 不复用头文件里的 CM_uint8 / CM_uint16 ... ：那些是 CANape 的旧式写法
    # （RAT_FUNC 且缺 COMPU_TAB_REF），按 ASAM-2MC 1.6 属无效定义，INCA 会
    # 判定引用它们的 measurement 不可用——表现为观测量恒为 0 且不再刷新。
    # 名字带 CM_A2L_ 前缀，与头文件已有定义不会重名。
    _defined_cm = _extract_defined_compu_method_names(a2l_content)
    for _cm in compu_blocks:
        _defined_cm.add(_extract_compu_method_name(_cm))
    _standard_cm_added = 0
    for _cm_name in sorted(set(DTYPE_TO_COMPU.values())):
        if _cm_name in _defined_cm:
            continue
        _dtype = next((_d for _d, _m in DTYPE_TO_COMPU.items() if _m == _cm_name), None)
        _fmt = DTYPE_TO_FORMAT.get(_dtype, "%8.6") if _dtype else "%8.6"
        insert_lines.append(
            f"    /begin COMPU_METHOD\n"
            f"        /* Name of CompuMethod    */      {_cm_name}\n"
            f"        /* Long identifier        */      \"Identity conversion ({_cm_name})\"\n"
            f"        /* Conversion Type        */      LINEAR\n"
            f"        /* Format                 */      \"{_fmt}\"\n"
            f"        /* Units                  */      \"\"\n"
            f"        COEFFS_LINEAR 1 0\n"
            f"    /end COMPU_METHOD\n"
        )
        _defined_cm.add(_cm_name)
        _standard_cm_added += 1
    if _standard_cm_added:
        log_cb(tf("   🔧 {n1} standard conversion methods (LINEAR identity) generated: the new signals do not reference the legacy RAT_FUNC methods of the header", n1=_standard_cm_added))

    # 兜底：补齐其它被引用但未定义的 COMPU_METHOD。
    # 悬空引用会让 INCA 的 ASAM-2MC 解析器在建立测量量-转换方法关联时报错卡住。
    _referenced_cm: set[str] = set()
    for _sig, _addr, _size, _compu, _block in matched:
        _m = re.search(r"Conversion method\s*\*/\s*([^\s\r\n]+)", _block)
        if _m:
            _referenced_cm.add(_m.group(1))
    _missing_cm = sorted(_referenced_cm - _defined_cm)
    if _missing_cm:
        log_cb(tf("   🔧 {n1} conversion method references were undefined and have been completed automatically: {n2}", n1=len(_missing_cm), n2=', '.join(_missing_cm[:5])))
        for _cm_name in _missing_cm:
            _dtype = next((_d for _d, _m in DTYPE_TO_COMPU.items() if _m == _cm_name), None)
            _fmt = DTYPE_TO_FORMAT.get(_dtype, "%8.6") if _dtype else "%8.6"
            insert_lines.append(
                f"    /begin COMPU_METHOD\n"
                f"        /* Name of CompuMethod    */      {_cm_name}\n"
                f"        /* Long identifier        */      \"Identity conversion ({_cm_name})\"\n"
                f"        /* Conversion Type        */      LINEAR\n"
                f"        /* Format                 */      \"{_fmt}\"\n"
                f"        /* Units                  */      \"\"\n"
                f"        COEFFS_LINEAR 1 0\n"
                f"    /end COMPU_METHOD\n"
            )

    for grp, items in groups.items():
        insert_lines.append(f"\n    /* ===== Group: {grp} ===== */\n")
        cal_items = [(s, a, sz, c, b) for s, a, sz, c, b in items if s.get("ObjectType") == "CALIBRATION"]
        meas_items = [(s, a, sz, c, b) for s, a, sz, c, b in items if s.get("ObjectType") != "CALIBRATION"]
        if cal_items:
            insert_lines.append(f"    /* --- {grp}: Calibrations --- */\n")
            for sig, addr, size, compu, block in cal_items:
                insert_lines.append(block)
        if meas_items:
            insert_lines.append(f"    /* --- {grp}: Measurements --- */\n")
            for sig, addr, size, compu, block in meas_items:
                insert_lines.append(block)

    if groups:
        insert_lines.append("\n    /* ===== ASAP2 GROUP definitions ===== */\n")
        for grp, items in groups.items():
            safe_grp = _safe_identifier(grp, "Other")
            ref_chars = []
            ref_meas = []
            for sig, _, _, _, _ in items:
                ref_name = str(sig.get("ShortName") or sig.get("Name") or "").strip()
                if not ref_name:
                    continue
                if sig.get("ObjectType") == "CALIBRATION":
                    ref_chars.append(ref_name)
                else:
                    ref_meas.append(ref_name)

            group_path = f"TCU/BSW/{grp}"
            insert_lines.append(f"\t\t/begin GROUP {safe_grp} \"{group_path}\"\n")
            insert_lines.append("\t\t\t/begin REF_CHARACTERISTIC\n")
            for ref_name in ref_chars:
                insert_lines.append(f"\t\t\t\t{ref_name}\n")
            insert_lines.append("\t\t\t/end REF_CHARACTERISTIC\n")
            insert_lines.append("\t\t\t/begin REF_MEASUREMENT\n")
            for ref_name in ref_meas:
                insert_lines.append(f"\t\t\t\t{ref_name}\n")
            insert_lines.append("\t\t\t/end REF_MEASUREMENT\n")
            insert_lines.append("\t\t/end GROUP\n")

    new_body = "\n".join(insert_lines)

    # 6. 插入到 A2L 结构（原始头文件内容保持不变，仅追加新生成的块）
    body_text = normalize_newlines(new_body, a2l_newline)
    if insert_line == 0:
        module_end = re.search(r'(?m)^[ \t]*/end MODULE\b', a2l_content)
        project_end = re.search(r'(?m)^[ \t]*/end PROJECT\b', a2l_content)
        anchor = module_end or project_end
        if anchor:
            updated = a2l_content[:anchor.start()] + body_text + a2l_newline + a2l_content[anchor.start():]
        else:
            updated = normalize_newlines(build_minimal_a2l_header(new_body), a2l_newline)
            log_cb(tr("   ⚠ The original file lacks /end MODULE and /end PROJECT; a standard A2L skeleton was generated"))
    else:
        lines = a2l_content.splitlines(keepends=True)
        body_for_insert = body_text if body_text.endswith(a2l_newline) else body_text + a2l_newline
        if insert_line > len(lines):
            lines.append(body_for_insert)
            log_cb(tf("   ⚠ The insert line number {n1} exceeds the total line count ({n2}); the content was appended at the end", n1=insert_line, n2=len(lines)))
        else:
            lines.insert(insert_line - 1, body_for_insert)
            log_cb(tf("   ✅ New content inserted at line {n1}", n1=insert_line))
        updated = "".join(lines)

    # 7. 写输出（沿用原文件编码与换行符）
    log_cb(tf("💾 Writing the output file: {n1}", n1=out_path))
    try:
        write_a2l_text_preserving_format(out_path, updated, a2l_encoding, a2l_newline)
    except Exception:
        # 写盘失败也要留下匹配报告 —— 这恰恰是最需要线索的时刻
        _emit_reports()
        raise
    log_cb(tf("   OK, {n1} lines in total", n1=len(updated.splitlines())))

    # 7.1 旁路记录来源信息：地址文件选错是本工具最容易犯的错（同一工程名
    #     在磁盘上常有多份不同版本的 AXF，选错会让全部观测量地址整体错位，
    #     INCA 侧表现为恒为 0 且不刷新）。这里把来源写进同名 .log，便于比对。
    try:
        _prov = [
            tr("A2L generation provenance"),
            "=" * 60,
            tf("Generated at     : {n1}", n1=format(datetime.now(), "%Y-%m-%d %H:%M:%S")),
            tf("Header file      : {n1}", n1=os.path.abspath(a2l_path)),
            tf("Address file     : {n1}", n1=os.path.abspath(addr_path)),
        ]
        if _axf_stat is not None:
            _prov.append(tf("Address file time: {n1}  size {n2} bytes", n1=format(datetime.fromtimestamp(_axf_stat.st_mtime), "%Y-%m-%d %H:%M:%S"), n2=format(_axf_stat.st_size, ",")))
        _prov.append(tf("Symbols total    : {n1}", n1=len(sym_details)))
        _prov.append(tf("Output file      : {n1}", n1=os.path.abspath(out_path)))
        _prov.append(tf("Measurements/Calibrations: {n1}", n1=len(matched)))
        _prov.append("")
        with open(str(out_path) + ".log", "w", encoding="utf-8") as _fh:
            _fh.write("\n".join(_prov) + "\n")
        log_cb(tf("   🧾 Provenance recorded: {n1}.log", n1=os.path.basename(str(out_path))))
    except Exception as _e:
        log_cb(tf("   (failed to record the provenance: {n1})", n1=_e))

    # 7.2 匹配证据报告 / 版本差异报告（正常流程与异常路径共用 _emit_reports）
    _emit_reports()

    log_cb(tf("\n✅ A2L update finished! {n1} signals written successfully.", n1=len(matched)))


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                              工作线程                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
class UpdateWorker(QThread):
    sig_log  = pyqtSignal(str)
    sig_done = pyqtSignal(bool, str)   # success, message/path

    def __init__(self, a2l_path, signal_source_path, addr_path, out_path, insert_line,
                 selected_signal_names=None, preview_signals=None,
                 address_range_config=None, repair_header=False,
                 expand_struct_array=True, reference_a2l_path="",
                 match_report_path="", diff_report_path="", prev_a2l_path="",
                 semantic_align=False, parent=None):
        super().__init__(parent)
        self.a2l_path = a2l_path
        self.signal_source_path = signal_source_path
        self.addr_path = addr_path
        self.out_path = out_path
        self.insert_line = insert_line
        self.selected_signal_names = list(selected_signal_names or [])
        self.preview_signals = [dict(sig) for sig in (preview_signals or [])]
        self.address_range_config = {
            "CALIBRATION": list((address_range_config or {}).get("CALIBRATION") or []),
            "MEASUREMENT": list((address_range_config or {}).get("MEASUREMENT") or []),
        }
        self.repair_header = bool(repair_header)
        self.expand_struct_array = bool(expand_struct_array)
        self.reference_a2l_path = str(reference_a2l_path or "").strip()
        self.match_report_path = str(match_report_path or "").strip()
        self.diff_report_path = str(diff_report_path or "").strip()
        self.prev_a2l_path = str(prev_a2l_path or "").strip()
        self.semantic_align = bool(semantic_align)

    def run(self):
        try:
            do_update_a2l(
                self.a2l_path, self.signal_source_path,
                self.addr_path, self.out_path,
                self.insert_line,
                self.sig_log.emit,
                self.selected_signal_names,
                self.preview_signals,
                self.address_range_config,
                self.repair_header,
                self.expand_struct_array,
                self.reference_a2l_path,
                self.match_report_path,
                self.diff_report_path,
                self.prev_a2l_path,
                self.semantic_align,
            )
            self.sig_done.emit(True, self.out_path)
        except Exception as e:
            detail = traceback.format_exc()
            self.sig_log.emit(tf("\n❌ Error: {n1}\n\nDetails:\n{n2}", n1=e, n2=detail))
            self.sig_done.emit(False, str(e))


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          文件选择行（复合控件）                               ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
class FilePickRow(QWidget):
    def __init__(self, label: str, filters: str, parent=None, pick_dir: bool = False,
                 show_status: bool = False):
        """label / 占位符均传英文原文（翻译表 key），控件自身登记 i18n property 以便即时刷新。"""
        super().__init__(parent)
        self._filters = filters
        self._pick_dir = pick_dir
        self._show_status = show_status
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        lay = QHBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QLabel(tr(label))
        lbl.setProperty("i18nText", label)
        lbl.setFixedWidth(130)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(lbl)

        self.edit = QLineEdit()
        ph_key = "No path selected ..." if pick_dir else "No file selected ..."
        self.edit.setPlaceholderText(tr(ph_key))
        self.edit.setProperty("i18nPlaceholder", ph_key)
        self.edit.setReadOnly(True)
        lay.addWidget(self.edit, 1)

        btn = QPushButton(tr("Browse…"))
        btn.setProperty("i18nText", "Browse…")
        btn.setFixedWidth(76)
        btn.clicked.connect(self._browse)
        lay.addWidget(btn)
        outer.addLayout(lay)

        # 状态行：显示父目录与文件修改时间。
        # 同一工程名（�� TcuBsw.axf）在磁盘上常有多份不同版本，只看文件名
        # 极易选错旧版，生成的地址会整体错位（INCA 侧表现为恒为 0）。
        self.status = QLabel("")
        self.status.setStyleSheet("color: #b08030; font-size: 11px;")
        self.status.setWordWrap(True)
        self.status.setVisible(False)
        outer.addWidget(self.status)

        self.edit.textChanged.connect(self._refresh_status)

    def _refresh_status(self, *_args):
        if not self._show_status:
            return
        p = self.edit.text().strip()
        if not p or not os.path.isfile(p):
            self.status.setVisible(False)
            return
        try:
            st = os.stat(p)
            mtime = datetime.fromtimestamp(st.st_mtime)
            self.status.setText(
                tf("📁 {n1}    🕒 {n2}    ({n3} bytes)", n1=os.path.dirname(p), n2=format(mtime, "%Y-%m-%d %H:%M:%S"), n3=format(st.st_size, ",")))
            self.status.setToolTip(os.path.abspath(p))
            self.status.setVisible(True)
        except OSError:
            self.status.setVisible(False)

    def _browse(self):
        if self._pick_dir:
            path = QFileDialog.getExistingDirectory(self, tr("Select directory"), self.edit.text().strip() or "")
        else:
            path, _ = QFileDialog.getOpenFileName(self, tr("Select file"), "", self._filters)
        if path:
            self.edit.setText(path)

    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, p: str):
        self.edit.setText(p)


class HistoryFilePickRow(QWidget):
    sig_path_committed = pyqtSignal(str)

    def __init__(self, label: str, filters: str, parent=None, placeholder: str = "Type or browse for a path ..."):
        """label / placeholder 均传英文原文（翻译表 key），控件自身登记 i18n property 以便即时刷新。"""
        super().__init__(parent)
        self._filters = filters
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        lbl = QLabel(tr(label))
        lbl.setProperty("i18nText", label)
        lbl.setFixedWidth(130)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(lbl)

        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.NoInsert)
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.combo.lineEdit().setPlaceholderText(tr(placeholder))
        self.combo.lineEdit().setProperty("i18nPlaceholder", placeholder)
        self.combo.lineEdit().editingFinished.connect(self._emit_current_path)
        self.combo.activated.connect(lambda *_: self._emit_current_path())
        lay.addWidget(self.combo, 1)

        btn = QPushButton(tr("Browse…"))
        btn.setProperty("i18nText", "Browse…")
        btn.setFixedWidth(76)
        btn.clicked.connect(self._browse)
        lay.addWidget(btn)

    def _browse(self):
        current = self.path()
        start_dir = current if os.path.isdir(current) else os.path.dirname(current) if current else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("Select file"), start_dir, self._filters)
        if path:
            self.set_path(path)
            self._emit_current_path()

    def path(self) -> str:
        return self.combo.currentText().strip()

    def set_path(self, path: str):
        text = str(path or "").strip()
        self.combo.setEditText(text)

    def set_history(self, paths: list[str], current_path: str = ""):
        normalized = _normalize_path_history(paths, current_path)
        current = str(current_path or self.path()).strip()
        self.combo.blockSignals(True)
        self.combo.clear()
        if normalized:
            self.combo.addItems(normalized)
        self.combo.setEditText(current)
        self.combo.blockSignals(False)

    def _emit_current_path(self):
        self.sig_path_committed.emit(self.path())



# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                           信号表预览页                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
class SignalTableTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns = [tr("Keep")] + SIGNAL_PREVIEW_COLS
        self._signals: list[dict] = []
        self._filtered_signals: list[dict] = []
        self._checked_names: set[str] = set()
        self._show_checked_only = False
        self._updating_table = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)

        # 图例
        legend = QHBoxLayout()
        for color, key in [("#1f497d", "Calibration (CAL)"), ("#375623", "Measurement (MEAS)")]:
            dot = QLabel("  ")
            dot.setStyleSheet(f"background:{color}; border-radius:3px;")
            dot.setFixedSize(18, 18)
            legend.addWidget(dot)
            legend_lbl = QLabel(tr(key))
            legend_lbl.setProperty("i18nText", key)
            legend.addWidget(legend_lbl)
            legend.addSpacing(20)
        legend.addStretch()
        lay.addLayout(legend)

        edit_tip = QLabel(tr("The preview shows the full signal configuration columns, supports the keep / do-not-keep choice, and merges on export according to the XLSX path set on the main window."))
        edit_tip.setProperty("i18nText", "The preview shows the full signal configuration columns, supports the keep / do-not-keep choice, and merges on export according to the XLSX path set on the main window.")
        edit_tip.setWordWrap(True)
        edit_tip.setStyleSheet(f"color: {ACCENT2}; font-size: 11px; padding: 2px 0 6px 0;")
        lay.addWidget(edit_tip)

        ctrl_row = QHBoxLayout()
        self.btn_refresh_preview = QPushButton(tr("Preview auto-detected signals"))
        self.btn_refresh_preview.setProperty("i18nText", "Preview auto-detected signals")
        self.btn_refresh_preview.clicked.connect(self._request_preview_refresh)
        ctrl_row.addWidget(self.btn_refresh_preview)

        self.btn_show_checked = QPushButton(tr("Show checked only"))
        self.btn_show_checked.setProperty("i18nText", "Show checked only")
        self.btn_show_checked.setCheckable(True)
        self.btn_show_checked.toggled.connect(self._toggle_show_checked_only)
        ctrl_row.addWidget(self.btn_show_checked)

        self.btn_export_checked = QPushButton(tr("Export signal XLSX"))
        self.btn_export_checked.setProperty("i18nText", "Export signal XLSX")
        self.btn_export_checked.clicked.connect(self._export_checked_xlsx)
        ctrl_row.addWidget(self.btn_export_checked)
        ctrl_row.addStretch()

        lay.addLayout(ctrl_row)

        self.table = QTableWidget(0, len(self._columns))
        self.table.setHorizontalHeaderLabels(self._columns)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.verticalHeader().setVisible(False)
        # Keep 列的勾选框单独绘制（表格项不受 QSS 的 ::indicator 控制）
        self._check_delegate = CheckIndicatorDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self._check_delegate)
        lay.addWidget(self.table)
        column_widths = {
            "Keep": 56,
            "Group": 90,
            "Type": 68,
            "Name": 180,
            "ShortName": 180,
            "DataType": 110,
            "ObjectType": 110,
            "ArraySize": 90,
            "LongName": 220,
            "Unit": 90,
            "Min": 90,
            "Max": 90,
            "Factor": 90,
            "Offset": 90,
            "Description": 320,
            "BitMask": 110,
            "AddrOffset": 90,
            "BitOffset": 90,
            "BitSize": 90,
        }
        for idx, col_name in enumerate(self._columns):
            self.table.setColumnWidth(idx, column_widths.get(col_name, 120))

        self.lbl_stat = QLabel(tr("Auto-detected signals have not been previewed yet"))
        self.lbl_stat.setProperty("i18nText", "Auto-detected signals have not been previewed yet")
        self.lbl_stat.setAlignment(Qt.AlignCenter)
        self.lbl_stat.setStyleSheet(f"color: {ACCENT2}; padding: 4px;")
        lay.addWidget(self.lbl_stat)

    def apply_language(self):
        """语言切换后刷新表头（表头不是普通控件，需单独处理）。"""
        self._columns = [tr("Keep")] + SIGNAL_PREVIEW_COLS
        try:
            self.table.setHorizontalHeaderLabels(self._columns)
        except Exception:
            pass

    def _signal_key(self, sig: dict) -> str:
        return _signal_key(sig)

    def get_checked_signal_names(self) -> list[str]:
        return sorted(self._checked_names)

    def get_all_signals(self) -> list[dict]:
        return [dict(sig) for sig in self._signals]

    def set_checked_signal_names(self, names: list[str] | set[str]):
        valid_names = {self._signal_key(sig) for sig in self._signals}
        self._checked_names = {str(name).strip() for name in (names or []) if str(name).strip() in valid_names}
        self._rebuild_table()

    def load(self, signals: list, checked_names: list[str] | set[str] | None = None):
        self._signals = list(signals or [])
        if checked_names is None:
            current = set(self._checked_names)
            valid_names = {self._signal_key(sig) for sig in self._signals}
            self._checked_names = (current & valid_names) if current else set(valid_names)
        else:
            self.set_checked_signal_names(checked_names)
            return
        self._apply_filter()

    def _apply_filter(self):
        if self._show_checked_only:
            self._filtered_signals = [
                sig for sig in self._signals
                if self._signal_key(sig) in self._checked_names
            ]
        else:
            self._filtered_signals = list(self._signals)
        self._rebuild_table()

    def _toggle_show_checked_only(self, checked: bool):
        self._show_checked_only = bool(checked)
        self.btn_show_checked.setText(tr("Show all signals") if self._show_checked_only else tr("Show checked only"))
        self._apply_filter()

    def _rebuild_table(self):
        self._updating_table = True
        self.table.blockSignals(True)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.clearContents()
            self.table.setRowCount(len(self._filtered_signals))
            cal_color  = QColor("#1f497d")
            obs_color  = QColor("#375623")
            for r, sig in enumerate(self._filtered_signals):
                otype = sig.get("ObjectType", "").upper()
                bg = cal_color if otype in ("CALIBRATION", "CAL") else obs_color
                key = self._signal_key(sig)

                check_item = QTableWidgetItem()
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                check_item.setCheckState(Qt.Checked if key in self._checked_names else Qt.Unchecked)
                check_item.setBackground(bg)
                check_item.setForeground(QColor("#ffffff"))
                check_item.setData(Qt.UserRole, key)
                check_item.setData(Qt.UserRole + 1, "signal")
                self.table.setItem(r, 0, check_item)

                for ci, col in enumerate(SIGNAL_PREVIEW_COLS, start=1):
                    item = QTableWidgetItem(sig.get(col, ""))
                    flags = item.flags() | Qt.ItemIsEnabled | Qt.ItemIsSelectable
                    flags &= ~Qt.ItemIsEditable
                    item.setFlags(flags)
                    item.setBackground(bg)
                    item.setForeground(QColor("#ffffff"))
                    item.setData(Qt.UserRole, key)
                    item.setData(Qt.UserRole + 1, "signal_data")
                    item.setData(Qt.UserRole + 2, col)
                    self.table.setItem(r, ci, item)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.blockSignals(False)
            self._updating_table = False
        self._update_stats()

    def _update_stats(self):
        n_total = len(self._signals)
        n_visible = len(self._filtered_signals)
        n_checked = sum(1 for sig in self._signals if self._signal_key(sig) in self._checked_names)
        n_cal  = sum(1 for s in self._filtered_signals if s.get("ObjectType","").upper() in ("CALIBRATION","CAL"))
        n_meas = len(self._filtered_signals) - n_cal
        self.lbl_stat.setText(
            tf("Total {n1}   |   Showing {n2}   |   Checked {n3}   |   CAL: {n4}   |   MEAS: {n5}", n1=n_total, n2=n_visible, n3=n_checked, n4=n_cal, n5=n_meas)
        )

    def _on_item_changed(self, item: QTableWidgetItem):
        if self._updating_table:
            return
        if item.column() != 0:
            return
        item_type = str(item.data(Qt.UserRole + 1) or "")
        if item_type != "signal":
            return
        key = str(item.data(Qt.UserRole) or "").strip()
        if not key:
            return
        if item.checkState() == Qt.Checked:
            self._checked_names.add(key)
        else:
            self._checked_names.discard(key)
        if self._show_checked_only:
            self._apply_filter()
            return
        self._update_stats()

    def _export_checked_xlsx(self):
        if not self._signals:
            QMessageBox.warning(self, tr("Notice"), tr("Please preview the AXF signals before exporting."))
            return
        if not HAS_OPENPYXL:
            QMessageBox.critical(self, tr("Missing dependency"), tr("Exporting xlsx requires openpyxl."))
            return
        parent = self.parent()
        export_path = ""
        if parent is not None and hasattr(parent, "_current_signal_config_path"):
            export_path = parent._current_signal_config_path()
        if not export_path:
            export_path, _ = QFileDialog.getSaveFileName(
                self,
                tr("Export signal parameter configuration"),
                str(Path(_runtime_app_dir()) / SIGNAL_CONFIG_FILENAME),
                "Excel Files (*.xlsx)"
            )
            if not export_path:
                return
        existing_rows = []
        existed_before = os.path.isfile(export_path)
        if parent is not None and hasattr(parent, "_load_signal_config_rows_from_path"):
            existing_rows = parent._load_signal_config_rows_from_path(export_path)

        export_signals = list(self._signals)
        export_keep_names = self.get_checked_signal_names()
        if parent is not None and hasattr(parent, "pick_addr") and hasattr(parent, "_get_address_range_config"):
            addr_path = parent.pick_addr.path()
            if addr_path and os.path.isfile(addr_path):
                try:
                    signal_rows = parent._load_signal_config_rows() if hasattr(parent, "_load_signal_config_rows") else []
                    export_signals, _, _, _ = build_axf_signal_preview(
                        addr_path,
                        parent._get_address_range_config(),
                        signal_rows,
                        expand_struct_array=_expand_flag(parent),
                        a2l_path=(parent.pick_a2l.path()
                                  if hasattr(parent, "pick_a2l") else None),
                    )
                    valid_names = {_signal_key(sig) for sig in export_signals if _signal_key(sig)}
                    export_keep_names = [name for name in export_keep_names if name in valid_names]
                    self._signals = list(export_signals)
                    self._checked_names = set(export_keep_names)
                    self._apply_filter()
                except Exception:
                    pass

        signals, keep_names, summary = sync_signal_config_xlsx(
            export_path,
            export_signals,
            existing_rows=existing_rows,
            keep_names=export_keep_names,
        )
        self._signals = signals
        self._checked_names = set(keep_names)
        self._apply_filter()
        if parent is not None and hasattr(parent, "_on_signal_config_imported"):
            parent._on_signal_config_imported(str(export_path))
        QMessageBox.information(
            self,
            tr("Export finished"),
            tf("Synced {n1} signals to the target XLSX, of which {n2} are kept:\n{n3}\n{n4}\nAdded {n5}, removed {n6}, updated core columns {n7}.", n1=len(self._signals), n2=len(keep_names), n3=export_path, n4=tr("The original import table was updated (add / remove / modify) and the supplement columns were kept.") if existed_before else tr("The original import table was not found, so a parse result table was generated directly."), n5=int(summary.get('added') or 0), n6=int(summary.get('removed') or 0), n7=int(summary.get('updated') or 0))
        )

    def _import_checked_xlsx(self):
        if not self._signals:
            QMessageBox.warning(self, tr("Notice"), tr("Please preview the AXF signals before importing."))
            return
        parent = self.parent()
        current_path = parent._current_signal_config_path() if parent is not None and hasattr(parent, "_current_signal_config_path") else ""
        start_dir = os.path.dirname(current_path) if current_path else ""
        path, _ = QFileDialog.getOpenFileName(self, tr("Import signal parameter configuration"), start_dir, "Excel Files (*.xlsx)")
        if not path:
            return
        imported_rows = parse_signal_config_xlsx(path)
        self._signals, kept_names, matched = merge_signal_config_rows_into_signals(self._signals, imported_rows)
        self._checked_names = set(kept_names)
        self._apply_filter()
        if self.parent() is not None and hasattr(self.parent(), "_on_signal_config_imported"):
            self.parent()._on_signal_config_imported(str(path))
        QMessageBox.information(
            self,
            tr("Import finished"),
            tf("The XLSX holds {n1} records and {n2} AXF signals matched successfully; only the supplement columns were merged, the core columns still keep the AXF detection result.\nThe current import path has been switched to: {n3}", n1=len(imported_rows), n2=matched, n3=path)
        )

    def _request_preview_refresh(self):
        host = self.window()
        if host is not None and hasattr(host, "_preview_xlsx"):
            host._preview_xlsx()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                              主窗口                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self._ui_scale = self._detect_ui_scale()
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(self._scaled_px(1050), self._scaled_px(720))
        self.resize(self._scaled_px(1280), self._scaled_px(800))

        self._worker = None
        self._signals = []
        self._signal_config_path = ""
        self._signal_config_history: list[str] = []
        self._saved_checked_signal_names: list[str] = []
        self._saved_preview_signal_names: list[str] = []
        self._last_generated_addresses: dict[str, int] = {}
        self._settings = QSettings("A2LUpdateTool", "A2L_Update_V1_1")
        self._tab_text_keys: list[tuple[int, str]] = []

        # 界面语言：默认英文（可持久化）。必须在建 UI 之前设定，
        # 否则界面文案会按上一次的默认语言生成。
        _lang = self._settings.value("ui_language", "en", type=str)
        set_language(_lang if _lang in ("en", "zh") else "en")

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET + self._build_scaled_style_override())
        self._load_paths()
        self.statusBar().showMessage(
            tr("Ready – select the A2L and the address file, then click \"⚡ Update A2L\"")
        )

    def _detect_ui_scale(self) -> float:
        screen = QApplication.primaryScreen()
        if screen is None:
            return 1.0
        rect = screen.availableGeometry()
        scale = min(rect.width() / 1920.0, rect.height() / 1080.0)
        return max(0.9, min(1.35, scale))

    def _scaled_px(self, value: int) -> int:
        return max(1, int(round(value * self._ui_scale)))

    def _build_scaled_style_override(self) -> str:
        return f"""
QMainWindow, QWidget {{ font-size: {self._scaled_px(13)}px; }}
QGroupBox::title {{ font-size: {self._scaled_px(13)}px; }}
QPushButton#btn_update {{ font-size: {self._scaled_px(15)}px; min-height: {self._scaled_px(54)}px; }}
QTextEdit {{ font-size: {self._scaled_px(12)}px; }}
QLineEdit {{ font-size: {self._scaled_px(13)}px; }}
QTableWidget {{ font-size: {self._scaled_px(12)}px; }}
QStatusBar {{ font-size: {self._scaled_px(11)}px; }}
"""

    def _runtime_app_dir(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent

    def _current_signal_config_path(self) -> str:
        path = self.pick_signal_config.path() if hasattr(self, "pick_signal_config") else self._signal_config_path
        self._signal_config_path = str(path or "").strip()
        return self._signal_config_path

    def _remember_signal_config_path(self, path: str):
        current = str(path or "").strip()
        self._signal_config_history = _normalize_path_history(self._signal_config_history, current)
        self._signal_config_path = current
        if hasattr(self, "pick_signal_config"):
            self.pick_signal_config.set_history(self._signal_config_history, current)

    def _runtime_command_line(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'
        return f'"{Path(sys.executable).resolve()}" "{Path(__file__).resolve()}"'

    # ── UI 构建 ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(8)

        # ── 标题栏 ──────────────────────────────────────────────────────────
        title_row = QHBoxLayout()
        title_lbl = QLabel(f"  {APP_TITLE}")
        title_lbl.setFont(QFont("Segoe UI", self._scaled_px(18), QFont.Bold))
        title_lbl.setStyleSheet(f"color: {ACCENT}; padding: 2px 0;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        ver_lbl = QLabel(f"{APP_AUTHOR}  |  {APP_DATE}")
        ver_lbl.setStyleSheet(f"color: {ACCENT2}; font-size: {self._scaled_px(11)}px;")

        # 右上角：版本/日期 + 中英文切换开关（位于日期下方）
        head_right = QVBoxLayout()
        head_right.setSpacing(3)
        head_right.setAlignment(Qt.AlignRight | Qt.AlignTop)
        head_right.addWidget(ver_lbl)
        self.cmb_lang = QComboBox()
        self.cmb_lang.setObjectName("cmb_lang")
        self.cmb_lang.addItem("中文", "zh")
        self.cmb_lang.addItem("English", "en")
        self.cmb_lang.setFixedWidth(self._scaled_px(104))
        self.cmb_lang.setToolTip(tr("Switch the interface language (Chinese / English)"))
        self.cmb_lang.setProperty("i18nToolTip", "Switch the interface language (Chinese / English)")
        self.cmb_lang.currentIndexChanged.connect(self._on_language_changed)
        self.cmb_lang.setCurrentIndex(0 if get_language() == "zh" else 1)
        head_right.addWidget(self.cmb_lang)
        title_row.addLayout(head_right)
        root.addLayout(title_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f"color: {BORDER};")
        root.addWidget(sep)

        # ── 主体（左右分割）──────────────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        # ── 左侧（放入滚动区）─────────────────────────────────────────────
        # 左侧由多个分组纵向堆叠，内容高度会随选项增多而变长；直接放进 splitter
        # 时，Qt 会在空间不足时压缩控件，导致文字与控件相互重叠。
        # 用 QScrollArea 承载后，内容再长也只出现滚动条，不会发生重叠。
        # 左侧 = 外层容器：上方为可滚动的分组区，下方为固定的操作区。
        # 操作区（进度条 / 一键更新按钮 / 依赖状态）不参与滚动，任何时候都可见。
        left_outer = QWidget()
        left_outer.setObjectName("left_outer")
        left_outer.setMinimumWidth(self._scaled_px(360))
        left_outer.setMaximumWidth(self._scaled_px(500))
        left_outer_lay = QVBoxLayout(left_outer)
        left_outer_lay.setContentsMargins(0, 0, 8, 0)
        left_outer_lay.setSpacing(10)

        left_scroll = QScrollArea()
        left_scroll.setObjectName("left_scroll")
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left_scroll.setMinimumWidth(self._scaled_px(340))
        left = QWidget()
        left.setObjectName("left_panel")
        left.setMinimumWidth(self._scaled_px(340))
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 8, 0)
        left_lay.setSpacing(10)
        left_scroll.setWidget(left)
        left_outer_lay.addWidget(left_scroll, 1)

        # 文件选择组
        grp_files = QGroupBox(tr("File import"))
        grp_files.setProperty("i18nText", "File import")
        files_lay = QVBoxLayout(grp_files)
        files_lay.setSpacing(12)
        files_lay.setContentsMargins(12, 18, 12, 14)

        self.pick_a2l = FilePickRow(
            "A2L header:",
            "A2L Files (*.a2l *.A2L *.txt);;All Files (*)"
        )
        self.pick_addr = FilePickRow(
            "Address file:",
            "Address Files (*.map *.elf *.axf *.bin);;All Files (*)",
            show_status=True
        )
        self.pick_signal_config = HistoryFilePickRow(
            "Signal parameter XLSX:",
            "Excel Files (*.xlsx);;All Files (*)",
            placeholder="Can be typed or browsed; used for supplement info such as Keep / Unit"
        )
        self.pick_signal_config.sig_path_committed.connect(self._on_signal_config_imported)

        # 参考 A2L（可选）：INCA 工程里正在使用的那份 A2L。用于交叉校验
        # 「地址文件」是否选对版本——选错时全部观测量地址会整体错位，
        # INCA 侧表现为恒为 0 且不刷新，靠肉眼看不出来。
        self.pick_reference_a2l = FilePickRow(
            "Reference A2L:",
            "A2L Files (*.a2l *.A2L);;All Files (*)",
            show_status=True
        )
        self.pick_reference_a2l.status.setToolTip(
            tr("Optional. Enter the A2L currently used by the INCA project; during generation the addresses of the same-named signals are compared, and a large-scale mismatch raises an alarm telling you that the wrong \"address file\" version was selected."))
        self.pick_reference_a2l.status.setProperty("i18nToolTip", "Optional. Enter the A2L currently used by the INCA project; during generation the addresses of the same-named signals are compared, and a large-scale mismatch raises an alarm telling you that the wrong \"address file\" version was selected.")

        # 文件类型说明
        tip = QLabel(
            tr("💡 The program first auto-detects calibration values, measurements, enums, structs and array dimensions from the DWARF debug information of the .elf/.axf; if a signal parameter XLSX is given, supplement columns such as Keep and Unit are merged in as well.")
        )
        tip.setProperty("i18nText", "💡 The program first auto-detects calibration values, measurements, enums, structs and array dimensions from the DWARF debug information of the .elf/.axf; if a signal parameter XLSX is given, supplement columns such as Keep and Unit are merged in as well.")
        tip.setStyleSheet(f"color: #888; font-size: {self._scaled_px(11)}px;")
        tip.setWordWrap(True)

        files_lay.addWidget(self.pick_a2l)
        files_lay.addWidget(self.pick_addr)
        files_lay.addWidget(self.pick_signal_config)
        files_lay.addWidget(self.pick_reference_a2l)
        files_lay.addWidget(tip)

        left_lay.addWidget(grp_files)

        grp_ranges = QGroupBox(tr("Address range configuration"))
        grp_ranges.setProperty("i18nText", "Address range configuration")
        ranges_lay = QVBoxLayout(grp_ranges)
        ranges_lay.setContentsMargins(12, 18, 12, 14)
        ranges_lay.setSpacing(8)

        cal_lbl = QLabel(tr("Calibration address ranges:"))
        cal_lbl.setProperty("i18nText", "Calibration address ranges:")
        ranges_lay.addWidget(cal_lbl)
        self.edit_cal_ranges = QTextEdit()
        self.edit_cal_ranges.setPlaceholderText(tr("Example: 0x08000000-0x0800FFFF; separate multiple ranges with a semicolon or a line break"))
        self.edit_cal_ranges.setProperty("i18nPlaceholder", "Example: 0x08000000-0x0800FFFF; separate multiple ranges with a semicolon or a line break")
        self.edit_cal_ranges.setFixedHeight(self._scaled_px(54))
        ranges_lay.addWidget(self.edit_cal_ranges)

        meas_lbl = QLabel(tr("Measurement address ranges:"))
        meas_lbl.setProperty("i18nText", "Measurement address ranges:")
        ranges_lay.addWidget(meas_lbl)
        self.edit_meas_ranges = QTextEdit()
        self.edit_meas_ranges.setPlaceholderText(tr("Example: 0x20000000-0x2001FFFF; separate multiple ranges with a semicolon or a line break"))
        self.edit_meas_ranges.setProperty("i18nPlaceholder", "Example: 0x20000000-0x2001FFFF; separate multiple ranges with a semicolon or a line break")
        self.edit_meas_ranges.setFixedHeight(self._scaled_px(54))
        ranges_lay.addWidget(self.edit_meas_ranges)

        range_btn_row = QHBoxLayout()
        self.btn_import_ranges = QPushButton(tr("Import ranges TXT"))
        self.btn_import_ranges.setProperty("i18nText", "Import ranges TXT")
        self.btn_import_ranges.clicked.connect(self._import_address_ranges_txt)
        range_btn_row.addWidget(self.btn_import_ranges)
        self.btn_export_ranges = QPushButton(tr("Export ranges TXT"))
        self.btn_export_ranges.setProperty("i18nText", "Export ranges TXT")
        self.btn_export_ranges.clicked.connect(self._export_address_ranges_txt)
        range_btn_row.addWidget(self.btn_export_ranges)
        ranges_lay.addLayout(range_btn_row)

        range_tip = QLabel(tr("Once filled in, the symbols parsed from AXF/ELF are re-classified into CAL or MEAS by the address ranges first, and then the preview and the A2L are generated."))
        range_tip.setProperty("i18nText", "Once filled in, the symbols parsed from AXF/ELF are re-classified into CAL or MEAS by the address ranges first, and then the preview and the A2L are generated.")
        range_tip.setWordWrap(True)
        range_tip.setStyleSheet(f"color: #b8c0d8; font-size: {self._scaled_px(11)}px;")
        ranges_lay.addWidget(range_tip)
        left_lay.addWidget(grp_ranges)

        # 输出设置组
        grp_out = QGroupBox(tr("Output settings"))
        grp_out.setProperty("i18nText", "Output settings")
        out_lay = QVBoxLayout(grp_out)
        out_lay.setContentsMargins(12, 18, 12, 14)

        keep_tip = QLabel(tr("Generation always takes the AXF/ELF detection result as the core; if an external signal XLSX is given, only supplement columns such as Keep and Unit are merged into the in-memory preview; the target table is written back only when you click export."))
        keep_tip.setProperty("i18nText", "Generation always takes the AXF/ELF detection result as the core; if an external signal XLSX is given, only supplement columns such as Keep and Unit are merged into the in-memory preview; the target table is written back only when you click export.")
        keep_tip.setWordWrap(True)
        keep_tip.setStyleSheet(f"color: #b8c0d8; font-size: {self._scaled_px(11)}px;")
        out_lay.addWidget(keep_tip)

        # 头文件处理策略：默认保持用户提供的头文件内容原样不动
        self.chk_repair_header = QCheckBox(tr("Auto-repair header compatibility issues (rewrites the original header content)"))
        self.chk_repair_header.setProperty("i18nText", "Auto-repair header compatibility issues (rewrites the original header content)")
        self.chk_repair_header.setChecked(False)
        self.chk_repair_header.setToolTip(
            tr("Off by default: the program keeps the A2L header content you provided intact (MEMORY_SEGMENT names, XCP SEGMENT numbering, PAG, indentation and line endings are untouched) and only appends the newly generated signal blocks before /end MODULE.\nWhen checked, legacy XCP tokens and duplicated MEMORY_SEGMENT names in the header are rewritten automatically.")
        )
        self.chk_repair_header.setProperty("i18nToolTip", "Off by default: the program keeps the A2L header content you provided intact (MEMORY_SEGMENT names, XCP SEGMENT numbering, PAG, indentation and line endings are untouched) and only appends the newly generated signal blocks before /end MODULE.\nWhen checked, legacy XCP tokens and duplicated MEMORY_SEGMENT names in the header are rewritten automatically.")
        out_lay.addWidget(self.chk_repair_header)

        # 结构体数组处理策略：默认逐元素展开为成员信号
        self.chk_expand_struct_array = QCheckBox(tr("Expand struct arrays into per-element member signals"))
        self.chk_expand_struct_array.setProperty("i18nText", "Expand struct arrays into per-element member signals")
        self.chk_expand_struct_array.setChecked(True)
        self.chk_expand_struct_array.setToolTip(
            tr("Checked (default): when the elements of an array are structs/unions, they are expanded into independent signals of the form \"name[i].member\",\naddress = element base address + i × element size + member offset, so the values read by INCA are exactly correct.\nUnchecked: the whole array is output as a raw byte stream (MATRIX_DIM = actual byte size of the symbol),\nso the data is complete and readable, but without field semantics. The number of signals grows noticeably after expansion.")
        )
        self.chk_expand_struct_array.setProperty("i18nToolTip", "Checked (default): when the elements of an array are structs/unions, they are expanded into independent signals of the form \"name[i].member\",\naddress = element base address + i × element size + member offset, so the values read by INCA are exactly correct.\nUnchecked: the whole array is output as a raw byte stream (MATRIX_DIM = actual byte size of the symbol),\nso the data is complete and readable, but without field semantics. The number of signals grows noticeably after expansion.")
        out_lay.addWidget(self.chk_expand_struct_array)
        
        # 插入行号设置
        line_row = QHBoxLayout()
        _i18n_lbl_1 = QLabel()
        _i18n_lbl_1.setText(tr("Insert line number:"))
        _i18n_lbl_1.setProperty("i18nText", "Insert line number:")
        line_row.addWidget(_i18n_lbl_1)
        self.edit_line = QLineEdit()
        self.edit_line.setPlaceholderText(tr("Recommended: 0 (automatic insertion, safest)"))
        self.edit_line.setProperty("i18nPlaceholder", "Recommended: 0 (automatic insertion, safest)")
        self.edit_line.setToolTip(tr("Recommended: 0 — the content is inserted automatically before /end MODULE or /end PROJECT; a manual line number may shift the enum block positions"))
        self.edit_line.setProperty("i18nToolTip", "Recommended: 0 — the content is inserted automatically before /end MODULE or /end PROJECT; a manual line number may shift the enum block positions")
        line_row.addWidget(self.edit_line, 1)
        out_lay.addLayout(line_row)
        
        # 输出路径设置
        out_row = QHBoxLayout()
        _i18n_lbl_2 = QLabel()
        _i18n_lbl_2.setText(tr("Output path:"))
        _i18n_lbl_2.setProperty("i18nText", "Output path:")
        out_row.addWidget(_i18n_lbl_2)
        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText(tr("Default: same directory as the A2L header, with the _updated suffix"))
        self.edit_out.setProperty("i18nPlaceholder", "Default: same directory as the A2L header, with the _updated suffix")
        out_row.addWidget(self.edit_out, 1)
        btn_out = QPushButton(tr("Save as…"))
        btn_out.setProperty("i18nText", "Save as…")
        btn_out.setFixedWidth(64)
        btn_out.clicked.connect(self._browse_out)
        out_row.addWidget(btn_out)
        out_lay.addLayout(out_row)

        # 地址一致性自检：与一个已知在 INCA 中能正常读数的 A2L 对比地址
        ref_row = QHBoxLayout()
        _i18n_lbl_3 = QLabel()
        _i18n_lbl_3.setText(tr("Address self-check reference:"))
        _i18n_lbl_3.setProperty("i18nText", "Address self-check reference:")
        ref_row.addWidget(_i18n_lbl_3)
        self.edit_ref_a2l = QLineEdit()
        self.edit_ref_a2l.setPlaceholderText(tr("Select an A2L known to read correctly; after generation the addresses can be compared"))
        self.edit_ref_a2l.setProperty("i18nPlaceholder", "Select an A2L known to read correctly; after generation the addresses can be compared")
        self.edit_ref_a2l.setToolTip(
            tr("Used to quickly determine whether \"INCA reads all 0\" is caused by the wrong address file:\nif the addresses of the same-named signals differ on a large scale from the reference A2L, the address file and the actual firmware are not from the same build.")
        )
        self.edit_ref_a2l.setProperty("i18nToolTip", "Used to quickly determine whether \"INCA reads all 0\" is caused by the wrong address file:\nif the addresses of the same-named signals differ on a large scale from the reference A2L, the address file and the actual firmware are not from the same build.")
        ref_row.addWidget(self.edit_ref_a2l, 1)
        btn_ref = QPushButton(tr("Select…"))
        btn_ref.setProperty("i18nText", "Select…")
        btn_ref.setFixedWidth(64)
        btn_ref.clicked.connect(self._browse_ref_a2l)
        ref_row.addWidget(btn_ref)
        btn_check = QPushButton(tr("Self-check"))
        btn_check.setProperty("i18nText", "Self-check")
        btn_check.setFixedWidth(64)
        btn_check.setToolTip(tr("Compare the addresses of the same-named signals between this run and the reference A2L"))
        btn_check.setProperty("i18nToolTip", "Compare the addresses of the same-named signals between this run and the reference A2L")
        btn_check.clicked.connect(self._on_check_address_consistency)
        ref_row.addWidget(btn_check)
        out_lay.addLayout(ref_row)

        # ── 报告生成（两项独立开关，默认全部关闭）────────────────────────
        # 说明文字一律放在 tooltip 里，界面只留短标签，避免左侧栏变高、
        # 文字换行后与相邻控件重叠。
        self.chk_match_report = QCheckBox(tr("Generate match report (per-signal evidence)"))
        self.chk_match_report.setProperty("i18nText", "Generate match report (per-signal evidence)")
        self.chk_match_report.setChecked(False)
        self.chk_match_report.setToolTip(
            tr("Off by default (always unchecked at startup). When checked, \"<output name>_match_report.txt\" is generated in the output A2L directory:\n  · Matched: lists the source of the address / type / bitfield for every signal (symbol table, DWARF member offset,\n    XLSX manual override, ...), which directly answers \"where does this address come from\";\n  · Failed: gives the failure reason and similar symbol suggestions (letter case, leading underscore, member base symbol),\n    to troubleshoot problems like \"the symbol name does not match, so all measurements read 0\".")
        )
        self.chk_match_report.setProperty("i18nToolTip", "Off by default (always unchecked at startup). When checked, \"<output name>_match_report.txt\" is generated in the output A2L directory:\n  · Matched: lists the source of the address / type / bitfield for every signal (symbol table, DWARF member offset,\n    XLSX manual override, ...), which directly answers \"where does this address come from\";\n  · Failed: gives the failure reason and similar symbol suggestions (letter case, leading underscore, member base symbol),\n    to troubleshoot problems like \"the symbol name does not match, so all measurements read 0\".")
        out_lay.addWidget(self.chk_match_report)

        self.chk_diff_report = QCheckBox(tr("Generate version diff report (vs. previous version)"))
        self.chk_diff_report.setProperty("i18nText", "Generate version diff report (vs. previous version)")
        self.chk_diff_report.setChecked(False)
        self.chk_diff_report.setToolTip(
            tr("Off by default (always unchecked at startup). When checked, \"<output name>_diff_report.txt\" is generated in the output A2L directory:\n  it compares the A2L of this run with the \"previous A2L\" given below object by object and lists additions / removals,\n  the address shift delta and its distribution, and the data type / conversion method / bitfield changes.\n  Use it to confirm whether both A2L versions correspond to the same firmware (a uniform address shift means they do not).")
        )
        self.chk_diff_report.setProperty("i18nToolTip", "Off by default (always unchecked at startup). When checked, \"<output name>_diff_report.txt\" is generated in the output A2L directory:\n  it compares the A2L of this run with the \"previous A2L\" given below object by object and lists additions / removals,\n  the address shift delta and its distribution, and the data type / conversion method / bitfield changes.\n  Use it to confirm whether both A2L versions correspond to the same firmware (a uniform address shift means they do not).")
        out_lay.addWidget(self.chk_diff_report)

        # ── 语义对齐（A2L-RAGAlign）──────────────────────────────────────
        # 默认关闭：打开后，精确匹配失败的信号会额外给出检索增强的语义候选，
        # 并在匹配报告里追加一节增量一致性检查（重名 / 类型漂移 / 孤儿对象）。
        self.chk_semantic_align = QCheckBox(tr("Enable semantic alignment (A2L-RAGAlign)"))
        self.chk_semantic_align.setProperty("i18nText", "Enable semantic alignment (A2L-RAGAlign)")
        self.chk_semantic_align.setChecked(False)
        _sem_tip = tr(
            "Off by default. When checked, signals that failed exact name matching are additionally "
            "matched by retrieval-augmented semantic alignment (A2L-RAGAlign), and the match report "
            "gains an incremental-consistency section (duplicate names / data-type drift / orphaned "
            "objects / candidate renames) against the existing A2L header.\n"
            "Semantic candidates are only reported, never applied automatically.")
        self.chk_semantic_align.setToolTip(_sem_tip)
        self.chk_semantic_align.setProperty("i18nToolTip", _sem_tip)
        out_lay.addWidget(self.chk_semantic_align)

        # 「上一版 A2L」路径行：只有勾选差异报告时才显示，默认隐藏以节省空间。
        self.prev_a2l_box = QWidget()
        prev_row = QHBoxLayout(self.prev_a2l_box)
        prev_row.setContentsMargins(0, 0, 0, 0)
        prev_row.setSpacing(6)
        _i18n_lbl_4 = QLabel()
        _i18n_lbl_4.setText(tr("Previous:"))
        _i18n_lbl_4.setProperty("i18nText", "Previous:")
        prev_row.addWidget(_i18n_lbl_4)
        self.edit_prev_a2l = QLineEdit()
        self.edit_prev_a2l.setPlaceholderText(tr("Select the previously generated A2L as the comparison baseline"))
        self.edit_prev_a2l.setProperty("i18nPlaceholder", "Select the previously generated A2L as the comparison baseline")
        self.edit_prev_a2l.setToolTip(
            tr("Only effective when \"Generate version diff report\" is checked.\nYou can type the path or click \"Select…\"; the program never guesses the previous file on its own,\nso that A2L files of different projects or firmwares are not compared and produce misleading conclusions.")
        )
        self.edit_prev_a2l.setProperty("i18nToolTip", "Only effective when \"Generate version diff report\" is checked.\nYou can type the path or click \"Select…\"; the program never guesses the previous file on its own,\nso that A2L files of different projects or firmwares are not compared and produce misleading conclusions.")
        prev_row.addWidget(self.edit_prev_a2l, 1)
        btn_prev = QPushButton(tr("Select…"))
        btn_prev.setProperty("i18nText", "Select…")
        btn_prev.setFixedWidth(56)
        btn_prev.setToolTip(tr("Browse for the previous A2L file"))
        btn_prev.setProperty("i18nToolTip", "Browse for the previous A2L file")
        btn_prev.clicked.connect(self._browse_prev_a2l)
        prev_row.addWidget(btn_prev)
        out_lay.addWidget(self.prev_a2l_box)
        self.chk_diff_report.toggled.connect(self._sync_report_controls)
        self._sync_report_controls(False)

        left_lay.addWidget(grp_out)

        grp_rules = QGroupBox(tr("Rules"))
        grp_rules.setProperty("i18nText", "Rules")
        rules_lay = QVBoxLayout(grp_rules)
        rules_lay.setContentsMargins(12, 18, 12, 14)
        rules_lbl = QLabel(
            tr("1. Group classification: by default a '_' within the first 5 characters of the signal name decides the group; if present, the characters before the '_' are used as the Group name.\n2. It is recommended to end signal names with '_unit' to state the physical unit by naming convention.")
        )
        rules_lbl.setProperty("i18nText", "1. Group classification: by default a '_' within the first 5 characters of the signal name decides the group; if present, the characters before the '_' are used as the Group name.\n2. It is recommended to end signal names with '_unit' to state the physical unit by naming convention.")
        rules_lbl.setWordWrap(True)
        rules_lbl.setStyleSheet(f"color: #b8c0d8; font-size: {self._scaled_px(11)}px;")
        rules_lay.addWidget(rules_lbl)
        left_lay.addWidget(grp_rules)
        left_lay.addStretch()

        # 进度条（固定在左栏底部，不随上方分组滚动）
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedHeight(self._scaled_px(10))
        left_outer_lay.addWidget(self.progress)

        # 一键更新按钮（固定在左栏底部，始终可见可点）
        self.btn_update = QPushButton(tr("⚡  Update A2L"))
        self.btn_update.setProperty("i18nText", "⚡  Update A2L")
        self.btn_update.setObjectName("btn_update")
        self.btn_update.setMinimumHeight(self._scaled_px(54))
        self.btn_update.clicked.connect(self._on_update)
        left_outer_lay.addWidget(self.btn_update)

        # 依赖状态
        self.dep_lbl = QLabel(self._dep_status_text())
        self.dep_lbl.setStyleSheet(f"font-size: {self._scaled_px(11)}px; color: #888;")
        self.dep_lbl.setAlignment(Qt.AlignCenter)
        left_outer_lay.addWidget(self.dep_lbl)

        # ── 右侧：Tabs ────────────────────────────────────────────────────
        self.tabs = QTabWidget()

        # 日志 Tab
        log_tab = QWidget()
        log_lay = QVBoxLayout(log_tab)
        log_lay.setContentsMargins(4, 4, 4, 4)
        log_header = QHBoxLayout()
        _i18n_lbl_5 = QLabel()
        _i18n_lbl_5.setText(tr("Run log"))
        _i18n_lbl_5.setProperty("i18nText", "Run log")
        log_header.addWidget(_i18n_lbl_5)
        log_header.addStretch()
        btn_clear = QPushButton(tr("Clear"))
        btn_clear.setProperty("i18nText", "Clear")
        btn_clear.setObjectName("btn_clear")
        btn_clear.setFixedWidth(64)
        btn_clear.clicked.connect(self._clear_log)
        log_header.addWidget(btn_clear)
        log_lay.addLayout(log_header)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        log_lay.addWidget(self.log_box)
        self._add_tab(log_tab, "📋 Run log")

        # 信号表预览 Tab
        self.sig_tab = SignalTableTab(self)
        self._add_tab(self.sig_tab, "📊 Signal scan preview")

        self.a2l_preview = None
        if ENABLE_A2L_TEXT_PREVIEW:
            a2l_tab = QWidget()
            a2l_lay = QVBoxLayout(a2l_tab)
            a2l_lay.setContentsMargins(4, 4, 4, 4)
            a2l_hdr = QHBoxLayout()
            _i18n_lbl_6 = QLabel()
            _i18n_lbl_6.setText(tr("Generated A2L content (read-only preview)"))
            _i18n_lbl_6.setProperty("i18nText", "Generated A2L content (read-only preview)")
            a2l_hdr.addWidget(_i18n_lbl_6)
            a2l_hdr.addStretch()
            a2l_lay.addLayout(a2l_hdr)
            self.a2l_preview = QTextEdit()
            self.a2l_preview.setReadOnly(True)
            a2l_lay.addWidget(self.a2l_preview)
            self._add_tab(a2l_tab, "📝 A2L preview")

        splitter.addWidget(left_outer)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        # 欢迎日志
        self._log(
            tf("Welcome to {n1}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nSteps:\n  ① Select the A2L header  (.a2l / .txt)\n  ② Select the address file     (.map / .elf / .axf)\n  ③ Preview the auto-detected AXF signals and confirm via the \"Keep\" column whether to generate them\n  ④ Optionally import the signal parameter XLSX / the address range TXT\n  ⑤ Click \"⚡ Update A2L\"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nFeatures:\n  • Automatically sorts out the calibration / measurement address ranges\n  • Automatically detects enum / struct / array (DWARF is preferred)\n  • The Group follows the automatic rule using the '_' within the first 5 characters\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\nopenpyxl  : {n2}\npyelftools: {n3}\n", n1=APP_TITLE, n2=tr("✔ installed") if HAS_OPENPYXL else tr("✘ not installed  →  py -m pip install openpyxl"), n3=tr("✔ installed") if HAS_ELF else tr("✘ not installed  →  py -m pip install pyelftools"))
        )

    def _clear_log(self):
        self.log_box.clear()

    def _browse_out(self):
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Save output A2L"), "", "A2L Files (*.a2l);;All Files (*)"
        )
        if path:
            self.edit_out.setText(path)

    def _browse_ref_a2l(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select the reference A2L (known to read correctly in INCA)"), "",
            "A2L Files (*.a2l *.A2L);;All Files (*)"
        )
        if path:
            self.edit_ref_a2l.setText(path)

    def _browse_prev_a2l(self):
        """手动选择「上一版 A2L」，作为版本差异报告的比对基准。"""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Select the previous A2L (the baseline of the version diff report)"), "",
            "A2L Files (*.a2l *.A2L);;All Files (*)"
        )
        if path:
            self.edit_prev_a2l.setText(path)

    def _sync_report_controls(self, checked: bool = False):
        """「上一版 A2L」路径行只在勾选版本差异报告时显示。"""
        box = getattr(self, "prev_a2l_box", None)
        if box is not None:
            box.setVisible(bool(checked))

    def _on_check_address_consistency(self):
        """把本次生成的地址与参考 A2L 对比，判断是否选错了固件。"""
        ref_path = str(self.edit_ref_a2l.text() or "").strip()
        if not ref_path or not os.path.isfile(ref_path):
            QMessageBox.warning(self, tr("Notice"),
                                tr("Please select a reference A2L (a version known to read correctly in INCA)."))
            return
        gen_map = dict(getattr(self, "_last_generated_addresses", {}) or {})
        if not gen_map:
            QMessageBox.warning(self, tr("Notice"),
                                tr("There is no address data of this run yet; please run \"Update A2L\" once first."))
            return
        ref_map = collect_a2l_object_addresses(ref_path)
        if not ref_map:
            QMessageBox.warning(self, tr("Notice"),
                                tr("No MEASUREMENT/CALIBRATION address could be parsed from the reference A2L."))
            return

        stat = compare_a2l_addresses(ref_map, gen_map)
        self._log(tr("\n🔍 Address consistency self-check"))
        self._log(tf("   Reference A2L: {n1}", n1=ref_path))
        self._log(tf("   Reference signals {n1}, signals generated this run {n2}, same-named {n3}", n1=stat['ref_total'], n2=stat['gen_total'], n3=stat['common']))
        self._log(tf("   Identical addresses: {n1}, different addresses: {n2}", n1=stat['same'], n2=len(stat['diff'])))
        for name, r_addr, g_addr, delta in stat["diff"][:10]:
            self._log(tf("      - {n1}: reference 0x{n2} / current 0x{n3} (delta {n4})", n1=name, n2=format(r_addr, "08X"), n3=format(g_addr, "08X"), n4=format(delta, "+d")))
        if stat["delta_hist"]:
            self._log(tr("   Delta distribution: ") + ", ".join(f"{d:+d}×{n}" for d, n in stat["delta_hist"]))
        if stat["only_ref"]:
            self._log(tf("   Only in the reference: {n1}", n1=len(stat['only_ref'])))
        if stat["only_gen"]:
            self._log(tf("   Only in this run: {n1}", n1=len(stat['only_gen'])))

        if stat["same"] == 0 and stat["common"] > 0:
            verdict = (tr("The addresses of all same-named signals differ — the address file and the reference A2L are not from the same build,\nso INCA reads at wrong addresses and the measurements usually show all 0 or no change."))
        elif stat["diff"]:
            verdict = (tf("{n1} same-named signals have inconsistent addresses (see the delta distribution in the log),\nif the INCA readings look wrong, please confirm that the firmware version matches the address file.", n1=len(stat['diff'])))
        else:
            verdict = tr("The addresses of all same-named signals are identical; the address file matches the reference A2L.")

        QMessageBox.information(
            self, tr("Address consistency self-check"),
            tf("Reference signals {n1}, generated this run {n2}, same-named {n3}\nIdentical addresses {n4}, inconsistent {n5}\n\n{n6}", n1=stat['ref_total'], n2=stat['gen_total'], n3=stat['common'], n4=stat['same'], n5=len(stat['diff']), n6=verdict)
        )

    def _get_address_range_config(self) -> dict[str, list[tuple[int, int]]]:
        try:
            return {
                "CALIBRATION": parse_address_ranges_text(self.edit_cal_ranges.toPlainText()),
                "MEASUREMENT": parse_address_ranges_text(self.edit_meas_ranges.toPlainText()),
            }
        except ValueError as e:
            raise ValueError(tf("The address range configuration is invalid: {n1}", n1=e))

    def _set_address_range_config(self, config: dict[str, list[tuple[int, int]]]):
        self.edit_cal_ranges.setPlainText(format_address_ranges_text((config or {}).get("CALIBRATION") or []))
        self.edit_meas_ranges.setPlainText(format_address_ranges_text((config or {}).get("MEASUREMENT") or []))

    def _load_signal_config_rows(self) -> list[dict]:
        return self._load_signal_config_rows_from_path(self._current_signal_config_path())

    def _load_signal_config_rows_from_path(self, candidate_path: str) -> list[dict]:
        candidate_path = str(candidate_path or "").strip()
        if not candidate_path or not os.path.isfile(candidate_path):
            return []
        try:
            return parse_signal_config_xlsx(candidate_path)
        except Exception as e:
            self._log(tf("⚠ Failed to read the signal parameter XLSX, it was ignored: {n1}\n   Reason: {n2}", n1=candidate_path, n2=e))
            return []

    def _on_signal_config_imported(self, path: str):
        normalized_path = str(path or "").strip()
        previous_path = str(self._signal_config_path or "").strip()
        self._remember_signal_config_path(normalized_path)
        self.statusBar().showMessage(tf("Signal parameter XLSX synchronized: {n1}", n1=self._signal_config_path))

        if normalized_path == previous_path:
            return
        if not self._signals:
            return

        addr_path = self.pick_addr.path()
        if not addr_path or not os.path.isfile(addr_path):
            return

        try:
            signal_rows = self._load_signal_config_rows()
            signals, kept_names, _, matched = build_axf_signal_preview(
                addr_path,
                self._get_address_range_config(),
                signal_rows,
                expand_struct_array=_expand_struct_array_flag(self),
                a2l_path=self.pick_a2l.path(),
            )
        except Exception as e:
            self._log(tf("⚠ The signal parameter XLSX was switched, but the current preview could not be refreshed automatically: {n1}", n1=e))
            return

        checked_names = kept_names if signal_rows else self._default_checked_names_for_signals(signals)
        self._signals = [dict(sig) for sig in signals]
        self._saved_checked_signal_names = list(checked_names)
        self._saved_preview_signal_names = [_signal_key(sig) for sig in signals]
        self.sig_tab.load(signals, checked_names)
        self._log(tr("🔄 The preview signals were refreshed according to the current signal parameter XLSX; the supplement columns and the keep state were synchronized into the software."))
        if signal_rows and matched:
            self._log(tf("📥 The signal parameter XLSX was re-applied: {n1} AXF signals matched", n1=matched))

    def _default_checked_names_for_signals(self, signals: list[dict]) -> list[str]:
        valid_names = [_signal_key(sig) for sig in signals if _signal_key(sig)]
        if not valid_names:
            return []
        current_checked = set(self.sig_tab.get_checked_signal_names()) if self._signals else set()
        previous_names = {_signal_key(sig) for sig in self._signals if _signal_key(sig)}
        if current_checked:
            merged = [name for name in valid_names if name in current_checked or name not in previous_names]
            return merged or list(valid_names)
        return list(valid_names)

    def _export_address_ranges_txt(self):
        try:
            config = self._get_address_range_config()
        except ValueError as e:
            QMessageBox.warning(self, tr("Export failed"), str(e))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr("Export the address range configuration"), "address_ranges.txt", "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(format_address_ranges_txt(config))
        QMessageBox.information(self, tr("Export complete"), tf("The address range configuration was exported to:\n{n1}", n1=path))

    def _import_address_ranges_txt(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Import the address range configuration"), "", "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        try:
            config = parse_address_ranges_txt_file(path)
            self._set_address_range_config(config)
            QMessageBox.information(self, tr("Import complete"), tf("The address range configuration was imported from:\n{n1}", n1=path))
        except Exception as e:
            QMessageBox.critical(self, tr("Import failed"), str(e))

    def _preview_xlsx(self):
        addr_path = self.pick_addr.path()
        if not addr_path:
            QMessageBox.warning(self, tr("Notice"), tr("Please select the address file first!"))
            return
        if not os.path.isfile(addr_path):
            QMessageBox.warning(self, tr("Notice"), tf("The address file does not exist:\n{n1}", n1=addr_path))
            return
        try:
            signal_path = self._current_signal_config_path()
            signal_rows = self._load_signal_config_rows()
            signals, kept_names, _, matched = build_axf_signal_preview(
                addr_path,
                self._get_address_range_config(),
                signal_rows,
                expand_struct_array=_expand_struct_array_flag(self),
                a2l_path=self.pick_a2l.path(),
            )
            change_summary = summarize_signal_config_changes(signals, signal_rows)
            checked_names = kept_names if signal_rows else self._default_checked_names_for_signals(signals)
            self._signals = signals
            self._saved_checked_signal_names = list(checked_names)
            self._saved_preview_signal_names = [_signal_key(sig) for sig in signals]
            self.sig_tab.load(signals, checked_names)
            if signal_rows:
                self._log(
                    tf("📋 AXF compared with the current signal parameter XLSX: {n1} added, {n2} removed, {n3} core columns updated", n1=int(change_summary.get('added') or 0), n2=int(change_summary.get('removed') or 0), n3=int(change_summary.get('updated') or 0))
                )
            if matched:
                self._log(tf("📥 The signal parameter XLSX was merged in: {n1} AXF signals matched", n1=matched))
            if signal_path and signal_rows:
                self._log(tf("📄 Current signal parameter XLSX: {n1}", n1=signal_path))
            self.statusBar().showMessage(
                tf("{n1} signals recognized ← {n2}", n1=len(signals), n2=addr_path)
            )
        except Exception as e:
            QMessageBox.critical(self, tr("Preview failed"),
                tf("Auto-detection of the signals failed:\n\n{n1}", n1=e))

    def _log(self, msg: str):
        self.log_box.append(msg)
        self.log_box.moveCursor(QTextCursor.End)

    def _on_update(self):
        a2l_path = self.pick_a2l.path()
        source_path = ""
        addr_path = self.pick_addr.path()
        try:
            address_range_config = self._get_address_range_config()
        except ValueError as e:
            QMessageBox.warning(self, tr("Input error"), str(e))
            return

        errors = []
        if not a2l_path:
            errors.append(tr("• No A2L header selected"))
        elif not os.path.isfile(a2l_path):
            errors.append(tf("• The A2L header does not exist:\n  {n1}", n1=a2l_path))
        if not addr_path:
            errors.append(tr("• No address file selected (.map / .elf / .axf)"))
        elif not os.path.isfile(addr_path):
            errors.append(tf("• The address file does not exist:\n  {n1}", n1=addr_path))

        if errors:
            QMessageBox.warning(self, tr("Incomplete input"),
                tr("Please check the following problems:\n\n") + "\n\n".join(errors))
            return

        try:
            signal_rows = self._load_signal_config_rows()
            preview_signals, xlsx_checked_names, _, matched = build_axf_signal_preview(
                addr_path,
                address_range_config,
                signal_rows,
                expand_struct_array=_expand_struct_array_flag(self),
                a2l_path=a2l_path,
            )
        except Exception as e:
            QMessageBox.critical(self, tr("Update failed"), tf("Auto-detection of the signals failed before generation:\n\n{n1}", n1=e))
            return

        if signal_rows:
            selected_signal_names = list(xlsx_checked_names)
        else:
            selected_signal_names = self._default_checked_names_for_signals(preview_signals)

        self._signals = [dict(sig) for sig in preview_signals]
        self._saved_checked_signal_names = list(selected_signal_names)
        self._saved_preview_signal_names = [_signal_key(sig) for sig in preview_signals]

        if self.sig_tab.get_all_signals():
            self.sig_tab.load(preview_signals, selected_signal_names)

        self._log(tr("🔄 This update re-read the current address file and the signal parameter XLSX, and regenerated the signals from the latest content."))
        if signal_rows and matched:
            self._log(tf("📥 The signal parameter XLSX was applied automatically: {n1} AXF signals matched", n1=matched))
        if not self.sig_tab.get_all_signals():
            self._log(tr("⚡ To reduce the lag, the \"Signal scan preview\" table was not expanded automatically in this run; click \"Preview auto-detected signals\" manually to see it."))

        if not selected_signal_names:
            QMessageBox.warning(self, tr("Notice"), tr("No signal is currently kept; please mark the signals to be generated as \"Keep\" on the preview page first."))
            return

        out_path = self.edit_out.text().strip()
        if not out_path:
            base = os.path.splitext(a2l_path)[0]
            out_path = base + "_updated.a2l"
            self.edit_out.setText(out_path)

        # 获取插入行号
        line_text = self.edit_line.text().strip()
        insert_line = 0
        if line_text:
            try:
                insert_line = int(line_text)
                if insert_line < 0:
                    insert_line = 0
            except ValueError:
                QMessageBox.warning(self, tr("Input error"), tr("The insert line number must be an integer!"))
                return

        # 地址文件一致性防呆检查：地址文件与实际固件不一致时 INCA 会读到全 0
        conflict_warnings = detect_address_file_conflicts(addr_path, out_path)
        if conflict_warnings:
            self._log(tr("   ⚠ The address file consistency check found problems:"))
            for item in conflict_warnings:
                self._log("      " + item.replace("\n", "\n      "))
            answer = QMessageBox.question(
                self, tr("Address file consistency check"),
                tr("The address file may not belong to the same firmware as the output file:\n\n")
                + "\n\n".join(conflict_warnings)
                + tr("\n\nIf the wrong address file is selected, the measurements in INCA will all be 0 and never change. Continue anyway?"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                self._log(tr("   ⛔ Generation cancelled (the address file consistency check did not pass)"))
                return

        # 报告开关：均默认关闭；差异报告必须给出「上一版 A2L」路径才可执行。
        want_match_report = self.chk_match_report.isChecked()
        want_diff_report = self.chk_diff_report.isChecked()
        prev_a2l_path = self.edit_prev_a2l.text().strip()
        if want_diff_report and not prev_a2l_path:
            QMessageBox.warning(self, tr("Incomplete input"),
                                tr("\"Generate version diff report\" is checked, but no \"previous A2L\" path was given.\n\nPlease type the path of the previous A2L manually (the version diff report needs it as the comparison baseline), or uncheck the option."))
            return
        if want_diff_report and not os.path.isfile(prev_a2l_path):
            QMessageBox.warning(self, tr("Incomplete input"),
                                tf("The \"previous A2L\" file does not exist:\n{n1}", n1=prev_a2l_path))
            return

        out_base = os.path.splitext(out_path)[0]
        match_report_path = (out_base + "_match_report.txt") if want_match_report else ""
        diff_report_path = (out_base + "_diff_report.txt") if want_diff_report else ""

        self._log(f"\n{'═'*52}")
        self._log(tr("Starting the A2L update ..."))
        self._log(f"  A2L   : {a2l_path}")
        self._log(f"  ADDR  : {addr_path}")
        try:
            _st = os.stat(addr_path)
            self._log(tf("          Modified at {n1}, size {n2} MB", n1=format(datetime.fromtimestamp(_st.st_mtime), "%Y-%m-%d %H:%M:%S"), n2=format(_st.st_size / 1024 / 1024, ".2f")))
        except OSError:
            pass
        self._log(tf("  KEEP  : {n1} / {n2} signals will be written to the A2L", n1=len(selected_signal_names), n2=len(preview_signals)))
        self._log(f"  OUT   : {out_path}")
        if insert_line > 0:
            self._log(tf("  Insert line number: {n1}", n1=insert_line))
            self._log(tr("  ⚠ Note: if enum signals are included, the program automatically switches to the safe insertion mode"))
        else:
            self._log(tr("  Insertion mode: auto-detected (inserted before /end MODULE or /end PROJECT)"))
        if self.chk_repair_header.isChecked():
            self._log(tr("  ⚠ Header handling: auto repair (rewrites the original header content)"))
        else:
            self._log(tr("  🔒 Header handling: the original content is kept unchanged (only the newly generated signal blocks are appended)"))
        if _expand_struct_array_flag(self):
            self._log(tr("  🧱 Struct arrays: expanded into per-element member signals (exact addresses)"))
        else:
            self._log(tr("  🧱 Struct arrays: output as a raw byte stream (data complete, no field semantics)"))
        if want_match_report:
            self._log(tf("  🔍 Match report: {n1}", n1=match_report_path))
        if want_diff_report:
            self._log(tf("  🔀 Version diff report: {n1}", n1=diff_report_path))
            self._log(tf("      Previous A2L: {n1}", n1=prev_a2l_path))
        if self.chk_semantic_align.isChecked():
            self._log(tr("  🧠 Semantic alignment (A2L-RAGAlign): enabled (semantic candidates + incremental consistency check)"))
        self._log(f"{'═'*52}")
        self.tabs.setCurrentIndex(0)

        self.btn_update.setEnabled(False)
        self.progress.setVisible(True)
        self.statusBar().showMessage(tr("Parsing the address file and updating the A2L, please wait ..."))

        self._worker = UpdateWorker(
            a2l_path, source_path, addr_path, out_path, insert_line,
            selected_signal_names, preview_signals, address_range_config,
            self.chk_repair_header.isChecked(),
            _expand_struct_array_flag(self),
            self.pick_reference_a2l.path(),
            match_report_path,
            diff_report_path,
            prev_a2l_path,
            self.chk_semantic_align.isChecked(),
        )
        self._worker.sig_log.connect(self._log)
        self._worker.sig_done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, success: bool, msg: str):
        self.btn_update.setEnabled(True)
        self.progress.setVisible(False)

        if success:
            out_path = msg
            # 记录本次生成的地址，供“地址一致性自检”使用
            try:
                self._last_generated_addresses = collect_a2l_object_addresses(out_path)
            except Exception:
                self._last_generated_addresses = {}
            if ENABLE_A2L_TEXT_PREVIEW and self.a2l_preview is not None:
                try:
                    with open(out_path, "r", errors="replace") as fh:
                        content = fh.read()
                    self.a2l_preview.setPlainText(content)
                    self.tabs.setCurrentIndex(2)
                except Exception:
                    pass
            self.statusBar().showMessage(tf("✔ A2L update succeeded  →  {n1}", n1=out_path))

            dlg = QMessageBox(self)
            dlg.setWindowTitle(tr("Update succeeded"))
            dlg.setProperty("i18nWindowTitle", "Update succeeded")
            dlg.setIcon(QMessageBox.Information)
            dlg.setText(tr("<b>🎉 A2L update succeeded!</b>"))
            dlg.setProperty("i18nText", "<b>🎉 A2L update succeeded!</b>")
            dlg.setInformativeText(
                tf("The output file was saved to:\n{n1}\n\n", n1=out_path)
                + (tr("You can switch to the \"📝 A2L preview\" tab to see the result.") if ENABLE_A2L_TEXT_PREVIEW else tr("To avoid lag on large files, the runtime A2L text preview is disabled by default since version 2.4."))
            )
            dlg.setStandardButtons(QMessageBox.Ok)
            dlg.exec_()
        else:
            self.statusBar().showMessage(tr("✘ A2L update failed, please check the run log"))
            dlg = QMessageBox(self)
            dlg.setWindowTitle(tr("Update failed"))
            dlg.setProperty("i18nWindowTitle", "Update failed")
            dlg.setIcon(QMessageBox.Critical)
            dlg.setText(tr("<b>❌ A2L update failed!</b>"))
            dlg.setProperty("i18nText", "<b>❌ A2L update failed!</b>")
            dlg.setInformativeText(
                tr("Please check the \"📋 Run log\" tab for the detailed error message,\nor expand \"Details\" below to see the exception stack.")
            )
            dlg.setDetailedText(msg)
            dlg.setStandardButtons(QMessageBox.Ok)
            dlg.exec_()
            self.tabs.setCurrentIndex(0)

    # ── 路径持久化 ─────────────────────────────────────────────────────────────
    # ── 语言切换 ─────────────────────────────────────────────────────────
    def _add_tab(self, widget, key: str):
        """添加标签页并登记标题，切换语言时统一刷新。"""
        idx = self.tabs.addTab(widget, tr(key))
        self._tab_text_keys.append((idx, key))
        return idx

    def _on_language_changed(self, index: int):
        """切换中/英文：立即刷新界面文案，并记住选择。"""
        try:
            lang = str(self.cmb_lang.itemData(index) or "en")
        except Exception:
            lang = "en"
        if lang not in ("en", "zh"):
            lang = "en"
        if lang == get_language():
            return
        set_language(lang)
        self._apply_ui_lang()
        try:
            self._settings.setValue("ui_language", lang)
        except Exception:
            pass

    def _dep_status_text(self) -> str:
        """依赖安装状态文案（调用时翻译，便于随语言刷新）。"""
        return tf("openpyxl : {n1}   pyelftools: {n2}",
                  n1=tr("✔ installed") if HAS_OPENPYXL else tr("✘ not installed"),
                  n2=tr("✔ installed") if HAS_ELF else tr("✘ not installed"))

    def _apply_ui_lang(self):
        """按当前语言刷新所有界面文案（含按钮、标签、提示与占位符）。"""
        try:
            from PyQt5.QtWidgets import QWidget as _QW
        except Exception:
            return
        for w in self.findChildren(_QW):
            for prop, setter in (("i18nText", "setText"),
                                ("i18nToolTip", "setToolTip"),
                                ("i18nPlaceholder", "setPlaceholderText"),
                                ("i18nTitle", "setTitle"),
                                ("i18nWindowTitle", "setWindowTitle")):
                key = w.property(prop)
                if not key:
                    continue
                try:
                    if prop == "i18nText":
                        # QGroupBox 等没有 setText，标题要走 setTitle。
                        if hasattr(w, "setText"):
                            w.setText(tr(key))
                        elif hasattr(w, "setTitle"):
                            w.setTitle(tr(key))
                    else:
                        getattr(w, setter)(tr(key))
                except Exception:
                    pass
            key = w.property("i18nItemText")
            if key:
                try:
                    w.setItemText(w.currentIndex(), tr(key))
                except Exception:
                    pass
        # 标签页标题（QTabBar 不是 QWidget，需单独刷新）
        for idx, key in getattr(self, "_tab_text_keys", []):
            try:
                self.tabs.setTabText(idx, tr(key))
            except Exception:
                pass
        # 表格表头（表头项不是普通控件，需单独刷新）
        sig_tab = getattr(self, "sig_tab", None)
        if sig_tab is not None and hasattr(sig_tab, "apply_language"):
            try:
                sig_tab.apply_language()
            except Exception:
                pass
        # 依赖状态行
        dep_lbl = getattr(self, "dep_lbl", None)
        if dep_lbl is not None:
            try:
                dep_lbl.setText(self._dep_status_text())
            except Exception:
                pass
        # 状态栏与分组标题等非 QWidget 文本
        try:
            self.statusBar().showMessage(
                tr("Ready – select the A2L and the address file, then click \"⚡ Update A2L\""))
        except Exception:
            pass

    def _load_paths(self):
        """从注册表/ini 恢复上次的文件路径记录。"""
        s = self._settings
        a2l  = s.value("last_a2l",  "", type=str)
        addr = s.value("last_addr", "", type=str)
        out  = s.value("last_out",  "", type=str)
        line = s.value("last_line", "", type=str)
        cal_ranges_text = s.value("last_cal_ranges", "", type=str)
        meas_ranges_text = s.value("last_meas_ranges", "", type=str)
        signal_config_path = s.value("last_signal_config_xlsx", "", type=str)
        signal_config_history_json = s.value("signal_config_xlsx_history", "", type=str)
        signal_config_history = []
        if signal_config_history_json:
            try:
                parsed = json.loads(signal_config_history_json)
                if isinstance(parsed, list):
                    signal_config_history = [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                signal_config_history = []
        self._signal_config_history = _normalize_path_history(signal_config_history, signal_config_path)
        self._signal_config_path = str(signal_config_path or "").strip()
        checked_json = s.value("last_checked_signals", "", type=str)
        preview_json = s.value("last_preview_signal_keys", "", type=str)
        # 报告开关：固定为「默认不生成」——不持久化勾选状态，每次启动一律未勾选，
        # 避免上次勾选过导致本次无意间又生成报告。「上一版 A2L」路径仍记忆，
        # 便于下次直接勾选使用。
        prev_a2l = s.value("last_prev_a2l", "", type=str)
        if prev_a2l:
            self.edit_prev_a2l.setText(prev_a2l)
        self.chk_match_report.setChecked(False)
        self.chk_diff_report.setChecked(False)
        self._sync_report_controls(False)
        s.remove("opt_match_report")
        s.remove("opt_diff_report")
        if a2l  and os.path.isfile(a2l):
            self.pick_a2l.set_path(a2l)
        if addr and os.path.isfile(addr):
            self.pick_addr.set_path(addr)
        if out:
            self.edit_out.setText(out)
        if line:
            self.edit_line.setText(line)
        if cal_ranges_text:
            self.edit_cal_ranges.setPlainText(cal_ranges_text)
        if meas_ranges_text:
            self.edit_meas_ranges.setPlainText(meas_ranges_text)
        self.pick_signal_config.set_history(self._signal_config_history, self._signal_config_path)
        if checked_json:
            try:
                parsed = json.loads(checked_json)
                if isinstance(parsed, list):
                    self._saved_checked_signal_names = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                self._saved_checked_signal_names = []
        if preview_json:
            try:
                parsed = json.loads(preview_json)
                if isinstance(parsed, list):
                    self._saved_preview_signal_names = [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                self._saved_preview_signal_names = []

    def _save_paths(self):
        """将当前文件路径持久化到注册表/ini。"""
        s = self._settings
        s.setValue("last_a2l",  self.pick_a2l.path())
        s.setValue("last_addr", self.pick_addr.path())
        s.setValue("last_out",  self.edit_out.text().strip())
        s.setValue("last_line", self.edit_line.text().strip())
        s.setValue("last_cal_ranges", self.edit_cal_ranges.toPlainText().strip())
        s.setValue("last_meas_ranges", self.edit_meas_ranges.toPlainText().strip())
        current_signal_config_path = self._current_signal_config_path()
        self._remember_signal_config_path(current_signal_config_path)
        s.setValue("last_signal_config_xlsx", current_signal_config_path)
        s.setValue("signal_config_xlsx_history", json.dumps(self._signal_config_history, ensure_ascii=False))
        s.setValue("last_checked_signals", json.dumps(self.sig_tab.get_checked_signal_names(), ensure_ascii=False))
        s.setValue("last_preview_signal_keys", json.dumps([_signal_key(sig) for sig in self.sig_tab.get_all_signals()], ensure_ascii=False))
        s.setValue("last_prev_a2l", self.edit_prev_a2l.text().strip())

    def closeEvent(self, event):
        """窗口关闭时自动保存路径记录。"""
        self._save_paths()
        super().closeEvent(event)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          生成 Demo xlsx（含 C 全数据类型，标定量/观测量分开，多 Group）        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def _generate_demo_xlsx(save_path: str):
    """
    生成带样式的 signal_demo.xlsx。
    按 Sheet 分组：
      - Calibration：标定量基础信号（整数/浮点/布尔/枚举）
      - Measurement：观测量基础信号
      - Calibration Arrays：标定量数组元素
      - Measurement Arrays：观测量数组元素
      - Calibration Structs：标定量结构体成员
      - Measurement Structs：观测量结构体成员
    这样可以沿用同一套列定义，方便各自按 Sheet 填写信号数据。
    """
    from collections import OrderedDict
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    wb = Workbook()

    thin  = Side(style="thin",   color="AAAAAA")
    thick = Side(style="medium", color="6060A0")
    bdr   = Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr   = Alignment(horizontal="center", vertical="center", wrap_text=True)
    lft   = Alignment(horizontal="left",   vertical="center", wrap_text=True)

    hdr_fill = PatternFill("solid", fgColor="2E4057")   # 标题行：深蓝灰
    cal_fill = PatternFill("solid", fgColor="1F497D")   # 标定量：蓝
    mea_fill = PatternFill("solid", fgColor="375623")   # 观测量：绿
    grp_fill = PatternFill("solid", fgColor="7030A0")   # Group：紫
    sub_fill = PatternFill("solid", fgColor="3D2457")   # 子标题：深紫

    col_widths = [12, 6, 28, 26, 40, 8, 8, 10, 8, 8, 16, 13, 24, 10, 10, 10, 10, 50]

    # ── 信号数据（每个 Group 先 CAL 后 MEAS）──────────────────────────────────
    ALL_SIGNALS = {
        # ──────────────────────────── Group: Engine ────────────────────────────
        "Engine": {
            "CAL": [
                ["Engine","CAL","Eng_IdleRpm_u8_C",   "IdleRpm_C",    "Idle Speed Setpoint (uint8_t)",
                 "rpm",  50,  250,  1.0,  0.0, "uint8_t",  "CALIBRATION","",      "uint8_t: idle speed setpoint (50~250 rpm, step 1)"],
                ["Engine","CAL","Eng_TempOfs_i8_C",   "TempOfs_C",    "Temperature Sensor Offset (int8_t)",
                 "degC",-40,   40,  1.0,  0.0, "int8_t",   "CALIBRATION","",      "int8_t: temperature sensor offset correction (-40~+40)"],
                ["Engine","CAL","Eng_MaxRpm_u16_C",   "MaxRpm_C",     "Maximum RPM Limit (uint16_t)",
                 "rpm", 0,  8000,  1.0,  0.0, "uint16_t", "CALIBRATION","",      "uint16_t: maximum engine speed limit"],
                ["Engine","CAL","Eng_TqOfs_i16_C",    "TqOfs_C",      "Torque Output Offset (int16_t)",
                 "Nm", -200,  200,  0.1,  0.0, "int16_t",  "CALIBRATION","",      "int16_t: torque compensation offset"],
                ["Engine","CAL","Eng_CycUs_u32_C",    "CycUs_C",      "Control Cycle Period (uint32_t)",
                 "us",  100, 100000, 1.0, 0.0, "uint32_t", "CALIBRATION","",      "uint32_t: base control task cycle in microseconds"],
                ["Engine","CAL","Eng_PwrOfs_i32_C",   "PwrOfs_C",     "Power Output Offset (int32_t)",
                 "W",-100000,100000,1.0, 0.0, "int32_t",  "CALIBRATION","",      "int32_t: power offset for calibration"],
                ["Engine","CAL","Eng_FuelGain_f32_C", "FuelGain_C",   "Fuel Injection Gain (float)",
                 "",    0.5,  2.0,  0.001,0.0, "float",    "CALIBRATION","",      "float: fuel injection gain factor (0.5~2.0)"],
                ["Engine","CAL","Eng_EmisFact_f64_C", "EmisFact_C",   "Emission Calibration Factor (double)",
                 "",    0.0,  1.0,  1e-9, 0.0, "double",   "CALIBRATION","",      "double: high-precision emission factor"],
                ["Engine","CAL","Eng_EnEcoMode_b_C",  "EnEcoMode_C",  "Economy Mode Enable (bool)",
                 "",    0,    1,    1.0,  0.0, "bool",     "CALIBRATION","0x01",  "bool: enable eco driving mode"],
                ["Engine","CAL","Eng_CalID_u64_C",    "CalID_C",      "Calibration Dataset ID (uint64_t)",
                 "",    0, 1.8e19, 1.0,  0.0, "uint64_t", "CALIBRATION","",      "uint64_t: unique calibration dataset identifier"],
                ["Engine","CAL","Eng_TimeOfs_i64_C",  "TimeOfs_C",    "Timestamp Offset (int64_t)",
                 "ns",-2e18, 2e18,  1.0, 0.0, "int64_t",  "CALIBRATION","",      "int64_t: RTC timestamp correction offset in nanoseconds"],
            ],
            "MEAS": [
                ["Engine","MEAS","Eng_Speed_u16_M",    "EngSpd_M",     "Engine Speed (uint16_t)",
                 "rpm",  0,  8000,  0.25, 0.0, "uint16_t", "MEASUREMENT","",      "uint16_t: measured engine speed"],
                ["Engine","MEAS","Eng_Torque_f32_M",   "EngTq_M",      "Engine Torque (float)",
                 "Nm", -100, 600,   0.1,  0.0, "float",    "MEASUREMENT","",      "float: actual torque output"],
                ["Engine","MEAS","Eng_CoolantT_i8_M",  "CoolantT_M",   "Coolant Temperature (int8_t)",
                 "degC",-40,  127,  1.0,  0.0, "int8_t",   "MEASUREMENT","",      "int8_t: coolant temperature (-40~127 degC)"],
                ["Engine","MEAS","Eng_RunFlag_b_M",    "RunFlag_M",    "Engine Running (bool)",
                 "",    0,    1,    1.0,  0.0, "bool",     "MEASUREMENT","0x01",  "bool: engine running status"],
                ["Engine","MEAS","Eng_ErrCode_u32_M",  "ErrCode_M",    "Error Code (uint32_t)",
                 "",    0,  4294967295,1.0,0.0,"uint32_t", "MEASUREMENT","",      "uint32_t: combined diagnostic error code"],
                ["Engine","MEAS","Eng_LoadRatio_f64_M","LoadRatio_M",  "Engine Load Ratio (double)",
                 "%",   0.0, 100,  1e-9,  0.0,"double",   "MEASUREMENT","",      "double: high-precision load ratio (0~100%)"],
                ["Engine","MEAS","Eng_RunTime_u64_M",  "RunTime_M",    "Total Run Time (uint64_t)",
                 "ms",  0,  1.8e19,1.0,  0.0, "uint64_t", "MEASUREMENT","",      "uint64_t: accumulated engine run time in ms"],
            ],
        },
        # ──────────────────────────── Group: Battery ───────────────────────────
        "Battery": {
            "CAL": [
                ["Battery","CAL","Bat_OVP_f32_C",      "OVP_C",        "OVP Threshold (float)",
                 "V",   3.0,  4.5,  0.001,0.0, "float",    "CALIBRATION","",      "float: per-cell over-voltage threshold"],
                ["Battery","CAL","Bat_OCP_u16_C",      "OCP_C",        "OCP Current Limit (uint16_t)",
                 "A",   0,   500,   0.1,  0.0, "uint16_t", "CALIBRATION","",      "uint16_t: over-current protection threshold"],
                ["Battery","CAL","Bat_OTP_i8_C",       "OTP_C",        "Over-Temp Threshold (int8_t)",
                 "degC",50,  85,   1.0,  0.0, "int8_t",   "CALIBRATION","",      "int8_t: pack over-temperature threshold"],
                ["Battery","CAL","Bat_BalEn_u16_C",    "BalEn_C",      "Balance Enable Flags (uint16_t)",
                 "",   0,  65535,  1.0,  0.0, "uint16_t", "CALIBRATION","0xFFFF","uint16_t: per-cell balance enable bitmask"],
                ["Battery","CAL","Bat_ChgGain_f64_C",  "ChgGain_C",    "Charge Gain Factor (double)",
                 "",   0.0,  2.0,  1e-9, 0.0, "double",   "CALIBRATION","",      "double: charge algorithm gain (high precision)"],
                ["Battery","CAL","Bat_CfgWord_u32_C",  "CfgWord_C",    "BMS Config Word (uint32_t)",
                 "",   0,  4294967295,1.0,0.0, "uint32_t", "CALIBRATION","0xFFFFFFFF","uint32_t: BMS configuration bitfield"],
                ["Battery","CAL","Bat_HeaterEn_b_C",   "HeaterEn_C",   "Battery Heater Enable (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "CALIBRATION","0x01",  "bool: enable pre-heat below 0 degC"],
            ],
            "MEAS": [
                ["Battery","MEAS","Bat_Volt_f32_M",    "BatVolt_M",    "Pack Voltage (float)",
                 "V",  200,  500,  0.01, 0.0, "float",    "MEASUREMENT","",      "float: total pack voltage"],
                ["Battery","MEAS","Bat_Curr_i16_M",    "BatCurr_M",    "Pack Current (int16_t)",
                 "A",  -500, 500,  0.1,  0.0, "int16_t",  "MEASUREMENT","",      "int16_t: charge(+)/discharge(-) current"],
                ["Battery","MEAS","Bat_SOC_u8_M",      "BatSOC_M",     "State of Charge (uint8_t)",
                 "%",   0,   100,  1.0,  0.0, "uint8_t",  "MEASUREMENT","",      "uint8_t: SOC 0~100%"],
                ["Battery","MEAS","Bat_Temp_i8_M",     "BatTemp_M",    "Cell Max Temperature (int8_t)",
                 "degC",-40, 85,   1.0,  0.0, "int8_t",   "MEASUREMENT","",      "int8_t: maximum cell temperature"],
                ["Battery","MEAS","Bat_InsulRes_u32_M","InsulRes_M",   "Insulation Resistance (uint32_t)",
                 "kOhm",0,100000, 1.0,  0.0, "uint32_t", "MEASUREMENT","",      "uint32_t: pack insulation resistance"],
                ["Battery","MEAS","Bat_CycCnt_u64_M",  "CycCnt_M",     "Charge Cycle Count (uint64_t)",
                 "",   0,  1e18,  1.0,  0.0, "uint64_t", "MEASUREMENT","",      "uint64_t: cumulative charge cycle counter"],
                ["Battery","MEAS","Bat_PrecSOH_f64_M", "PrecSOH_M",    "Precise SOH (double)",
                 "%",   0.0, 100,  1e-9, 0.0, "double",   "MEASUREMENT","",      "double: high-precision state-of-health"],
                ["Battery","MEAS","Bat_FaultBit_b_M",  "FaultBit_M",   "Fault Active Flag (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "MEASUREMENT","0x01",  "bool: any active fault"],
                ["Battery","MEAS","Bat_EngyWh_i32_M",  "EngyWh_M",     "Net Energy (int32_t)",
                 "Wh",-2147483648,2147483647,1.0,0.0,"int32_t","MEASUREMENT","", "int32_t: net energy in/out Wh"],
            ],
        },
        # ──────────────────────────── Group: Transmission ──────────────────────
        "Trans": {
            "CAL": [
                ["Trans","CAL","Trn_ShiftRpm_u16_C",  "ShiftRpm_C",   "Gear Shift Point (uint16_t)",
                 "rpm",1000,7000,  1.0,  0.0, "uint16_t", "CALIBRATION","",      "uint16_t: automatic upshift RPM point"],
                ["Trans","CAL","Trn_ClutGain_f32_C",  "ClutGain_C",   "Clutch Gain (float)",
                 "",   0.0,  2.0,  0.001,0.0, "float",    "CALIBRATION","",      "float: clutch engagement gain"],
                ["Trans","CAL","Trn_OilLmt_u16_C",    "OilLmt_C",     "Oil Pressure Limit (uint16_t)",
                 "kPa",50,   800,  0.1,  0.0, "uint16_t", "CALIBRATION","",      "uint16_t: max oil pressure limit"],
                ["Trans","CAL","Trn_Mode_u8_C",       "TrnMode_C",    "Transmission Mode (uint8_t)",
                 "",   0,    3,    1.0,  0.0, "uint8_t",  "CALIBRATION","0x03",  "uint8_t: 0=N 1=D 2=S 3=M"],
                ["Trans","CAL","Trn_LockEn_b_C",      "LockEn_C",     "Torque Converter Lock Enable (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "CALIBRATION","0x01",  "bool: enable torque converter lock-up"],
            ],
            "MEAS": [
                ["Trans","MEAS","Trn_Gear_u8_M",      "TrnGear_M",    "Current Gear (uint8_t)",
                 "",   0,    8,    1.0,  0.0, "uint8_t",  "MEASUREMENT","",      "uint8_t: current gear (0=N,1~8)"],
                ["Trans","MEAS","Trn_InShaft_u16_M",  "InShaft_M",    "Input Shaft Speed (uint16_t)",
                 "rpm",0,  8000,  0.25, 0.0, "uint16_t", "MEASUREMENT","",      "uint16_t: input shaft speed"],
                ["Trans","MEAS","Trn_OilTemp_i16_M",  "OilTemp_M",    "Oil Temperature (int16_t)",
                 "degC",-40,200,  0.1,-40.0, "int16_t",  "MEASUREMENT","",      "int16_t: transmission oil temperature"],
                ["Trans","MEAS","Trn_TqRatio_f32_M",  "TqRatio_M",    "Torque Ratio (float)",
                 "",   0.0,  1.0,  0.001,0.0, "float",    "MEASUREMENT","",      "float: torque converter speed ratio"],
                ["Trans","MEAS","Trn_SlipCnt_u32_M",  "SlipCnt_M",    "Slip Event Counter (uint32_t)",
                 "",   0,  4294967295,1.0,0.0,"uint32_t", "MEASUREMENT","",      "uint32_t: clutch slip event counter"],
            ],
        },
        # ──────────────────────────── Group: HVAC ──────────────────────────────
        "HVAC": {
            "CAL": [
                ["HVAC","CAL","HVAC_TgtTemp_u8_C",    "TgtTemp_C",    "Target Cabin Temp (uint8_t)",
                 "degC",16,   32,   0.5,  0.0, "uint8_t",  "CALIBRATION","",      "uint8_t: target cabin temperature 16~32 degC"],
                ["HVAC","CAL","HVAC_CompGain_f32_C",  "CompGain_C",   "Compressor PID Gain (float)",
                 "",   0.0,  5.0,  0.001,0.0, "float",    "CALIBRATION","",      "float: A/C compressor PID gain"],
                ["HVAC","CAL","HVAC_BlwLvl_u8_C",     "BlwLvl_C",     "Blower Level (uint8_t)",
                 "",   0,    7,    1.0,  0.0, "uint8_t",  "CALIBRATION","0x07",  "uint8_t bit[2:0]: blower level 0~7"],
                ["HVAC","CAL","HVAC_AutoEn_b_C",      "AutoEn_C",     "Auto Climate Enable (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "CALIBRATION","0x01",  "bool: enable automatic climate control"],
            ],
            "MEAS": [
                ["HVAC","MEAS","HVAC_CabinT_i16_M",   "CabinT_M",     "Cabin Temperature (int16_t)",
                 "degC",-10,  60,   0.1,  0.0, "int16_t",  "MEASUREMENT","",      "int16_t: measured cabin temperature"],
                ["HVAC","MEAS","HVAC_Duty_u8_M",      "CompDuty_M",   "Compressor Duty Cycle (uint8_t)",
                 "%",   0,   100,   1.0,  0.0, "uint8_t",  "MEASUREMENT","",      "uint8_t: A/C compressor duty 0~100%"],
                ["HVAC","MEAS","HVAC_EvapT_i8_M",     "EvapT_M",      "Evaporator Temperature (int8_t)",
                 "degC",-20,  30,   0.1,-20.0, "int8_t",   "MEASUREMENT","",      "int8_t: evaporator coil temperature"],
                ["HVAC","MEAS","HVAC_Status_u16_M",   "HVACStatus_M", "HVAC Status Word (uint16_t)",
                 "",   0,  65535,  1.0,  0.0, "uint16_t", "MEASUREMENT","0xFFFF","uint16_t: HVAC status bit-word"],
                ["HVAC","MEAS","HVAC_Energy_f64_M",   "HVACEnergy_M", "HVAC Energy Usage (double)",
                 "kWh", 0.0,  1e6,  1e-6, 0.0, "double",   "MEASUREMENT","",      "double: accumulated HVAC energy consumption"],
            ],
        },
        # ──────────────────────────── Group: VehicleCtrl ───────────────────────
        "VehCtrl": {
            "CAL": [
                ["VehCtrl","CAL","Veh_MaxSpd_u8_C",   "MaxSpd_C",     "Vehicle Speed Limit (uint8_t)",
                 "km/h",0,  250,   1.0,  0.0, "uint8_t",  "CALIBRATION","",      "uint8_t: maximum allowed vehicle speed"],
                ["VehCtrl","CAL","Veh_ABSGain_f32_C", "ABSGain_C",    "ABS Control Gain (float)",
                 "",   0.0,  3.0,  0.001,0.0, "float",    "CALIBRATION","",      "float: ABS slip control gain"],
                ["VehCtrl","CAL","Veh_SteerOfs_i16_C","SteerOfs_C",   "Steering Angle Offset (int16_t)",
                 "deg",-20,  20,   0.01, 0.0, "int16_t",  "CALIBRATION","",      "int16_t: steering wheel zero-offset correction"],
                ["VehCtrl","CAL","Veh_OdoCal_u32_C",  "OdoCal_C",     "Odometer Calibration (uint32_t)",
                 "pulse/km",100,50000,1.0, 0.0,"uint32_t", "CALIBRATION","",      "uint32_t: pulses-per-km odometer calibration"],
                ["VehCtrl","CAL","Veh_ESPEn_b_C",     "ESPEn_C",      "ESP Enable (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "CALIBRATION","0x01",  "bool: enable electronic stability program"],
                ["VehCtrl","CAL","Veh_PrecGain_f64_C","PrecGain_C",   "Precision Control Gain (double)",
                 "",   0.0,  1.0,  1e-12,0.0, "double",   "CALIBRATION","",      "double: ultra-precision lateral control gain"],
            ],
            "MEAS": [
                ["VehCtrl","MEAS","Veh_Speed_u16_M",  "VehSpd_M",     "Vehicle Speed (uint16_t)",
                 "km/h",0,  300,   0.01, 0.0, "uint16_t", "MEASUREMENT","",      "uint16_t: actual vehicle speed"],
                ["VehCtrl","MEAS","Veh_AccelX_i16_M", "AccelX_M",     "Longitudinal Acceleration (int16_t)",
                 "m/s2",-200,200,  0.01, 0.0, "int16_t",  "MEASUREMENT","",      "int16_t: longitudinal acceleration"],
                ["VehCtrl","MEAS","Veh_AccelY_f32_M", "AccelY_M",     "Lateral Acceleration (float)",
                 "m/s2",-50,  50,  0.001,0.0, "float",    "MEASUREMENT","",      "float: lateral acceleration"],
                ["VehCtrl","MEAS","Veh_Odo_u32_M",    "Odo_M",        "Odometer (uint32_t)",
                 "km",  0,  4294967295,1.0,0.0,"uint32_t", "MEASUREMENT","",      "uint32_t: total odometer reading in km"],
                ["VehCtrl","MEAS","Veh_YawRate_f64_M","YawRate_M",    "Yaw Rate (double)",
                 "rad/s",-10,10,   1e-9, 0.0, "double",   "MEASUREMENT","",      "double: high-precision yaw rate"],
                ["VehCtrl","MEAS","Veh_DrvMode_u8_M", "DrvMode_M",    "Drive Mode Status (uint8_t)",
                 "",   0,    7,    1.0,  0.0, "uint8_t",  "MEASUREMENT","0x07",  "uint8_t: 0=Eco 1=Normal 2=Sport 3=Off-road"],
                ["VehCtrl","MEAS","Veh_FaultWord_u64_M","FaultWord_M","Fault Status Word (uint64_t)",
                 "",   0,  1.8e19, 1.0,  0.0, "uint64_t", "MEASUREMENT","",      "uint64_t: 64-bit fault status bitmask"],
                ["VehCtrl","MEAS","Veh_ABSAct_b_M",   "ABSAct_M",     "ABS Active (bool)",
                 "",   0,    1,    1.0,  0.0, "bool",     "MEASUREMENT","0x01",  "bool: ABS intervention active flag"],
                ["VehCtrl","MEAS","Veh_DistToE_i32_M","DistToE_M",    "Distance to Empty (int32_t)",
                 "km",-1,   1500,  1.0,  0.0, "int32_t",  "MEASUREMENT","",      "int32_t: estimated range remaining (-1=unavail)"],
            ],
        },
        # ──────────────── Group: Advanced（枚举 / 数组 / 结构体）────────────────
        "Advanced": {
            "CAL": [
                ["Advanced","CAL","Adv_DrvModeSel_e_C",  "DrvModeSel_C", "Drive Mode Selection (enum_u8)",
                 "",   0,    4,    1.0,  0.0, "enum_u8",  "CALIBRATION","0=\"A\" 1=\"B\" 2=\"C\" 3=\"D\" 4=\"E\"", "", "enum_u8: Drive mode selection"],
                ["Advanced","CAL","Adv_FaultLvl_e_C",    "FaultLvl_C",   "Fault Level Threshold (enum_u16)",
                 "",   0,    3,    1.0,  0.0, "enum_u16", "CALIBRATION","", "enum_u16: Min fault level to trigger shutdown"],
                ["Advanced","CAL","Adv_CtrlState_e_C",   "CtrlState_C",  "Controller State Machine (enum_u32)",
                 "",   0,    7,    1.0,  0.0, "enum_u32", "CALIBRATION","", "enum_u32: Controller state enum"],
                ["Advanced","CAL","Adv_PidKp_Arr0_C",    "PidKp0_C",     "PID Kp Array[0] (array_f32)",
                 "",   0.0,  10.0, 0.001,0.0, "array_f32","CALIBRATION","", "array_f32: PID Kp proportional gain [0]"],
                ["Advanced","CAL","Adv_PidKp_Arr1_C",    "PidKp1_C",     "PID Kp Array[1] (array_f32)",
                 "",   0.0,  10.0, 0.001,0.0, "array_f32","CALIBRATION","", "array_f32: PID Kp proportional gain [1]"],
                ["Advanced","CAL","Adv_LutIn_Arr0_C",    "LutIn0_C",     "Lookup Table Input[0] (array_u16)",
                 "rpm",0,   8000,  1.0,  0.0, "array_u16","CALIBRATION","", "array_u16: LUT input breakpoint [0]"],
                ["Advanced","CAL","Adv_LutOut_Arr0_C",   "LutOut0_C",    "Lookup Table Output[0] (array_i16)",
                 "Nm",-500,  500,  0.1,  0.0, "array_i16","CALIBRATION","", "array_i16: LUT output value [0]"],
                ["Advanced","CAL","Adv_PidCfg_Kp_C",     "PidCfg_Kp_C",  "PID Config struct.Kp (struct_f32)",
                 "",   0.0,  20.0, 0.001,0.0, "struct_f32","CALIBRATION","", "struct_f32: PID Kp member"],
                ["Advanced","CAL","Adv_PidCfg_Ki_C",     "PidCfg_Ki_C",  "PID Config struct.Ki (struct_f32)",
                 "",   0.0,   5.0,  0.001,0.0, "struct_f32","CALIBRATION","", "struct_f32: PID Ki member"],
                ["Advanced","CAL","Adv_PidCfg_Kd_C",     "PidCfg_Kd_C",  "PID Config struct.Kd (struct_f32)",
                 "",   0.0,   2.0,  0.001,0.0, "struct_f32","CALIBRATION","", "struct_f32: PID Kd member"],
                ["Advanced","CAL","Adv_PidCfg_SatMax_C", "PidCfg_Sat_C", "PID Config struct.SatMax (struct_u16)",
                 "",   0,   1000,  1.0,  0.0, "struct_u16","CALIBRATION","", "struct_u16: PID saturation limit"],
                ["Advanced","CAL","Adv_PackedCtrl_C",     "Adv_PackedCtrl_C.enable", "Packed Control enable bitfield",
                 "",   0,    1,    1.0,  0.0, "struct_u8", "CALIBRATION", "", "1", "0", "0", "1", tr("bitfield example: bit0 in the same byte, boolean switch")],
                ["Advanced","CAL","Adv_PackedCtrl_C",     "Adv_PackedCtrl_C.mode",   "Packed Control mode enum bitfield",
                 "",   0,    7,    1.0,  0.0, "enum_u8",   "CALIBRATION", "0=\"OFF\" 1=\"IDLE\" 2=\"RUN\" 3=\"SAFE\" 4=\"LIMP\"", "1", "0", "1", "3", tr("bitfield enum example: bits[3:1] in the same byte, the enum values are written in BitMask")],
            ],
            "MEAS": [
                ["Advanced","MEAS","Adv_SysState_e_M",   "SysState_M",   "System State (enum_u8)",
                 "",   0,    5,    1.0,  0.0, "enum_u8",  "MEASUREMENT","0x07", "enum_u8: System state machine value"],
                ["Advanced","MEAS","Adv_WarnCode_e_M",   "WarnCode_M",   "Warning Code (enum_u16)",
                 "",   0,  255,    1.0,  0.0, "enum_u16", "MEASUREMENT","", "enum_u16: Warning code enum"],
                ["Advanced","MEAS","Adv_CellV_Arr0_M",   "CellV0_M",     "Cell Voltage Array[0] (array_u16)",
                 "mV", 2500, 4200,  1.0,  0.0, "array_u16","MEASUREMENT","", "array_u16: Cell voltage [0]"],
                ["Advanced","MEAS","Adv_CellV_Arr1_M",   "CellV1_M",     "Cell Voltage Array[1] (array_u16)",
                 "mV", 2500, 4200,  1.0,  0.0, "array_u16","MEASUREMENT","", "array_u16: Cell voltage [1]"],
                ["Advanced","MEAS","Adv_PhCurr_Arr0_M",  "PhCurr0_M",    "Phase Current Array[0] (array_i16)",
                 "A",  -500,  500,  0.1,  0.0, "array_i16","MEASUREMENT","", "array_i16: Phase current [0]"],
                ["Advanced","MEAS","Adv_TempArr_Arr0_M", "TempArr0_M",   "Temp Sensor Array[0] (array_i8)",
                 "degC",-40, 125,   1.0,  0.0, "array_i8", "MEASUREMENT","", "array_i8: Temp sensor [0]"],
                ["Advanced","MEAS","Adv_AiRaw_Arr0_M",   "AiRaw0_M",     "ADC Raw Array[0] (array_u32)",
                 "",   0, 4294967295,1.0, 0.0, "array_u32","MEASUREMENT","", "array_u32: ADC raw [0]"],
                ["Advanced","MEAS","Adv_MotSt_Speed_M",  "MotSpeed_M",   "Motor Status struct.Speed (struct_u16)",
                 "rpm",  0, 15000,  0.1,  0.0, "struct_u16","MEASUREMENT","", "struct_u16: Motor speed member"],
                ["Advanced","MEAS","Adv_MotSt_Torque_M", "MotTorque_M",  "Motor Status struct.Torque (struct_u16)",
                 "Nm",   0,  500,   0.1,  0.0, "struct_u16","MEASUREMENT","", "struct_u16: Motor torque member"],
                ["Advanced","MEAS","Adv_MotSt_Temp_M",   "MotTemp_M",    "Motor Status struct.Temp (struct_i16)",
                 "degC",-40,  180,   0.1,-40.0,"struct_i16","MEASUREMENT","", "struct_i16: Motor temp member"],
                ["Advanced","MEAS","Adv_MotSt_Flags_M",  "MotFlags_M",   "Motor Status struct.Flags (struct_u32)",
                 "",   0,  4294967295,1.0,0.0, "struct_u32","MEASUREMENT","", "struct_u32: Motor flags member"],
                ["Advanced","MEAS","Adv_NavPos_Lat_M",   "NavLat_M",     "Navigation struct.Latitude (struct_f64)",
                 "deg", -90,   90,  1e-9, 0.0, "struct_f64","MEASUREMENT","", "struct_f64: Navigation latitude"],
                ["Advanced","MEAS","Adv_NavPos_Lon_M",   "NavLon_M",     "Navigation struct.Longitude (struct_f64)",
                 "deg",-180,  180,  1e-9, 0.0, "struct_f64","MEASUREMENT","", "struct_f64: Navigation longitude"],
                ["Advanced","MEAS","Adv_NavPos_Alt_M",   "NavAlt_M",     "Navigation struct.Altitude (struct_f32)",
                 "m",  -500, 9000,  0.1,  0.0, "struct_f32","MEASUREMENT","", "struct_f32: Navigation altitude"],
                ["Advanced","MEAS","Adv_PackedStat_M",    "Adv_PackedStat_M.warnLevel", "Packed Status warnLevel enum bitfield",
                 "",   0,    7,    1.0,  0.0, "enum_u8",   "MEASUREMENT", "0=\"NONE\" 1=\"LOW\" 2=\"MID\" 3=\"HIGH\"", "1", "1", "0", "3", tr("bitfield enum example: bits[2:0] of the first byte")],
                ["Advanced","MEAS","Adv_PackedStat_M",    "Adv_PackedStat_M.faultLatched", "Packed Status fault latched bit",
                 "",   0,    1,    1.0,  0.0, "struct_u8", "MEASUREMENT", "", "1", "1", "7", "1", tr("bitfield example: bit7 of the first byte, latched fault flag")],
            ],
        },
    }

    def create_header(ws):
        for ci, h in enumerate(XLSX_COLS, 1):
            w = col_widths[ci - 1] if ci - 1 < len(col_widths) else 14
            c = ws.cell(1, ci, h)
            c.font      = Font(bold=True, color="FFFFFF", size=10, name="Calibri")
            c.fill      = hdr_fill
            c.alignment = ctr
            c.border    = bdr
            ws.column_dimensions[get_column_letter(ci)].width = w
        ws.row_dimensions[1].height = 22
        ws.freeze_panes = "A2"

    def is_array_dtype(dtype: str) -> bool:
        return dtype.startswith("array_")

    def is_member_alias_row(row) -> bool:
        name = str(row[2]).strip() if len(row) > 2 else ""
        short = str(row[3]).strip() if len(row) > 3 else ""
        return bool(name and short and short.startswith(name + "."))

    def is_struct_dtype(dtype: str, row=None) -> bool:
        return dtype.startswith("struct_") or (row is not None and is_member_alias_row(row))

    sheet_specs = [
        ("Calibration",         lambda section, dtype, row: section == "CAL" and not is_array_dtype(dtype) and not is_struct_dtype(dtype, row)),
        ("Measurement",         lambda section, dtype, row: section == "MEAS" and not is_array_dtype(dtype) and not is_struct_dtype(dtype, row)),
        ("Calibration Arrays",  lambda section, dtype, row: section == "CAL" and is_array_dtype(dtype)),
        ("Measurement Arrays",  lambda section, dtype, row: section == "MEAS" and is_array_dtype(dtype)),
        ("Calibration Structs", lambda section, dtype, row: section == "CAL" and is_struct_dtype(dtype, row)),
        ("Measurement Structs", lambda section, dtype, row: section == "MEAS" and is_struct_dtype(dtype, row)),
    ]

    sheet_groups = {name: OrderedDict() for name, _ in sheet_specs}
    group_order = ["Engine", "Battery", "Trans", "HVAC", "VehCtrl", "Advanced"]
    for grp_name in group_order:
        grp_data = ALL_SIGNALS[grp_name]
        for section in ("CAL", "MEAS"):
            for row in grp_data[section]:
                dtype = str(row[10]).strip() if len(row) > 10 else ""
                for name, predicate in sheet_specs:
                    if predicate(section, dtype, row):
                        sheet_groups[name].setdefault(grp_name, []).append(row)
                        break

    sheet_names = [name for name, _ in sheet_specs]
    ws_objs = {}
    for idx, sheet_name in enumerate(sheet_names):
        if idx == 0:
            ws = wb.active
            ws.title = sheet_name
        else:
            ws = wb.create_sheet(sheet_name)
        create_header(ws)
        ws_objs[sheet_name] = ws

    data_rows_by_sheet = {name: [] for name in sheet_names}
    for sheet_name in sheet_names:
        ws = ws_objs[sheet_name]
        groups = sheet_groups[sheet_name]
        ri = 2
        fill = cal_fill if sheet_name.startswith("Calibration") else mea_fill

        for grp_name, rows in groups.items():
            if not rows:
                continue

            ws.merge_cells(start_row=ri, start_column=1,
                           end_row=ri, end_column=len(XLSX_COLS))
            gc = ws.cell(ri, 1, f"  ▶  Group: {grp_name}")
            gc.font      = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
            gc.fill      = grp_fill
            gc.alignment = lft
            ws.row_dimensions[ri].height = 18
            ri += 1

            for row in rows:
                row_values = list(row)
                while len(row_values) < len(XLSX_COLS):
                    row_values.insert(XLSX_COLS.index("Description"), "")
                for ci, val in enumerate(row_values, 1):
                    c = ws.cell(ri, ci, val)
                    c.fill      = fill
                    c.font      = Font(color="FFFFFF", size=9, name="Calibri")
                    c.alignment = ctr
                    c.border    = bdr
                ws.row_dimensions[ri].height = 20
                data_rows_by_sheet[sheet_name].append(ri)
                ri += 1

            ri += 1

    dtype_col_idx    = XLSX_COLS.index("DataType") + 1
    dtype_col_letter = get_column_letter(dtype_col_idx)

    ws_opts = wb.create_sheet("_DTypeOptions")
    ws_opts.sheet_state = "hidden"
    for i, opt in enumerate(DTYPE_DROPDOWN_OPTIONS, 1):
        ws_opts.cell(i, 1, opt)
    n_opts = len(DTYPE_DROPDOWN_OPTIONS)
    list_ref = f"_DTypeOptions!$A$1:$A${n_opts}"

    for sheet_name, rows in data_rows_by_sheet.items():
        if not rows:
            continue
        dv = DataValidation(
            type="list",
            formula1=list_ref,
            allow_blank=True,
            showDropDown=False,
            showErrorMessage=True,
            errorTitle=tr("Invalid data type"),
            error=tr("Please choose a valid C data type from the drop-down list"),
            showInputMessage=True,
            promptTitle=tr("Select a C data type"),
            prompt=tr("Please choose the matching C data type from the drop-down list"),
        )
        dv.sqref = f"{dtype_col_letter}2:{dtype_col_letter}{rows[-1]}"
        ws_objs[sheet_name].add_data_validation(dv)

    wb.save(save_path)


def _runtime_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_saved_runtime_config() -> dict[str, object]:
    settings = QSettings("A2LUpdateTool", "A2L_Update_V1_1")
    cal_text = settings.value("last_cal_ranges", "", type=str)
    meas_text = settings.value("last_meas_ranges", "", type=str)
    checked_json = settings.value("last_checked_signals", "", type=str)
    checked_names = []
    if checked_json:
        try:
            parsed = json.loads(checked_json)
            if isinstance(parsed, list):
                checked_names = [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            checked_names = []
    try:
        address_range_config = {
            "CALIBRATION": parse_address_ranges_text(cal_text),
            "MEASUREMENT": parse_address_ranges_text(meas_text),
        }
    except Exception:
        address_range_config = {"CALIBRATION": [], "MEASUREMENT": []}
    return {
        "a2l_path": settings.value("last_a2l", "", type=str),
        "addr_path": settings.value("last_addr", "", type=str),
        "out_path": settings.value("last_out", "", type=str),
        "insert_line": settings.value("last_line", "", type=str),
        "signal_config_path": settings.value("last_signal_config_xlsx", "", type=str),
        "checked_names": checked_names,
        "address_range_config": address_range_config,
    }


def run_saved_autorun(log_cb=None) -> str:
    logger = log_cb or (lambda msg: None)
    cfg = _load_saved_runtime_config()
    a2l_path = str(cfg.get("a2l_path") or "").strip()
    addr_path = str(cfg.get("addr_path") or "").strip()
    out_path = str(cfg.get("out_path") or "").strip()
    signal_config_path = str(cfg.get("signal_config_path") or "").strip()
    address_range_config = cfg.get("address_range_config") or {"CALIBRATION": [], "MEASUREMENT": []}

    if not a2l_path or not os.path.isfile(a2l_path):
        raise FileNotFoundError(tf("Auto update failed: the A2L header was not found: {n1}", n1=a2l_path))
    if not addr_path or not os.path.isfile(addr_path):
        raise FileNotFoundError(tf("Auto update failed: the address file was not found: {n1}", n1=addr_path))
    if not out_path:
        out_path = os.path.splitext(a2l_path)[0] + "_updated.a2l"

    insert_line = 0
    try:
        insert_line = max(0, int(str(cfg.get("insert_line") or "0").strip() or "0"))
    except Exception:
        insert_line = 0

    signal_rows = parse_signal_config_xlsx(signal_config_path) if signal_config_path and os.path.isfile(signal_config_path) else []
    preview_signals, selected_signal_names, _, matched = build_axf_signal_preview(
        addr_path,
        address_range_config,
        signal_rows,
        logger,
        expand_struct_array=True,
        a2l_path=a2l_path,
    )

    if not selected_signal_names:
        raise ValueError(tr("Auto update failed: there is currently no kept signal to generate."))

    logger(f"[AUTORUN] A2L={a2l_path}")
    logger(f"[AUTORUN] ADDR={addr_path}")
    logger(f"[AUTORUN] KEEP={len(selected_signal_names)}/{len(preview_signals)}")
    if matched:
        logger(tf("[AUTORUN] The signal parameter XLSX was merged in, {n1} signals matched", n1=matched))
    if signal_rows and signal_config_path:
        logger(tf("[AUTORUN] Using the signal parameter XLSX: {n1}", n1=signal_config_path))

    do_update_a2l(
        a2l_path,
        "",
        addr_path,
        out_path,
        insert_line,
        logger,
        selected_signal_names,
        preview_signals,
        address_range_config,
    )
    return out_path


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                        命令行（无头）入口                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
def _force_utf8_console() -> None:
    """把标准输出/错误切到 UTF-8。

    Windows 控制台默认是 GBK(cp936)，无法编码日志里的 emoji（🧬 ⚡ ✅ …），
    无头模式下会直接抛 UnicodeEncodeError。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run_cli(argv: list[str] | None = None) -> int:
    """无头命令行入口：与 GUI 共用同一条更新流水线，便于 CI 与论文复现。

    与 --autorun 的区别：--autorun 读取 GUI 保存的运行配置（依赖交互式操作），
    本入口的输入全部显式给出，可在干净环境中复现。
    """
    import argparse

    _force_utf8_console()

    parser = argparse.ArgumentParser(
        prog="A2L_Update_Tool",
        description="Regenerate an A2L description file from an address file (headless).",
    )
    parser.add_argument("--headless", action="store_true",
                        help="run without the GUI (implied when --a2l/--addr are given)")
    parser.add_argument("--version", action="version",
                        version="A2L Update Tool %s (%s)" % (APP_VERSION, APP_DATE))
    parser.add_argument("--a2l", required=True, help="A2L header file (.a2l/.A2L/.txt)")
    parser.add_argument("--addr", required=True, help="address file (.axf/.elf/.map/.bin)")
    parser.add_argument("--out", default="", help="output A2L path (default: <a2l>_updated.a2l)")
    parser.add_argument("--signals", default="",
                        help="signal parameter XLSX controlling which signals are emitted")
    parser.add_argument("--prev-a2l", default="",
                        help="previous A2L, used as the baseline of the version diff report")
    parser.add_argument("--reference-a2l", default="",
                        help="A2L currently used by the INCA project, for the address cross-check")
    parser.add_argument("--insert-line", type=int, default=0,
                        help="insert at line N (1-based); 0 = auto-detect")
    parser.add_argument("--cal-ranges", default="",
                        help="calibration address ranges, e.g. '0x1000-0x2000;0x3000-0x3100'")
    parser.add_argument("--meas-ranges", default="",
                        help="measurement address ranges, same syntax as --cal-ranges")
    parser.add_argument("--report", action="store_true",
                        help="write the per-signal match report next to the output")
    parser.add_argument("--diff-report", action="store_true",
                        help="write the version diff report (requires --prev-a2l)")
    parser.add_argument("--repair-header", action="store_true",
                        help="allow rewriting the original header (default: keep it byte-for-byte)")
    parser.add_argument("--no-expand-struct-array", action="store_true",
                        help="emit struct arrays as a raw byte stream instead of per-element members")
    parser.add_argument("--semantic-align", action="store_true",
                        help="enable A2L-RAGAlign: report semantic candidates for unmatched signals "
                             "and add the incremental-consistency section to the match report")
    parser.add_argument("--language", default="", choices=["", "en", "zh"],
                        help="language of the reports and messages (default: en)")
    parser.add_argument("--quiet", action="store_true", help="suppress progress output")
    args = parser.parse_args(argv)

    if args.language:
        set_language(args.language)

    a2l_path = os.path.abspath(args.a2l)
    addr_path = os.path.abspath(args.addr)
    if not os.path.isfile(a2l_path):
        print(tf("A2L header not found: {n1}", n1=a2l_path), file=sys.stderr)
        return 2
    if not os.path.isfile(addr_path):
        print(tf("Address file not found: {n1}", n1=addr_path), file=sys.stderr)
        return 2

    out_path = (os.path.abspath(args.out) if args.out
                else os.path.splitext(a2l_path)[0] + "_updated.a2l")
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    stem = os.path.splitext(out_path)[0]
    match_report_path = (stem + "_match_report.txt") if args.report else ""
    diff_report_path = (stem + "_version_diff.txt") if (args.diff_report and args.prev_a2l) else ""

    def _logger(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)

    try:
        address_range_config = {
            "CALIBRATION": parse_address_ranges_text(args.cal_ranges),
            "MEASUREMENT": parse_address_ranges_text(args.meas_ranges),
        }
    except ValueError as exc:
        print(tf("Invalid address range configuration: {n1}", n1=exc), file=sys.stderr)
        return 2

    expand = not args.no_expand_struct_array
    signal_rows = (parse_signal_config_xlsx(args.signals)
                   if args.signals and os.path.isfile(args.signals) else [])
    preview_signals, selected_signal_names, _kept, _matched = build_axf_signal_preview(
        addr_path,
        address_range_config,
        signal_rows,
        _logger,
        expand_struct_array=expand,
        a2l_path=a2l_path,
    )
    if not selected_signal_names:
        print(tr("No signal to write."), file=sys.stderr)
        return 3

    _logger(tf("[CLI] A2L  = {n1}", n1=a2l_path))
    _logger(tf("[CLI] ADDR = {n1}", n1=addr_path))
    _logger(tf("[CLI] OUT  = {n1}", n1=out_path))
    _logger(tf("[CLI] KEEP = {n1} / {n2}", n1=len(selected_signal_names), n2=len(preview_signals)))

    try:
        do_update_a2l(
            a2l_path,
            "",
            addr_path,
            out_path,
            max(0, int(args.insert_line or 0)),
            _logger,
            selected_signal_names,
            preview_signals,
            address_range_config,
            repair_header=bool(args.repair_header),
            expand_struct_array=expand,
            reference_a2l_path=args.reference_a2l or None,
            match_report_path=match_report_path or None,
            diff_report_path=diff_report_path or None,
            prev_a2l_path=args.prev_a2l or None,
            semantic_align=bool(args.semantic_align),
        )
    except Exception as exc:
        print(tf("A2L update failed: {n1}", n1=exc), file=sys.stderr)
        if os.environ.get("A2L_CLI_TRACEBACK"):
            traceback.print_exc()
        return 1

    print(out_path)
    return 0


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                               程序入口                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
if __name__ == "__main__":
    _cli_flags = ("--headless", "--help", "-h", "--a2l", "--addr", "--version")
    if any(a in sys.argv for a in _cli_flags):
        sys.exit(run_cli(sys.argv[1:]))

    if "--autorun" in sys.argv:
        _force_utf8_console()
        log_path = _runtime_app_dir() / "A2L_AutoUpdate.log"

        def _autorun_logger(msg: str):
            line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            if "--hidden" not in sys.argv:
                print(line)

        try:
            out_path = run_saved_autorun(_autorun_logger)
            _autorun_logger(f"[AUTORUN] SUCCESS -> {out_path}")
            sys.exit(0)
        except Exception as e:
            _autorun_logger(f"[AUTORUN] FAILED -> {e}")
            _autorun_logger(traceback.format_exc())
            sys.exit(1)

            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    # Fusion 之上叠加高对比度勾选框绘制（深色主题下默认指示器几乎不可见）
    app.setStyle(HighContrastCheckStyle("Fusion"))
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

"""回归测试：A2L 匹配证据报告 与 A2L 版本差异报告。

两个功能都是「事后可审计」产物：
  · 匹配报告 —— 逐信号记录地址/类型/位域的来源，失败时给出可操作的排查线索；
  · 差异报告 —— 逐对象对比两版 A2L 的新增/删除/地址平移/类型变化。
两者默认关闭，由 GUI 开关启用。
"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import A2L_Update_Tool_main as m  # noqa: E402


HEADER_TEMPLATE = """ASAP2_VERSION 1 60
/begin PROJECT TestProject ""
  /begin HEADER
    "{title}"
    VERSION "1.0"
  /end HEADER

  /begin MODULE TestModule ""
    /begin MEMORY_SEGMENT MEARAM
            "MEASUREMENT_DSRAM" DATA RAM INTERN 0x30100000 0x1000 -1 -1 -1 -1 -1
    /end MEMORY_SEGMENT
    /begin MEMORY_SEGMENT MEARAM
            "MEASUREMENT_DSRAM2" DATA RAM INTERN 0x30101000 0x1000 -1 -1 -1 -1 -1
    /end MEMORY_SEGMENT
    /begin IF_DATA XCP
        /begin SEGMENT
            0x00
            0x10
            0x20
            /begin PAGE
                0x01
                ECU_ACCESS_DONT_CARE
                XCP_READ_ACCESS_DONT_CARE
                XCP_WRITE_ACCESS_DONT_CARE
            /end PAGE
        /end SEGMENT
    /end IF_DATA
  /end MODULE
/end PROJECT
"""

MAP_V1 = """.text
0x20000000 0x00000004 g_SpeedValue
0x20000010 0x00000002 g_CounterValue
0x20000020 0x00000004 g_ExtraValue
"""

# 第二版：地址整体平移 +0x100，并新增一个符号
MAP_V2 = """.text
0x20000100 0x00000004 g_SpeedValue
0x20000110 0x00000002 g_CounterValue
0x20000120 0x00000004 g_ExtraValue
0x20000130 0x00000004 g_NewValue
"""


def _write(path, text, encoding="ascii"):
    with open(path, "w", encoding=encoding, newline="\n") as fh:
        fh.write(text)
    return path


def _write_header(tmp, title="原始头文件内容"):
    p = os.path.join(tmp, "head.a2l")
    _write(p, HEADER_TEMPLATE.format(title=title).replace("\n", "\r\n"),
           encoding="utf-8")
    return p


def _run(a2l_path, map_path, out_path, **kw):
    logs = []
    m.do_update_a2l(
        a2l_path, "", map_path, out_path, 0,
        logs.append, None, None, None, False,
        **kw,
    )
    return logs


# ────────────────────────────── 单元：对象详情解析 ──────────────────────────────

def test_collect_a2l_object_details_parses_all_fields():
    text = """/begin MEASUREMENT
    /* Name */     g_Speed
    /* Data type */    UWORD
    /* Conversion method */   CM_A2L_uint16
    ECU_ADDRESS 0x82028004
/end MEASUREMENT
/begin CHARACTERISTIC
    /* Name */     g_Cal
    /* Data type */    UBYTE
    /* Conversion method */   CM_A2L_uint8
    ECU_ADDRESS 0x82028010
/end CHARACTERISTIC
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = _write(os.path.join(tmp, "x.a2l"), text)
        det = m.collect_a2l_object_details(p)
    assert set(det) == {"g_Speed", "g_Cal"}
    assert det["g_Speed"]["address"] == 0x82028004
    assert det["g_Speed"]["data_type"] == "UWORD"
    assert det["g_Speed"]["conversion"] == "CM_A2L_uint16"
    assert det["g_Speed"]["object_type"] == "MEASUREMENT"
    assert det["g_Cal"]["object_type"] == "CALIBRATION"


def test_collect_a2l_object_details_handles_missing_file():
    assert m.collect_a2l_object_details("D:/__not_exist__/x.a2l") == {}


# ────────────────────────────── 单元：失败候选建议 ──────────────────────────────

def test_suggest_symbol_candidates_finds_case_variant():
    out = m.suggest_symbol_candidates("G_SPEED", ["g_speed", "other"])
    assert any("g_speed" in o and "大小写" in o for o in out)


def test_suggest_symbol_candidates_finds_base_symbol():
    # 成员形式 g_cfg.buf -> 提示基符号 g_cfg
    out = m.suggest_symbol_candidates("g_cfg.buf", ["g_cfg", "g_other"])
    assert any("g_cfg" in o and "基符号" in o for o in out)


def test_suggest_symbol_candidates_finds_underscore_variant():
    out = m.suggest_symbol_candidates("_g_flag", ["g_flag"])
    assert any("g_flag" in o and "下划线" in o for o in out)


def test_suggest_symbol_candidates_gives_hint_when_nothing_similar():
    """找不到任何相近符号时，也要给出说明而不是留空。"""
    out = m.suggest_symbol_candidates("zzz", ["g_speed"])
    assert len(out) == 1
    assert "没有找到任何相近的符号" in out[0]


# ────────────────────────────── 单元：匹配报告内容 ──────────────────────────────

def _sample_records():
    return [
        {
            "status": "OK", "name": "g_Speed", "shortname": "g_Speed",
            "object_type": "MEASUREMENT", "data_type": "UWORD", "unit": "km/h",
            "address": 0x20000000, "size": 4, "section": ".text",
            "evidence": [
                (m.EVID_ADDR_SYMBOL, "符号 g_Speed → 0x20000000（大小 4 字节，段 .text）"),
                (m.EVID_TYPE_DWARF, "数据类型与数组维度取自地址文件的 DWARF 调试信息"),
            ],
            "reason": "", "candidates": [],
        },
        {
            "status": "FAIL", "name": "G_MISSING", "shortname": "G_MISSING",
            "object_type": "MEASUREMENT", "data_type": "UWORD", "unit": "",
            "address": None, "size": 0, "section": "",
            "evidence": [],
            "reason": "地址文件符号表中不存在同名符号，无法确定 ECU 地址",
            "candidates": ["g_missing   ← 仅大小写不同"],
        },
    ]


def test_build_match_report_contains_evidence_and_failure_reason():
    txt = m.build_match_report(_sample_records(), {"generated_at": "2026-10-24 10:00:00"})
    assert "A2L 匹配报告" in txt
    assert "匹配成功：1" in txt
    assert "匹配失败：1" in txt
    # 成功的信号必须写清地址来源
    assert m.tr(m.EVID_ADDR_SYMBOL) in txt
    assert "0x20000000" in txt
    # 失败的信号必须写清原因与候选
    assert "地址文件符号表中不存在同名符号" in txt
    assert "g_missing" in txt


def test_build_match_report_lists_evidence_statistics():
    txt = m.build_match_report(_sample_records(), {})
    assert m.tr(m.EVID_ADDR_SYMBOL) in txt
    assert m.tr(m.EVID_TYPE_DWARF) in txt


def test_build_match_report_handles_empty():
    txt = m.build_match_report([], {})
    assert "候选信号总数：0" in txt
    assert "（无，全部候选信号均匹配成功）" in txt


# ────────────────────────────── 单元：差异报告内容 ──────────────────────────────

def _write_two_versions(tmp):
    v1 = """/begin MEASUREMENT
    /* Name */     g_Speed
    /* Data type */    UWORD
    ECU_ADDRESS 0x20000000
/end MEASUREMENT
/begin MEASUREMENT
    /* Name */     g_Counter
    /* Data type */    UWORD
    ECU_ADDRESS 0x20000010
/end MEASUREMENT
/begin MEASUREMENT
    /* Name */     g_Old
    /* Data type */    UWORD
    ECU_ADDRESS 0x20000020
/end MEASUREMENT
"""
    v2 = """/begin MEASUREMENT
    /* Name */     g_Speed
    /* Data type */    ULONG
    ECU_ADDRESS 0x20000100
/end MEASUREMENT
/begin MEASUREMENT
    /* Name */     g_Counter
    /* Data type */    UWORD
    ECU_ADDRESS 0x20000110
/end MEASUREMENT
/begin MEASUREMENT
    /* Name */     g_New
    /* Data type */    UWORD
    ECU_ADDRESS 0x20000130
/end MEASUREMENT
"""
    p1 = _write(os.path.join(tmp, "v1.a2l"), v1)
    p2 = _write(os.path.join(tmp, "v2.a2l"), v2)
    return p1, p2


def test_build_a2l_diff_report_detects_all_change_kinds():
    with tempfile.TemporaryDirectory() as tmp:
        p1, p2 = _write_two_versions(tmp)
        txt = m.build_a2l_diff_report(p1, p2, {})

    assert "A2L 版本差异报告" in txt
    # 新增 / 删除
    assert "g_New" in txt
    assert "g_Old" in txt
    # 地址平移
    assert "0x20000000" in txt and "0x20000100" in txt
    assert "偏差 +256" in txt
    # 类型变化
    assert "数据类型变化" in txt
    assert "ULONG" in txt and "UWORD" in txt
    # 偏差分布
    assert "偏差分布" in txt


def test_build_a2l_diff_report_with_identical_files():
    with tempfile.TemporaryDirectory() as tmp:
        p1, p2 = _write_two_versions(tmp)
        txt = m.build_a2l_diff_report(p1, p1, {})
    assert "地址变化：0" in txt
    assert "本次新增：0" in txt
    assert "本次删除：0" in txt


# ────────────────────────────── 端到端 ──────────────────────────────

def test_reports_not_generated_by_default():
    """默认关闭：既不传报告路径，就不该产生任何报告文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        out = os.path.join(tmp, "head_updated.a2l")
        _run(a2l, mp, out)
        assert os.path.isfile(out)
        assert not os.path.exists(os.path.join(tmp, "head_updated_match_report.txt"))
        assert not os.path.exists(os.path.join(tmp, "head_updated_diff_report.txt"))


def test_match_report_generated_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        out = os.path.join(tmp, "head_updated.a2l")
        rpt = os.path.join(tmp, "head_updated_match_report.txt")
        logs = _run(a2l, mp, out, match_report_path=rpt)

        assert os.path.isfile(rpt)
        with open(rpt, encoding="utf-8") as fh:
            txt = fh.read()
        assert "A2L 匹配报告" in txt
        assert "g_SpeedValue" in txt
        assert m.tr(m.EVID_ADDR_SYMBOL) in txt
        assert "0x20000000" in txt
        assert any("匹配报告已生成" in str(x) for x in logs)


def test_match_report_lists_failed_signals_with_candidates():
    """人为让信号名与符号名对不上，报告里必须出现失败明细与候选建议。

    注意：这种情况下工具无法生成任何 A2L 对象并会抛异常，但匹配报告必须
    已经落盘 —— 这正是用户最需要线索的时刻。
    """
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        out = os.path.join(tmp, "head_updated.a2l")
        rpt = os.path.join(tmp, "head_updated_match_report.txt")
        # 预览信号里塞一个不存在的符号名
        preview = [{
            "Name": "g_NoSuchSymbol", "ShortName": "g_NoSuchSymbol",
            "LongName": "g_NoSuchSymbol", "DataType": "UWORD",
            "ObjectType": "MEASUREMENT", "ArraySize": "1", "Description": "",
        }]
        raised = False
        try:
            m.do_update_a2l(
                a2l, "", mp, out, 0, lambda _x: None, None, preview, None, False,
                match_report_path=rpt,
            )
        except ValueError:
            raised = True
        assert raised, "全部信号匹配失败时应当报错"
        assert os.path.isfile(rpt), "报错前必须先把匹配报告落盘"
        with open(rpt, encoding="utf-8") as fh:
            txt = fh.read()
        assert "匹配失败：1" in txt
        assert "g_NoSuchSymbol" in txt
        assert "不在地址文件符号表中" in txt
        # 近似符号建议应指向真实存在的 g_SpeedValue
        assert "g_SpeedValue" in txt


def test_match_report_lists_failed_signals_from_excel_source():
    """非预览路径（Excel 信号源）下，符号名对不上的信号同样要进失败明细。"""
    pytest = __import__("pytest")
    if not getattr(m, "HAS_OPENPYXL", False):
        pytest.skip("未安装 openpyxl")
    from openpyxl import Workbook

    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        xlsx = os.path.join(tmp, "signals.xlsx")
        wb = Workbook()
        ws = wb.active
        # parse_xlsx 以第一列 "Group" 作为表头行标记
        ws.append(["Group", "Name", "ShortName", "DataType", "ObjectType", "ArraySize"])
        ws.append(["G1", "g_NotInSymbolTable", "g_NotInSymbolTable",
                   "UWORD", "MEASUREMENT", "1"])
        wb.save(xlsx)

        out = os.path.join(tmp, "head_updated.a2l")
        rpt = os.path.join(tmp, "head_updated_match_report.txt")
        logs = []
        raised = False
        try:
            m.do_update_a2l(
                a2l, xlsx, mp, out, 0, logs.append, None, None, None, False,
                match_report_path=rpt,
            )
        except ValueError:
            raised = True
        assert raised
        assert os.path.isfile(rpt)
        with open(rpt, encoding="utf-8") as fh:
            txt = fh.read()
        assert "匹配失败：1" in txt
        assert "g_NotInSymbolTable" in txt
        assert "地址文件符号表中不存在同名符号" in txt


def test_diff_report_generated_end_to_end():
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp1 = _write(os.path.join(tmp, "v1.map"), MAP_V1)
        mp2 = _write(os.path.join(tmp, "v2.map"), MAP_V2)
        prev_out = os.path.join(tmp, "prev_updated.a2l")
        _run(a2l, mp1, prev_out)

        out = os.path.join(tmp, "head_updated.a2l")
        rpt = os.path.join(tmp, "head_updated_diff_report.txt")
        logs = _run(a2l, mp2, out, diff_report_path=rpt, prev_a2l_path=prev_out)

        assert os.path.isfile(rpt)
        with open(rpt, encoding="utf-8") as fh:
            txt = fh.read()
        assert "A2L 版本差异报告" in txt
        assert "g_NewValue" in txt          # 新增
        assert "地址变化" in txt
        assert any("版本差异报告已生成" in str(x) for x in logs)


def test_diff_report_skipped_when_prev_missing():
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        out = os.path.join(tmp, "head_updated.a2l")
        rpt = os.path.join(tmp, "head_updated_diff_report.txt")
        logs = _run(a2l, mp, out, diff_report_path=rpt,
                    prev_a2l_path=os.path.join(tmp, "nope.a2l"))
        # 文件不存在时不应崩，且不应写出报告
        assert not os.path.exists(rpt)
        assert any("版本差异报告跳过" in str(x) for x in logs)


def test_report_failure_does_not_break_update():
    """报告写盘失败不能影响 A2L 主产物。"""
    with tempfile.TemporaryDirectory() as tmp:
        a2l = _write_header(tmp)
        mp = _write(os.path.join(tmp, "syms.map"), MAP_V1)
        out = os.path.join(tmp, "head_updated.a2l")
        # 指向一个不存在的目录 -> 写盘必然失败
        bad = os.path.join(tmp, "no_such_dir", "rpt.txt")
        logs = _run(a2l, mp, out, match_report_path=bad)
        assert os.path.isfile(out)
        assert any("匹配报告生成失败" in str(x) for x in logs)


# ────────────────────────────── GUI 开关与持久化 ──────────────────────────────

def _gui_available():
    try:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import PyQt5.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


def test_gui_switches_exist_and_default_off():
    """两个开关默认必须关闭，「上一版 A2L」路径默认必须为空。"""
    pytest = __import__("pytest")
    if not _gui_available():
        pytest.skip("无 Qt 环境")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    win = m.MainWindow()
    assert win.chk_match_report.isChecked() is False
    assert win.chk_diff_report.isChecked() is False
    assert win.edit_prev_a2l.text().strip() == ""


def test_gui_settings_roundtrip():
    """勾选状态不持久化：重启后两个开关必须回到「未勾选」（即默认不生成报告）。

    「上一版 A2L」路径仍会记忆，方便下次直接勾选使用。
    """
    pytest = __import__("pytest")
    if not _gui_available():
        pytest.skip("无 Qt 环境")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    win = m.MainWindow()
    win.chk_match_report.setChecked(True)
    win.chk_diff_report.setChecked(True)
    win.edit_prev_a2l.setText("D:/__prev__/x.a2l")
    win._save_paths()
    try:
        win2 = m.MainWindow()
        # 开关：必须回到未勾选
        assert win2.chk_match_report.isChecked() is False
        assert win2.chk_diff_report.isChecked() is False
        # 路径：仍然保留
        assert win2.edit_prev_a2l.text() == "D:/__prev__/x.a2l"
    finally:
        # 复位，避免污染用户配置
        win.chk_match_report.setChecked(False)
        win.chk_diff_report.setChecked(False)
        win.edit_prev_a2l.setText("")
        win._save_paths()


def test_report_switch_state_never_persisted():
    """注册表里不得再出现 opt_match_report / opt_diff_report 键。"""
    pytest = __import__("pytest")
    if not _gui_available():
        pytest.skip("无 Qt 环境")
    from PyQt5.QtCore import QSettings
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    win = m.MainWindow()
    win.chk_match_report.setChecked(True)
    win.chk_diff_report.setChecked(True)
    win._save_paths()
    s = QSettings("A2LUpdateTool", "A2L_Update_V1_1")
    assert s.contains("opt_match_report") is False
    assert s.contains("opt_diff_report") is False
    win.chk_match_report.setChecked(False)
    win.chk_diff_report.setChecked(False)
    win._save_paths()


def test_prev_a2l_row_hidden_until_diff_report_checked():
    """「上一版 A2L」路径行默认隐藏，勾选版本差异报告后才显示。"""
    pytest = __import__("pytest")
    if not _gui_available():
        pytest.skip("无 Qt 环境")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    win = m.MainWindow()
    win.show()
    app.processEvents()
    assert win.chk_diff_report.isChecked() is False
    assert win.prev_a2l_box.isVisible() is False
    win.chk_diff_report.setChecked(True)
    app.processEvents()
    assert win.prev_a2l_box.isVisible() is True
    win.chk_diff_report.setChecked(False)
    app.processEvents()
    assert win.prev_a2l_box.isVisible() is False
    win.close()


def test_left_column_scrolls_and_action_button_stays_visible():
    """左栏分组区可滚动，且「一键更新」按钮固定可见（不被滚动吞掉）。"""
    pytest = __import__("pytest")
    if not _gui_available():
        pytest.skip("无 Qt 环境")
    from PyQt5.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    win = m.MainWindow()
    win.show()
    app.processEvents()
    scroll = win.findChild(QWidget, "left_scroll")
    assert scroll is not None, "左栏必须放在 QScrollArea 里，避免控件被压缩重叠"
    assert scroll.isAncestorOf(win.btn_update) is False, \
        "「一键更新」按钮必须在滚动区之外，任何时候都可见"
    g = win.btn_update.mapTo(win, win.btn_update.rect().topLeft())
    assert g.y() >= 0 and g.y() + win.btn_update.height() <= win.height(), \
        "「一键更新」按钮必须完整落在窗口可视区域内"
    win.close()


def test_do_update_a2l_new_params_are_optional():
    """新增参数必须有默认值，旧的 CLI / 脚本调用方式不受影响。"""
    import inspect
    sig = inspect.signature(m.do_update_a2l)
    for p in ("match_report_path", "diff_report_path", "prev_a2l_path"):
        assert p in sig.parameters
        assert sig.parameters[p].default is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

# -*- coding: utf-8 -*-
"""国际化（中英文切换）专项测试。"""
import ast
import io
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import i18n
import A2L_Update_Tool_main as m

from PyQt5.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton

HAN = re.compile(r"[\u4e00-\u9fff]")
SETTINGS_SCOPE = ("A2LUpdateTool", "A2L_Update_V1_1")


@pytest.fixture(autouse=True)
def _english_ui():
    """本模块专门验证英文模式（覆盖 conftest 里固定的中文）。"""
    old = i18n.get_language()
    i18n.set_language("en")
    yield
    i18n.set_language(old)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = m.MainWindow()
    yield w
    w.close()


def placeholders(text):
    return set(re.findall(r"\{(n\d+|[^{}]+)\}", text))


def test_default_language_is_english():
    assert i18n.DEFAULT_LANG == "en"
    assert i18n.get_language() == "en"
    assert i18n.tr("Browse…") == "Browse…"
    assert i18n.tr("Browse…") != "浏览…"


def test_tr_falls_back_to_english_when_missing():
    i18n.set_language("zh")
    try:
        assert i18n.tr("__not_a_real_key__") == "__not_a_real_key__"
    finally:
        i18n.set_language("en")


def test_tr_supports_format_kwargs():
    i18n.set_language("zh")
    try:
        out = i18n.tr("  {n1}: {n2}", n1="A", n2="B")
        assert out == "  A：B"
    finally:
        i18n.set_language("en")


def test_every_tr_key_has_a_chinese_translation():
    """源码里所有 tr()/tf() 的 key 都必须能在 ZH 表里查到译文。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "A2L_Update_Tool_main.py")
    src = io.open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in ("tr", "tf"):
            if node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
    assert keys, "未找到任何 tr/tf 调用"
    missing = sorted(k for k in keys if k not in i18n.ZH)
    assert not missing, "缺少中文译文: %r" % (missing[:5],)


def test_placeholder_arguments_are_always_supplied():
    """key 里带占位符的 tr()/tf() 必须把对应实参传进来，否则文案里会残留 {n1}。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "A2L_Update_Tool_main.py")
    src = io.open(path, encoding="utf-8").read()
    bad = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in ("tr", "tf")):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        key = node.args[0].value
        names = {m.strip() for m in re.findall(r"\{([^{}]*)\}", key) if m.strip()}
        if not names:
            continue
        given = {kw.arg for kw in node.keywords if kw.arg}
        if node.func.id == "tr" and not given:
            bad.append((node.lineno, key))
            continue
        if names - given:
            bad.append((node.lineno, key))
    assert not bad, "占位符未正确传参: %r" % (bad[:5],)


def test_zh_table_keys_are_unique():
    """i18n.py 的 ZH 表不能出现重复 key（否则后写的会静默覆盖前者）。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "i18n.py")
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "ZH":
                    for k in node.value.keys:
                        if isinstance(k, ast.Constant):
                            keys.append(k.value)
    assert len(keys) == len(set(keys)), "ZH 表存在重复 key"
    assert len(keys) == len(i18n.ZH), "ZH 表字面量条目数与实际表不一致"


def test_translated_placeholders_match_source():
    """译文里的占位符必须与英文原文一一对应（否则运行时格式化会炸）。"""
    bad = []
    for en, zh in i18n.ZH.items():
        if not en or not zh:
            continue
        if placeholders(en) != placeholders(zh):
            bad.append((en, zh))
    assert not bad, "占位符不一致: %r" % (bad[:3],)


def test_ui_switch_updates_all_text(win):
    """切换语言后，标签页标题、分组标题、按钮、标签、占位符都要即时刷新。"""
    win.cmb_lang.setCurrentIndex(0)   # 中文
    assert i18n.get_language() == "zh"

    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    groups = [g.title() for g in win.findChildren(QGroupBox)]
    label = win.pick_a2l.findChild(QLabel).text()
    placeholder = win.pick_a2l.edit.placeholderText()
    browse = win.pick_a2l.findChild(QPushButton).text()
    dep = win.dep_lbl.text()

    for text in titles + groups + [label, placeholder, browse, dep]:
        assert HAN.search(text), "未切换成中文: %r" % (text,)

    win.cmb_lang.setCurrentIndex(1)   # 回到英文
    assert i18n.get_language() == "en"
    titles = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    groups = [g.title() for g in win.findChildren(QGroupBox)]
    label = win.pick_a2l.findChild(QLabel).text()
    placeholder = win.pick_a2l.edit.placeholderText()
    browse = win.pick_a2l.findChild(QPushButton).text()
    dep = win.dep_lbl.text()

    for text in titles + groups + [label, placeholder, browse, dep]:
        assert not HAN.search(text), "未切回英文: %r" % (text,)


def test_ui_switch_updates_table_header(win):
    win.cmb_lang.setCurrentIndex(0)
    assert win.sig_tab.table.horizontalHeaderItem(0).text() == i18n.tr("Keep")
    win.cmb_lang.setCurrentIndex(1)
    assert win.sig_tab.table.horizontalHeaderItem(0).text() == "Keep"


def test_language_choice_is_persisted(win):
    from PyQt5.QtCore import QSettings
    s = QSettings(*SETTINGS_SCOPE)
    old = s.value("ui_language", None)
    try:
        win.cmb_lang.setCurrentIndex(0)
        s.sync()
        assert s.value("ui_language", None) == "zh"
        win.cmb_lang.setCurrentIndex(1)
        s.sync()
        assert s.value("ui_language", None) == "en"
    finally:
        if old is None:
            s.remove("ui_language")
        else:
            s.setValue("ui_language", old)
        s.sync()


def test_startup_uses_persisted_language(app):
    from PyQt5.QtCore import QSettings
    s = QSettings(*SETTINGS_SCOPE)
    old = s.value("ui_language", None)
    try:
        s.setValue("ui_language", "zh")
        s.sync()
        w = m.MainWindow()
        assert i18n.get_language() == "zh"
        assert w.cmb_lang.currentIndex() == 0
        w.close()

        s.setValue("ui_language", "en")
        s.sync()
        w = m.MainWindow()
        assert i18n.get_language() == "en"
        assert w.cmb_lang.currentIndex() == 1
        w.close()
    finally:
        if old is None:
            s.remove("ui_language")
        else:
            s.setValue("ui_language", old)
        s.sync()


def test_report_text_follows_language():
    """报告/日志等运行时输出同样随语言变化。"""
    i18n.set_language("zh")
    assert HAN.search(i18n.tr("                     A2L Version Diff Report"))
    i18n.set_language("en")
    assert i18n.tr("                     A2L Version Diff Report") == \
        "                     A2L Version Diff Report"


def test_hex_format_specs_are_preserved():
    """回写过程不能丢掉 :08X / :+d 之类的格式说明。"""
    import inspect
    src = inspect.getsource(m.build_a2l_diff_report)
    assert 'format(pa, "08X")' in src
    assert 'format(ca - pa, "+d")' in src

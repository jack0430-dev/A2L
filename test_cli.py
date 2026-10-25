# -*- coding: utf-8 -*-
"""无头 CLI 的测试。

用合成的小型 MAP + A2L 走完整流水线，保证测试既快又能覆盖真实路径；
同时覆盖参数处理、退出码与控制台编码（Windows 默认 GBK 不能输出 emoji）。
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.abspath(__file__))
MAIN = os.path.join(ROOT, "A2L_Update_Tool_main.py")

MAP_TEXT = """\
.text
        0x00200000        0x00000004 EngSpeed
        0x00200004        0x00000004 VehSpeed
        0x00200008        0x00000004 ThrottlePos
        0x0020000c        0x00000004 BrakePedal
"""

A2L_TEXT = """\
ASAP2_VERSION 1 60
/begin PROJECT Test ""
  /begin MODULE Test ""
    /begin MEMORY_SEGMENT Ram 0x00200000 0x1000 -1 -1 -1 -1 -1
    /end MEMORY_SEGMENT
  /end MODULE
/end PROJECT
"""


def _run(*args):
    """执行 CLI 子进程；统一用 UTF-8 解码，验证控制台编码处理。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = ""          # 让被测进程走自己的编码逻辑
    return subprocess.run(
        [sys.executable, MAIN, *args],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env=env, timeout=300)


@pytest.fixture()
def workdir(tmp_path):
    (tmp_path / "test.map").write_text(MAP_TEXT, encoding="ascii")
    (tmp_path / "test.a2l").write_text(A2L_TEXT, encoding="ascii")
    return tmp_path


# ── 参数处理与退出码 ──────────────────────────────────────────────────────
def test_help_exits_zero():
    r = _run("--help")
    assert r.returncode == 0
    assert "usage" in r.stdout.lower()
    assert "--headless" in r.stdout
    assert "--semantic-align" in r.stdout


def test_version_prints_version():
    r = _run("--version")
    assert r.returncode == 0
    assert "A2L Update Tool" in r.stdout
    assert "V2.7" in r.stdout


def test_missing_required_arguments():
    r = _run("--headless")
    assert r.returncode == 2
    assert "--a2l" in r.stderr and "--addr" in r.stderr


def test_missing_a2l_file_returns_2(workdir):
    r = _run("--headless", "--a2l", str(workdir / "nope.a2l"),
             "--addr", str(workdir / "test.map"))
    assert r.returncode == 2
    assert "not found" in r.stderr.lower()


def test_missing_addr_file_returns_2(workdir):
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "nope.map"))
    assert r.returncode == 2
    assert "not found" in r.stderr.lower()


def test_invalid_range_returns_2(workdir):
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"),
             "--out", str(workdir / "o.a2l"),
             "--cal-ranges", "garbage")
    assert r.returncode == 2


# ── 端到端 ────────────────────────────────────────────────────────────────
def test_headless_end_to_end(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert out.is_file()
    text = out.read_text(encoding="utf-8", errors="replace")
    assert text.count("/begin MEASUREMENT") == 4
    for sym in ("EngSpeed", "VehSpeed", "ThrottlePos", "BrakePedal"):
        assert sym in text
    assert "ECU_ADDRESS 0x00200000" in text


def test_headless_preserves_original_header(workdir):
    """原 A2L 头文件内容必须原样保留（默认不修复头文件）。"""
    out = workdir / "out.a2l"
    _run("--headless", "--a2l", str(workdir / "test.a2l"),
         "--addr", str(workdir / "test.map"), "--out", str(out))
    text = out.read_text(encoding="utf-8", errors="replace")
    assert "ASAP2_VERSION 1 60" in text
    assert "/begin PROJECT Test" in text
    assert "/begin MEMORY_SEGMENT Ram 0x00200000" in text


def test_report_option_writes_match_report(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out),
             "--report")
    assert r.returncode == 0, r.stderr
    rep = workdir / "out_match_report.txt"
    assert rep.is_file()
    text = rep.read_text(encoding="utf-8", errors="replace")
    assert "EngSpeed" in text
    assert "Success rate" in text


def test_emoji_output_does_not_crash_on_windows_console(workdir):
    """日志里有 emoji；GBK 控制台不能编码时必须自动降级而不是崩溃。"""
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out))
    assert r.returncode == 0
    assert "UnicodeEncodeError" not in (r.stderr or "")


def test_quiet_suppresses_progress(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out),
             "--quiet")
    assert r.returncode == 0
    assert "Matching signal addresses" not in r.stdout
    assert len(r.stdout.strip().splitlines()) <= 2


def test_language_switch_changes_output(workdir):
    out = workdir / "out.a2l"
    r_en = _run("--headless", "--a2l", str(workdir / "test.a2l"),
                "--addr", str(workdir / "test.map"), "--out", str(out),
                "--language", "en")
    r_zh = _run("--headless", "--a2l", str(workdir / "test.a2l"),
                "--addr", str(workdir / "test.map"), "--out", str(out),
                "--language", "zh")
    assert r_en.returncode == 0 and r_zh.returncode == 0
    assert r_en.stdout != r_zh.stdout
    assert "匹配" in r_zh.stdout or "地址" in r_zh.stdout


def test_semantic_align_does_not_change_generated_a2l(workdir):
    """语义对齐只影响报告，绝不自动改写生成的 A2L 对象。"""
    out_a = workdir / "a.a2l"
    out_b = workdir / "b.a2l"
    r_a = _run("--headless", "--a2l", str(workdir / "test.a2l"),
               "--addr", str(workdir / "test.map"), "--out", str(out_a))
    r_b = _run("--headless", "--a2l", str(workdir / "test.a2l"),
               "--addr", str(workdir / "test.map"), "--out", str(out_b),
               "--semantic-align")
    assert r_a.returncode == 0 and r_b.returncode == 0
    assert out_a.read_text(encoding="utf-8", errors="replace") == \
        out_b.read_text(encoding="utf-8", errors="replace")


def test_semantic_align_adds_consistency_section(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out),
             "--report", "--semantic-align")
    assert r.returncode == 0, r.stderr
    rep = workdir / "out_match_report.txt"
    text = rep.read_text(encoding="utf-8", errors="replace")
    assert "Incremental consistency check" in text


def test_no_semantic_align_omits_consistency_section(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out),
             "--report")
    assert r.returncode == 0, r.stderr
    rep = workdir / "out_match_report.txt"
    text = rep.read_text(encoding="utf-8", errors="replace")
    assert "Incremental consistency check" not in text


def test_diff_report_requires_prev_a2l(workdir):
    out = workdir / "out.a2l"
    prev = workdir / "prev.a2l"
    prev.write_text(A2L_TEXT, encoding="ascii")
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out),
             "--prev-a2l", str(prev), "--diff-report")
    assert r.returncode == 0, r.stderr
    assert (workdir / "out_version_diff.txt").is_file()


def test_provenance_log_is_written(workdir):
    out = workdir / "out.a2l"
    r = _run("--headless", "--a2l", str(workdir / "test.a2l"),
             "--addr", str(workdir / "test.map"), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert (workdir / "out.a2l.log").is_file()

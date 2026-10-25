"""回归测试：更新 A2L 时不得改写用户提供的头文件内容。"""

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import A2L_Update_Tool_main as a2l_module  # noqa: E402


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

MAP_CONTENT = """.text
0x20000000 0x00000004 g_SpeedValue
0x20000010 0x00000002 g_CounterValue
"""


def _write_map(path):
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        fh.write(MAP_CONTENT)


def _run_update(a2l_path, map_path, out_path, insert_line=0, repair_header=False):
    logs = []
    a2l_module.do_update_a2l(
        a2l_path, "", map_path, out_path, insert_line,
        logs.append, None, None, None, repair_header,
    )
    return logs


def _expected_header(original):
    """比对基准：工具**不得修改头文件的任何一个字节**。

    头文件是用户提供的既有资产，里面允许存在段名重名、旧式 COMPU_METHOD
    等历史问题——这些只能靠新生成信号自身的写法规范来规避，
    绝不允许改名段、重排块或插入额外块。
    """
    return original


def _split_around_insertion(original, updated):
    """断言原头文件前后段完全保留，返回被插入的内容。"""
    for marker in ("  /end MODULE", "/end PROJECT"):
        pos = original.find(marker)
        if pos >= 0:
            break
    else:
        raise AssertionError("测试用头文件缺少 /end MODULE")

    head = original[:pos]
    tail = original[pos:]
    assert updated.startswith(head), "原头文件前半段被改写"
    assert updated.endswith(tail), "原头文件后半段被改写"
    return head, updated[len(head):len(updated) - len(tail)]


def test_default_update_keeps_original_header_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "head.a2l")
        map_path = os.path.join(tmp, "syms.map")
        out_path = os.path.join(tmp, "head_updated.a2l")

        original = HEADER_TEMPLATE.format(title="原始头文件内容").replace("\n", "\r\n")
        with open(a2l_path, "wb") as fh:
            fh.write(original.encode("utf-8"))
        _write_map(map_path)

        logs = _run_update(a2l_path, map_path, out_path)

        assert any("保持原始头文件内容不变" in item for item in logs)
        assert not any("已自动修复头文件兼容性问题" in item for item in logs)

        with open(out_path, "rb") as fh:
            updated_bytes = fh.read()
        updated = updated_bytes.decode("utf-8")

        head, inserted = _split_around_insertion(_expected_header(original), updated)
        assert "MEASUREMENT_DSRAM" in head
        assert "ECU_ACCESS_DONT_CARE" in head, "旧版 XCP token 不应被自动替换"
        assert "MEASUREMENT_DSRAM2" in head
        assert "g_SpeedValue" in inserted
        assert "g_CounterValue" in inserted
        # 头文件里重名的内存段必须原样保留（不得改名）
        assert head.count("/begin MEMORY_SEGMENT MEARAM") == 2

        # 输出仍是纯 CRLF
        assert b"\r\n" in updated_bytes
        assert updated_bytes.replace(b"\r\n", b"").count(b"\n") == 0


def test_repair_flag_is_opt_in():
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "head.a2l")
        map_path = os.path.join(tmp, "syms.map")
        out_path = os.path.join(tmp, "head_updated.a2l")

        original = HEADER_TEMPLATE.format(title="原始头文件内容")
        with open(a2l_path, "wb") as fh:
            fh.write(original.encode("utf-8"))
        _write_map(map_path)

        logs = _run_update(a2l_path, map_path, out_path, repair_header=True)
        assert any("已自动修复头文件兼容性问题" in item for item in logs)

        with open(out_path, "r", encoding="utf-8") as fh:
            updated = fh.read()
        assert "ECU_ACCESS_ALLOWED_DONT_CARE" in updated
        assert "ECU_ACCESS_DONT_CARE" not in updated


def test_gbk_encoding_and_lf_newline_are_preserved():
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "head_gbk.a2l")
        map_path = os.path.join(tmp, "syms.map")
        out_path = os.path.join(tmp, "head_gbk_updated.a2l")

        original = HEADER_TEMPLATE.format(title="中文注释保持不变")
        with open(a2l_path, "wb") as fh:
            fh.write(original.encode("gbk"))
        _write_map(map_path)

        _run_update(a2l_path, map_path, out_path)

        with open(out_path, "rb") as fh:
            updated_bytes = fh.read()
        # 仍可用 GBK 解码，说明编码未被强制改成 UTF-8
        updated = updated_bytes.decode("gbk")
        assert "中文注释保持不变" in updated
        assert b"\r\n" not in updated_bytes
        head, _inserted = _split_around_insertion(_expected_header(original), updated)
        assert "中文注释保持不变" in head


def test_manual_insert_line_keeps_original_lines():
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "head.a2l")
        map_path = os.path.join(tmp, "syms.map")
        out_path = os.path.join(tmp, "head_updated.a2l")

        original = HEADER_TEMPLATE.format(title="原始头文件内容")
        with open(a2l_path, "wb") as fh:
            fh.write(original.encode("utf-8"))
        _write_map(map_path)

        _run_update(a2l_path, map_path, out_path, insert_line=5)

        with open(out_path, "r", encoding="utf-8", newline="") as fh:
            updated = fh.read()

        original_lines = _expected_header(original).splitlines(keepends=True)
        updated_lines = updated.splitlines(keepends=True)
        inserted_count = len(updated_lines) - len(original_lines)
        assert inserted_count > 0
        assert updated_lines[:4] == original_lines[:4], "插入点之前的原始行被改写"
        assert updated_lines[4 + inserted_count:] == original_lines[4:], "插入点之后的原始行被改写"


def test_collect_and_compare_addresses():
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "ref.a2l")
        body = (
            "/begin PROJECT P \"\"\n"
            "  /begin MODULE M \"\"\n"
            "    /begin MEASUREMENT\n"
            "      /* Name                  */\t\tSig_A\n"
            "      /* Data type             */\t\tULONG\n"
            "      ECU_ADDRESS 0x10001000\n"
            "    /end MEASUREMENT\n"
            "    /begin MEASUREMENT\n"
            "      /* Name                  */\t\tSig_B\n"
            "      /* Data type             */\t\tUWORD\n"
            "      ECU_ADDRESS 0x10002000\n"
            "    /end MEASUREMENT\n"
            "  /end MODULE\n"
            "/end PROJECT\n"
        )
        with open(a2l_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(body)

        addrs = a2l_module.collect_a2l_object_addresses(a2l_path)
        assert addrs == {"Sig_A": 0x10001000, "Sig_B": 0x10002000}

        stat = a2l_module.compare_a2l_addresses(addrs, {"Sig_A": 0x10001000, "Sig_C": 0x10003000})
        assert stat["common"] == 1
        assert stat["same"] == 1
        assert stat["diff"] == []
        assert stat["only_ref"] == ["Sig_B"]
        assert stat["only_gen"] == ["Sig_C"]

        stat2 = a2l_module.compare_a2l_addresses(addrs, {"Sig_A": 0x20001000, "Sig_B": 0x10002000})
        assert stat2["same"] == 1
        assert len(stat2["diff"]) == 1
        assert stat2["diff"][0][3] == 0x10000000


def test_detect_address_file_conflicts():
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = os.path.join(tmp, "project", "Release")
        os.makedirs(out_dir)
        local_axf = os.path.join(out_dir, "TcuBsw.axf")
        with open(local_axf, "wb") as fh:
            fh.write(b"\x7fELF")

        other_axf = os.path.join(tmp, "other_project", "TcuBsw.axf")
        os.makedirs(os.path.dirname(other_axf))
        with open(other_axf, "wb") as fh:
            fh.write(b"\x7fELF")

        out_a2l = os.path.join(out_dir, "A2lHeader_updated.a2l")

        # 选了别的工程的固件 -> 必须报警
        warns = a2l_module.detect_address_file_conflicts(other_axf, out_a2l)
        assert warns, "选错工程的固件时应给出告警"
        joined = "\n".join(warns)
        assert "TcuBsw.axf" in joined

        # 用了同目录的固件 -> 不应因“同名固件冲突”报警
        warns_ok = a2l_module.detect_address_file_conflicts(local_axf, out_a2l)
        assert not any("另一份同名固件" in w for w in warns_ok)


def test_header_is_never_rewritten_end_to_end():
    """端到端：头文件一个字节都不能改。

    头文件里允许存在重名 MEMORY_SEGMENT、旧式 RAT_FUNC COMPU_METHOD 等历史
    问题——工具只能靠「新生成信号自身的写法规范」来规避，绝不改动头文件。
    """
    header = (
        "ASAP2_VERSION 1 60\n"
        "/begin PROJECT P \"\"\n"
        "  /begin MODULE M \"\"\n"
        "    /begin MEMORY_SEGMENT MEARAM\n"
        "        \"RAM1\" DATA RAM INTERN 0x30100000 0x1000 -1 -1 -1 -1 -1\n"
        "    /end MEMORY_SEGMENT\n"
        "    /begin MEMORY_SEGMENT MEARAM\n"
        "        \"RAM2\" DATA RAM INTERN 0x30101000 0x1000 -1 -1 -1 -1 -1\n"
        "    /end MEMORY_SEGMENT\n"
        "    /begin COMPU_METHOD\n"
        "CM_uint16 \"Q = V\"\n  RAT_FUNC \"%5.0\" \"\"\n  COEFFS 0 1 0 0 0 1\n"
        "    /end COMPU_METHOD\n"
        "  /end MODULE\n"
        "/end PROJECT\n")
    with tempfile.TemporaryDirectory() as tmp:
        a2l_path = os.path.join(tmp, "head.a2l")
        map_path = os.path.join(tmp, "syms.map")
        out_path = os.path.join(tmp, "head_updated.a2l")
        with open(a2l_path, "wb") as fh:
            fh.write(header.encode("utf-8"))
        _write_map(map_path)

        _run_update(a2l_path, map_path, out_path)

        with open(out_path, "r", encoding="utf-8", newline="") as fh:
            updated = fh.read()

    # 头文件原有内容必须逐字保留（只允许在 /end MODULE 前插入新内容）
    marker = "  /end MODULE"
    pos = header.find(marker)
    assert pos > 0
    assert updated.startswith(header[:pos]), "头文件前半段被改写"
    assert updated.endswith(header[pos:]), "头文件后半段被改写"
    # 重名段不得被改名，也不得被删减
    assert updated.count("/begin MEMORY_SEGMENT MEARAM") == 2
    assert "MEARAM_2" not in updated
    # 旧式 COMPU_METHOD 不得被删除或改写
    assert 'CM_uint16 "Q = V"' in updated
    assert "RAT_FUNC" in updated
    # 新信号不得再引用头文件里那些旧式转换方法
    assert "CM_A2L_" in updated
    assert not re.search(r"Conversion method\s*\*/\s*CM_uint16", updated)

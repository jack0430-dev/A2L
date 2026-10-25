import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import A2L_Update_Tool_main as m


# The production image is released with the repository, so the integration
# tests below run in CI instead of being skipped.  A private project image can
# still be pointed at through the A2L_UPDATE_REAL_AXF environment variable.
REPO_AXF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "data", "TcuBsw.axf")
REAL_AXF = os.environ.get("A2L_UPDATE_REAL_AXF") or REPO_AXF


def _sig(**kw):
    base = {
        "Name": "g_test",
        "ShortName": "g_test",
        "LongName": "g_test",
        "DataType": "uint8_t",
        "ObjectType": "MEASUREMENT",
        "ArraySize": "1",
        "Description": "",
    }
    base.update(kw)
    return base


def _parse(block):
    dt = re.search(r"/\*\s*Data type\s*\*/\s*(\S+)", block)
    md = re.search(r"MATRIX_DIM\s+([\d\s]+)", block)
    addr = re.search(r"ECU_ADDRESS\s+(0x[0-9A-Fa-f]+)", block)
    return {
        "dtype": dt.group(1) if dt else None,
        "dims": [int(x) for x in md.group(1).split()] if md else [],
        "addr": int(addr.group(1), 0) if addr else None,
    }


# 方案 B: 元素是结构体/联合体时, 按符号实际字节数输出字节流
def test_struct_element_array_becomes_full_byte_stream():
    # DWARF 元素是 36 字节结构体、共 16 个元素 -> 576 字节; A2L 必须声明 576 字节
    sig = _sig(DataType="array_u8", ArraySize="16", _elem_bytes="36")
    block, _ = m.generate_a2l_block(sig, 0x820282DC, 576)
    info = _parse(block)
    assert info["dtype"] == "UBYTE"
    assert info["dims"] == [1, 576], "字节流维度必须等于符号实际字节数"
    assert info["dims"][0] * info["dims"][1] == 576


def test_byte_stream_mode_not_applied_to_plain_byte_array():
    # 元素本来就是 1 字节的数组不应被改写
    sig = _sig(DataType="array_u8", ArraySize="16", _elem_bytes="1")
    block, _ = m.generate_a2l_block(sig, 0x10001000, 16)
    info = _parse(block)
    assert info["dtype"] == "UBYTE"
    assert info["dims"] == [1, 16]


def test_byte_stream_mode_skipped_for_member_alias():
    # 结构体成员别名的 size 是整个结构体大小, 不能拿来当字节数
    sig = _sig(Name="g_cfg", ShortName="g_cfg.buf", DataType="array_u8",
               ArraySize="8", _elem_bytes="16")
    block, _ = m.generate_a2l_block(sig, 0x10001000, 64)
    info = _parse(block)
    assert info["dims"] == [1, 8], "成员信号不应被改写成整结构体字节流"


# 方案 C: 符号实际大小与类型宽度冲突时以符号为准
def test_size_conflict_downgrades_to_ubyte():
    # 符号只有 1 字节但 DWARF 声明 unsigned long -> 降级为 UBYTE, 避免越界读
    sig = _sig(DataType="uint32_t")
    block, _ = m.generate_a2l_block(sig, 0x10002000, 1)
    info = _parse(block)
    assert info["dtype"] == "UBYTE"
    assert "size-conflict" in block


def test_normal_scalar_keeps_declared_width():
    sig = _sig(DataType="uint32_t")
    block, _ = m.generate_a2l_block(sig, 0x10002000, 4)
    assert _parse(block)["dtype"] == "ULONG"


def test_member_alias_not_downgraded_by_whole_struct_size():
    # 成员信号 size 是整个结构体大小(远大于成员宽度), 不应触发降级
    sig = _sig(Name="g_cfg", ShortName="g_cfg.speed", DataType="uint32_t")
    block, _ = m.generate_a2l_block(sig, 0x10003000, 512)
    assert _parse(block)["dtype"] == "ULONG"


# 方案 A: 命名与开关
def test_dot_index_suffix_from_dims():
    assert m._dot_index_suffix_from_dims(0, [16]) == ".0"
    assert m._dot_index_suffix_from_dims(5, [2, 3]) == ".1.2"
    assert m._dot_index_suffix_from_dims(15, [16]) == ".15"


def test_expand_flag_defaults_to_true():
    class _NoCtl:
        pass

    assert m._expand_struct_array_flag(_NoCtl()) is True

    class _Ctl:
        def __init__(self, checked):
            self.chk_expand_struct_array = self
            self._checked = checked

        def isChecked(self):
            return self._checked

    assert m._expand_struct_array_flag(_Ctl(False)) is False
    assert m._expand_struct_array_flag(_Ctl(True)) is True


# 集成: 用真实 AXF 验证结构体数组被逐元素展开且地址精确
@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_real_axf_struct_array_expanded_with_exact_address():
    det = m.load_address_file_details(REAL_AXF)
    cfg = {"CALIBRATION": [(0x90860000, 0x909DFFFF)],
           "MEASUREMENT": [(0x10000000, 0x10027FFF), (0x82028000, 0x8202FFFF),
                           (0x30100400, 0x3010CFFF), (0x3010D000, 0x3013BFFF)]}
    det = m.apply_address_range_overrides(det, cfg, filter_unmatched=True)
    sigs = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=True)
    expanded = [s for s in sigs if re.match(r"^.+\.\d+\.", str(s.get("ShortName") or ""))]
    assert expanded, "结构体数组应被逐元素展开为成员信号"
    syms = m.parse_elf_axf_detailed(REAL_AXF)
    checked = 0
    for s in expanded:
        base = str(s.get("Name") or "")
        sym = syms.get(base)
        if not sym:
            continue
        offset = int(s.get("AddrOffset") or 0)
        addr = int(sym.get("address") or 0) + offset
        size = int(sym.get("size") or 0)
        assert addr >= int(sym.get("address") or 0)
        assert addr < int(sym.get("address") or 0) + size, "展开信号地址必须落在符号范围内"
        checked += 1
    assert checked > 0


@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_real_axf_top_level_sizes_all_consistent():
    """顶层信号的 A2L 声明字节数必须与符号实际大小一致。"""
    det = m.load_address_file_details(REAL_AXF)
    cfg = {"CALIBRATION": [(0x90860000, 0x909DFFFF)],
           "MEASUREMENT": [(0x10000000, 0x10027FFF), (0x82028000, 0x8202FFFF),
                           (0x30100400, 0x3010CFFF), (0x3010D000, 0x3013BFFF)]}
    det = m.apply_address_range_overrides(det, cfg, filter_unmatched=True)
    sigs = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=True)
    syms = m.parse_elf_axf_detailed(REAL_AXF)
    sizes = {"UBYTE": 1, "SBYTE": 1, "UWORD": 2, "SWORD": 2, "ULONG": 4, "SLONG": 4,
             "A_UINT64": 8, "A_INT64": 8, "FLOAT32_IEEE": 4, "FLOAT64_IEEE": 8}
    bad = []
    for s in sigs:
        name = str(s.get("Name") or "")
        short = str(s.get("ShortName") or "")
        if short != name or "." in name:
            continue
        sym = syms.get(name)
        if not sym:
            continue
        sym_size = int(sym.get("size") or 0)
        if not sym_size:
            continue
        block, _ = m.generate_a2l_block(s, int(sym.get("address") or 0), sym_size)
        info = _parse(block)
        n = 1
        for d in info["dims"]:
            n *= max(1, d)
        total = sizes.get(info["dtype"], 0) * n
        if total and total != sym_size:
            bad.append((name, sym_size, total, info["dtype"], info["dims"]))
    assert not bad, "顶层信号尺寸失配: %s" % bad[:8]


# ── 成员信号维度推导与越界防护 ────────────────────────────────────────────
def test_member_signal_uses_own_byte_count_not_whole_struct_size():
    """成员自身的字节数已知时，必须用它推导维度，而不是整个结构体的大小。"""
    sig = _sig(Name="g_cfg", ShortName="g_cfg.gain", DataType="array_f32",
               ArraySize="1", _member_bytes="8")
    block, _ = m.generate_a2l_block(sig, 0x20000000, 64)
    info = _parse(block)
    assert info["dims"] == [1, 2], "8 字节 / 4 字节元素 = 2 个元素，不能用 64 字节的结构体大小"


def test_member_signal_clamped_to_struct_remaining_space():
    """成员声明的总字节数不得超过「结构体末尾 - 成员偏移」。"""
    sig = _sig(Name="g_cfg", ShortName="g_cfg.gain", DataType="array_f32",
               ArraySize="1", AddrOffset="40", _member_bytes="4")
    block, _ = m.generate_a2l_block(sig, 0x20000028, 48)
    info = _parse(block)
    n = 1
    for d in info["dims"]:
        n *= max(1, d)
    total = n * 4
    assert 0x20000028 + total <= 0x20000028 + (48 - 40), "成员读取不能越出结构体末尾"


def test_scalar_member_with_count1_array_type_stays_scalar():
    """DWARF 把标量成员描述成 array_type + count=1 时，必须保持标量。"""
    sig = _sig(Name="g_cfg", ShortName="g_cfg.flag", DataType="struct_u8",
               ArraySize="1", _member_bytes="1")
    block, _ = m.generate_a2l_block(sig, 0x20000000, 48)
    info = _parse(block)
    assert info["dtype"] == "UBYTE"
    assert not info["dims"], "标量不应输出 MATRIX_DIM（count=1 的数组实质是标量）"


@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_real_axf_no_member_signal_reads_out_of_symbol():
    """真实工程里所有成员信号（含结构体数组展开的元素级信号）都不得越出符号范围。"""
    det = m.load_address_file_details(REAL_AXF)
    cfg = {"CALIBRATION": [(0x90860000, 0x909DFFFF)],
           "MEASUREMENT": [(0x10000000, 0x10027FFF), (0x82028000, 0x8202FFFF),
                           (0x30100400, 0x3010CFFF), (0x3010D000, 0x3013BFFF)]}
    det = m.apply_address_range_overrides(det, cfg, filter_unmatched=True)
    syms = m.parse_elf_axf_detailed(REAL_AXF)
    sizes = {"UBYTE": 1, "SBYTE": 1, "UWORD": 2, "SWORD": 2, "ULONG": 4, "SLONG": 4,
             "A_UINT64": 8, "A_INT64": 8, "FLOAT32_IEEE": 4, "FLOAT64_IEEE": 8}
    bad = []
    checked = 0
    for expand in (True, False):
        sigs = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=expand)
        for s in sigs:
            short = str(s.get("ShortName") or "")
            base = str(s.get("Name") or "")
            if short == base or "." not in short:
                continue
            sym = syms.get(base)
            if not sym:
                continue
            sym_addr = int(sym.get("address") or 0)
            sym_size = int(sym.get("size") or 0)
            if not sym_size:
                continue
            offset = int(s.get("AddrOffset") or 0)
            addr = sym_addr + offset
            block, _ = m.generate_a2l_block(s, addr, sym_size)
            info = _parse(block)
            n = 1
            for d in info["dims"]:
                n *= max(1, d)
            total = sizes.get(info["dtype"], 0) * n
            if total <= 0:
                continue
            checked += 1
            if addr < sym_addr or addr + total > sym_addr + sym_size:
                bad.append((short, hex(addr), total, hex(sym_addr), sym_size))
    assert checked > 0
    assert not bad, "成员信号越界: %s" % bad[:8]


@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_real_axf_count1_array_member_is_scalar():
    """真实工程里 count=1 的 array_type 成员（Dem/Os 等）必须保持标量。"""
    det = m.load_address_file_details(REAL_AXF)
    sigs = m.build_signals_from_dwarf(REAL_AXF, det, None)
    by_name = {str(s.get("ShortName") or ""): s for s in sigs}
    for name in ("Dem_DummyEventMemoryEntry.cntHeal",
                 "Dem_DummyEventMemoryInfo.enSlot"):
        if name not in by_name:
            continue
        sig = by_name[name]
        assert str(sig.get("DataType") or "").startswith("struct_"), \
            "%s 应为标量成员，实际 %s" % (name, sig.get("DataType"))
        assert str(sig.get("ArraySize") or "1") == "1"


# ── ASAM-2MC 命名唯一性（INCA 解析卡死的直接原因） ─────────────────────────
def test_compu_method_and_vtab_names_are_unique():
    """同一枚举类型被多个信号引用时，只能输出一份 COMPU_METHOD / COMPU_VTAB。"""
    sig = _sig(Name="g_arr", ShortName="g_arr.0.state", DataType="enum_uint8",
               ArraySize="1", BitMask='0="OFF" 1="RUN"',
               _enum_type_name="EcuState")
    block, compu = m.generate_a2l_block(sig, 0x20000000, 4)
    assert "Name of CompuMethod" in compu
    assert "EcuState_Enum_Method" in compu
    assert "EcuState_Vtab" in compu
    # 描述文本不能带信号名，否则同枚举不同信号会生成内容不同的同名块
    assert "g_arr.0.state" not in compu


def test_extract_compu_method_name_supports_both_formats():
    new_style = "    /begin COMPU_METHOD\n        /* Name of CompuMethod    */      CM_x\n    /end COMPU_METHOD"
    old_style = "    /begin COMPU_METHOD\nCM_y \"Q = V\"\n  RAT_FUNC \"%3.0\" \"\"\n    /end COMPU_METHOD"
    assert m._extract_compu_method_name(new_style) == "CM_x"
    assert m._extract_compu_method_name(old_style) == "CM_y"
    assert m._extract_compu_method_name("") == ""


def test_defined_compu_method_names_reads_legacy_header_style():
    legacy = ("/begin COMPU_METHOD\nCM_uint8 \"Q = V\"\n RAT_FUNC \"%3.0\" \"\"\n/end COMPU_METHOD\n"
              "/begin COMPU_METHOD\n /* Name of CompuMethod */ CM_new\n/end COMPU_METHOD")
    names = m._extract_defined_compu_method_names(legacy)
    assert "CM_uint8" in names
    assert "CM_new" in names


# ── INCA 可读性：保留原有信号名 ────────────────────────────────────────────


@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_expand_keeps_all_original_signal_names():
    """展开结构体数组时必须保留原有顶层信号名：
    否则 INCA 里按原名引用的观测量会因对象消失而恒为 0 且不再刷新。"""
    det = m.load_address_file_details(REAL_AXF)
    plain = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=False)
    expanded = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=True)
    plain_names = {str(s.get("Name") or "") for s in plain}
    expanded_names = {str(s.get("Name") or "") for s in expanded}
    missing = sorted(n for n in plain_names if n and n not in expanded_names)
    assert not missing, "展开后丢失原有信号名: %s" % missing[:8]


@pytest.mark.skipif(not os.path.isfile(REAL_AXF), reason="need the released (or a local) project AXF")
def test_expand_adds_indexed_member_signals():
    """展开后应额外提供「名.i.成员」的元素级信号。"""
    det = m.load_address_file_details(REAL_AXF)
    expanded = m.build_signals_from_dwarf(REAL_AXF, det, None, expand_struct_array=True)
    indexed = [s for s in expanded
               if re.search(r"\.\d+\.", str(s.get("ShortName") or s.get("Name") or ""))]
    assert indexed, "未生成任何元素级信号"


# ── 地址区间：并入头文件 MEMORY_SEGMENT 声明的区间 ─────────────────────────
def test_parse_header_memory_segment_ranges():
    header = (
        "/begin MODULE M \"\"\n"
        "    /begin MEMORY_SEGMENT MEARAM\n"
        "        \"RAM1\" DATA RAM INTERN 0x10000000 0x28000 -1 -1 -1 -1 -1\n"
        "    /end MEMORY_SEGMENT\n"
        "    /begin MEMORY_SEGMENT CALROM\n"
        "        \"ROM1\" DATA FLASH INTERN 0x90860000 0x2000 -1 -1 -1 -1 -1\n"
        "    /end MEMORY_SEGMENT\n"
        "  /end MODULE\n")
    r = m._parse_header_memory_segment_ranges(header)
    assert r["MEASUREMENT"] == [(0x10000000, 0x10027FFF)]
    assert r["CALIBRATION"] == [(0x90860000, 0x90861FFF)]


def test_merge_ranges_merges_adjacent():
    got = m._merge_ranges([(0x1000, 0x1FFF), (0x2000, 0x2FFF), (0x4000, 0x4FFF)])
    assert got == [(0x1000, 0x2FFF), (0x4000, 0x4FFF)]


def test_header_segment_range_merged_into_filter():
    """用户手工配置漏掉某段时，该段的符号不能凭空消失。

    本工程头文件声明了 0x30504000 起的 MEARAM 段，手工地址区间配置漏了它，
    该段的信号就不会被生成，INCA 侧表现为观测量恒为 0 且不刷新。
    """
    header = (
        "/begin MODULE M \"\"\n"
        "    /begin MEMORY_SEGMENT MEARAM\n"
        "        \"RAM1\" DATA RAM INTERN 0x30504000 0x1000 -1 -1 -1 -1 -1\n"
        "    /end MEMORY_SEGMENT\n"
        "  /end MODULE\n")
    syms = {
        "in_user_range": {"address": 0x10001000, "size": 2, "object_type": "MEASUREMENT"},
        "in_header_range": {"address": 0x30504100, "size": 2, "object_type": "MEASUREMENT"},
        "in_neither": {"address": 0x08110000, "size": 2, "object_type": "MEASUREMENT"},
    }
    user = {"CALIBRATION": [], "MEASUREMENT": [(0x10000000, 0x1000FFFF)]}
    hr = m._parse_header_memory_segment_ranges(header)
    merged = {k: m._merge_ranges(list(user.get(k) or []) + hr.get(k, []))
              for k in ("CALIBRATION", "MEASUREMENT")}
    out = m.apply_address_range_overrides(syms, merged)
    assert "in_user_range" in out
    assert "in_header_range" in out, "头文件声明的区间必须被并入"
    assert "in_neither" not in out




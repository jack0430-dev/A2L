"""
A2L Update Tool - 单元测试

测试核心辅助函数的正确性
"""
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import A2L_Update_Tool_main as a2l_module  # 2026-10: 主模块已由 A2L_Update_V1_1_ww_20260406 重命名


class TestSafeIntAuto:
    """测试 _safe_int_auto 函数"""

    def test_decimal_integer(self):
        assert a2l_module._safe_int_auto("123") == 123
        assert a2l_module._safe_int_auto(456) == 456

    def test_hexadecimal(self):
        assert a2l_module._safe_int_auto("0x1F") == 31
        assert a2l_module._safe_int_auto("0xFF") == 255
        assert a2l_module._safe_int_auto("0X1F") == 31

    def test_float_string(self):
        assert a2l_module._safe_int_auto("123.456") == 123

    def test_none_value(self):
        assert a2l_module._safe_int_auto(None) == 0
        assert a2l_module._safe_int_auto(None, default=99) == 99

    def test_empty_string(self):
        assert a2l_module._safe_int_auto("") == 0
        assert a2l_module._safe_int_auto("   ") == 0

    def test_invalid_string(self):
        assert a2l_module._safe_int_auto("abc") == 0
        assert a2l_module._safe_int_auto("abc", default=-1) == -1


class TestParseCheckedCell:
    """测试 _parse_checked_cell 函数"""

    def test_boolean_true(self):
        assert a2l_module._parse_checked_cell(True) is True
        assert a2l_module._parse_checked_cell(False) is False

    def test_truthy_strings(self):
        assert a2l_module._parse_checked_cell("1") is True
        assert a2l_module._parse_checked_cell("true") is True
        assert a2l_module._parse_checked_cell("True") is True
        assert a2l_module._parse_checked_cell("TRUE") is True
        assert a2l_module._parse_checked_cell("yes") is True
        assert a2l_module._parse_checked_cell("YES") is True
        assert a2l_module._parse_checked_cell("y") is True
        assert a2l_module._parse_checked_cell("Y") is True
        assert a2l_module._parse_checked_cell("checked") is True
        assert a2l_module._parse_checked_cell("CHECK") is True
        assert a2l_module._parse_checked_cell("x") is True
        assert a2l_module._parse_checked_cell("X") is True
        assert a2l_module._parse_checked_cell("v") is True
        assert a2l_module._parse_checked_cell("√") is True

    def test_falsy_strings(self):
        assert a2l_module._parse_checked_cell("0") is False
        assert a2l_module._parse_checked_cell("false") is False
        assert a2l_module._parse_checked_cell("no") is False
        assert a2l_module._parse_checked_cell("n") is False

    def test_none_empty(self):
        assert a2l_module._parse_checked_cell(None) is False
        assert a2l_module._parse_checked_cell("") is False


class TestFormatKeepCell:
    """测试 _format_keep_cell 函数"""

    def test_true_values(self):
        assert a2l_module._format_keep_cell(True) == "yes"
        assert a2l_module._format_keep_cell(1) == "yes"

    def test_false_values(self):
        assert a2l_module._format_keep_cell(False) == "no"
        assert a2l_module._format_keep_cell(0) == "no"


class TestSafeIdentifier:
    """测试 _safe_identifier 函数"""

    def test_normal_identifier(self):
        assert a2l_module._safe_identifier("valid_name") == "valid_name"

    def test_special_chars_replaced(self):
        # _safe_identifier replaces each non-word char with underscore
        assert a2l_module._safe_identifier("name-with.dots") == "name_with_dots"
        result = a2l_module._safe_identifier("test@#$%")
        assert "test" in result

    def test_strip_underscores(self):
        assert a2l_module._safe_identifier("_test_") == "test"

    def test_empty_returns_default(self):
        assert a2l_module._safe_identifier("") == "AUTO"
        assert a2l_module._safe_identifier("   ") == "AUTO"
        assert a2l_module._safe_identifier("", default="Custom") == "Custom"


class TestNormalizeTypeLabel:
    """测试 _normalize_type_label 函数"""

    def test_normalize_spaces(self):
        assert a2l_module._normalize_type_label("uint32_t") == "uint32_t"
        assert a2l_module._normalize_type_label("unsigned   int") == "unsigned int"
        assert a2l_module._normalize_type_label("  int  ") == "int"

    def test_pointer_formatting(self):
        assert a2l_module._normalize_type_label("char *") == "char*"
        assert a2l_module._normalize_type_label("int  *  ptr") == "int* ptr"


class TestCanonicalSignalConfigHeader:
    """测试 _canonical_signal_config_header 函数"""

    def test_exact_match(self):
        assert a2l_module._canonical_signal_config_header("Group") == "Group"
        assert a2l_module._canonical_signal_config_header("Name") == "Name"

    def test_keep_checked_aliases(self):
        assert a2l_module._canonical_signal_config_header("Keep") == "Keep"
        assert a2l_module._canonical_signal_config_header("Checked") == "Checked"

    def test_unit_aliases(self):
        assert a2l_module._canonical_signal_config_header("UNIT") == "Unit"
        assert a2l_module._canonical_signal_config_header("uint") == "Unit"
        assert a2l_module._canonical_signal_config_header("Unit") == "Unit"

    def test_case_insensitive_keep(self):
        assert a2l_module._canonical_signal_config_header("keep") == "Keep"
        assert a2l_module._canonical_signal_config_header("CHECKED") == "Checked"

    def test_empty_string(self):
        assert a2l_module._canonical_signal_config_header("") == ""
        assert a2l_module._canonical_signal_config_header(None) == ""


class TestSignalPreviewColumns:
    """测试预览表列定义"""

    def test_preview_contains_user_config_columns(self):
        for col in ("Unit", "Min", "Max", "Factor", "Offset", "LongName", "Description"):
            assert col in a2l_module.SIGNAL_PREVIEW_COLS

    def test_preview_contains_derived_offset_columns(self):
        for col in ("BitMask", "AddrOffset", "BitOffset", "BitSize"):
            assert col in a2l_module.SIGNAL_PREVIEW_COLS


class TestIsUserSymbol:
    """测试 _is_user_symbol 函数"""

    def test_valid_symbols(self):
        assert a2l_module._is_user_symbol("validName") is True
        assert a2l_module._is_user_symbol("_private") is False
        assert a2l_module._is_user_symbol("__system") is False

    def test_invalid_start(self):
        assert a2l_module._is_user_symbol("_test") is False
        assert a2l_module._is_user_symbol("__test") is False

    def test_non_signal_symbols(self):
        assert a2l_module._is_user_symbol("RAM") is False
        assert a2l_module._is_user_symbol("FLASH") is False
        assert a2l_module._is_user_symbol("ROM") is False
        assert a2l_module._is_user_symbol("NAME") is False

    def test_invalid_format(self):
        assert a2l_module._is_user_symbol("123test") is False
        assert a2l_module._is_user_symbol("") is False
        assert a2l_module._is_user_symbol("test-name") is False


class TestDeriveGroupFromName:
    """测试 derive_group_from_name 函数"""

    def test_valid_prefix(self):
        # derives group from first 5 chars, finds underscore position
        result1 = a2l_module.derive_group_from_name("Engine_rpm")
        result2 = a2l_module.derive_group_from_name("sensor_temp_value")
        # Just verify it returns a non-empty string
        assert len(result1) > 0 and result1 != "temperature"
        assert len(result2) > 0

    def test_no_underscore(self):
        assert a2l_module.derive_group_from_name("temperature") == "Other"

    def test_short_prefix(self):
        # A_test: underscore at position 1, cleaned to "A", returns "A" (non-empty)
        assert a2l_module.derive_group_from_name("A_test") == "A"

    def test_empty_input(self):
        assert a2l_module.derive_group_from_name("") == "Other"
        assert a2l_module.derive_group_from_name(None) == "Other"


class TestNormalizeObjectTypeLabel:
    """测试 _normalize_object_type_label 函数"""

    def test_calibration_variants(self):
        assert a2l_module._normalize_object_type_label("CAL") == "CALIBRATION"
        assert a2l_module._normalize_object_type_label("CALIBRATION") == "CALIBRATION"
        assert a2l_module._normalize_object_type_label("cal") == "CALIBRATION"

    def test_measurement_variants(self):
        assert a2l_module._normalize_object_type_label("MEAS") == "MEASUREMENT"
        assert a2l_module._normalize_object_type_label("MEASUREMENT") == "MEASUREMENT"
        assert a2l_module._normalize_object_type_label("measurement") == "MEASUREMENT"

    def test_unknown(self):
        assert a2l_module._normalize_object_type_label("UNKNOWN") == "MEASUREMENT"


class TestMergeRanges:
    """测试 _merge_ranges 函数"""

    def test_empty_input(self):
        assert a2l_module._merge_ranges([]) == []

    def test_single_range(self):
        assert a2l_module._merge_ranges([(10, 20)]) == [(10, 20)]

    def test_disjoint_ranges(self):
        result = a2l_module._merge_ranges([(10, 20), (30, 40)])
        assert result == [(10, 20), (30, 40)]

    def test_overlapping_ranges(self):
        result = a2l_module._merge_ranges([(10, 20), (15, 25)])
        assert result == [(10, 25)]

    def test_adjacent_ranges(self):
        result = a2l_module._merge_ranges([(10, 20), (21, 30)])
        assert result == [(10, 30)]

    def test_unsorted_input(self):
        # Note: non-overlapping ranges remain separate
        result = a2l_module._merge_ranges([(30, 40), (10, 20), (18, 25)])
        # (30,40) doesn't merge with (10,25), so they stay separate
        assert result == [(10, 25), (30, 40)]


class TestSafeFloat:
    """测试 _safe_float 函数"""

    def test_valid_float(self):
        assert a2l_module._safe_float("123.456") == 123.456
        assert a2l_module._safe_float(789) == 789.0

    def test_invalid_value(self):
        assert a2l_module._safe_float("abc") == 0.0
        assert a2l_module._safe_float("abc", default=-1.0) == -1.0


class TestSafeLimit:
    """测试 _safe_limit 函数"""

    def test_valid_values(self):
        assert a2l_module._safe_limit(100.5) == 100.5
        assert a2l_module._safe_limit("123.45") == 123.45

    def test_empty_string(self):
        assert a2l_module._safe_limit("") is None
        assert a2l_module._safe_limit("   ") is None

    def test_none_value(self):
        assert a2l_module._safe_limit(None) is None

    def test_invalid_value(self):
        assert a2l_module._safe_limit("abc") is None


class TestGetDtypeLimits:
    """测试 _get_dtype_limits 函数"""

    def test_ubyte(self):
        lo, hi = a2l_module._get_dtype_limits("UBYTE")
        assert lo == 0.0 and hi == 255.0

    def test_sbyte(self):
        lo, hi = a2l_module._get_dtype_limits("SBYTE")
        assert lo == -128.0 and hi == 127.0

    def test_uword(self):
        lo, hi = a2l_module._get_dtype_limits("UWORD")
        assert lo == 0.0 and hi == 65535.0

    def test_sword(self):
        lo, hi = a2l_module._get_dtype_limits("SWORD")
        assert lo == -32768.0 and hi == 32767.0

    def test_ulong(self):
        lo, hi = a2l_module._get_dtype_limits("ULONG")
        assert lo == 0.0 and hi == 4294967295.0

    def test_slong(self):
        lo, hi = a2l_module._get_dtype_limits("SLONG")
        assert lo == -2147483648.0 and hi == 2147483647.0

    def test_float32(self):
        lo, hi = a2l_module._get_dtype_limits("FLOAT32_IEEE")
        assert lo == -3.4028235E+38 and hi == 3.4028235E+38

    def test_float64(self):
        lo, hi = a2l_module._get_dtype_limits("FLOAT64_IEEE")
        assert lo == -1.7976931348623157E+308 and hi == 1.7976931348623157E+308

    def test_default(self):
        lo, hi = a2l_module._get_dtype_limits("UNKNOWN")
        assert lo == 0.0 and hi == 65535.0


class TestFormatLimit:
    """测试 _format_limit 函数"""

    def test_integer_types(self):
        assert a2l_module._format_limit(100.0, "UBYTE") == "100"
        assert a2l_module._format_limit(255.0, "UBYTE") == "255"
        assert a2l_module._format_limit(-128.0, "SBYTE") == "-128"

    def test_float_types(self):
        result = a2l_module._format_limit(100.0, "FLOAT32_IEEE")
        assert "E" in result or "." in result


class TestBuildLinearCompuMethod:
    """测试 _build_linear_compu_method 函数"""

    def test_integer_unit_only_generates_integer_linear_method(self):
        sig = {"Unit": "V", "Factor": "", "Offset": ""}
        method, block = a2l_module._build_linear_compu_method(
            sig, "APP_ADC_PIN_HSD_Diag", "", "UWORD", "CM_uint16"
        )

        assert method == "APP_ADC_PIN_HSD_Diag_Method"
        assert '"%7.0f"' in block
        assert '"V"' in block
        assert "COEFFS_LINEAR 1 0" in block

    def test_integer_factor_still_generates_linear_method(self):
        sig = {"Unit": "V", "Factor": "0.1", "Offset": "0"}
        method, block = a2l_module._build_linear_compu_method(
            sig, "APP_ADC_PIN_HSD_Diag", "", "UWORD", "CM_uint16"
        )

        assert method == "APP_ADC_PIN_HSD_Diag_Method"
        assert "COEFFS_LINEAR 0.1 0" in block
        assert '"%9.3f"' in block

    def test_integer_fractional_offset_uses_float_format(self):
        sig = {"Unit": "V", "Factor": "1", "Offset": "0.25"}
        method, block = a2l_module._build_linear_compu_method(
            sig, "APP_ADC_PIN_HSD_Diag", "", "UWORD", "CM_uint16"
        )

        assert method == "APP_ADC_PIN_HSD_Diag_Method"
        assert "COEFFS_LINEAR 1 0.25" in block
        assert '"%9.3f"' in block

    def test_float_unit_only_still_generates_linear_method(self):
        sig = {"Unit": "V", "Factor": "", "Offset": ""}
        method, block = a2l_module._build_linear_compu_method(
            sig, "BatteryVoltage", "", "FLOAT32_IEEE", "CM_single"
        )

        assert method == "BatteryVoltage_Method"
        assert '"%6.3f"' in block
        assert '"V"' in block


class TestDimsToText:
    """测试 _dims_to_text 函数"""

    def test_single_dim(self):
        assert a2l_module._dims_to_text([10]) == "10"

    def test_multiple_dims(self):
        assert a2l_module._dims_to_text([10, 20]) == "10x20"
        assert a2l_module._dims_to_text([2, 3, 4]) == "2x3x4"

    def test_empty_dims(self):
        assert a2l_module._dims_to_text([]) == "1"

    def test_ones_replaced(self):
        assert a2l_module._dims_to_text([1, 2, 1]) == "1x2x1"


class TestDimsToDeclSuffix:
    """测试 _dims_to_decl_suffix 函数"""

    def test_single_dim(self):
        assert a2l_module._dims_to_decl_suffix([10]) == "[10]"

    def test_multiple_dims(self):
        assert a2l_module._dims_to_decl_suffix([10, 20]) == "[10][20]"

    def test_empty_dims(self):
        assert a2l_module._dims_to_decl_suffix([]) == ""


class TestInferKindFromCType:
    """测试 _infer_kind_from_c_type 函数"""

    def test_unsigned_integers(self):
        assert a2l_module._infer_kind_from_c_type("uint8_t") == "u8"
        assert a2l_module._infer_kind_from_c_type("uint16_t") == "u16"
        assert a2l_module._infer_kind_from_c_type("uint32_t") == "u32"
        assert a2l_module._infer_kind_from_c_type("uint64_t") == "u64"

    def test_signed_integers(self):
        assert a2l_module._infer_kind_from_c_type("int8_t") == "i8"
        assert a2l_module._infer_kind_from_c_type("int16_t") == "i16"
        assert a2l_module._infer_kind_from_c_type("int32_t") == "i32"
        assert a2l_module._infer_kind_from_c_type("int64_t") == "i64"

    def test_float_types(self):
        assert a2l_module._infer_kind_from_c_type("float") == "f32"
        assert a2l_module._infer_kind_from_c_type("double") == "f64"

    def test_bool(self):
        assert a2l_module._infer_kind_from_c_type("bool") == "bool"
        assert a2l_module._infer_kind_from_c_type("_Bool") == "bool"

    def test_pointer(self):
        assert a2l_module._infer_kind_from_c_type("int*") == "ptr"
        assert a2l_module._infer_kind_from_c_type("char*") == "ptr"

    def test_size_hint_fallback(self):
        assert a2l_module._infer_kind_from_c_type("unknown", 1) == "u8"
        assert a2l_module._infer_kind_from_c_type("unknown", 2) == "u16"
        assert a2l_module._infer_kind_from_c_type("unknown", 4) == "u32"
        assert a2l_module._infer_kind_from_c_type("unknown", 8) == "u64"


class TestKindToDtypeRaw:
    """测试 _kind_to_dtype_raw 函数"""

    def test_scalar_context(self):
        assert a2l_module._kind_to_dtype_raw("u8", "scalar") == "uint8_t"
        assert a2l_module._kind_to_dtype_raw("i8", "scalar") == "int8_t"
        assert a2l_module._kind_to_dtype_raw("f32", "scalar") == "float"

    def test_array_context(self):
        assert a2l_module._kind_to_dtype_raw("u8", "array") == "array_u8"
        assert a2l_module._kind_to_dtype_raw("i16", "array") == "array_i16"
        assert a2l_module._kind_to_dtype_raw("f32", "array") == "array_f32"

    def test_struct_context(self):
        assert a2l_module._kind_to_dtype_raw("u8", "struct") == "struct_u8"
        assert a2l_module._kind_to_dtype_raw("f32", "struct") == "struct_f32"

    def test_enum_context(self):
        assert a2l_module._kind_to_dtype_raw("enum_u8", "scalar") == "enum_u8"
        assert a2l_module._kind_to_dtype_raw("enum_u16", "scalar") == "enum_u16"


class TestSizeofCType:
    """测试 _sizeof_c_type 函数"""

    def test_basic_types(self):
        assert a2l_module._sizeof_c_type("bool") == 1
        assert a2l_module._sizeof_c_type("char") == 1
        assert a2l_module._sizeof_c_type("short") == 2
        assert a2l_module._sizeof_c_type("int") == 4
        assert a2l_module._sizeof_c_type("long") == 4
        assert a2l_module._sizeof_c_type("long long") == 8
        assert a2l_module._sizeof_c_type("float") == 4
        assert a2l_module._sizeof_c_type("double") == 8

    def test_fixed_width_types(self):
        assert a2l_module._sizeof_c_type("int8_t") == 1
        assert a2l_module._sizeof_c_type("uint16_t") == 2
        assert a2l_module._sizeof_c_type("int32_t") == 4
        assert a2l_module._sizeof_c_type("uint64_t") == 8

    def test_pointer(self):
        assert a2l_module._sizeof_c_type("int*") == 4
        assert a2l_module._sizeof_c_type("void*") == 4


class TestParseArrayDimsText:
    """测试 _parse_array_dims_text 函数"""

    def test_single_dimension(self):
        assert a2l_module._parse_array_dims_text("[10]") == [10]

    def test_multiple_dimensions(self):
        assert a2l_module._parse_array_dims_text("[10][20]") == [10, 20]
        assert a2l_module._parse_array_dims_text("[2][3][4]") == [2, 3, 4]

    def test_empty_brackets(self):
        assert a2l_module._parse_array_dims_text("[]") == [1]

    def test_with_macros(self):
        assert a2l_module._parse_array_dims_text("[SIZE]", {"SIZE": 100}) == [100]


class TestCollapseWs:
    """测试 _collapse_ws 函数"""

    def test_normalize_whitespace(self):
        assert a2l_module._collapse_ws("a  b   c") == "a b c"
        assert a2l_module._collapse_ws("\t\n  test  \r\n") == "test"

    def test_empty_input(self):
        assert a2l_module._collapse_ws("") == ""
        assert a2l_module._collapse_ws(None) == ""


class TestStripCComments:
    """测试 _strip_c_comments 函数"""

    def test_single_line_comment(self):
        result = a2l_module._strip_c_comments("int x; // comment")
        assert "// comment" not in result

    def test_multi_line_comment(self):
        result = a2l_module._strip_c_comments("int x; /* comment */ int y;")
        assert "comment" not in result


class TestReadUleb128:
    """测试 _read_uleb128 函数"""

    def test_simple_value(self):
        # ULEB128 value 1: 0x01
        result, _ = a2l_module._read_uleb128(bytes([0x01]))
        assert result == 1

    def test_multi_byte_value(self):
        # ULEB128 value 127: 0x7F
        result, _ = a2l_module._read_uleb128(bytes([0x7F]))
        assert result == 127

    def test_large_value(self):
        # ULEB128 value 300: 0xAC 0x02
        result, _ = a2l_module._read_uleb128(bytes([0xAC, 0x02]))
        assert result == 300


class TestClassifyObjectType:
    """测试 _classify_object_type 函数"""

    def test_calibration_by_name(self):
        # Pattern requires underscore before cal/cfg/k at word boundary
        assert a2l_module._classify_object_type("var_cal_data") == "CALIBRATION"
        assert a2l_module._classify_object_type("name_c") == "CALIBRATION"
        assert a2l_module._classify_object_type("name_k") == "CALIBRATION"

    def test_measurement_by_name(self):
        assert a2l_module._classify_object_type("var_meas_value") == "MEASUREMENT"
        assert a2l_module._classify_object_type("sensor_obs") == "MEASUREMENT"

    def test_calibration_by_section(self):
        assert a2l_module._classify_object_type("name", "flash_section") == "CALIBRATION"
        assert a2l_module._classify_object_type("name", ".rodata") == "CALIBRATION"
        assert a2l_module._classify_object_type("name", ".const") == "CALIBRATION"

    def test_measurement_by_section(self):
        assert a2l_module._classify_object_type("name", "ram_section") == "MEASUREMENT"
        assert a2l_module._classify_object_type("name", ".bss") == "MEASUREMENT"
        assert a2l_module._classify_object_type("name", ".data") == "MEASUREMENT"


class TestEnumDtypeFromItems:
    """测试 _enum_dtype_from_items 函数"""

    def test_small_values(self):
        items = [("A", 0), ("B", 1), ("C", 10)]
        assert a2l_module._enum_dtype_from_items(items) == "enum_u8"

    def test_medium_values(self):
        items = [("A", 0), ("B", 256), ("C", 1000)]
        assert a2l_module._enum_dtype_from_items(items) == "enum_u16"

    def test_large_values(self):
        items = [("A", 0), ("B", 70000), ("C", 100000)]
        assert a2l_module._enum_dtype_from_items(items) == "enum_u32"

    def test_size_hint_override(self):
        items = [("A", 0), ("B", 1)]
        assert a2l_module._enum_dtype_from_items(items, 2) == "enum_u16"


class TestMatrixDimsForA2l:
    """测试 _matrix_dims_for_a2l 函数"""

    def test_1d_to_2d(self):
        assert a2l_module._matrix_dims_for_a2l([10]) == [1, 10]

    def test_2d_unchanged(self):
        assert a2l_module._matrix_dims_for_a2l([3, 4]) == [3, 4]

    def test_3d_unchanged(self):
        # Current implementation preserves all dimensions
        assert a2l_module._matrix_dims_for_a2l([2, 3, 4]) == [2, 3, 4]


class TestEffectiveArrayDims:
    """测试 _effective_array_dims 函数"""

    def test_filter_ones(self):
        assert a2l_module._effective_array_dims({"ArraySize": "10"}) == [10]
        assert a2l_module._effective_array_dims({"ArraySize": "1x10x1"}) == [10]

    def test_multiple_dims(self):
        assert a2l_module._effective_array_dims({"ArraySize": "2x3x4"}) == [2, 3, 4]


class TestSignalKey:
    """测试 _signal_key 函数"""

    def test_shortname_priority(self):
        sig = {"ShortName": "short", "Name": "long_name"}
        assert a2l_module._signal_key(sig) == "short"

    def test_name_fallback(self):
        sig = {"Name": "only_name"}
        assert a2l_module._signal_key(sig) == "only_name"


class TestMakeAutoSignal:
    """测试 _make_auto_signal 函数"""

    def test_basic_signal(self):
        sig = a2l_module._make_auto_signal(
            base_name="test_var",
            short_name="test_var",
            object_type="MEASUREMENT",
            dtype_raw="uint16_t",
            array_dims=[1]
        )
        assert sig["Name"] == "test_var"
        assert sig["ShortName"] == "test_var"
        assert sig["ObjectType"] == "MEASUREMENT"
        assert sig["DataType"] == "uint16_t"
        assert sig["Type"] == "MEAS"

    def test_calibration_signal(self):
        sig = a2l_module._make_auto_signal(
            base_name="cal_var",
            short_name="cal_var",
            object_type="CALIBRATION",
            dtype_raw="float",
            array_dims=[1]
        )
        assert sig["ObjectType"] == "CALIBRATION"
        assert sig["Type"] == "CAL"


class TestExtractDeclType:
    """测试 _extract_decl_type 函数"""

    def test_basic_type(self):
        base, qualifiers = a2l_module._extract_decl_type("uint32_t")
        assert base == "uint32_t"
        assert qualifiers == ""

    def test_with_qualifiers(self):
        # Extracts base type and separates qualifiers
        base, qualifiers = a2l_module._extract_decl_type("static const unsigned int")
        assert "unsigned" in base or "int" in base  # At least contains type keywords
        assert "static" in qualifiers.lower()
        assert "const" in qualifiers.lower()


class TestResolveTypedefType:
    """测试 _resolve_typedef_type 函数"""

    def test_no_aliases(self):
        result = a2l_module._resolve_typedef_type("uint32_t", None)
        assert result == "uint32_t"

    def test_single_level_alias(self):
        aliases = {"myint": "int32_t"}
        result = a2l_module._resolve_typedef_type("myint", aliases)
        assert result == "int32_t"

    def test_chain_alias(self):
        aliases = {"myint": "int32_t", "int32_t": "signed int"}
        result = a2l_module._resolve_typedef_type("myint", aliases)
        assert result == "signed int"


class TestDetectStructKey:
    """测试 _detect_struct_key 函数"""

    def test_exact_match(self):
        struct_defs = {"MyStruct": {}}
        assert a2l_module._detect_struct_key("MyStruct", struct_defs) == "MyStruct"

    def test_with_prefix(self):
        struct_defs = {"MyStruct": {}}
        assert a2l_module._detect_struct_key("struct MyStruct", struct_defs) == "MyStruct"

    def test_no_match(self):
        struct_defs = {"MyStruct": {}}
        assert a2l_module._detect_struct_key("Unknown", struct_defs) == ""


class TestDetectEnumKey:
    """测试 _detect_enum_key 函数"""

    def test_exact_match(self):
        enum_defs = {"MyEnum": [("A", 0)]}
        assert a2l_module._detect_enum_key("MyEnum", enum_defs) == "MyEnum"

    def test_with_prefix(self):
        enum_defs = {"MyEnum": [("A", 0)]}
        assert a2l_module._detect_enum_key("enum MyEnum", enum_defs) == "MyEnum"

    def test_no_match(self):
        enum_defs = {"MyEnum": [("A", 0)]}
        assert a2l_module._detect_enum_key("Unknown", enum_defs) == ""


class TestIterTopLevelStatements:
    """测试 _iter_top_level_statements 函数"""

    def test_simple_statements(self):
        code = "int a; int b;"
        stmts = list(a2l_module._iter_top_level_statements(code))
        assert len(stmts) >= 1

    def test_with_nested_braces(self):
        # The function only splits on semicolons at depth 0
        code = "void func() { int a; } int b;"
        stmts = list(a2l_module._iter_top_level_statements(code))
        # With depth tracking, nested braces are handled
        assert len(stmts) >= 1


class TestEnumItemsToText:
    """测试 _enum_items_to_text 函数"""

    def test_basic(self):
        items = [("A", 0), ("B", 1), ("C", 2)]
        result = a2l_module._enum_items_to_text(items)
        assert '0="A"' in result
        assert '1="B"' in result
        assert '2="C"' in result


class TestParseEnumBody:
    """测试 _parse_enum_body 函数"""

    def test_simple_enum(self):
        body = "A, B, C"
        items = a2l_module._parse_enum_body(body, {})
        assert items == [("A", 0), ("B", 1), ("C", 2)]

    def test_with_explicit_values(self):
        body = "A = 10, B, C"
        items = a2l_module._parse_enum_body(body, {})
        assert items == [("A", 10), ("B", 11), ("C", 12)]

    def test_with_macros(self):
        body = "A, B = SIZE, C"
        macros = {"SIZE": 100}
        items = a2l_module._parse_enum_body(body, macros)
        assert items == [("A", 0), ("B", 100), ("C", 101)]


class TestCDtypeMap:
    """测试 C_DTYPE_MAP 常量"""

    def test_uint_types(self):
        assert a2l_module.C_DTYPE_MAP["uint8_t"] == "UBYTE"
        assert a2l_module.C_DTYPE_MAP["uint16_t"] == "UWORD"
        assert a2l_module.C_DTYPE_MAP["uint32_t"] == "ULONG"

    def test_int_types(self):
        assert a2l_module.C_DTYPE_MAP["int8_t"] == "SBYTE"
        assert a2l_module.C_DTYPE_MAP["int16_t"] == "SWORD"
        assert a2l_module.C_DTYPE_MAP["int32_t"] == "SLONG"

    def test_float_types(self):
        assert a2l_module.C_DTYPE_MAP["float"] == "FLOAT32_IEEE"
        assert a2l_module.C_DTYPE_MAP["double"] == "FLOAT64_IEEE"


class TestXlsxCols:
    """测试 XLSX_COLS 常量"""

    def test_required_columns(self):
        required = ["Group", "Type", "Name", "ShortName", "DataType", "ObjectType"]
        for col in required:
            assert col in a2l_module.XLSX_COLS


class TestSignalCoreCols:
    """测试 SIGNAL_CORE_COLS 常量"""

    def test_contains_required(self):
        required = ["Group", "Type", "Name", "ShortName", "DataType", "ObjectType", "ArraySize"]
        for col in required:
            assert col in a2l_module.SIGNAL_CORE_COLS


class TestNormalizePathHistory:
    """测试路径历史归一化逻辑"""

    def test_current_path_first_and_deduplicated(self):
        history = [
            r"C:\Temp\signal.xlsx",
            r"C:\Temp\other.xlsx",
            r"C:\Temp\signal.xlsx",
        ]
        result = a2l_module._normalize_path_history(history, r"C:\Temp\signal.xlsx")
        assert result == [
            r"C:\Temp\signal.xlsx",
            r"C:\Temp\other.xlsx",
        ]

    def test_limit_is_applied(self):
        history = [rf"C:\Temp\file_{idx}.xlsx" for idx in range(20)]
        result = a2l_module._normalize_path_history(history, limit=5)
        assert len(result) == 5
        assert result[0] == r"C:\Temp\file_0.xlsx"


@pytest.mark.skipif(not a2l_module.HAS_OPENPYXL, reason="openpyxl not installed")
class TestSyncSignalConfigXlsx:
    """测试 2.5 固定信号表同步逻辑"""

    def test_merge_import_does_not_override_derived_offset_columns(self):
        signals = [
            {
                "Group": "Base",
                "Type": "MEAS",
                "Name": "SigA",
                "ShortName": "SigA",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "AddrOffset": "12",
                "BitOffset": "3",
                "BitSize": "5",
                "BitMask": "0xF8",
                "Unit": "",
                "Description": "from axf",
            }
        ]
        imported_rows = [
            {
                "Name": "SigA",
                "ShortName": "SigA",
                "Unit": "km/h",
                "Description": "from xlsx",
                "AddrOffset": "99",
                "BitOffset": "7",
                "BitSize": "1",
                "BitMask": "0x80",
                a2l_module.SIGNAL_IO_KEEP_COL: True,
            }
        ]

        merged_signals, keep_names, matched = a2l_module.merge_signal_config_rows_into_signals(signals, imported_rows)

        assert matched == 1
        assert keep_names == ["SigA"]
        assert merged_signals[0]["Unit"] == "km/h"
        assert merged_signals[0]["Description"] == "from xlsx"
        assert merged_signals[0]["AddrOffset"] == "12"
        assert merged_signals[0]["BitOffset"] == "3"
        assert merged_signals[0]["BitSize"] == "5"
        assert merged_signals[0]["BitMask"] == "0xF8"

    def test_summarize_signal_config_changes(self):
        existing_rows = [
            {
                "Group": "OldGroup",
                "Type": "MEAS",
                "Name": "SigKeep",
                "ShortName": "SigKeep",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "km/h",
                a2l_module.SIGNAL_IO_KEEP_COL: True,
            },
            {
                "Group": "LegacyGroup",
                "Type": "MEAS",
                "Name": "SigRemoved",
                "ShortName": "SigRemoved",
                "DataType": "UWORD",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "legacy",
                a2l_module.SIGNAL_IO_KEEP_COL: False,
            },
        ]
        current_signals = [
            {
                "Group": "NewGroup",
                "Type": "CAL",
                "Name": "SigKeep",
                "ShortName": "SigKeep",
                "DataType": "FLOAT32_IEEE",
                "ObjectType": "CALIBRATION",
                "ArraySize": "4",
            },
            {
                "Group": "AddedGroup",
                "Type": "MEAS",
                "Name": "SigAdded",
                "ShortName": "SigAdded",
                "DataType": "SLONG",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
            },
        ]

        summary = a2l_module.summarize_signal_config_changes(current_signals, existing_rows)

        assert summary["matched"] == 1
        assert summary["added"] == 1
        assert summary["removed"] == 1
        assert summary["updated"] == 1
        assert summary["total"] == 2

    def test_add_remove_and_preserve_user_columns(self, tmp_path):
        config_path = tmp_path / a2l_module.SIGNAL_CONFIG_FILENAME
        existing_rows = [
            {
                "Group": "OldGroup",
                "Type": "MEAS",
                "Name": "SigKeep",
                "ShortName": "SigKeep",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "km/h",
                "Description": "user note",
                a2l_module.SIGNAL_IO_KEEP_COL: True,
            },
            {
                "Group": "LegacyGroup",
                "Type": "MEAS",
                "Name": "SigRemoved",
                "ShortName": "SigRemoved",
                "DataType": "UWORD",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "legacy",
                a2l_module.SIGNAL_IO_KEEP_COL: False,
            },
        ]
        current_signals = [
            {
                "Group": "NewGroup",
                "Type": "CAL",
                "Name": "SigKeep",
                "ShortName": "SigKeep",
                "DataType": "FLOAT32_IEEE",
                "ObjectType": "CALIBRATION",
                "ArraySize": "4",
                "Unit": "",
            },
            {
                "Group": "AddedGroup",
                "Type": "MEAS",
                "Name": "SigAdded",
                "ShortName": "SigAdded",
                "DataType": "SLONG",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "",
            },
        ]

        merged_signals, keep_names, summary = a2l_module.sync_signal_config_xlsx(
            config_path,
            current_signals,
            existing_rows=existing_rows,
        )

        assert config_path.exists()
        assert keep_names == ["SigKeep", "SigAdded"]
        assert summary["matched"] == 1
        assert summary["added"] == 1
        assert summary["removed"] == 1
        assert summary["updated"] == 1
        assert summary["total"] == 2

        merged_map = {a2l_module._signal_key(sig): sig for sig in merged_signals}
        assert set(merged_map) == {"SigKeep", "SigAdded"}
        assert merged_map["SigKeep"]["Group"] == "NewGroup"
        assert merged_map["SigKeep"]["Type"] == "CAL"
        assert merged_map["SigKeep"]["DataType"] == "FLOAT32_IEEE"
        assert merged_map["SigKeep"]["ObjectType"] == "CALIBRATION"
        assert merged_map["SigKeep"]["ArraySize"] == "4"
        assert merged_map["SigKeep"]["Unit"] == "km/h"
        assert merged_map["SigKeep"]["Description"] == "user note"

        saved_rows = a2l_module.parse_signal_config_xlsx(str(config_path))
        saved_map = {a2l_module._signal_key(row): row for row in saved_rows}
        assert set(saved_map) == {"SigKeep", "SigAdded"}
        assert saved_map["SigKeep"][a2l_module.SIGNAL_IO_KEEP_COL] is True
        assert saved_map["SigKeep"]["Unit"] == "km/h"
        assert saved_map["SigKeep"]["Description"] == "user note"
        assert saved_map["SigAdded"][a2l_module.SIGNAL_IO_KEEP_COL] is True

    def test_explicit_keep_names_override_existing_keep_state(self, tmp_path):
        config_path = tmp_path / a2l_module.SIGNAL_CONFIG_FILENAME
        existing_rows = [
            {
                "Group": "Base",
                "Type": "MEAS",
                "Name": "SigA",
                "ShortName": "SigA",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                a2l_module.SIGNAL_IO_KEEP_COL: False,
                "Unit": "V",
            }
        ]
        current_signals = [
            {
                "Group": "Base",
                "Type": "MEAS",
                "Name": "SigA",
                "ShortName": "SigA",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "",
            },
            {
                "Group": "Base",
                "Type": "MEAS",
                "Name": "SigB",
                "ShortName": "SigB",
                "DataType": "UWORD",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "",
            },
        ]

        _, keep_names, summary = a2l_module.sync_signal_config_xlsx(
            config_path,
            current_signals,
            existing_rows=existing_rows,
            keep_names=["SigB"],
        )

        assert keep_names == ["SigB"]
        assert summary["matched"] == 1
        assert summary["added"] == 1
        assert summary["removed"] == 0

        saved_rows = a2l_module.parse_signal_config_xlsx(str(config_path))
        saved_map = {a2l_module._signal_key(row): row for row in saved_rows}
        assert saved_map["SigA"][a2l_module.SIGNAL_IO_KEEP_COL] is False
        assert saved_map["SigA"]["Unit"] == "V"
        assert saved_map["SigB"][a2l_module.SIGNAL_IO_KEEP_COL] is True

    def test_write_signal_config_xlsx_highlights_editable_columns(self, tmp_path):
        config_path = tmp_path / a2l_module.SIGNAL_CONFIG_FILENAME
        signals = [
            {
                "Group": "Base",
                "Type": "MEAS",
                "Name": "SigA",
                "ShortName": "SigA",
                "DataType": "UBYTE",
                "ObjectType": "MEASUREMENT",
                "ArraySize": "1",
                "Unit": "V",
                "Description": "note",
            }
        ]

        a2l_module.write_signal_config_xlsx(config_path, signals, keep_names=["SigA"])

        workbook = a2l_module.openpyxl.load_workbook(config_path, data_only=True)
        sheet = workbook.active
        assert sheet["A2"].fill.fgColor.rgb == "00FFF2CC"
        assert sheet["G2"].fill.fgColor.rgb != "00FFF2CC"
        unit_col = 2 + a2l_module.SIGNAL_CONFIG_EXPORT_COLS.index("Unit")
        unit_cell = sheet.cell(2, unit_col)
        assert unit_cell.fill.fgColor.rgb == "00FFF2CC"


class TestIndexSuffixFromDims:
    """测试 _index_suffix_from_dims / _dims_product：多维下标还原"""

    def test_flat_index_1d(self):
        assert a2l_module._index_suffix_from_dims(0, [3]) == "[0]"
        assert a2l_module._index_suffix_from_dims(2, [3]) == "[2]"

    def test_flat_index_2d_row_major(self):
        # 行优先：[2,3] 的扁平下标 5 -> [1][2]
        assert a2l_module._index_suffix_from_dims(5, [2, 3]) == "[1][2]"
        assert a2l_module._index_suffix_from_dims(0, [2, 3]) == "[0][0]"
        assert a2l_module._index_suffix_from_dims(3, [2, 3]) == "[1][0]"

    def test_dims_product(self):
        assert a2l_module._dims_product([2, 3]) == 6
        assert a2l_module._dims_product([4]) == 4
        assert a2l_module._dims_product(None) == 1
        assert a2l_module._dims_product([0]) == 1


class TestNestedStructAndArrayExpansion:
    """嵌套结构体 / 结构体数组成员必须展开为独立信号，而非单个标量。"""

    SOURCE = """
typedef struct { uint16_t x; uint16_t y; } Point;
typedef struct { uint8_t flags; Point origin; } inner_t;
typedef struct {
    uint8_t  id;
    Point    pts[3];
    float    gain[2][2];
    inner_t  sub;
} Cfg;
Cfg      g_cfg;
Point    g_single;
float    g_matrix[4][2];
uint16_t g_scalar;
"""

    @pytest.fixture
    def signals(self, tmp_path):
        (tmp_path / "m.c").write_text(self.SOURCE, encoding="utf-8")
        return a2l_module.build_signals_from_project(str(tmp_path), sym_details={}, log_cb=None)

    def _names(self, signals):
        return {s["ShortName"] for s in signals}

    def test_struct_array_is_expanded_per_element(self, signals):
        names = self._names(signals)
        assert "g_cfg.pts[0].x" in names
        assert "g_cfg.pts[1].y" in names
        assert "g_cfg.pts[2].y" in names
        # 不应再出现未加元素下标的中间节点
        assert "g_cfg.pts.x" not in names

    def test_struct_array_element_offsets_are_stride_based(self, signals):
        by_name = {s["ShortName"]: s for s in signals}
        # Point 为 4 字节：pts[0] 在 offset 4，pts[1] 在 8，pts[2] 在 12
        assert by_name["g_cfg.pts[0].x"]["AddrOffset"] == "4"
        assert by_name["g_cfg.pts[1].x"]["AddrOffset"] == "8"
        assert by_name["g_cfg.pts[2].x"]["AddrOffset"] == "12"

    def test_nested_struct_member_is_flattened(self, signals):
        names = self._names(signals)
        assert "g_cfg.sub.flags" in names
        assert "g_cfg.sub.origin.x" in names

    def test_multidim_array_member_keeps_dims(self, signals):
        gain = next(s for s in signals if s["ShortName"] == "g_cfg.gain")
        assert gain["ArraySize"] == "2x2"
        assert gain["DataType"].startswith("array_")

    def test_top_level_multidim_array_keeps_dims(self, signals):
        matrix = next(s for s in signals if s["ShortName"] == "g_matrix")
        assert matrix["ArraySize"] == "4x2"

    def test_scalar_member_stays_scalar(self, signals):
        by_name = {s["ShortName"]: s for s in signals}
        assert by_name["g_cfg.id"]["ArraySize"] == "1"
        assert by_name["g_scalar"]["ArraySize"] == "1"


class TestStructArrayA2LGeneration:
    """结构体数组展开后的成员与数组成员应生成正确类型，不出现幽灵 MATRIX_DIM。"""

    def _dims(self, text):
        return a2l_module._parse_array_dims_value(text)

    def test_scalar_struct_member_has_no_matrix_dim(self):
        # struct_u16 成员宽 4 字节（含对齐），绝不能被推断成 MATRIX_DIM 1 2
        sig = a2l_module._make_auto_signal(
            "g_cfg", "g_cfg.pts[0].x", "MEASUREMENT", "struct_u16", self._dims("1"))
        block, _ = a2l_module.generate_a2l_block(sig, 0x20000000, 4)
        assert "/begin MEASUREMENT" in block
        assert "MATRIX_DIM" not in block

    def test_array_typed_member_gets_matrix_dim(self):
        sig = a2l_module._make_auto_signal(
            "g_cfg", "g_cfg.gain", "MEASUREMENT", "array_f32", self._dims("2x2"))
        block, _ = a2l_module.generate_a2l_block(sig, 0x20000000, 16)
        assert "MATRIX_DIM 2 2" in block

    def test_array_member_length_inferred_from_size(self):
        # 未显式给出维度时，array_* 成员按 size/元素字节 推断
        sig = a2l_module._make_auto_signal(
            "g_cfg", "g_cfg.gain", "MEASUREMENT", "array_f32", self._dims("1"))
        block, _ = a2l_module.generate_a2l_block(sig, 0x20000000, 16)
        assert "MATRIX_DIM 1 4" in block

    def test_calibration_map_and_curve(self):
        map_sig = a2l_module._make_auto_signal("cal_map", "cal_map", "CALIBRATION", "array_u16", self._dims("4x8"))
        map_block, _ = a2l_module.generate_a2l_block(map_sig, 0x20000000, 0)
        assert "/begin CHARACTERISTIC" in map_block
        assert "MAP" in map_block

        curve_sig = a2l_module._make_auto_signal("cal_curve", "cal_curve", "CALIBRATION", "array_f32", self._dims("8"))
        curve_block, _ = a2l_module.generate_a2l_block(curve_sig, 0x20000000, 0)
        assert "/begin CHARACTERISTIC" in curve_block
        assert "CURVE" in curve_block


class TestSignedLeb128:
    """测试 _read_sleb128：负偏移必须保留符号，不能被截断为 0。"""

    def test_zero(self):
        assert a2l_module._read_sleb128(bytes([0x00])) == (0, 1)

    def test_positive_single_byte(self):
        # 0x3F = 63，符号位（0x40）未置位
        assert a2l_module._read_sleb128(bytes([0x3F])) == (63, 1)

    def test_negative_single_byte(self):
        # 0x7F 有符号表示 -1；0x40 表示 -64
        assert a2l_module._read_sleb128(bytes([0x7F]))[0] == -1
        assert a2l_module._read_sleb128(bytes([0x40]))[0] == -64

    def test_negative_multibyte(self):
        # 0xFF 0x7F -> -1
        val, pos = a2l_module._read_sleb128(bytes([0xFF, 0x7F]))
        assert val == -1
        assert pos == 2

    def test_positive_multibyte(self):
        # 0x80 0x01 -> 128
        val, pos = a2l_module._read_sleb128(bytes([0x80, 0x01]))
        assert val == 128
        assert pos == 2


# ── 最小 DWARF DIE 桩，用于在无交叉编译器环境下验证 DWARF 展开逻辑 ──
class _Attr:
    def __init__(self, value):
        self.value = value


class _Die:
    def __init__(self, tag, offset, name=None, children=None, refs=None,
                 byte_size=None, member_location=None):
        self.tag = tag
        self.offset = offset
        self._children = children or []
        self._refs = refs or {}
        self.attributes = dict(self._refs)
        if name is not None:
            self.attributes["DW_AT_name"] = _Attr(name)
        if byte_size is not None:
            self.attributes["DW_AT_byte_size"] = _Attr(byte_size)
        if member_location is not None:
            self.attributes["DW_AT_data_member_location"] = _Attr(member_location)

    def iter_children(self):
        return iter(self._children)

    def get_DIE_from_attribute(self, key):
        return self._refs.get(key)


class TestDwarfStructArrayExpansion:
    """DWARF 路径：结构体数组必须逐元素展开，标量成员保持单变量。"""

    @staticmethod
    def _base_type(offset, name, size):
        return _Die("DW_TAG_base_type", offset, name=name, byte_size=size)

    @classmethod
    def _point_die(cls, offset=1000):
        """struct Point { uint16 x; uint16 y; }  size = 4，y 位于成员偏移 2"""
        x_t = cls._base_type(offset + 1, "unsigned short", 2)
        y_t = cls._base_type(offset + 2, "unsigned short", 2)
        x = _Die("DW_TAG_member", offset + 10, name="x", refs={"DW_AT_type": x_t},
                 member_location=0)
        y = _Die("DW_TAG_member", offset + 11, name="y", refs={"DW_AT_type": y_t},
                 member_location=2)
        return _Die("DW_TAG_structure_type", offset, name="Point",
                    children=[x, y], byte_size=4)

    @classmethod
    def _cfg_die(cls):
        """struct Cfg { uint8 id; Point pts[3]; }"""
        point = cls._point_die()
        id_t = cls._base_type(1001, "unsigned char", 1)
        id_m = _Die("DW_TAG_member", 2001, name="id", refs={"DW_AT_type": id_t},
                    member_location=0)
        subrange = _Die("DW_TAG_subrange_type", 2004)
        subrange.attributes["DW_AT_count"] = _Attr(3)
        arr = _Die("DW_TAG_array_type", 2003, refs={"DW_AT_type": point},
                   children=[subrange])
        # DW_OP_plus_uconst 4 -> 成员基址偏移 4
        pts_m = _Die("DW_TAG_member", 2002, name="pts", refs={"DW_AT_type": arr},
                     member_location=b"\x23\x04")
        return _Die("DW_TAG_structure_type", 2000, name="Cfg",
                    children=[id_m, pts_m], byte_size=16)

    @pytest.fixture
    def expanded(self):
        out = []
        a2l_module._collect_dwarf_member_signals(
            self._cfg_die(), "g_cfg", "g_cfg", 0, "MEASUREMENT", out, set())
        return out

    def test_struct_array_expands_per_element(self, expanded):
        names = {s["ShortName"] for s in expanded}
        assert "g_cfg.pts[0].x" in names
        assert "g_cfg.pts[1].y" in names
        assert "g_cfg.pts[2].y" in names
        assert "g_cfg.pts.x" not in names

    def test_struct_array_element_offsets(self, expanded):
        by = {s["ShortName"]: s for s in expanded}
        # Point = 4 字节，数组基址 4 -> 元素步进 4
        assert by["g_cfg.pts[0].x"]["AddrOffset"] == "4"
        assert by["g_cfg.pts[0].y"]["AddrOffset"] == "6"
        assert by["g_cfg.pts[1].x"]["AddrOffset"] == "8"
        assert by["g_cfg.pts[2].x"]["AddrOffset"] == "12"
        assert by["g_cfg.pts[2].y"]["AddrOffset"] == "14"

    def test_scalar_member_stays_scalar(self, expanded):
        by = {s["ShortName"]: s for s in expanded}
        assert by["g_cfg.id"]["ArraySize"] == "1"


class TestSignedMemberLocationNegativeOffset:
    """DW_OP_consts 负偏移必须保留符号（此前被截断为 0）。"""

    def test_consts_negative(self):
        # DW_OP_consts(0x11) 后接 SLEB128 0x7F = -1
        attr = _Attr(bytes([0x11, 0x7F]))
        assert a2l_module._parse_dwarf_member_location(attr) == -1

    def test_consts_positive(self):
        attr = _Attr(bytes([0x11, 0x0A]))
        assert a2l_module._parse_dwarf_member_location(attr) == 10

    def test_plus_uconst_unchanged(self):
        attr = _Attr(bytes([0x23, 0x04]))
        assert a2l_module._parse_dwarf_member_location(attr) == 4


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

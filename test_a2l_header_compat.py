import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import A2L_Update_Tool_main as a2l_module  # 2026-10: 主模块已由 A2L_Update_V1_1_ww_20260406 重命名


WORKSPACE_DIR = os.path.dirname(os.path.abspath(__file__))

# The two header fixtures live under data/ so that they are tracked by git.
# They used to sit in the workspace root, where .gitignore excludes them,
# which made this module fail on a fresh clone.
LEGACY_HEADER = "data/A2lHeader.a2l"
PORTABLE_HEADER = "data/A2lHeader_CANape_Compatible.a2l"


def _read_text(name: str) -> str:
    path = os.path.join(WORKSPACE_DIR, name)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_legacy_header_reports_canape_incompatible_xcp_tokens():
    findings = a2l_module.validate_canape_header_compatibility(_read_text(LEGACY_HEADER))
    assert any("ECU_ACCESS_DONT_CARE" in item for item in findings)
    assert any("XCP_READ_ACCESS_DONT_CARE" in item for item in findings)
    assert any("XCP_WRITE_ACCESS_DONT_CARE" in item for item in findings)
    assert any("Duplicate MEMORY_SEGMENT identifier" in item for item in findings)
    assert any("Duplicate XCP SEGMENT number" in item for item in findings)
    assert any("PAG MAX_SEGMENTS mismatch" in item for item in findings)


def test_portable_header_passes_static_compatibility_check():
    findings = a2l_module.validate_canape_header_compatibility(
        _read_text(PORTABLE_HEADER)
    )
    assert findings == []


def test_normalize_legacy_canape_header_tokens_rewrites_incompatible_page_access_names():
    text = _read_text(LEGACY_HEADER)
    normalized, replacements = a2l_module.normalize_legacy_canape_header_tokens(text)
    assert replacements
    assert "ECU_ACCESS_DONT_CARE" not in normalized
    assert "XCP_READ_ACCESS_DONT_CARE" not in normalized
    assert "XCP_WRITE_ACCESS_DONT_CARE" not in normalized
    assert "ECU_ACCESS_ALLOWED_DONT_CARE" in normalized
    assert "XCP_READ_ACCESS_ALLOWED_DONT_CARE" in normalized
    assert "XCP_WRITE_ACCESS_ALLOWED_DONT_CARE" in normalized


def test_normalized_legacy_header_passes_static_compatibility_check():
    text = _read_text(LEGACY_HEADER)
    normalized, _ = a2l_module.normalize_legacy_canape_header_tokens(text)
    findings = a2l_module.validate_canape_header_compatibility(normalized)
    assert all("Legacy XCP PAGE token" not in item for item in findings)


def test_repair_a2l_header_for_tool_compatibility_fixes_structure_issues():
    repaired, repair_log = a2l_module.repair_a2l_header_for_tool_compatibility(_read_text(LEGACY_HEADER))
    assert repair_log
    findings = a2l_module.validate_canape_header_compatibility(repaired)
    assert findings == []

    mem_ids = re.findall(r'^\s*/begin MEMORY_SEGMENT\s+([A-Za-z_][A-Za-z0-9_]*)', repaired, re.M)
    assert len(mem_ids) == len(set(mem_ids))

    seg_nums = [int(item, 0) for item in re.findall(r'/begin IF_DATA XCP\s*[\r\n]+\s*/begin SEGMENT\s*[\r\n]+\s*(0x[0-9A-Fa-f]+|\d+)', repaired)]
    assert len(seg_nums) == len(set(seg_nums))


def test_saved_portable_header_matches_generator_output():
    expected = a2l_module.build_minimal_a2l_header()
    actual = _read_text(PORTABLE_HEADER)
    assert actual == expected
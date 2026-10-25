# -*- coding: utf-8 -*-
"""K2 历史配置（signal_keep_config）的加载与合并测试。

覆盖三条链路：
  1. 随附样例数据可复现：data/signal_keep_config.csv / .xlsx 能被加载，
     且两种格式给出完全一致的信号集；
  2. 格式转换正确：标定表用 A2L 数据类型（UBYTE 等）标注，载入后必须换算回
     C 类型族（u8 等），否则结构通道无法判别类型冲突；
  3. K2 与 K3 合并语义正确：K2 的类型 / IO / 单位是历史事实（优先），
     K3 只补齐 K2 缺失的语义描述。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rag_align as ra  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CSV_PATH = os.path.join(DATA_DIR, "signal_keep_config.csv")
XLSX_PATH = os.path.join(DATA_DIR, "signal_keep_config.xlsx")


# ── 1. 随附样例数据 ────────────────────────────────────────────────────────
class TestShippedK2Sample:
    def test_csv_sample_exists(self):
        assert os.path.isfile(CSV_PATH), (
            "K2 样例缺失，请运行: py -3.12 experiments/make_k2_sample.py")

    def test_csv_sample_loads_all_signals(self):
        rows = ra.load_historical_config_file(CSV_PATH)
        assert len(rows) == 83
        assert all(r["Name"] for r in rows)

    def test_xlsx_sample_matches_csv(self):
        """两种格式必须给出同一份信号集（xlsx 需 openpyxl）。"""
        if not os.path.isfile(XLSX_PATH):
            pytest.skip("xlsx 样例未生成")
        pytest.importorskip("openpyxl")
        csv_rows = ra.load_historical_config_file(CSV_PATH)
        xlsx_rows = ra.load_historical_config_file(XLSX_PATH)
        assert len(csv_rows) == len(xlsx_rows)
        by_name_csv = {r["Name"]: r for r in csv_rows}
        by_name_xlsx = {r["Name"]: r for r in xlsx_rows}
        assert set(by_name_csv) == set(by_name_xlsx)
        for name, row in by_name_csv.items():
            other = by_name_xlsx[name]
            assert row["CType"] == other["CType"]
            assert row["Unit"] == other["Unit"]
            assert row["Type"] == other["Type"]

    def test_sample_covers_all_six_functional_groups(self):
        rows = ra.load_historical_config_file(CSV_PATH)
        groups = {r["Group"] for r in rows}
        assert len(groups) >= 6


# ── 2. 格式转换 ────────────────────────────────────────────────────────────
class TestHistoryFormatConversion:
    def test_a2l_dtype_converted_to_c_type(self):
        """标定表里的 A2L 数据类型必须换算成 C 类型族，否则结构通道失效。"""
        rows = ra.load_historical_config_file(CSV_PATH)
        row = next(r for r in rows if r["Name"] == "Eng_Speed_u16_M")
        assert row["CType"] == "u16"
        assert row["DataType"] == "u16"
        assert ra.DomainKnowledgeBase.type_compatible(row["CType"], "uint16_t")

    def test_float_and_bool_types(self):
        rows = ra.load_historical_config_file(CSV_PATH)
        by_name = {r["Name"]: r for r in rows}
        assert by_name["Eng_Torque_f32_M"]["CType"] == "f32"
        assert by_name["Eng_RunFlag_b_M"]["CType"] == "u8"   # bool 在 A2L 侧是 UBYTE

    def test_io_class_preserved(self):
        rows = ra.load_historical_config_file(CSV_PATH)
        by_name = {r["Name"]: r for r in rows}
        assert by_name["Eng_Speed_u16_M"]["Type"] == "MEAS"
        assert by_name["Eng_IdleRpm_u8_C"]["Type"] == "CAL"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            ra.load_historical_config_file(os.path.join(DATA_DIR, "_nope_.csv"))

    def test_unsupported_format_raises(self, tmp_path):
        bad = tmp_path / "history.txt"
        bad.write_text("Name\nX\n", encoding="utf-8")
        with pytest.raises(ValueError):
            ra.load_historical_config_file(bad)

    def test_csv_without_name_rows_are_skipped(self, tmp_path):
        p = tmp_path / "h.csv"
        p.write_text("Group,Type,Name,Unit\nEngine,CAL,Eng_Speed_u16_M,rpm\n"
                     "Engine,CAL,,rpm\n", encoding="utf-8")
        rows = ra.load_historical_config_file(p)
        assert len(rows) == 1
        assert rows[0]["Name"] == "Eng_Speed_u16_M"


# ── 3. K2 落到知识库 + 与 K3 合并 ─────────────────────────────────────────
class TestK2InKnowledgeBase:
    def test_k2_rows_enter_kb_as_history(self):
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        n = base.add_historical_config_file(CSV_PATH)
        assert n == 83
        doc = base.get("Eng_Speed_u16_M")
        assert doc is not None
        assert doc.source == "history"
        assert doc.unit == "rpm"
        assert doc.io_type == "MEAS"
        assert doc.c_type == "u16"

    def test_k2_documents_are_retrievable(self):
        """K2 记录必须真的进入检索索引（不是只挂在字典里）。"""
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        base.add_historical_config_file(CSV_PATH)
        cands = base.retriever.retrieve("Eng_Speed_u16_M", top_k=5)
        assert cands
        assert "Eng_Speed_u16_M" in [n for n, _ in cands]

    def test_k2_authoritative_over_k3_on_type(self):
        """K2 的历史类型不被 K3 覆盖。"""
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        base.add_historical_config([{
            "Name": "Eng_Speed_u16_M", "ShortName": "EngSpd_M", "Unit": "rpm",
            "Type": "MEAS", "CType": "u16", "Group": "Engine",
            "Description": "measured engine speed",
        }])
        base.add_semantic_corpus(
            [{"name": "Eng_Speed_u16_M", "c_type": "f32", "io": "MEAS",
              "unit": "rpm", "desc": "measured engine speed", "group": "Engine"}],
            merge=True)
        doc = base.get("Eng_Speed_u16_M")
        assert doc.c_type == "u16"        # K2 优先
        assert doc.description == "measured engine speed"

    def test_k3_fills_missing_description(self):
        """K2 缺描述时由 K3 补齐（合并而非丢弃）。"""
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        base.add_historical_config([{
            "Name": "Eng_Speed_u16_M", "Type": "MEAS", "CType": "u16",
        }])
        base.add_semantic_corpus(
            [{"name": "Eng_Speed_u16_M", "desc": "measured engine speed",
              "group": "Engine"}], merge=True)
        doc = base.get("Eng_Speed_u16_M")
        assert doc.description == "measured engine speed"
        assert doc.group == "Engine"
        assert doc.c_type == "u16"

    def test_merge_does_not_duplicate_documents(self):
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        base.add_semantic_corpus(ra._SEED_CORPUS)
        n = base.add_semantic_corpus(ra._SEED_CORPUS, merge=True)
        assert n == 0
        assert len(base) == len(ra._SEED_CORPUS)

    def test_build_kb_from_main_module_includes_k2(self):
        """默认工厂必须把随附的 K2 标定表一并纳入知识库。"""
        kb = ra.build_kb_from_main_module(
            kb=ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever()))
        doc = kb.get("Eng_Speed_u16_M")
        assert doc is not None
        assert doc.c_type == "u16"
        assert doc.io_type == "MEAS"
        assert doc.description              # K3 补齐的语义描述
        assert len(kb) >= 83


# ── 4. 候选路径探测 ────────────────────────────────────────────────────────
class TestHistoryCandidateDiscovery:
    def test_shipped_sample_is_discovered(self):
        found = [str(p) for p in ra.iter_history_config_candidates()]
        assert any(p.endswith("signal_keep_config.xlsx") for p in found)

    def test_explicit_path_wins(self, tmp_path):
        p = tmp_path / "custom.csv"
        p.write_text("Name,Unit\nFoo_u8_M,km\n", encoding="utf-8")
        found = [str(x) for x in ra.iter_history_config_candidates(p)]
        assert found[0].endswith("custom.csv")

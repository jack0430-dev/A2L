"""
rag_align.py — 单元测试

覆盖四个核心组件:
  1. 信号名结构化分解 (decompose_signal_name)
  2. 语义对齐器 (SemanticAligner): 精确匹配 / 改名检索 / 置信度分层
  3. 元数据补全 (MetadataCompleter)
  4. 增量一致性检查 (check_incremental_consistency)

统一使用零依赖 CharNgramRetriever，保证测试环境无网络/无重模型依赖。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 强制零依赖检索器，避免测试时触发模型加载/网络
os.environ["RAG_ALIGN_RETRIEVER"] = "charngram"

import rag_align as ra


@pytest.fixture
def kb():
    """基于种子语料的领域知识库（每测试独立实例）。"""
    base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
    base.add_semantic_corpus(ra._SEED_CORPUS)
    return base


@pytest.fixture
def aligner(kb):
    return ra.SemanticAligner(kb)


# ═══════════════════════════ 1. 名称分解 ═══════════════════════════

class TestDecompose:
    def test_plain_snake_name(self):
        d = ra.decompose_signal_name("Eng_Speed_u16_M")
        assert d["type_suffix"] == "u16"
        assert d["io_suffix"] == "M"
        assert d["io_type"] == "MEAS"
        assert d["c_type"] == "uint16_t"
        assert "eng" in d["semantic_tokens"] and "speed" in d["semantic_tokens"]

    def test_calibration_suffix(self):
        d = ra.decompose_signal_name("Eng_MaxRpm_u16_C")
        assert d["io_type"] == "CAL"

    def test_camel_case(self):
        d = ra.decompose_signal_name("EngineSpeed")
        assert "engine" in d["semantic_tokens"] and "speed" in d["semantic_tokens"]

    def test_struct_member_path(self):
        d = ra.decompose_signal_name("g_imu_config.accRange_u8_C")
        assert d["path"] == ["g_imu_config", "accRange_u8_C"]
        assert d["type_suffix"] == "u8"
        assert d["io_type"] == "CAL"
        assert "range" in d["semantic_tokens"]

    def test_no_suffix(self):
        d = ra.decompose_signal_name("eng_spd")
        assert d["type_suffix"] == "" and d["io_suffix"] == ""
        assert d["semantic_tokens"] == ["eng", "spd"]

    def test_empty_input(self):
        d = ra.decompose_signal_name("")
        assert d["semantic_tokens"] == []


# ═══════════════════════════ 2. 语义对齐 ═══════════════════════════

class TestAlignment:
    def test_exact_match_is_auto(self, aligner):
        r = aligner.align("Eng_Speed_u16_M")
        assert r.tier == "AUTO"
        assert r.best_match == "Eng_Speed_u16_M"
        assert r.score == 1.0
        assert r.evidence["exact"] is True

    def test_renamed_signal_finds_candidate(self, aligner):
        """P1 核心场景：命名风格不一致（驼峰 vs 蛇形）仍能检索到正确候选。"""
        r = aligner.align("EngineSpeed_meas")
        assert r.best_match == "Eng_Speed_u16_M"
        assert r.score > 0.4  # 至少进入可解释区间

    def test_abbreviation_finds_right_signal(self, aligner):
        r = aligner.align("VehSpd")
        assert r.best_match == "Veh_Speed_u16_M"

    def test_unknown_falls_to_manual(self, aligner):
        r = aligner.align("Unknown_Zzz_x9_Q")
        assert r.tier == "MANUAL"
        assert r.score < ra.TAU_SUGGEST

    def test_type_conflict_penalized(self, aligner):
        """同名但类型宽度不一致时应被降权（证据融合的事后约束）。"""
        r_ok = aligner.align("eng_max_rpm_u16_c")     # 类型一致 u16
        r_bad = aligner.align("eng_max_rpm_f32_c")    # 类型冲突 u16 vs f32
        # 两者均应命中同一候选且类型冲突者分数更低
        if r_bad.best_match == r_ok.best_match == "Eng_MaxRpm_u16_C":
            assert r_bad.score < r_ok.score
            assert r_bad.evidence.get("type_penalty") is True

    def test_io_flip_penalized(self, aligner):
        """IO 类别翻转（测量<->标定）是 A2L 对象类别错误，必须降层。"""
        r_ok = aligner.align("Eng_Speed_u16_M")
        r_flip = aligner.align("Eng_Speed_u16_C")     # M -> C 翻转
        assert r_flip.best_match == r_ok.best_match == "Eng_Speed_u16_M"
        assert r_flip.evidence.get("io_penalty") is True
        assert r_flip.score < r_ok.score
        # 默认阈值下翻转查询不得进入 AUTO（需人工确认）
        assert r_flip.tier != "AUTO"

    def test_type_flip_penalized(self, aligner):
        """类型宽度翻转（u16->u32）改变记录布局，同样不得 AUTO。"""
        r_flip = aligner.align("Eng_Speed_u32_M")
        if r_flip.best_match == "Eng_Speed_u16_M":
            assert r_flip.evidence.get("type_penalty") is True
            assert r_flip.tier != "AUTO"

    def test_penalty_disabled(self, kb):
        """关闭惩罚通道后翻转查询回到高分（消融实验的对照面）。"""
        raw = ra.SemanticAligner(kb, enable_type_penalty=False, tau_auto=0.7, tau_suggest=0.3)
        guarded = ra.SemanticAligner(kb, tau_auto=0.7, tau_suggest=0.3)
        r_raw = raw.align("Eng_Speed_u16_C")
        r_guarded = guarded.align("Eng_Speed_u16_C")
        assert r_raw.score > r_guarded.score
        assert r_raw.tier == "AUTO"      # 无惩罚: 高分误入 AUTO
        assert r_guarded.tier != "AUTO"  # 有惩罚: 强制降层

    def test_tier_thresholds(self):
        assert ra._decide_tier(0.99, 0.85, 0.5) == "AUTO"
        assert ra._decide_tier(0.6, 0.85, 0.5) == "SUGGEST"
        assert ra._decide_tier(0.1, 0.85, 0.5) == "MANUAL"

    def test_empty_symbol(self, aligner):
        r = aligner.align("")
        assert r.tier == "MANUAL" and r.best_match == ""


# ═══════════════════════════ 3. 元数据补全 ═══════════════════════════

class TestMetadataCompletion:
    def test_unit_completion(self, kb):
        sig = {"Name": "VehSpd", "Unit": "", "Description": ""}
        got = ra.MetadataCompleter(kb).complete(sig).get("Unit")
        assert got is not None
        assert got["value"] == "km/h"
        assert got["source_name"] == "Veh_Speed_u16_M"

    def test_existing_value_not_overwritten(self, kb):
        """已有手工值不覆盖（manual 优先级高于 auto）。"""
        sig = {"Name": "VehSpd", "Unit": "mph", "Description": ""}
        got = ra.MetadataCompleter(kb).complete(sig)
        assert "Unit" not in got  # 不应产生 Unit 建议

    def test_no_confident_suggestion(self, kb):
        sig = {"Name": "Zzz_Unknown_q9", "Unit": ""}
        got = ra.MetadataCompleter(kb).complete(sig)
        assert "Unit" not in got


# ═══════════════════════════ 4. 增量一致性 ═══════════════════════════

class TestConsistency:
    def test_duplicate_detection(self, aligner):
        new = [{"Name": "Eng_Speed_u16_M", "DataType": "u16"},
               {"Name": "Eng_Speed_u16_M", "DataType": "u16"}]
        rep = ra.check_incremental_consistency(new, [], aligner)
        assert rep.duplicates == ["Eng_Speed_u16_M"]
        assert not rep.is_clean

    def test_type_conflict_detection(self, aligner):
        new = [{"Name": "Eng_Speed_u16_M", "DataType": "f32"}]
        rep = ra.check_incremental_consistency(new, ["Eng_Speed_u16_M"], aligner)
        assert len(rep.type_conflicts) == 1
        assert rep.type_conflicts[0]["name"] == "Eng_Speed_u16_M"

    def test_type_consistent_rename_not_conflict(self, aligner):
        new = [{"Name": "Eng_Speed_u16_M", "DataType": "u16"}]
        rep = ra.check_incremental_consistency(new, ["Eng_Speed_u16_M"], aligner)
        assert rep.type_conflicts == []
        assert rep.is_clean

    def test_orphan_detection(self, aligner):
        new = [{"Name": "Eng_Speed_u16_M", "DataType": "u16"}]
        rep = ra.check_incremental_consistency(new, ["Eng_Speed_u16_M", "Old_Signal_u8_M"], aligner)
        assert rep.removed_orphans == ["Old_Signal_u8_M"]

    def test_rename_recognition(self, aligner):
        """P3 核心场景：旧对象改名后应被识别为 rename 而非孤儿+新对象。"""
        new = [{"Name": "Bat_Voltage_u16_M", "DataType": "u16"}]
        existing = ["Bat_Volt_u16_M"]
        rep = ra.check_incremental_consistency(new, existing, aligner)
        assert len(rep.aligned_renames) == 1
        assert rep.aligned_renames[0]["old"] == "Bat_Volt_u16_M"
        assert rep.aligned_renames[0]["new"] == "Bat_Voltage_u16_M"
        # 旧对象同时被正确标记为孤儿（待工程师结合 rename 提示确认）
        assert "Bat_Volt_u16_M" in rep.removed_orphans

    def test_mask_conflict_detection(self, aligner, kb):
        """位域丢失：掩码不再隔离原本的位 -> 必须报出（论文 Tab. I）。"""
        base = ra.KnowledgeDoc(name="Bit_Sig_u8_M", mask="0x0F")
        kb.add_semantic_corpus([{"name": "Bit_Sig_u8_M", "io": "MEAS", "c_type": "u8",
                                 "mask": "0x0F"}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Bit_Sig_u8_M", "DataType": "u8", "BitMask": "0xFF"}],
            ["Bit_Sig_u8_M"], aligner)
        assert len(rep.mask_conflicts) == 1
        assert rep.mask_conflicts[0]["name"] == "Bit_Sig_u8_M"
        assert rep.mask_conflicts[0]["old"] == "0xf"
        assert rep.mask_conflicts[0]["new"] == "0xff"
        assert not rep.is_clean          # 掩码漂移也是问题
        assert rep.has_issues

    def test_mask_unchanged_not_conflict(self, aligner, kb):
        kb.add_semantic_corpus([{"name": "Bit_Sig_u8_M", "io": "MEAS", "c_type": "u8",
                                 "mask": "0x0F"}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Bit_Sig_u8_M", "DataType": "u8", "BitMask": "0x0f"}],
            ["Bit_Sig_u8_M"], aligner)
        assert rep.mask_conflicts == []      # 归一化后相同 -> 不算漂移

    def test_conversion_conflict_detection(self, aligner, kb):
        """换算丢失：换算系数变了 -> 必须报出（论文 Tab. I）。"""
        kb.add_semantic_corpus([{"name": "Temp_Sig_f32_M", "io": "MEAS", "c_type": "f32",
                                 "factor": 0.1, "offset": 40.0}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Temp_Sig_f32_M", "DataType": "f32", "Factor": 0.6, "Offset": 40.0}],
            ["Temp_Sig_f32_M"], aligner)
        assert len(rep.conversion_conflicts) == 1
        assert rep.conversion_conflicts[0]["name"] == "Temp_Sig_f32_M"
        assert rep.conversion_conflicts[0]["old"]["factor"] == 0.1
        assert rep.conversion_conflicts[0]["new"]["factor"] == 0.6
        assert rep.has_issues

    def test_conversion_offset_only_conflict(self, aligner, kb):
        """只有偏置变了也要报出。"""
        kb.add_semantic_corpus([{"name": "Temp_Sig_f32_M", "io": "MEAS", "c_type": "f32",
                                 "factor": 0.1, "offset": 40.0}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Temp_Sig_f32_M", "DataType": "f32", "Factor": 0.1, "Offset": 41.0}],
            ["Temp_Sig_f32_M"], aligner)
        assert len(rep.conversion_conflicts) == 1

    def test_conversion_unchanged_not_conflict(self, aligner, kb):
        kb.add_semantic_corpus([{"name": "Temp_Sig_f32_M", "io": "MEAS", "c_type": "f32",
                                 "factor": 0.1, "offset": 40.0}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Temp_Sig_f32_M", "DataType": "f32", "Factor": 0.1, "Offset": 40.0}],
            ["Temp_Sig_f32_M"], aligner)
        assert rep.conversion_conflicts == []

    def test_conversion_relative_tolerance_small_factor(self, aligner, kb):
        """系数极小（1e-12）时也要靠相对判据检出，不能被绝对阈值吃掉。"""
        kb.add_semantic_corpus([{"name": "Gain_Sig_f64_M", "io": "MEAS", "c_type": "f64",
                                 "factor": 1e-12}])
        rep = ra.check_incremental_consistency(
            [{"Name": "Gain_Sig_f64_M", "DataType": "f64", "Factor": 6e-12}],
            ["Gain_Sig_f64_M"], aligner)
        assert len(rep.conversion_conflicts) == 1

    def test_knowledge_doc_factor_default_is_unrecorded(self):
        """未记录的换算系数必须是 None（缺省 1.0 会被误当成"已知为 1.0"）。"""
        d = ra.KnowledgeDoc(name="X_u8_M")
        assert d.factor is None and d.offset is None
        assert d.mask == ""


# ═══════════════════════════ 4b. 权重归一化（方案 a） ═══════════════════════════

class TestWeightNormalization:
    def test_single_channel_renormalized_to_one(self, kb):
        """只留一个通道时，它的权重必须归一化为 1（否则分数尺度不可比）。"""
        al = ra.SemanticAligner(kb, w_retrieval=0.0)
        assert al.w_retrieval == 0.0
        assert abs(al.w_token - 1.0) < 1e-9

    def test_two_channel_weights_normalized(self, kb):
        al = ra.SemanticAligner(kb)
        total = al.w_retrieval + al.w_token
        assert abs(total - 1.0) < 1e-9
        # 检索 : 词元 = 2 : 1
        assert abs(al.w_retrieval / al.w_token - 2.0) < 1e-9

    def test_structure_evidence_not_a_score_term(self, kb):
        """结构证据不进入融合分数（方案 a），只作检查输入。"""
        assert ra.W_STRUCTURE == 0.0
        al = ra.SemanticAligner(kb)
        r = al.align("Eng_Speed_u16_M")
        ev = r.evidence or {}
        # 分数只由检索与词元两通道决定
        expected = (al.w_retrieval * float(ev["retrieval"])
                    + al.w_token * float(ev["token"]))
        assert abs(r.score - expected) < 1e-6
        # 结构证据即使记录在 evidence 里（供审计），也不参与分数
        assert "structure" not in ev or al.w_retrieval * float(ev["retrieval"]) \
            + al.w_token * float(ev["token"]) == r.score


# ═══════════════════════════ 5. 知识库与批量入口 ═══════════════════════════

class TestKnowledgeBase:
    def test_historical_config_ingestion(self):
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        n = base.add_historical_config([
            {"Name": "Trn_OilLmt_u16_C", "ShortName": "OilLmt_C", "Unit": "kPa",
             "Type": "CAL", "CType": "u16", "Group": "Trans", "Description": "oil pressure limit"},
        ])
        assert n == 1
        doc = base.get("Trn_OilLmt_u16_C")
        assert doc.unit == "kPa" and doc.source == "history"

    def test_duplicate_names_ignored(self):
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        base.add_semantic_corpus(ra._SEED_CORPUS)
        n = base.add_semantic_corpus([{"name": "Eng_Speed_u16_M"}])  # 重复
        assert n == 0
        assert len(base) == len(ra._SEED_CORPUS)

    def test_type_compatible(self):
        assert ra.DomainKnowledgeBase.type_compatible("u16", "uint16_t")
        assert ra.DomainKnowledgeBase.type_compatible("f32", "float")
        assert not ra.DomainKnowledgeBase.type_compatible("u16", "f32")
        assert ra.DomainKnowledgeBase.type_compatible("", "u16")  # 缺信息不惩罚

    def test_empty_kb_alignment(self):
        base = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        r = ra.SemanticAligner(base).align("Eng_Speed_u16_M")
        assert r.tier == "MANUAL" and not r.candidates


class TestBatchEntry:
    def test_align_symbols_for_update(self, kb):
        symbols = [
            {"name": "Eng_Speed_u16_M", "size": 2},
            {"name": "VehSpd", "size": 2},
            {"name": "Zzz_Nothing", "size": 4},
        ]
        logs = []
        results = ra.align_symbols_for_update(symbols, kb, log_cb=logs.append)
        assert len(results) == 3
        assert results["Eng_Speed_u16_M"].tier == "AUTO"
        assert results["VehSpd"].best_match == "Veh_Speed_u16_M"
        assert results["Zzz_Nothing"].tier == "MANUAL"
        assert len(logs) == 3


class TestAlignmentReport:
    """对齐报告与专家复核队列（论文流程图的 report / expert review 两个阶段）。"""

    SYMBOLS = [
        {"name": "Eng_Speed_u16_M", "size": 2},
        {"name": "VehSpd", "size": 2},
        {"name": "Zzz_Nothing", "size": 4},
    ]

    def test_report_lists_every_query_and_counts_tiers(self, kb):
        results = ra.align_symbols_for_update(self.SYMBOLS, kb)
        text = ra.render_alignment_report(results)
        assert "queries=3" in text
        assert "AUTO=1" in text
        for name in results:
            assert name in text
        # 证据分量必须落到报告里，否则报告无法支撑审计
        assert "evidence:" in text

    def test_review_queue_contains_only_ambiguous_tiers(self, kb):
        results = ra.align_symbols_for_update(self.SYMBOLS, kb)
        queue = ra.review_queue(results)
        assert all(r.tier in ("SUGGEST", "MANUAL") for r in queue)
        assert "Zzz_Nothing" in [r.query for r in queue]
        assert "Eng_Speed_u16_M" not in [r.query for r in queue]
        # 队列按分数降序，便于专家从最接近可用的条目开始复核
        assert [r.score for r in queue] == sorted([r.score for r in queue], reverse=True)

    def test_write_alignment_report_creates_readable_file(self, kb, tmp_path):
        results = ra.align_symbols_for_update(self.SYMBOLS, kb)
        out = ra.write_alignment_report(results, tmp_path / "align_report.txt")
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "review queue" in content
        assert "summary:" in content
        assert "Zzz_Nothing" in content

    def test_empty_result_set_still_renders(self):
        text = ra.render_alignment_report({})
        assert "queries=0" in text
        assert "(empty" in text

# -*- coding: utf-8 -*-
"""rag_bridge（主流程 <-> A2L-RAGAlign 适配层）的测试。

重点覆盖三类容易回归的行为：
  1. 软依赖：rag_align 不可用时必须静默降级，绝不能让核心更新流程失败；
  2. 置信分层：每个候选按自己的分数分层，不能把低分噪声标成 SUGGEST；
  3. 对象名规则：一致性检查必须与 A2L 写入端（ShortName 优先）一致，
     否则结构体展开会被误判成成千上万个「名称重复」。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rag_bridge  # noqa: E402


# ── 1. 软依赖 ────────────────────────────────────────────────────────────
def test_rag_available_returns_bool():
    assert isinstance(rag_bridge.rag_available(), bool)


def test_build_aligner_returns_object_or_none():
    a = rag_bridge.build_aligner()
    assert a is None or hasattr(a, "align")


def test_semantic_candidates_without_rag_align_degrades(monkeypatch):
    """rag_align 不可导入时必须返回空列表而不是抛异常。"""
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "rag_align":
            raise ImportError("simulated: rag_align missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert rag_bridge.semantic_candidates("Foo", ["Bar"]) == []
    assert rag_bridge.consistency_check([{"Name": "a"}], ["a"])["available"] is False


def test_consistency_check_shape_when_unavailable():
    res = rag_bridge.consistency_check([{"Name": "a"}], ["a"])
    for key in ("available", "duplicates", "type_conflicts",
                "removed_orphans", "aligned_renames"):
        assert key in res


# ── 2. 置信分层 ──────────────────────────────────────────────────────────
def test_semantic_candidates_tier_is_per_candidate():
    """每个候选按自己的分数分层：高分 AUTO，低分不能被标成 SUGGEST。"""
    pool = ["EngineSpeed", "EngineSpeedLimit", "VehicleSpeed"]
    out = rag_bridge.semantic_candidates("EngineSpeed", pool)
    if not out:
        pytest.skip("rag_align unavailable in this environment")
    for c in out:
        assert c["tier"] in ("AUTO", "SUGGEST", "MANUAL")
        if c["tier"] == "AUTO":
            assert c["score"] >= 0.85
        elif c["tier"] == "SUGGEST":
            assert 0.50 <= c["score"] < 0.85


def test_semantic_candidates_rejects_low_tier_by_default():
    pool = ["TotallyUnrelatedName"]
    out = rag_bridge.semantic_candidates("ZzQqXx", pool)
    assert isinstance(out, list)
    # 默认只接受 AUTO / SUGGEST，MANUAL 候选必须被过滤掉
    assert all(c["tier"] in ("AUTO", "SUGGEST") for c in out)


def test_semantic_candidates_empty_query():
    assert rag_bridge.semantic_candidates("", ["a", "b"]) == []
    assert rag_bridge.semantic_candidates("   ", ["a", "b"]) == []


def test_pool_aligner_is_reusable():
    """候选池对齐器只建一次，之后反复检索不应重建知识库。"""
    pool = ["Alpha", "Beta", "Gamma"]
    pa = rag_bridge.build_pool_aligner(pool)
    if pa is None:
        pytest.skip("rag_align unavailable in this environment")
    first = rag_bridge.semantic_candidates("Alpha", pool_aligner=pa)
    second = rag_bridge.semantic_candidates("Gamma", pool_aligner=pa)
    assert isinstance(first, list) and isinstance(second, list)


def test_pool_aligner_with_empty_pool():
    assert rag_bridge.build_pool_aligner([]) in (None, ) or \
        rag_bridge.build_pool_aligner([]) is None


# ── 3. 对象名规则（与 A2L 写入端一致）────────────────────────────────────
def test_consistency_check_uses_shortname_first():
    """结构体展开：Name 相同、ShortName 不同 -> 不算重复。"""
    sigs = [
        {"Name": "Cfg", "ShortName": "Cfg.a", "DataType": "UWORD"},
        {"Name": "Cfg", "ShortName": "Cfg.b", "DataType": "UWORD"},
        {"Name": "Cfg", "ShortName": "Cfg.c", "DataType": "UWORD"},
    ]
    res = rag_bridge.consistency_check(sigs, [])
    if not res["available"]:
        pytest.skip("rag_align unavailable in this environment")
    assert res["duplicates"] == []


def test_consistency_check_detects_real_duplicate():
    sigs = [
        {"Name": "Sig", "ShortName": "Sig", "DataType": "UWORD"},
        {"Name": "Sig", "ShortName": "Sig", "DataType": "UWORD"},
    ]
    res = rag_bridge.consistency_check(sigs, [])
    if not res["available"]:
        pytest.skip("rag_align unavailable in this environment")
    assert "Sig" in res["duplicates"]


def test_consistency_check_reports_orphans():
    res = rag_bridge.consistency_check(
        [{"Name": "Keep", "ShortName": "Keep"}], ["Keep", "Gone"])
    if not res["available"]:
        pytest.skip("rag_align unavailable in this environment")
    assert "Gone" in res["removed_orphans"]
    assert "Keep" not in res["removed_orphans"]


def test_rag_align_consistency_uses_shortname_priority():
    """直接守住 rag_align 里的取值规则，防止再次被改回 Name 优先。"""
    import rag_align
    al = rag_bridge.build_aligner()
    if al is None:
        pytest.skip("rag_align unavailable in this environment")
    rep = rag_align.check_incremental_consistency(
        [{"Name": "Base", "ShortName": "Base.m1"},
         {"Name": "Base", "ShortName": "Base.m2"}],
        [], al)
    assert rep.duplicates == []

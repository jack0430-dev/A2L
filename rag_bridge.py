# -*- coding: utf-8 -*-
"""A2L 更新流程 <-> rag_align（A2L-RAGAlign）的薄适配层。

设计原则
--------
1. **软依赖**：主流程不硬依赖 rag_align。模块缺失、依赖未装或内部异常时，
   一律静默降级为「原有行为」，绝不让可选功能拖垮核心更新流水线。
2. **无 GUI 依赖**：本模块不 import 任何 PyQt5 符号，可在无头 / CI 环境下
   单独使用与测试。
3. **只读**：适配层不修改传入的信号列表与已有 A2L 对象集合。

提供的能力
----------
* :func:`semantic_candidates` —— 对精确匹配失败的信号，给出检索增强的语义候选
  （论文 RQ1/RQ2 的 AUTO / SUGGEST 分层结果），补足主流程原先只有词法候选
  （编辑距离）的不足。
* :func:`consistency_check` —— 增量更新一致性检查（重名 / 类型漂移 / 孤儿对象 /
  候选改名），补齐主流程「提取了已有 A2L 对象名却从未使用」的缺口。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

log = logging.getLogger("a2l.rag_bridge")

__all__ = [
    "rag_available",
    "build_aligner",
    "build_pool_aligner",
    "semantic_candidates",
    "consistency_check",
    "RAG_BRIDGE_VERSION",
]

RAG_BRIDGE_VERSION = "1.0"


def rag_available() -> bool:
    """rag_align 是否可用（可导入且具备对齐能力）。"""
    try:
        import rag_align  # noqa: F401
    except Exception:
        return False
    return True


def build_aligner(kb: Any = None, **kwargs: Any):
    """构造语义对齐器；失败时返回 None（调用方需容错）。"""
    try:
        import rag_align
        if kb is None:
            kb = rag_align.build_kb_from_main_module()
        return rag_align.SemanticAligner(kb, **kwargs)
    except Exception:
        log.debug("rag_align aligner unavailable", exc_info=True)
        return None


def build_pool_aligner(candidate_pool: Iterable[str], base_aligner: Any = None):
    """为一个候选池构建一次性的检索器。

    候选池（地址文件符号表）通常有上万项，若每个信号都重建一次知识库，
    代价是 O(信号数 × 池大小)。这里把池只索引一次，之后复用。

    :returns: 可直接用于 ``SemanticAligner.align`` 的对齐器；失败时返回 None。
    """
    try:
        import rag_align
        if base_aligner is None:
            base_aligner = build_aligner()
        if base_aligner is None:
            return None
        pool = [str(c).strip() for c in (candidate_pool or []) if str(c).strip()]
        if not pool:
            return None
        pool_kb = rag_align.DomainKnowledgeBase(
            retriever=type(base_aligner.kb.retriever)())
        pool_kb.add_semantic_corpus([{"name": n} for n in pool])
        return rag_align.SemanticAligner(
            pool_kb, tau_auto=base_aligner.tau_auto,
            tau_suggest=base_aligner.tau_suggest)
    except Exception:
        log.debug("build_pool_aligner failed", exc_info=True)
        return None


def semantic_candidates(name: str,
                        candidate_pool: Iterable[str] = None,
                        aligner: Any = None,
                        pool_aligner: Any = None,
                        top_k: int = 5,
                        accept_tiers: tuple = ("AUTO", "SUGGEST"),
                        max_results: int = 5) -> list:
    """为匹配失败的信号名检索语义候选。

    :param name: 未能在地址文件符号表中命中的信号名。
    :param candidate_pool: 候选名集合（通常是地址文件符号表或已有 A2L 对象名）。
    :param aligner: 已构造的 ``rag_align.SemanticAligner``；None 时内部构造。
    :param pool_aligner: 预先构建好的候选池对齐器（见 :func:`build_pool_aligner`）。
        批量处理时务必传入，否则每个信号都要重建一次池索引。
    :param top_k: 检索返回的候选数。
    :param accept_tiers: 接受哪些置信层（``AUTO`` / ``SUGGEST`` / ``MANUAL``）。
    :param max_results: 最多返回多少条。
    :returns: ``[{"name": ..., "score": ..., "tier": ...}, ...]``；不可用时为空列表。
    """
    query = str(name or "").strip()
    if not query:
        return []
    try:
        import rag_align
        if pool_aligner is None:
            pool_aligner = build_pool_aligner(candidate_pool, aligner)
        if pool_aligner is None:
            return []

        res = pool_aligner.align(query, top_k=top_k)

        out = []
        for cand, score in (res.candidates or [])[:max_results]:
            # 逐候选按自己的分数分层：AlignmentResult.tier 只描述最佳匹配，
            # 若把非最佳候选一律标成 SUGGEST，会把低分噪声伪装成「待人工确认」。
            tier = rag_align._decide_tier(float(score),
                                          pool_aligner.tau_auto,
                                          pool_aligner.tau_suggest)
            if tier not in accept_tiers:
                continue
            out.append({"name": str(cand), "score": round(float(score), 4),
                        "tier": tier})
        return out
    except Exception:
        log.debug("semantic_candidates failed for %r", name, exc_info=True)
        return []


def consistency_check(signals: list,
                      existing_a2l_names: Iterable[str],
                      aligner: Any = None,
                      tau_rename: float = None) -> dict:
    """增量更新一致性检查。

    返回形如::

        {
          "available": bool,
          "duplicates": [...],
          "type_conflicts": [{"name":..., "old":..., "new":...}, ...],
          "removed_orphans": [...],
          "aligned_renames": [{"old":..., "new":..., "score":...}, ...],
        }

    任何内部异常都被吞掉并退化为 ``{"available": False, ...}``，不影响主流程。
    """
    empty = {
        "available": False,
        "duplicates": [],
        "type_conflicts": [],
        "removed_orphans": [],
        "aligned_renames": [],
    }
    try:
        import rag_align
        if aligner is None:
            aligner = build_aligner()
        if aligner is None:
            return empty
        report = rag_align.check_incremental_consistency(
            list(signals or []),
            list(existing_a2l_names or []),
            aligner,
            tau_rename=tau_rename,
        )
        return {
            "available": True,
            "duplicates": list(report.duplicates),
            "type_conflicts": list(report.type_conflicts),
            "removed_orphans": list(report.removed_orphans),
            "aligned_renames": list(report.aligned_renames),
        }
    except Exception:
        log.debug("consistency_check failed", exc_info=True)
        return empty

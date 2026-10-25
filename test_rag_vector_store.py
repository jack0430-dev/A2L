# -*- coding: utf-8 -*-
"""VectorStoreRetriever（外部向量库后端）的测试。

重点守住三件事：
  1. 协议兼容：向量库检索器与 CharNgramRetriever 对上层完全透明
     （build / retrieve 签名一致，SemanticAligner 无需任何改动）；
  2. 后端等价：FAISS / Chroma / numpy 三个后端给出相同的 top-k 排序
     （同一编码器 + 同一余弦度量）；
  3. 确定性：HashingEncoder 对同一文本必须给出同一向量（不依赖 Python 的
     带盐 hash()），否则向量库无法复现实验。
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rag_align as ra  # noqa: E402

BACKENDS = ("numpy", "faiss", "chroma")

# 后端名 -> 实际模块名（chroma 后端用的是 chromadb 包）
_BACKEND_MODULE = {"numpy": "numpy", "faiss": "faiss", "chroma": "chromadb"}

# chromadb 1.x 的 Rust 绑定与 PyQt5 的原生运行库冲突：同一进程内先 import
# PyQt5 再调用 Collection.add() 会触发 Windows 原生 access violation
#（Python 无法捕获，会直接带走 pytest 进程）。因此 chroma 的进程内用例
# 必须在没有加载 PyQt5 时才运行；完整的 chroma 验证由下面的独立进程用例完成。
_CHROMA_CONFLICTS_WITH_PYQT = "chroma"


def _backend_unavailable_reason(backend: str) -> str:
    """返回后端不可用的原因；空字符串表示可用。"""
    import importlib.util
    module = _BACKEND_MODULE.get(backend, backend)
    if backend == _CHROMA_CONFLICTS_WITH_PYQT and "PyQt5" in sys.modules:
        return "PyQt5 已加载，chromadb 原生绑定会崩溃（改由独立进程用例覆盖）"
    try:
        if importlib.util.find_spec(module) is None:
            return "%s 未安装" % module
    except (ImportError, ValueError):
        return "%s 不可导入" % module
    return ""


def _backend_available(backend: str) -> bool:
    return not _backend_unavailable_reason(backend)


def _kb(backend: str) -> ra.DomainKnowledgeBase:
    kb = ra.DomainKnowledgeBase(
        retriever=ra.VectorStoreRetriever(backend=backend))
    kb.add_semantic_corpus(ra._SEED_CORPUS)
    return kb


# ── 1. 编码器 ──────────────────────────────────────────────────────────────
class TestHashingEncoder:
    def test_deterministic(self):
        enc = ra.HashingEncoder(dim=64)
        a = enc.encode(["Eng_Speed_u16_M measured engine speed"])
        b = enc.encode(["Eng_Speed_u16_M measured engine speed"])
        assert a == b

    def test_normalized(self):
        enc = ra.HashingEncoder(dim=64)
        vec = enc.encode(["Eng_Speed_u16_M measured engine speed"])[0]
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6

    def test_dimension_respected(self):
        enc = ra.HashingEncoder(dim=32)
        assert len(enc.encode(["a", "b"])[0]) == 32

    def test_empty_text_gives_zero_vector(self):
        enc = ra.HashingEncoder(dim=16)
        assert enc.encode([""])[0] == [0.0] * 16

    def test_rejects_tiny_dimension(self):
        with pytest.raises(ValueError):
            ra.HashingEncoder(dim=2)


# ── 2. 后端选择 ────────────────────────────────────────────────────────────
class TestBackendSelection:
    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError):
            ra.VectorStoreRetriever(backend="elasticsearch")

    def test_explicit_backend_is_kept(self):
        r = ra.VectorStoreRetriever(backend="numpy")
        assert r.backend == "numpy"

    def test_auto_picks_an_available_backend(self):
        r = ra.VectorStoreRetriever(backend="auto")
        assert r.backend in ra.VectorStoreRetriever.BACKENDS

    def test_auto_never_picks_chroma(self, monkeypatch):
        """auto 不得选中 chroma：其 Rust 绑定与 PyQt5 同进程会触发原生崩溃。"""
        monkeypatch.setenv("RAG_ALIGN_VECTOR_BACKEND", "")
        r = ra.VectorStoreRetriever(backend="auto")
        assert r.backend in ra.VectorStoreRetriever.AUTO_PROBE_BACKENDS
        assert r.backend != "chroma"

    def test_explicit_chroma_is_allowed(self):
        r = ra.VectorStoreRetriever(backend="chroma")
        assert r.backend == "chroma"

    def test_make_default_retriever_honours_env(self, monkeypatch):
        monkeypatch.setenv("RAG_ALIGN_RETRIEVER", "numpy")
        r = ra.make_default_retriever()
        assert isinstance(r, ra.VectorStoreRetriever)
        assert r.backend == "numpy"

    def test_make_default_retriever_vector_alias(self, monkeypatch):
        monkeypatch.setenv("RAG_ALIGN_RETRIEVER", "vector")
        monkeypatch.setenv("RAG_ALIGN_VECTOR_BACKEND", "numpy")
        r = ra.make_default_retriever()
        assert isinstance(r, ra.VectorStoreRetriever)
        assert r.backend == "numpy"

    def test_charngram_still_zero_dependency(self, monkeypatch):
        monkeypatch.setenv("RAG_ALIGN_RETRIEVER", "charngram")
        assert isinstance(ra.make_default_retriever(), ra.CharNgramRetriever)


# ── 3. 协议兼容与检索行为 ─────────────────────────────────────────────────
class TestVectorStoreRetriever:
    def test_satisfies_protocol(self):
        r = ra.VectorStoreRetriever(backend="numpy")
        assert hasattr(r, "build") and hasattr(r, "retrieve")

    def test_retrieve_finds_renamed_signal(self):
        kb = _kb("numpy")
        cands = kb.retriever.retrieve("Eng_Speed_u16_M", top_k=5)
        assert cands
        assert cands[0][0] == "Eng_Speed_u16_M"

    def test_retrieve_returns_scores_in_descending_order(self):
        kb = _kb("numpy")
        cands = kb.retriever.retrieve("VehSpd", top_k=5)
        scores = [s for _, s in cands]
        assert scores == sorted(scores, reverse=True)

    def test_empty_kb_returns_nothing(self):
        kb = ra.DomainKnowledgeBase(retriever=ra.VectorStoreRetriever(backend="numpy"))
        assert kb.retriever.retrieve("anything") == []

    def test_top_k_is_respected(self):
        kb = _kb("numpy")
        assert len(kb.retriever.retrieve("Eng", top_k=2)) <= 2

    def test_aligner_works_unchanged_on_vector_store(self):
        """上层语义对齐器对向量库后端完全透明。"""
        kb = _kb("numpy")
        aligner = ra.SemanticAligner(kb)
        r = aligner.align("Eng_Speed_u16_M")
        assert r.tier == "AUTO" and r.best_match == "Eng_Speed_u16_M"
        r2 = aligner.align("EngineSpeed_meas")
        assert r2.best_match == "Eng_Speed_u16_M"
        assert r2.evidence.get("retrieval") is not None

    def test_contract_check_still_applies_over_vector_store(self):
        """结构约束与检索后端无关：IO 翻转仍必须降层。"""
        kb = _kb("numpy")
        aligner = ra.SemanticAligner(kb)
        r = aligner.align("Eng_Speed_u16_C")
        assert r.evidence.get("io_penalty") is True
        assert r.tier != "AUTO"

    def test_kb_rebuild_after_add(self):
        """KB 增量加载后必须重建向量索引。"""
        kb = ra.DomainKnowledgeBase(retriever=ra.VectorStoreRetriever(backend="numpy"))
        kb.add_semantic_corpus(ra._SEED_CORPUS)
        kb.add_semantic_corpus([{"name": "Trn_GearPos_u8_M", "unit": ""}])
        names = [n for n, _ in kb.retriever.retrieve("GearPos", top_k=3)]
        assert "Trn_GearPos_u8_M" in names


# ── 4. 后端等价性（需要对应的第三方库）─────────────────────────────────────
@pytest.mark.parametrize("backend", BACKENDS)
class TestBackendEquivalence:
    def test_top1_matches_numpy_reference(self, backend):
        reason = _backend_unavailable_reason(backend)
        if reason:
            pytest.skip(reason)
        ref = _kb("numpy").retriever.retrieve("EngineSpeed", top_k=3)
        got = _kb(backend).retriever.retrieve("EngineSpeed", top_k=3)
        assert [n for n, _ in got] == [n for n, _ in ref]

    def test_scores_match_numpy_reference(self, backend):
        reason = _backend_unavailable_reason(backend)
        if reason:
            pytest.skip(reason)
        ref = _kb("numpy").retriever.retrieve("VehSpd", top_k=3)
        got = _kb(backend).retriever.retrieve("VehSpd", top_k=3)
        for (_, s_ref), (_, s_got) in zip(ref, got):
            assert abs(s_ref - s_got) < 1e-4

    def test_build_and_retrieve_cycle(self, backend):
        reason = _backend_unavailable_reason(backend)
        if reason:
            pytest.skip(reason)
        kb = _kb(backend)
        cands = kb.retriever.retrieve("Eng_Speed_u16_M", top_k=3)
        assert cands[0][0] == "Eng_Speed_u16_M"


# ── 5. chroma 后端：必须在独立进程中验证 ──────────────────────────────────
class TestChromaInCleanSubprocess:
    """chromadb 的 Rust 绑定与 PyQt5 同进程会触发原生崩溃，因此用一个未加载
    PyQt5 的独立解释器来验证 chroma 后端的完整链路。"""

    ROOT = os.path.dirname(os.path.abspath(__file__))

    def _run(self, body: str) -> subprocess.CompletedProcess:
        import importlib.util
        if importlib.util.find_spec("chromadb") is None:
            pytest.skip("chromadb 未安装")
        code = ("import sys; sys.path.insert(0, %r);\n" % self.ROOT) + body
        return subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True, timeout=300)

    def test_chroma_backend_end_to_end(self):
        proc = self._run(
            "import rag_align as ra\n"
            "kb = ra.DomainKnowledgeBase(retriever=ra.VectorStoreRetriever(backend='chroma'))\n"
            "kb.add_semantic_corpus(ra._SEED_CORPUS)\n"
            "r = ra.SemanticAligner(kb).align('Eng_Speed_u16_M')\n"
            "print('RESULT', r.best_match, r.tier)\n")
        assert proc.returncode == 0, proc.stderr
        assert "RESULT Eng_Speed_u16_M AUTO" in proc.stdout

    def test_chroma_scores_match_numpy(self):
        proc = self._run(
            "import rag_align as ra\n"
            "def top(backend, q):\n"
            "    kb = ra.DomainKnowledgeBase(retriever=ra.VectorStoreRetriever(backend=backend))\n"
            "    kb.add_semantic_corpus(ra._SEED_CORPUS)\n"
            "    return kb.retriever.retrieve(q, top_k=3)\n"
            "a = top('numpy', 'VehSpd')\n"
            "b = top('chroma', 'VehSpd')\n"
            "assert [n for n, _ in a] == [n for n, _ in b], (a, b)\n"
            "for (_, sa), (_, sb) in zip(a, b):\n"
            "    assert abs(sa - sb) < 1e-4, (sa, sb)\n"
            "print('RESULT match')\n")
        assert proc.returncode == 0, proc.stderr
        assert "RESULT match" in proc.stdout

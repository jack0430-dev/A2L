"""
rag_align.py — RAG 语义对齐原型 (A2L-RAGAlign Prototype)
========================================================

面向论文: "Retrieval-Augmented Semantic Alignment for Automotive ECU
Calibration Description Updates" (SANER 2027 Tool Demo / ICPC 2027)

定位与设计
----------
在现有 A2L_Update_Tool（确定性规则驱动）之上，补齐三个检索增强能力：

  P1 信号语义对齐   —— 替代 do_update_a2l 中的精确字符串匹配（L3386）
  P2 元数据自动补全 —— Unit/Factor/Min/Max/Description 检索式补全
  P3 增量一致性检查 —— 补齐 existing_a2l_names（L3221）未使用的缺口

知识库三类知识源（对应论文 Sec. 3.2 / 代码资产 K1-K3）：
  K1 确定性规则:  C 类型 <-> A2L DTYPE 映射、命名约定分类（复用主模块，主模块缺失时降级）
  K2 历史配置:    signal_keep_config.xlsx 中人工标注的信号（Unit/AddrOffset/Keep...）
  K3 语义语料:    信号名 + 物理语义描述（单位/量程/factor/枚举）

检索器抽象（对应论文 Sec. 3.3）：
  默认 CharNgramRetriever（纯标准库、字符 n-gram TF 余弦，零依赖可运行）；
  可插拔 SentenceTransformerRetriever（安装 sentence-transformers 后自动启用）；
  可插拔 VectorStoreRetriever（外部向量库后端：FAISS / Chroma / numpy）——
  这是 EmbeddingRetriever 协议预留扩展点的落地实现：把知识库编码为稠密向量
  写入外部向量库，查询用内积（= 余弦）取 top-k，对上层完全透明。

重要：本模块**不介入任何 LLM 大模型**。"RAG" 在此指"检索增强的对齐"——
检索到的知识被确定性地打分、融合、加约束、分层，而不是交给生成模型改写。

置信度分层（对应论文 Sec. 3.3 confidence-ranked fusion）：
  AUTO    score >= tau_auto    自动接受
  SUGGEST tau_suggest<=score   人工确认
  MANUAL  其他                 完全人工

用法：
  python rag_align.py --demo        # 运行内置演示
  python rag_align.py --selftest    # 内置自检（无 pytest 依赖）

依赖：仅 Python 标准库；可选 numpy / faiss / chromadb / sentence-transformers
      提升检索质量或接入外部向量库。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import logging
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Protocol

log = logging.getLogger("rag_align")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  0. 常量与数据结构                                                            ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 置信度阈值（论文消融实验的可调参数）
TAU_AUTO = 0.85
TAU_SUGGEST = 0.50

# 类型后缀 -> C 类型家族（K1 规则的子集，完整表优先复用主模块 _C_TYPE_SIZE_MAP）
_TYPE_SUFFIX_RULES = {
    "u8": "uint8_t", "u16": "uint16_t", "u32": "uint32_t", "u64": "uint64_t",
    "i8": "int8_t", "i16": "int16_t", "i32": "int32_t", "i64": "int64_t",
    "f32": "float", "f64": "double", "b": "bool",
}
# IO 后缀（与主模块 _classify_object_type 的命名约定一致）
_IO_SUFFIX_RULES = {"C": "CAL", "M": "MEAS"}

AlignmentTier = str  # "AUTO" | "SUGGEST" | "MANUAL"


@dataclass
class AlignmentResult:
    """单次对齐结果（论文 Fig.3 confidence-ranked fusion 的输出）。"""
    query: str                        # 待对齐符号（如 ELF/DWARF 中的符号名）
    best_match: str = ""              # 最佳候选（如历史配置中的信号名/ShortName）
    score: float = 0.0                # 融合后的置信度 [0,1]
    tier: AlignmentTier = "MANUAL"    # 置信度分层
    candidates: list[tuple[str, float]] = field(default_factory=list)  # top-k (候选, 分数)
    evidence: dict = field(default_factory=dict)  # 各证据分量，供论文消融分析

    def __post_init__(self) -> None:
        # 默认分层用模块级阈值；SemanticAligner.align() 随后总是用实例阈值
        # （校准得到的 tau_auto/tau_suggest）覆盖它，所以这里只是独立使用
        # 时的安全兜底。
        self.tier = self._decide_tier(self.score)

    @staticmethod
    def _decide_tier(score: float) -> AlignmentTier:
        if score >= TAU_AUTO:
            return "AUTO"
        if score >= TAU_SUGGEST:
            return "SUGGEST"
        return "MANUAL"


@dataclass
class KnowledgeDoc:
    """知识库文档：信号级检索单元（K2/K3 的统一表示）。"""
    name: str                 # 规范信号名（如 Eng_Speed_u16_M）
    short_name: str = ""      # ShortName（如 EngSpd_M）
    description: str = ""     # 物理语义描述
    unit: str = ""            # 物理单位
    io_type: str = ""         # CAL / MEAS
    c_type: str = ""          # C 类型（u8/u16/float... 或 uint8_t...）
    group: str = ""           # 功能域分组
    factor: float | None = None   # 物理换算系数（raw count -> physical value）；None = 未记录
    offset: float | None = None   # 物理换算偏置；None = 未记录
    mask: str = ""            # 位域掩码（如 0x0F）
    source: str = "corpus"    # 知识来源标记（history/corpus/rule），供溯源与消融

    def text(self) -> str:
        """检索文本表示：名称词干 + 语义描述 + 单位 + 分组。"""
        parts = [self.name, self.short_name, decompose_to_text(self.name),
                 self.description, self.unit, self.group]
        return " ".join(p for p in parts if p)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  1. 信号名结构化分解（论文 Sec. 3.3 前置步骤）                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

_STRUCT_PATH_RE = re.compile(r"[.\-/>]")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def decompose_signal_name(name: str) -> dict:
    """
    把信号名分解为结构化字段。

    示例:
      "g_imu_config.accRange_u8_C" ->
        {"path": ["g_imu_config", "accRange"], "stem": "accRange",
         "tokens": ["acc", "range"], "type_suffix": "u8", "io_suffix": "C",
         "io_type": "CAL", "c_type": "uint8_t", "prefix": "acc"}

    这是纯确定性分解，为后续向量化检索提供结构化证据（而非仅整串匹配）。
    """
    raw = (name or "").strip()
    segs = [s for s in _STRUCT_PATH_RE.split(raw) if s]
    stem = segs[-1] if segs else raw

    type_suffix, io_suffix = "", ""
    m = re.search(r"_?([a-z]\d+|[bB])_([CMcm])$", stem)
    if m:
        type_suffix, io_suffix = m.group(1).lower(), m.group(2).upper()
    else:
        m2 = re.search(r"_([CMcm])$", stem)
        if m2:
            io_suffix = m2.group(1).upper()
            stem_no_io = stem[: m2.start()]
            m3 = re.search(r"_([a-z]\d+|[bB])$", stem_no_io)
            if m3:
                type_suffix = m3.group(1).lower()
                stem_no_io = stem_no_io[: m3.start()]
            stem = stem_no_io

    # 词元切分: snake_case + camelCase
    tokens: list[str] = []
    for seg in segs:
        for w in seg.split("_"):
            if not w:
                continue
            tokens.extend(t.lower() for t in _CAMEL_RE.split(w) if t)
    # 去掉类型/IO 后缀词元，保留语义词元
    semantic_tokens = [t for t in tokens if t not in (type_suffix, io_suffix.lower())]

    return {
        "path": segs,
        "stem": stem,
        "tokens": tokens,
        "semantic_tokens": semantic_tokens,
        "type_suffix": type_suffix,
        "io_suffix": io_suffix,
        "io_type": _IO_SUFFIX_RULES.get(io_suffix, ""),
        "c_type": _TYPE_SUFFIX_RULES.get(type_suffix, ""),
        "prefix": semantic_tokens[0] if semantic_tokens else "",
    }


def decompose_to_text(name: str) -> str:
    """名称分解后的检索友好文本（空格分隔语义词元）。"""
    d = decompose_signal_name(name)
    return " ".join(d["semantic_tokens"]) or name


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  2. 检索器抽象（论文 Sec. 3.3 embedding-based retrieval）                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class EmbeddingRetriever(Protocol):
    """检索器协议：后续可替换为向量库/LLM 重排序实现（论文扩展点）。"""
    def build(self, docs: list[KnowledgeDoc]) -> None: ...
    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]: ...


class CharNgramRetriever:
    """
    零依赖基线检索器：字符 n-gram TF 余弦相似度。

    兼作论文实验中的 retrieval baseline 之一
    （vs. exact / Levenshtein / BM25 / sentence-transformers，见 Sec. 5.2）。
    """
    N = (2, 3)

    def __init__(self) -> None:
        self._docs: list[KnowledgeDoc] = []
        self._doc_vecs: list[Counter] = []

    @classmethod
    def _ngrams(cls, text: str) -> Counter:
        text = re.sub(r"\s+", " ", text.lower()).strip()
        cnt: Counter = Counter()
        for n in cls.N:
            for i in range(len(text) - n + 1):
                cnt[text[i:i + n]] += 1
        return cnt

    @staticmethod
    def _cosine(a: Counter, b: Counter) -> float:
        if not a or not b:
            return 0.0
        dot = sum(v * b.get(k, 0) for k, v in a.items())
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def build(self, docs: list[KnowledgeDoc]) -> None:
        self._docs = list(docs)
        self._doc_vecs = [self._ngrams(d.text()) for d in self._docs]

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        qv = self._ngrams(query)
        scored = [
            (d.name, self._cosine(qv, dv))
            for d, dv in zip(self._docs, self._doc_vecs)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


class SentenceTransformerRetriever:
    """
    可选增强检索器：sentence-transformers 语义向量（安装后自动启用）。

    pip install sentence-transformers
    model_name 需已缓存到本地（首次使用请手动联网下载一次）。
    """
    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 offline: bool = True) -> None:
        import os
        if offline:
            # 离线模式：仅从本地缓存加载，未缓存则快速失败（避免网络重试阻塞）
            os.environ["HF_HUB_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer  # noqa: F401 延迟导入
        self._model = SentenceTransformer(model_name)
        self._docs: list[KnowledgeDoc] = []
        self._emb = None

    def build(self, docs: list[KnowledgeDoc]) -> None:
        import numpy as np
        self._docs = list(docs)
        self._emb = self._model.encode([d.text() for d in docs], normalize_embeddings=True)
        self._np = np

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        np = self._np
        q = self._model.encode([query], normalize_embeddings=True)[0]
        sims = (self._emb @ q).tolist()
        ranked = sorted(zip((d.name for d in self._docs), sims), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class HashingEncoder:
    """
    零依赖确定性稠密编码器（hashing trick + 字符 n-gram 词袋）。

    它的作用不是替代语义模型，而是让向量库后端（FAISS / Chroma）在**不安装
    任何深度学习依赖**的环境下也能被真实地跑通与回归测试：同一文本总是得到
    同一向量（用 blake2b 而非 Python 的 hash()，后者对 str 加盐、不稳定），
    且向量已 L2 归一化，于是内积即余弦相似度。

    研究/生产场景请把预训练的 sentence-transformers 模型包装成同样的
    ``encode(texts) -> list[list[float]]`` 接口传给
    :class:`VectorStoreRetriever`。
    """

    N = (2, 3)

    def __init__(self, dim: int = 256) -> None:
        if dim < 8:
            raise ValueError("hash 编码维度过小（dim >= 8）")
        self.dim = dim

    @staticmethod
    def _ngrams(text: str) -> list[str]:
        text = re.sub(r"\s+", " ", (text or "").lower()).strip()
        out: list[str] = []
        for n in HashingEncoder.N:
            out.extend(text[i:i + n] for i in range(len(text) - n + 1))
        return out

    def encode(self, texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for text in texts or []:
            vec = [0.0] * self.dim
            for token in self._ngrams(text):
                digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
                idx = int.from_bytes(digest[:4], "big") % self.dim
                vec[idx] += 1.0 if digest[4] & 1 else -1.0
            norm = math.sqrt(sum(x * x for x in vec))
            vecs.append([x / norm for x in vec] if norm > 0 else vec)
        return vecs


class _SentenceTransformerEncoder:
    """把 sentence-transformers 模型包装成与 :class:`HashingEncoder` 相同的接口。"""

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
                 offline: bool = True) -> None:
        import os
        if offline:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in
                self._model.encode(list(texts), normalize_embeddings=True)]


class VectorStoreRetriever:
    """
    可插拔**向量库**检索器：把知识库文档编码为稠密向量写入外部向量库
    （FAISS / Chroma），查询时用向量内积取 top-k。

    这是论文中 ``EmbeddingRetriever`` 协议（预留扩展点）的落地实现：检索层
    从"进程内倒排"升级为"外部向量库"，而对上层 :class:`SemanticAligner`
    完全透明——``build`` / ``retrieve`` 两个方法签名不变。

    后端（``backend`` 参数，或环境变量 ``RAG_ALIGN_VECTOR_BACKEND``）:
      - ``faiss``   : FAISS ``IndexFlatIP``（精确内积，进程内）
      - ``chroma``  : Chroma 集合（``persist_dir`` 给定时落盘为持久化客户端）
      - ``numpy``   : 纯 numpy 暴力内积（无需 faiss/chroma，用于自检/回归）
      - ``auto``    : 按 faiss -> chroma -> numpy 的顺序探测可用后端

    编码器（``encoder`` 参数）:
      - 默认 :class:`HashingEncoder`（零依赖、确定性）
      - ``encoder="st"`` 或传入带 ``encode`` 方法的对象即可切换为预训练语义向量

    注意：本类不调用任何 LLM；向量库在此只是检索基础设施。
    """

    BACKENDS = ("faiss", "numpy", "chroma")
    # 后端名 -> 实际模块名（chroma 后端用的是 chromadb 包）
    _BACKEND_MODULE = {"faiss": "faiss", "chroma": "chromadb", "numpy": "numpy"}
    # 自动探测顺序。chroma **不参与**自动探测，只能显式启用：
    # chromadb 1.x 的 Rust 绑定与 PyQt5 的原生运行库存在冲突——同一进程内先
    # import PyQt5 再调用 Collection.add() 会触发 Windows 原生 access
    # violation，Python 的 try/except 无法捕获，会直接带走宿主进程。
    # 因此 auto 只在 faiss / numpy 之间选择；chroma 需显式指定
    # （RAG_ALIGN_RETRIEVER=chroma），且建议在未加载 PyQt5 的独立进程中运行。
    AUTO_PROBE_BACKENDS = ("faiss", "numpy")

    def __init__(self, backend: str = "auto", encoder=None,
                 collection: str = "a2l_rag_kb", persist_dir: str | None = None,
                 model_name: str = "paraphrase-multilingual-MiniLM-L12-v2") -> None:
        self.backend = self._resolve_backend(backend)
        self.encoder = encoder if encoder is not None else self._default_encoder(model_name)
        self.collection = collection
        self.persist_dir = persist_dir
        self._names: list[str] = []
        self._ids: list[str] = []
        self._name_by_id: dict[str, str] = {}
        self._store = None
        self._dim = 0

    # ---- 后端与编码器选择 ----------------------------------------------------
    @classmethod
    def _resolve_backend(cls, backend: str) -> str:
        import importlib.util
        import os
        choice = str(backend or "auto").strip().lower()
        if choice == "auto":
            choice = os.environ.get("RAG_ALIGN_VECTOR_BACKEND", "").strip().lower() or "auto"
        if choice != "auto":
            if choice not in cls.BACKENDS:
                raise ValueError("未知的向量库后端 %r（可选 %s）"
                                 % (backend, " / ".join(cls.BACKENDS)))
            return choice
        for name in cls.AUTO_PROBE_BACKENDS:
            try:
                if importlib.util.find_spec(cls._BACKEND_MODULE[name]) is not None:
                    return name
            except (ImportError, ValueError):
                continue
        raise ImportError(
            "未找到可用的向量库后端（faiss / numpy 均不可用）。"
            "如需使用 chroma，请显式设置 RAG_ALIGN_RETRIEVER=chroma，"
            "并注意 chromadb 的 Rust 绑定不应与 PyQt5 同进程加载。")

    def _default_encoder(self, model_name: str):
        import os
        choice = os.environ.get("RAG_ALIGN_ENCODER", "").strip().lower()
        if choice in ("st", "sentence-transformers", "sbert"):
            try:
                return _SentenceTransformerEncoder(model_name)
            except Exception as exc:  # 模型未缓存 / 依赖缺失
                log.info("sentence-transformers 编码器不可用（%s），降级为 HashingEncoder", exc)
        return HashingEncoder()

    # ---- EmbeddingRetriever 协议 --------------------------------------------
    def build(self, docs: list[KnowledgeDoc]) -> None:
        self._names = [d.name for d in docs]
        self._ids = ["%d::%s" % (i, n) for i, n in enumerate(self._names)]
        self._name_by_id = dict(zip(self._ids, self._names))
        if not self._names:
            self._store = None
            self._dim = 0
            return
        texts = [d.text() for d in docs]
        vecs = self.encoder.encode(texts)
        self._dim = len(vecs[0]) if vecs else 0
        if self.backend == "faiss":
            import faiss
            import numpy as np
            index = faiss.IndexFlatIP(self._dim)
            index.add(np.asarray(vecs, dtype="float32"))
            self._store = index
        elif self.backend == "chroma":
            self._store = self._build_chroma(texts, vecs)
        else:
            import numpy as np
            self._store = np.asarray(vecs, dtype="float32")

    def _build_chroma(self, texts: list[str], vecs: list[list[float]]):
        import chromadb
        if self.persist_dir:
            client = chromadb.PersistentClient(path=self.persist_dir)
        else:
            client = chromadb.EphemeralClient()
        try:
            client.delete_collection(self.collection)
        except Exception:
            pass  # 集合尚不存在
        try:
            collection = client.create_collection(
                name=self.collection, metadata={"hnsw:space": "cosine"})
        except Exception:
            collection = client.create_collection(name=self.collection)
        collection.add(ids=self._ids, documents=texts,
                       embeddings=[[float(x) for x in v] for v in vecs])
        return collection

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        if self._store is None or not self._names:
            return []
        qvec = self.encoder.encode([query])[0]
        k = max(1, min(int(top_k), len(self._names)))
        if self.backend == "faiss":
            import numpy as np
            scores, ids = self._store.search(np.asarray([qvec], dtype="float32"), k)
            out: list[tuple[str, float]] = []
            for score, idx in zip(scores[0].tolist(), ids[0].tolist()):
                if idx < 0:
                    continue
                out.append((self._names[idx], float(score)))
            return out
        if self.backend == "chroma":
            res = self._store.query(query_embeddings=[[float(x) for x in qvec]], n_results=k)
            ids = (res.get("ids") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            # chroma 的 cosine 距离 = 1 - 余弦相似度
            return [(self._name_by_id.get(i, i), 1.0 - float(d)) for i, d in zip(ids, dists)]
        import numpy as np
        sims = self._store @ np.asarray(qvec, dtype="float32")
        order = np.argsort(-sims)[:k]
        return [(self._names[int(i)], float(sims[int(i)])) for i in order]


def make_default_retriever() -> EmbeddingRetriever:
    """
    检索器选择策略（可用环境变量 RAG_ALIGN_RETRIEVER 强制指定）：

      - ``st``         强制 sentence-transformers（允许联网下载模型）
      - ``charngram``  强制零依赖字符 n-gram 基线
      - ``faiss``      强制 FAISS 向量库后端
      - ``chroma``     强制 Chroma 向量库后端
      - ``vector``     强制向量库后端（具体实现由 RAG_ALIGN_VECTOR_BACKEND 决定）
      - 未设置/``auto`` 优先尝试本地缓存的语义模型（离线探测，秒级失败），
                        不可用时降级为 CharNgramRetriever
    """
    import os
    choice = os.environ.get("RAG_ALIGN_RETRIEVER", "auto").lower()
    if choice == "charngram":
        return CharNgramRetriever()
    if choice in ("faiss", "chroma", "numpy"):
        return VectorStoreRetriever(backend=choice)
    if choice == "vector":
        # 具体实现由 RAG_ALIGN_VECTOR_BACKEND 决定；未指定则自动探测
        return VectorStoreRetriever(backend="auto")
    try:
        # offline=True: 模型未缓存时立即抛错降级，不触发网络重试
        return SentenceTransformerRetriever(offline=(choice != "st"))
    except Exception as exc:  # ImportError / 模型未缓存 / 加载失败
        if choice == "st":
            raise
        log.info("sentence-transformers 不可用（%s），降级为 CharNgramRetriever", exc)
        return CharNgramRetriever()


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  3. 领域知识库（论文 Sec. 3.2，知识源 K1/K2/K3）                                ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class DomainKnowledgeBase:
    """信号级领域知识库：聚合历史配置（K2）与语义语料（K3），K1 规则复用主模块。"""

    def __init__(self, retriever: EmbeddingRetriever | None = None) -> None:
        self.retriever: EmbeddingRetriever = retriever or make_default_retriever()
        self.docs: list[KnowledgeDoc] = []
        self._by_name: dict[str, KnowledgeDoc] = {}

    # ---- 构建接口 ------------------------------------------------------------
    def add_historical_config(self, signals: Iterable[dict]) -> int:
        """K2: 从 signal_keep_config.xlsx 导出的信号 dict 列表（主模块格式）。"""
        added = 0
        for sig in signals or []:
            name = str(sig.get("Name") or sig.get("ShortName") or "").strip()
            if not name or name in self._by_name:
                continue
            doc = KnowledgeDoc(
                name=name,
                short_name=str(sig.get("ShortName") or ""),
                description=str(sig.get("Description") or ""),
                unit=str(sig.get("Unit") or ""),
                io_type=str(sig.get("Type") or ""),
                c_type=str(sig.get("CType") or sig.get("DataType") or ""),
                group=str(sig.get("Group") or ""),
                factor=_to_float(sig.get("Factor"), None),
                offset=_to_float(sig.get("Offset"), None),
                mask=str(sig.get("BitMask") or "").strip(),
                source="history",
            )
            self.docs.append(doc)
            self._by_name[name] = doc
            added += 1
        self._rebuild()
        return added

    def add_historical_config_file(self, path: str | Path) -> int:
        """K2: 直接从标定表文件（.xlsx / .csv）加载历史标注。"""
        return self.add_historical_config(load_historical_config_file(path))

    def add_semantic_corpus(self, entries: Iterable[dict], merge: bool = False) -> int:
        """
        K3: 语义语料条目。每条至少含 name，可选 desc/unit/io/c_type/group。
        种子语料可从主模块内置演示数据（ALL_SIGNALS, L4709）抽取。

        ``merge=True`` 时，已存在的同名文档（通常来自 K2 历史配置）不被跳过，
        而是用本条的非空字段补全其空缺——K2 的类型/IO/单位是历史事实，K3 的
        语义描述是补充，两者合并才不丢信息。
        """
        added = 0
        merged = False
        for e in entries or []:
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            if name in self._by_name:
                if merge:
                    self._enrich_doc(self._by_name[name],
                                     short_name=e.get("short_name", ""),
                                     description=e.get("desc", ""),
                                     unit=e.get("unit", ""),
                                     io_type=e.get("io", ""),
                                     c_type=e.get("c_type", ""),
                                     group=e.get("group", ""),
                                     factor=e.get("factor", None),
                                     offset=e.get("offset", None),
                                     mask=e.get("mask", ""))
                    merged = True
                continue
            doc = KnowledgeDoc(
                name=name,
                short_name=str(e.get("short_name") or ""),
                description=str(e.get("desc") or ""),
                unit=str(e.get("unit") or ""),
                io_type=str(e.get("io") or ""),
                c_type=str(e.get("c_type") or ""),
                group=str(e.get("group") or ""),
                factor=_to_float(e.get("factor"), None),
                offset=_to_float(e.get("offset"), None),
                mask=str(e.get("mask") or "").strip(),
                source="corpus",
            )
            self.docs.append(doc)
            self._by_name[name] = doc
            added += 1
        if added or merged:
            self._rebuild()
        return added

    @staticmethod
    def _enrich_doc(doc: KnowledgeDoc, **fields) -> None:
        """用非空字段补全已有文档的空缺（K2 与 K3 合并时不丢信息）。"""
        for key, value in (fields or {}).items():
            if value is None:
                continue
            if key in ("factor", "offset"):
                # 换算系数/偏置只有“已记录”与“未记录”两态：未记录（None）时
                # 允许被补全，已记录时 K2 的历史事实不能被 K3 覆盖。
                if getattr(doc, key, None) is None and value is not None:
                    setattr(doc, key, float(value))
                continue
            if value == "":
                continue
            if not getattr(doc, key, ""):
                setattr(doc, key, value)

    def _rebuild(self) -> None:
        self.retriever.build(self.docs)

    # ---- 查询接口 ------------------------------------------------------------
    def get(self, name: str) -> KnowledgeDoc | None:
        return self._by_name.get(name)

    def __len__(self) -> int:
        return len(self.docs)

    @staticmethod
    def type_compatible(c_type_a: str, c_type_b: str) -> bool:
        """K1 辅助：两个 C 类型是否同族（宽松：宽度+符号一致即兼容）。"""
        a, b = _normalize_c_type(c_type_a), _normalize_c_type(c_type_b)
        return (not a or not b) or a == b


# 长/短形式 C 类型 -> 统一短形式族（u8/u16/f32/...），供类型一致性判定
_LONG_TO_SHORT = {"uint8": "u8", "uint16": "u16", "uint32": "u32", "uint64": "u64",
                  "int8": "i8", "int16": "i16", "int32": "i32", "int64": "i64",
                  "float": "f32", "double": "f64", "bool": "b"}


def _normalize_c_type(t: str) -> str:
    """C 类型归一化: 'uint16_t'/'u16'/'uint16' -> 'u16'。"""
    t = (t or "").strip().lower()
    t = re.sub(r"_t$", "", t)
    return _LONG_TO_SHORT.get(t, t)


def _to_float(value, default: float = 1.0) -> float:
    """宽松地把 A2L/标定表里的数值字段转成 float（失败时返回缺省值）。"""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return float(int(text, 0))          # "0x10" 之类
    except ValueError:
        return default


_IO_ALIASES = {"m": "MEAS", "meas": "MEAS", "measurement": "MEAS", "v": "MEAS",
               "c": "CAL", "cal": "CAL", "calibration": "CAL", "k": "CAL"}


def _normalize_io(io: str) -> str:
    """IO 类别归一化: 'M'/'meas' -> 'MEAS', 'C'/'cal' -> 'CAL'。"""
    return _IO_ALIASES.get((io or "").strip().lower(), "")


# ── K2 历史配置文件的读取 ───────────────────────────────────────────────────
# A2L DATATYPE -> C 类型族（标定表用 A2L 数据类型标注，这里还原成 C 类型族，
# 才能与查询符号的类型后缀做同族判定）
_A2L_DTYPE_TO_C_TYPE = {
    "UBYTE": "u8", "SBYTE": "i8", "UWORD": "u16", "SWORD": "i16",
    "ULONG": "u32", "SLONG": "i32", "A_UINT64": "u64", "A_INT64": "i64",
    "FLOAT32_IEEE": "f32", "FLOAT64_IEEE": "f64",
}

# 标定表列名 -> 内部信号 dict 键
_HISTORY_COL_ALIASES = {
    "group": "Group", "type": "Type", "io": "Type",
    "name": "Name", "shortname": "ShortName", "longname": "LongName",
    "unit": "Unit", "min": "Min", "max": "Max",
    "factor": "Factor", "offset": "Offset",
    "datatype": "DataType", "ctype": "CType", "objecttype": "ObjectType",
    "arraysize": "ArraySize", "bitmask": "BitMask",
    "addroffset": "AddrOffset", "bitoffset": "BitOffset", "bitsize": "BitSize",
    "description": "Description",
}


def _read_history_csv(path: Path) -> list[dict]:
    """读取 CSV 标定表（纯标准库，UTF-8 BOM 兼容）。"""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = []
        for raw in reader:
            row = {}
            for key, value in raw.items():
                if key is None:
                    continue
                canon = _HISTORY_COL_ALIASES.get(str(key).strip().lower())
                if canon:
                    row[canon] = str(value or "").strip()
            rows.append(row)
    return rows


def _read_history_xlsx(path: Path) -> list[dict]:
    """读取 xlsx 标定表（需要 openpyxl；主工具本就依赖它）。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    rows: list[dict] = []
    header: dict[str, int] = {}
    try:
        for ws in wb.worksheets:
            if ws.title.startswith("_"):   # 隐藏辅助 Sheet
                continue
            header = {}
            for raw in ws.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in raw]
                if not header:
                    # 首个含 Name / ShortName 的行即表头
                    canon = [_HISTORY_COL_ALIASES.get(c.lower(), "") for c in cells]
                    if "Name" in canon or "ShortName" in canon:
                        header = {c: i for i, c in enumerate(canon) if c}
                    continue
                row = {}
                for col, idx in header.items():
                    if idx < len(cells) and cells[idx]:
                        row[col] = cells[idx]
                if row:
                    rows.append(row)
    finally:
        wb.close()
    return rows


def load_historical_config_file(path: str | Path) -> list[dict]:
    """
    读取 K2 历史配置文件（标定表导出的逐信号标注），返回信号 dict 列表。

    支持两种格式:
      - ``.csv``  : 纯标准库可读，保证零依赖环境也能复现 K2
      - ``.xlsx`` : 需要 openpyxl（主工具本就依赖它）

    返回的 dict 键与主工具 ``parse_signal_config_xlsx`` 一致
    （Name / ShortName / Type / DataType / Group / Unit / Description ...），
    其中 ``DataType`` 若是 A2L 数据类型（UBYTE 等）会被换算成 C 类型族
    （u8 等），以便知识库的类型同族判定与结构通道生效。
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError("历史配置文件不存在: %s" % p)
    suffix = p.suffix.lower()
    if suffix == ".csv":
        raw_rows = _read_history_csv(p)
    elif suffix == ".xlsx":
        raw_rows = _read_history_xlsx(p)
    else:
        raise ValueError("不支持的历史配置格式 %r（仅 .csv / .xlsx）" % suffix)

    signals: list[dict] = []
    for row in raw_rows:
        name = str(row.get("Name") or row.get("ShortName") or "").strip()
        if not name:
            continue
        dtype = str(row.get("DataType") or row.get("CType") or "").strip()
        c_type = _A2L_DTYPE_TO_C_TYPE.get(dtype.upper(), dtype)
        sig = {
            "Name": name,
            "ShortName": str(row.get("ShortName") or "").strip(),
            "Type": str(row.get("Type") or "").strip(),
            "DataType": c_type,
            "CType": c_type,
            "Group": str(row.get("Group") or "").strip(),
            "Unit": str(row.get("Unit") or "").strip(),
            "Description": str(row.get("Description") or "").strip(),
            # 物理换算与位域：位域漂移 / 换算漂移的检出依据（论文 Tab. I）
            "Factor": _to_float(row.get("Factor"), None),
            "Offset": _to_float(row.get("Offset"), None),
            "BitMask": str(row.get("BitMask") or "").strip(),
        }
        signals.append(sig)
    return signals


def iter_history_config_candidates(history_path: str | Path | None = None):
    """
    枚举 K2 历史配置文件的候选路径（按优先级）：

      1. 显式传入的 ``history_path``
      2. 环境变量 ``A2L_SIGNAL_CONFIG``
      3. 仓库 ``data/`` 目录下的 signal_keep_config.xlsx / .csv

    只返回真实存在的文件；一个都没有时产出空列表（K2 退化为"不可用"，
    不影响 K1/K3 构成的最小知识库）。
    """
    import os

    root = Path(__file__).resolve().parent
    candidates: list[Path] = []
    if history_path:
        candidates.append(Path(history_path))
    env = os.environ.get("A2L_SIGNAL_CONFIG", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(root / "data" / "signal_keep_config.xlsx")
    candidates.append(root / "data" / "signal_keep_config.csv")

    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        if cand.is_file():
            yield cand


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  4. 语义对齐器（论文 Sec. 3.3，P1 贡献）                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 证据融合权重（消融实验参数）
#
# 结构证据（I/O 类别 + 类型族）**不进入融合分数**，它只喂给后融合的契约检查
# （SemanticAligner._retier）与对齐报告。理由有三：
#   (1) 结构证据是类别级判据，只有"一致/冲突"两态，而分数需要连续可比的大小；
#   (2) 折进分数会产生"相互抵消"：IO 一致 (+0.4) 会盖过类型族冲突 (-0.3)，
#       使一个类型翻转的候选仍拿到正的结构分（净 +0.1），与"结构不一致"的
#       语义相反；
#   (3) 消融数据显示它对排序无益、对分层有害：去掉结构通道后 Top-1 不变，
#       而 AUTO 覆盖率 90.1% -> 93.6%、干扰误报 3.3% -> 1.7%。
# 因此分数只由检索与词元两个通道决定，有效权重 2/3 : 1/3（构造函数内归一化）。
W_RETRIEVAL = 0.6
W_TOKEN = 0.3
W_STRUCTURE = 0.0


class SemanticAligner:
    """
    置信度分层的信号语义对齐器。

    融合两类**打分**证据（evidence dict 逐项记录，供论文消融分析）:
      retrieval  检索相似度（K2/K3 知识库 top-1）
      token      语义词元 Jaccard（结构化分解后）
    外加一类**判据**证据（不进入分数，只驱动后融合契约检查与对齐报告）:
      structure  结构一致性（IO 后缀 + 类型后缀）
    精确匹配直接置信 1.0（与现有工具行为兼容的快路径）。
    """

    def __init__(self, kb: DomainKnowledgeBase, tau_auto: float = TAU_AUTO,
                 tau_suggest: float = TAU_SUGGEST,
                 w_retrieval: float = W_RETRIEVAL, w_token: float = W_TOKEN,
                 w_structure: float = W_STRUCTURE,
                 enable_type_penalty: bool = True,
                 penalty_factor: float = 0.55) -> None:
        self.kb = kb
        self.tau_auto, self.tau_suggest = tau_auto, tau_suggest
        # 类型/IO 冲突惩罚因子（乘法降权，0.55 使典型 AUTO 分数降入 SUGGEST 区间）
        self.penalty_factor = penalty_factor
        # 证据融合权重与惩罚开关：构造参数化以支持论文消融实验
        # （w_* 任一为 0 即消融对应通道）
        self.w_retrieval, self.w_token, self.w_structure = w_retrieval, w_token, w_structure
        self.enable_type_penalty = enable_type_penalty
        # 归一化：非零权重之和为 1。结构通道权重为 0 时，有效权重为
        # 2/3（检索）: 1/3（词元），保证 s 落在 [0,1]，且消融配置之间
        # 分数尺度可比（不会把"被删通道的权重缺失"混进消融对比）。
        total = self.w_retrieval + self.w_token + self.w_structure
        if total > 0:
            self.w_retrieval /= total
            self.w_token /= total
            self.w_structure /= total

    def align(self, symbol: str, context: dict | None = None, top_k: int = 5) -> AlignmentResult:
        context = context or {}
        symbol = (symbol or "").strip()
        if not symbol:
            return AlignmentResult(query=symbol)
        if self.kb.get(symbol) is not None:  # 快路径：精确匹配
            exact = AlignmentResult(query=symbol, best_match=symbol, score=1.0,
                                    candidates=[(symbol, 1.0)],
                                    evidence={"exact": True, "retrieval": 1.0,
                                              "token": 1.0, "structure": 1.0})
            # 用实例阈值（而非模块级默认值）定层：论文实验的 tau 是校准出来的
            exact.tier = _decide_tier(exact.score, self.tau_auto, self.tau_suggest)
            return exact

        cands = self.kb.retriever.retrieve(self._query_text(symbol), top_k=top_k)
        if not cands:
            return AlignmentResult(query=symbol)

        sym_dec = decompose_signal_name(symbol)
        sym_tok = set(sym_dec["semantic_tokens"])

        best_name, best_evidence, best_score = "", {}, -1.0
        all_scored: list[tuple[str, float]] = []
        for cand_name, ret_score in cands:
            doc = self.kb.get(cand_name)
            cand_dec = decompose_signal_name(cand_name)
            cand_tok = set(cand_dec["semantic_tokens"])

            token_score = _jaccard(sym_tok, cand_tok)
            structure = self._structure_score(sym_dec, cand_dec, doc, context)
            score = self.w_retrieval * ret_score + self.w_token * token_score \
                + self.w_structure * structure
            all_scored.append((cand_name, score))
            if score > best_score:
                best_name, best_score = cand_name, score
                best_evidence = {"exact": False, "retrieval": round(ret_score, 4),
                                 "token": round(token_score, 4),
                                 "structure": round(structure, 4)}

        best_score = max(0.0, min(1.0, best_score))
        result = AlignmentResult(query=symbol, best_match=best_name,
                                 score=round(best_score, 4),
                                 candidates=[(n, round(s, 4)) for n, s in all_scored],
                                 evidence=best_evidence)
        return self._retier(result)

    def _retier(self, result: AlignmentResult) -> AlignmentResult:
        """
        事后安全约束：类型族冲突或 IO 类别冲突（测量<->标定）时降权，
        再按校准阈值重新分层。IO/类型冲突会改变 A2L 对象类别与记录布局，
        属于必须人工确认的类别级错误：分数乘 penalty_factor 降权之外，
        最高层硬封顶为 SUGGEST（对齐到"类别冲突的对象"作为自动写入
        永远不安全，无论检索/词法分数多高）。
        """
        if result.best_match and self.enable_type_penalty:
            doc = self.kb.get(result.best_match)
            sym_dec = decompose_signal_name(result.query)
            if doc:
                conflict = False
                sym_type = sym_dec["c_type"]
                if sym_type and doc.c_type and not DomainKnowledgeBase.type_compatible(sym_type, doc.c_type):
                    conflict = True
                    result.evidence["type_penalty"] = True
                sym_io = sym_dec["io_type"]            # CAL / MEAS / ""
                doc_io = _normalize_io(doc.io_type)
                if sym_io and doc_io and sym_io != doc_io:
                    conflict = True
                    result.evidence["io_penalty"] = True
                if conflict:
                    result.score = round(result.score * self.penalty_factor, 4)
                    result.evidence["hard_cap"] = True
                    # 降权后必须用**实例阈值**重新分层：AlignmentResult 的默认
                    # 分层用的是模块级默认阈值 (0.85/0.50)，与实验校准出来的
                    # tau_auto/tau_suggest 不同；若不重算，冲突条目落在 SUGGEST
                    # 还是 MANUAL 就由默认阈值决定，而不是论文定义的语义。
                    result.tier = _decide_tier(result.score,
                                               self.tau_auto, self.tau_suggest)
                    # 硬封顶：类别冲突的候选永远不得自动接受
                    if result.tier == "AUTO":
                        result.tier = "SUGGEST"
                    return result
        result.tier = _decide_tier(result.score, self.tau_auto, self.tau_suggest)
        return result

    def _structure_score(self, sym: dict, cand: dict, doc: KnowledgeDoc | None,
                         context: dict) -> float:
        """结构一致性: IO 后缀 + 类型后缀 + 符号大小（可选 context['size']）。"""
        score = 0.0
        if sym["io_suffix"] and cand["io_suffix"]:
            score += 0.4 if sym["io_suffix"] == cand["io_suffix"] else -0.4
        elif doc and doc.io_type and sym["io_type"]:
            score += 0.4 if sym["io_type"] == _normalize_io(doc.io_type) else -0.4
        if sym["type_suffix"] and cand["type_suffix"]:
            score += 0.3 if sym["type_suffix"] == cand["type_suffix"] else -0.3
        elif doc and doc.c_type and sym["c_type"]:
            score += 0.3 if DomainKnowledgeBase.type_compatible(sym["c_type"], doc.c_type) else -0.3
        size = context.get("size")
        if size and doc:
            expected = _C_TYPE_WIDTH.get(doc.c_type)
            if expected and size != expected:
                score -= 0.1  # 符号大小与候选类型宽度不符，轻惩罚
        return max(-1.0, min(1.0, score))

    @staticmethod
    def _query_text(symbol: str) -> str:
        """检索查询文本 = 名称分解词元 + 原名（兼顾字符级与语义级信息）。"""
        return f"{decompose_to_text(symbol)} {symbol}".strip()


def _decide_tier(score: float, tau_auto: float, tau_suggest: float) -> AlignmentTier:
    if score >= tau_auto:
        return "AUTO"
    if score >= tau_suggest:
        return "SUGGEST"
    return "MANUAL"


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


_C_TYPE_WIDTH = {"u8": 1, "u16": 2, "u32": 4, "u64": 8,
                 "i8": 1, "i16": 2, "i32": 4, "i64": 8, "f32": 4, "f64": 8, "b": 1}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  5. 元数据自动补全（论文 Sec. 3.4，P2 贡献）                                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

class MetadataCompleter:
    """
    检索式元数据补全：对缺失 Unit/Description 的信号，从知识库检索相似信号并
    提议 Unit 等元数据，附带置信度；已有手工值不覆盖（尊重现有证据优先级，
    与主模块 L3396-3428 的 manual->auto 分层一致）。
    """

    METADATA_FIELDS = ("Unit", "Description")

    def __init__(self, kb: DomainKnowledgeBase, aligner: SemanticAligner | None = None,
                 min_score: float = 0.35) -> None:
        self.kb = kb
        self.aligner = aligner or SemanticAligner(kb)
        # 建议性补全阈值低于对齐阈值: 不覆盖人工值、仅作为提示呈现给工程师
        self.min_score = min_score

    def complete(self, signal: dict, top_k: int = 3) -> dict:
        """返回 {字段: {"value": ..., "score": ..., "source_name": ...}} 建议集。"""
        name = str(signal.get("Name") or signal.get("ShortName") or "")
        result = self.aligner.align(name, top_k=top_k)
        suggestions: dict = {}
        for cand_name, score in result.candidates[:top_k]:
            doc = self.kb.get(cand_name)
            if not doc or score < self.min_score:
                continue
            for fld in self.METADATA_FIELDS:
                value = doc.unit if fld == "Unit" else doc.description
                current = str(signal.get(fld) or "").strip()
                if not value or current:  # 已有手工值不覆盖
                    continue
                if fld not in suggestions or score > suggestions[fld]["score"]:
                    suggestions[fld] = {"value": value, "score": round(score, 4),
                                        "source_name": cand_name}
        return suggestions


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  6. 增量一致性检查（论文 Sec. 3.5，P3 贡献）                                    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

@dataclass
class ConsistencyReport:
    """增量更新一致性报告（补齐主模块 L3221 未使用的 existing_a2l_names 缺口）。"""
    duplicates: list[str] = field(default_factory=list)        # 新增对象与已有对象重名
    type_conflicts: list[dict] = field(default_factory=list)   # 同名但 DataType 不一致
    mask_conflicts: list[dict] = field(default_factory=list)   # 同名但位域掩码不一致
    conversion_conflicts: list[dict] = field(default_factory=list)  # 同名但换算系数/偏置不一致
    removed_orphans: list[str] = field(default_factory=list)   # 已有但本轮不再出现的对象
    aligned_renames: list[dict] = field(default_factory=list)  # RAG 判定的改名映射

    @property
    def is_clean(self) -> bool:
        """没有任何**错误**（重名、类型/位域/换算冲突）。孤儿与改名是提示，不是错误。"""
        return not (self.duplicates or self.type_conflicts
                    or self.mask_conflicts or self.conversion_conflicts)

    @property
    def has_issues(self) -> bool:
        """是否存在任何需要工程师确认的差异（含孤儿与改名提示）。"""
        return bool(self.duplicates or self.type_conflicts
                    or self.mask_conflicts or self.conversion_conflicts
                    or self.removed_orphans or self.aligned_renames)


def _normalize_mask(mask: str) -> str:
    """位域掩码归一化: '0x0f' / '0F' / '15' -> '0x0f'（便于逐位比较）。"""
    text = str(mask or "").strip().lower()
    if not text:
        return ""
    try:
        value = int(text, 0)
    except ValueError:
        try:
            value = int(text, 16)
        except ValueError:
            return text
    return "0x%x" % value


def _value_drifted(new: float | None, old: float | None) -> bool:
    """换算系数/偏置是否发生了相对变化（相对判据，才能覆盖 1e-12 这类小量级）。"""
    if new is None or old is None:
        return False
    if old == 0.0:
        return abs(new) > 1e-12
    return abs(new - old) / abs(old) > 1e-9


def check_incremental_consistency(new_signals: list[dict],
                                  existing_a2l_names: set[str] | list[str],
                                  aligner: SemanticAligner,
                                  tau_auto: float = TAU_AUTO,
                                  tau_rename: float | None = None) -> ConsistencyReport:
    """
    对照已有 A2L 对象集合检查本轮待写入信号。

    - 重名检测（确定性）
    - 类型冲突检测（确定性，新类型 vs 知识库中该信号的历史类型）
    - 孤儿检测（确定性，提示工程师确认是否删除）
    - 改名识别（RAG）：新名字对"未复用的旧对象名"做检索对齐，
      命中且分数 >= tau_rename => 报告为候选 rename（供人工确认）。
      tau_rename 缺省继承 aligner.tau_suggest（与对齐阈值同源校准），
      临时知识库尽量复用主知识库的完整语义文档（描述/单位/IO/类型）。
    """
    report = ConsistencyReport()
    existing = {str(n).strip() for n in (existing_a2l_names or []) if str(n).strip()}
    if tau_rename is None:
        tau_rename = aligner.tau_suggest

    seen_new: set[str] = set()
    for sig in new_signals or []:
        # A2L 对象名的取值规则必须与主模块 generate_a2l_block 一致（ShortName 优先，
        # 否则用 Name）。结构体展开后同一组信号的 Name 相同（基符号名）而 ShortName
        # 各不相同（成员路径）；若按 Name 去重，会把一次正常的结构体展开误判成
        # 成百上千个「名称重复」。
        nm = str(sig.get("ShortName") or sig.get("Name") or "").strip()
        if not nm:
            continue
        if nm in seen_new:
            report.duplicates.append(nm)
        seen_new.add(nm)
        if nm in existing:
            # 同名：检查类型/位域/换算是否相对历史知识漂移
            doc = aligner.kb.get(nm)
            old_type = doc.c_type if doc else ""
            new_type = str(sig.get("DataType") or sig.get("CType") or "")
            if old_type and new_type and not DomainKnowledgeBase.type_compatible(old_type, new_type):
                report.type_conflicts.append({"name": nm, "old": old_type, "new": new_type})
            if doc is not None:
                # 位域漂移：掩码不再隔离原本的位（论文 Tab. I "bit-layout loss"）
                old_mask = _normalize_mask(doc.mask)
                new_mask = _normalize_mask(sig.get("BitMask") or sig.get("Mask") or "")
                if old_mask and new_mask and old_mask != new_mask:
                    report.mask_conflicts.append(
                        {"name": nm, "old": old_mask, "new": new_mask})
                # 换算漂移：系数/偏置变了，物理值随之成比例偏移
                # （论文 Tab. I "scaling loss"）
                new_factor = _to_float(sig.get("Factor"), None)
                new_offset = _to_float(sig.get("Offset"), None)
                if (_value_drifted(new_factor, doc.factor)
                        or _value_drifted(new_offset, doc.offset)):
                    report.conversion_conflicts.append(
                        {"name": nm,
                         "old": {"factor": doc.factor, "offset": doc.offset},
                         "new": {"factor": new_factor if new_factor is not None else doc.factor,
                                 "offset": new_offset if new_offset is not None else doc.offset}})

    report.removed_orphans = sorted(existing - seen_new)

    # RAG 改名识别：新名对"未复用的旧对象名"检索对齐
    orphans = report.removed_orphans
    fresh = [nm for nm in seen_new if nm not in existing]
    if orphans and fresh:
        tmp_kb = DomainKnowledgeBase(retriever=type(aligner.kb.retriever)())
        # 语义回填：旧对象名优先复用主知识库完整文档（K3 描述/单位提升检索质量），
        # 主库未收录的退化为 name-only
        entries = []
        for n in orphans:
            doc = aligner.kb.get(n)
            if doc is not None:
                entries.append({"name": doc.name, "short_name": doc.short_name,
                                "desc": doc.description, "unit": doc.unit,
                                "io": doc.io_type, "c_type": doc.c_type,
                                "group": doc.group})
            else:
                entries.append({"name": n})
        tmp_kb.add_semantic_corpus(entries)
        tmp_aligner = SemanticAligner(tmp_kb, tau_auto=tau_auto,
                                      tau_suggest=aligner.tau_suggest)
        for nm in fresh:
            res = tmp_aligner.align(nm)
            if res.best_match and res.score >= tau_rename:
                report.aligned_renames.append(
                    {"old": res.best_match, "new": nm, "score": res.score})
    return report


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  7. 集成入口（与主模块 do_update_a2l 的对接点）                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def align_symbols_for_update(symbols: list[dict], kb: DomainKnowledgeBase,
                             log_cb: Callable[[str], None] | None = None) -> dict[str, AlignmentResult]:
    """
    批量对齐：为地址文件中的符号（sym_details 项，含 name/size）寻找配置信号。

    返回 {符号名: AlignmentResult}。AUTO/SUGGEST 结果可回填到主流程地址匹配
    （do_update_a2l 第4步, L3372-3471），MANUAL 保持现状走人工确认。
    """
    aligner = SemanticAligner(kb)
    results: dict[str, AlignmentResult] = {}
    for sym in symbols or []:
        name = str(sym.get("name") or sym.get("Name") or "").strip()
        if not name:
            continue
        res = aligner.align(name, context={"size": sym.get("size") or sym.get("Size")})
        results[name] = res
        if log_cb:
            log_cb(f"[{res.tier}] {name} -> {res.best_match or '<manual>'} "
                   f"(score={res.score}, evidence={res.evidence})")
    return results


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  5. 对齐报告与专家复核队列（论文 Fig. 的 "Alignment report" / "Expert review"）   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _fmt_evidence(evidence: dict) -> str:
    """把 evidence dict 渲染成一行可读文本（通道分数 + 安全约束标记）。"""
    if not evidence:
        return "(none)"
    parts = []
    for k in ("retrieval", "token", "structure"):
        if k in evidence:
            try:
                parts.append("%s=%.4f" % (k, float(evidence[k])))
            except (TypeError, ValueError):
                parts.append("%s=%s" % (k, evidence[k]))
    if evidence.get("exact"):
        parts.append("exact match")
    for k, v in evidence.items():
        if k in ("retrieval", "token", "structure", "exact") or isinstance(v, bool):
            continue
        parts.append("%s=%s" % (k, v))
    return " ".join(parts) if parts else "(none)"


def review_queue(results: dict[str, AlignmentResult]) -> list[AlignmentResult]:
    """需要专家人工确认的条目（SUGGEST / MANUAL），按分数降序、同分按符号名。

    AUTO 条目无需人工介入；这里只返回证据不足或被后融合安全约束降级的条目，
    对应论文流程图中 "Expert review" 阶段只处理歧义样本。
    """
    pending = [r for r in results.values() if r.tier in ("SUGGEST", "MANUAL")]
    pending.sort(key=lambda r: (-r.score, r.query))
    return pending


def render_alignment_report(results: dict[str, AlignmentResult],
                             title: str = "A2L-RAGAlign alignment report") -> str:
    """把对齐结果渲染为可人工审查的文本报告。

    报告分两段：自动采纳的条目（AUTO，附证据），以及需要专家确认的复核队列
    （SUGGEST / MANUAL，附证据、约束标记与 top-3 候选）。该文本与主流程已有的
    ``*_match_report.txt`` / ``*_diff_report.txt`` 共同构成可审计产物。
    """
    counts = {"AUTO": 0, "SUGGEST": 0, "MANUAL": 0}
    for r in results.values():
        counts[r.tier] = counts.get(r.tier, 0) + 1
    queue = review_queue(results)

    lines = [title, "=" * len(title), ""]
    lines.append("queries=%d   AUTO=%d   SUGGEST=%d   MANUAL=%d"
                 % (len(results), counts["AUTO"], counts["SUGGEST"], counts["MANUAL"]))
    lines.append("")
    lines.append("-- auto-accepted ---------------------------------------------")
    for r in sorted(results.values(), key=lambda r: r.query):
        if r.tier != "AUTO":
            continue
        lines.append("[AUTO   ] %s -> %s  (score=%.4f)"
                     % (r.query, r.best_match, r.score))
        lines.append("          evidence: %s" % _fmt_evidence(r.evidence))
    lines.append("")
    lines.append("-- review queue (expert sign-off required) --------------------")
    if not queue:
        lines.append("(empty: every query was auto-accepted)")
    for r in queue:
        flags = [k for k in ("type_penalty", "io_penalty", "hard_cap")
                 if r.evidence.get(k)]
        fl = "  flags=%s" % ",".join(flags) if flags else ""
        lines.append("[%-7s] %s -> %s  (score=%.4f)%s"
                     % (r.tier, r.query, r.best_match or "(no candidate)", r.score, fl))
        lines.append("          evidence: %s" % _fmt_evidence(r.evidence))
        if r.candidates:
            lines.append("          candidates: %s"
                         % ", ".join("%s=%.4f" % (n, s) for n, s in r.candidates[:3]))
    lines.append("")
    lines.append("summary: %d auto-accepted, %d require expert review"
                 % (counts["AUTO"], len(queue)))
    return "\n".join(lines) + "\n"


def write_alignment_report(results: dict[str, AlignmentResult], path) -> Path:
    """把对齐报告落盘（可审计产物），返回写入的路径。"""
    p = Path(path)
    if str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_alignment_report(results), encoding="utf-8")
    return p


def build_kb_from_main_module(kb: DomainKnowledgeBase | None = None,
                              history_path: str | Path | None = None) -> DomainKnowledgeBase:
    """
    便捷工厂：从主模块内置演示语料（K3）+ 历史标定表（K2）构建知识库。

    - K3: 优先从主模块源码提取 ALL_SIGNALS 完整演示数据集（83 信号，含单位/
      量程/语义描述）；主模块不可用时退回内置精简种子 _SEED_CORPUS。
    - K2: 若工作空间中存在 signal_keep_config.xlsx / .csv（历史标定表），
      则一并载入。K2 的类型 / IO 类别 / 单位是历史事实，优先于 K3；K3 只补
      齐 K2 缺失的语义描述，二者合并为同一份知识库。
    - K1: 规则（类型后缀表、IO 后缀表、类型同族判定）内建于本模块。

    ``history_path`` 可显式指定标定表路径；缺省时按
    :func:`iter_history_config_candidates` 的顺序自动探测。
    """
    kb = kb or DomainKnowledgeBase()
    # K2 先入：历史标定表的类型/IO/单位是权威值，不能被 K3 覆盖
    history_rows: list[dict] = []
    for cand in iter_history_config_candidates(history_path):
        try:
            rows = load_historical_config_file(cand)
        except Exception as exc:
            log.warning("读取历史配置失败（%s）: %s", cand, exc)
            continue
        if rows:
            log.info("载入 K2 历史配置 %s（%d 条）", cand, len(rows))
            history_rows = rows
            break
    if history_rows:
        kb.add_historical_config(history_rows)
    # K3 后入并合并：补齐 K2 未记录的描述/分组
    corpus = load_corpus_from_main_source() or _SEED_CORPUS
    kb.add_semantic_corpus(corpus, merge=True)
    return kb


def load_corpus_from_main_source(main_path: str | None = None) -> list[dict]:
    """
    从主模块源码文本中提取 ALL_SIGNALS 内置演示数据集（K3 语料，~60 信号）。

    演示行格式（L4709-4900）:
      [Group, IO, Name, ShortName, DisplayDesc, Unit, Min, Max, Factor, Offset,
       CType, ObjectType, Mask, Description]
    直接读源码文本而非 import，避免触发主模块的 PyQt5 依赖。
    """
    import ast as _ast
    path = main_path or (Path(__file__).with_name("A2L_Update_Tool_main.py"))
    try:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("读取主模块源码失败: %s", exc)
        return []

    m = re.search(r"ALL_SIGNALS\s*=\s*\{", src)
    if not m:
        return []
    start = m.end() - 1
    depth, i = 0, start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = src[start:i + 1]

    corpus: list[dict] = []
    for row_m in re.finditer(r"\[[^\[\]]*\]", block):
        try:
            row = _ast.literal_eval(row_m.group(0))
        except (ValueError, SyntaxError):
            continue
        if not (isinstance(row, list) and len(row) >= 14 and isinstance(row[0], str)
                and row[2].strip()):
            continue
        corpus.append({
            "name": str(row[2]).strip(),
            "short_name": str(row[3]).strip(),
            "io": str(row[1]).strip(),
            "unit": str(row[5]).strip(),
            "c_type": str(row[10]).strip(),
            "group": str(row[0]).strip(),
            "desc": str(row[13] or row[4] or "").strip(),
            # 换算系数 / 偏置 / 位域掩码：位域漂移与换算漂移的检出依据
            "factor": _to_float(row[8], None),
            "offset": _to_float(row[9], None),
            "mask": str(row[12] or "").strip(),
        })
    return corpus


# 种子语义语料（K3 初始版本：源自主模块 L4709-4900 内置演示数据集的精简快照）
_SEED_CORPUS: list[dict] = [
    {"name": "Eng_IdleRpm_u8_C", "short_name": "IdleRpm_C", "io": "CAL", "unit": "rpm",
     "c_type": "u8", "group": "Engine", "desc": "uint8_t: idle speed setpoint (50~250 rpm, step 1)"},
    {"name": "Eng_MaxRpm_u16_C", "short_name": "MaxRpm_C", "io": "CAL", "unit": "rpm",
     "c_type": "u16", "group": "Engine", "desc": "uint16_t: maximum engine speed limit"},
    {"name": "Eng_Speed_u16_M", "short_name": "EngSpd_M", "io": "MEAS", "unit": "rpm",
     "c_type": "u16", "group": "Engine", "desc": "uint16_t: measured engine speed"},
    {"name": "Eng_Torque_f32_M", "short_name": "EngTq_M", "io": "MEAS", "unit": "Nm",
     "c_type": "f32", "group": "Engine", "desc": "float: actual torque output"},
    {"name": "Eng_CoolantT_i8_M", "short_name": "CoolantT_M", "io": "MEAS", "unit": "degC",
     "c_type": "i8", "group": "Engine", "desc": "int8_t: coolant temperature (-40~127 degC)"},
    {"name": "Eng_FuelGain_f32_C", "short_name": "FuelGain_C", "io": "CAL", "unit": "",
     "c_type": "f32", "group": "Engine", "desc": "float: fuel injection gain factor (0.5~2.0)"},
    {"name": "Bat_Volt_u16_M", "short_name": "BatVolt_M", "io": "MEAS", "unit": "V",
     "c_type": "u16", "group": "Battery", "desc": "uint16_t: battery voltage"},
    {"name": "Bat_Soc_u8_M", "short_name": "Soc_M", "io": "MEAS", "unit": "%",
     "c_type": "u8", "group": "Battery", "desc": "uint8_t: state of charge"},
    {"name": "Trn_OilLmt_u16_C", "short_name": "OilLmt_C", "io": "CAL", "unit": "kPa",
     "c_type": "u16", "group": "Trans", "desc": "uint16_t: oil pressure limit"},
    {"name": "Trn_GearPos_u8_M", "short_name": "GearPos_M", "io": "MEAS", "unit": "",
     "c_type": "u8", "group": "Trans", "desc": "uint8_t: current gear position"},
    {"name": "Hvac_CabinT_i16_M", "short_name": "CabinT_M", "io": "MEAS", "unit": "degC",
     "c_type": "i16", "group": "HVAC", "desc": "int16_t: cabin temperature"},
    {"name": "Veh_Speed_u16_M", "short_name": "VehSpd_M", "io": "MEAS", "unit": "km/h",
     "c_type": "u16", "group": "VehCtrl", "desc": "uint16_t: vehicle speed"},
]


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  8. 演示与自检                                                                 ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _demo() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    kb = DomainKnowledgeBase()          # 默认零依赖检索器；装 st 后自动升级
    kb.add_semantic_corpus(_SEED_CORPUS)
    print(f"知识库文档数: {len(kb)}  检索器: {type(kb.retriever).__name__}\n")

    aligner = SemanticAligner(kb)
    demo_symbols = [
        "Eng_Speed_u16_M",          # 精确匹配（快路径）
        "EngineSpeed_meas",         # 命名不一致：蛇形 vs 驼峰 + meas 后缀
        "eng_spd",                  # 缩写 + 无后缀
        "VehSpd",                   # 驼峰缩写
        "g_cfg.accRange",           # 结构体成员路径（DWARF 展开后）
        "Unknown_Zzz_x9_Q",         # 应落入 MANUAL
    ]
    print("== P1 语义对齐 ==")
    for sym in demo_symbols:
        r = aligner.align(sym, context={"size": 2})
        print(f"  [{r.tier:7s}] {sym:24s} -> {r.best_match or '<manual>':20s} "
              f"score={r.score:<6} evidence={r.evidence}")

    print("\n== P2 元数据补全 ==")
    completer = MetadataCompleter(kb)
    sig = {"Name": "EngineSpeed_meas", "Unit": "", "Description": ""}
    for fld, s in completer.complete(sig).items():
        print(f"  {fld:12s} = {s['value']!r}  (score={s['score']}, from {s['source_name']})")

    print("\n== P3 增量一致性 ==")
    new_signals = [
        {"Name": "Eng_Speed_u16_M", "DataType": "u16"},      # 重名+类型一致
        {"Name": "Eng_Speed_u16_M", "DataType": "f32"},      # 重名+类型冲突
        {"Name": "Bat_Voltage_u16_M", "DataType": "u16"},    # 疑似由 Bat_Volt_u16_M 改名
    ]
    existing = ["Eng_Speed_u16_M", "Bat_Volt_u16_M", "Trn_GearPos_u8_M", "Old_Signal_u8_M"]
    report = check_incremental_consistency(new_signals, existing, aligner)
    print(f"  duplicates     : {report.duplicates}")
    print(f"  type_conflicts : {report.type_conflicts}")
    print(f"  renames        : {report.aligned_renames}")
    print(f"  orphan(待确认) : {report.removed_orphans}")


def _selftest() -> int:
    """轻量自检（不依赖 pytest）：断言核心链路可用。"""
    kb = DomainKnowledgeBase(retriever=CharNgramRetriever())
    kb.add_semantic_corpus(_SEED_CORPUS)
    aligner = SemanticAligner(kb)

    r1 = aligner.align("Eng_Speed_u16_M")
    assert r1.tier == "AUTO" and r1.best_match == "Eng_Speed_u16_M", r1

    r2 = aligner.align("EngineSpeed_meas")
    assert r2.best_match, "改名信号应检索到候选"

    sig = {"Name": "VehSpd", "Unit": ""}
    got = MetadataCompleter(kb).complete(sig).get("Unit")
    assert got and got["value"] == "km/h", got

    rep = check_incremental_consistency(
        [{"Name": "Eng_Speed_u16_M", "DataType": "f32"},
         {"Name": "Eng_Speed_u16_M", "DataType": "u16"}],
        ["Eng_Speed_u16_M"], aligner)
    assert rep.duplicates, "应检测到重复"
    print("selftest OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="A2L-RAGAlign 原型 (RAG 语义对齐)")
    parser.add_argument("--demo", action="store_true", help="运行内置演示")
    parser.add_argument("--selftest", action="store_true", help="运行内置自检")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.selftest:
        return _selftest()
    _demo()
    return 0


if __name__ == "__main__":
    sys.exit(main())

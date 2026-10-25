r"""
run_experiments.py — A2L-RAGAlign 实验框架（论文 Sec. 5 评测）

实验设计（对应论文 RQ1-RQ4）：
  RQ1 对齐准确率:  Ours vs Exact / Levenshtein / BM25 / token-Jaccard /
                   Retrieval-only（Top-1/Top-5/MRR）+ identity 对照
  RQ2 消融实验:    打分通道（retrieval/token）+ 后融合约束 + 知识源
                   （结构证据不进入分数，只驱动后融合契约检查，见
                    rag_align.W_STRUCTURE）
  RQ3 置信度分层:  分层分布/精度/工作量节省 + 阈值敏感性扫描
  RQ4 下游任务:    元数据补全准确率、增量一致性检出率（类型漂移/重名/改名）
  安全评测:        混淆对（IO 翻转/类型翻转，正确行为=不得 AUTO）上的
                   误报率对比 —— 融合与惩罚通道相对纯检索的安全增益

方法论（论文 Sec. 5.1/5.3）：
  语料: 主模块内置演示数据集 ALL_SIGNALS（85 个信号，含单位/量程/语义描述）
  查询: 9 类真实重命名变换（含 camel+缩写展开等多跳组合，字符串基线显著退化）
  负样本: 语料词元随机重组的不存在信号（正确行为 = 不给 AUTO）
  混淆对: IO 后缀/类型宽度翻转的"孪生"查询（词法与语义证据均指向错误对象，
          仅结构证据可判别，正确行为 = 降层至 SUGGEST/MANUAL）
  划分: 按信号 50/50 划分校准集/测试集（阈值校准与评测无泄漏）
  阈值校准: 网格搜索 tau_auto/tau_suggest，满足 AUTO 精度>=98%、
            负样本误报<=5% 约束下最大化覆盖率

输出：
  experiments/results/results.json        原始指标（论文引用数字的唯一来源）
  paper/tables/tab_*.tex                  LaTeX 结果表（\input 直接引用）
  控制台摘要

用法: py -3.12 experiments/run_experiments.py
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("RAG_ALIGN_RETRIEVER", "charngram")  # 零依赖可复现

import rag_align as ra  # noqa: E402

RESULTS_DIR = HERE / "results"
TABLES_DIR = ROOT / "paper" / "tables"
TOP_K = 5
SEED = 20261011

# 阈值校准约束
TARGET_AUTO_PRECISION = 0.98
TARGET_SUGGEST_PRECISION = 0.85
TARGET_AUTO_FALSE_POSITIVE = 0.05  # 负样本（不存在的信号）误入 AUTO 的比例上限

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  1. 基准构建（论文 Sec. 5.1）                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# 领域常用缩写 <-> 全称（源自 ECU 标定工程命名习惯）
_ABBREV = {
    "eng": "Engine", "spd": "Speed", "tq": "Torque", "temp": "Temperature",
    "volt": "Voltage", "pres": "Pressure", "cur": "Current", "pos": "Position",
    "bat": "Battery", "trn": "Transmission", "hvac": "HVAC",
    "veh": "Vehicle", "soc": "Soc", "rpm": "RPM",
}
_EXPAND = {v.lower(): k for k, v in _ABBREV.items()}
_TYPE_TOKENS = set(ra._TYPE_SUFFIX_RULES) | {"cal", "meas", "c", "m"}


def _apply_token_map(name: str, mapping: dict[str, str]) -> str:
    out = []
    for tok in name.split("_"):
        low = tok.lower()
        out.append(mapping.get(low, tok) if low not in _TYPE_TOKENS else tok)
    return "_".join(out)


def _to_camel(name: str) -> str:
    words = [w for w in name.split("_") if w and w.lower() not in _TYPE_TOKENS]
    if not words:
        return name
    return words[0] + "".join(w.capitalize() for w in words[1:])


def make_variant(name: str, kind: str) -> str:
    """对规范信号名施加一类真实重命名变换。"""
    if kind == "lower":                 # T1 全小写（map/nm 输出风格）
        return name.lower()
    if kind == "camel":                 # T2 驼峰 + 去后缀（应用层代码风格）
        return _to_camel(name)
    if kind == "abbrev":                # T3 全称 -> 缩写（跨团队命名习惯）
        return _apply_token_map(name, _EXPAND)
    if kind == "expand":                # T4 缩写 -> 全称
        return _apply_token_map(name, _ABBREV)
    if kind == "suffix_drop":           # T5 丢弃类型/IO 后缀（简写配置）
        return re.sub(r"_?[a-z]\d+_[CMcm]$", "", name)
    if kind == "struct_path":           # T6 DWARF 结构体成员路径
        return "g_cfg." + name
    if kind == "io_token":              # T7 IO 后缀换词元（meas/cal 全称）
        return re.sub(r"_[CM]$", lambda m: "_meas" if m.group() == "_M" else "_cal", name)
    if kind == "camel_expand":          # T8 多跳: 缩写展开 + 驼峰（字符串差异最大）
        return _to_camel(_apply_token_map(name, _ABBREV))
    if kind == "camel_abbrev":          # T9 多跳: 全称缩写 + 驼峰
        return _to_camel(_apply_token_map(name, _EXPAND))
    raise ValueError(kind)


TRANSFORMS = ["lower", "camel", "abbrev", "expand", "suffix_drop", "struct_path",
              "io_token", "camel_expand", "camel_abbrev"]
# 多跳组合变换（字符串相似度基线在此显著退化，论文重点分析）
HARD_TRANSFORMS = {"camel_expand", "camel_abbrev"}


def build_benchmark(corpus: list[dict]) -> tuple[list[dict], list[str]]:
    """返回 (查询对 [{query,target,transform,unit,c_type}], 负样本查询列表)。"""
    corpus_names = {e["name"] for e in corpus}
    pairs: list[dict] = []
    for e in corpus:
        for kind in TRANSFORMS:
            q = make_variant(e["name"], kind)
            if not q or q == e["name"] or q in corpus_names:
                continue
            pairs.append({"query": q, "target": e["name"], "transform": kind,
                          "unit": e.get("unit", ""), "c_type": e.get("c_type", "")})

    # 负样本：真实词元重组的不存在信号（正确行为 = 不进入 AUTO）
    rng = random.Random(SEED)
    stems = []
    for e in corpus:
        toks = [t for t in ra.decompose_signal_name(e["name"])["semantic_tokens"]
                if len(t) >= 3 and t.isalpha()]
        if toks:
            stems.append(toks)
    distractors: list[str] = []
    seen = set(corpus_names)
    tries = 0
    while len(distractors) < 120 and tries < 10000:
        tries += 1
        a, b = rng.choice(stems), rng.choice(stems)
        cand = (f"{rng.choice(a).capitalize()}_{rng.choice(b).capitalize()}_"
                f"{rng.choice(['u8', 'u16', 'f32'])}_{'M' if rng.random() < 0.5 else 'C'}")
        if cand not in seen:
            seen.add(cand)
            distractors.append(cand)
    return pairs, distractors


def split_by_signal(pairs: list[dict], seed: int = SEED) -> tuple[list[dict], list[dict]]:
    """按信号 50/50 划分校准集/测试集（同一信号的变体不跨集，避免泄漏）。"""
    rng = random.Random(seed)
    sigs = sorted({p["target"] for p in pairs})
    rng.shuffle(sigs)
    half = len(sigs) // 2
    calib_sigs, test_sigs = set(sigs[:half]), set(sigs[half:])
    return ([p for p in pairs if p["target"] in calib_sigs],
            [p for p in pairs if p["target"] in test_sigs])


_TYPE_FLIP = {"u8": "u16", "u16": "u32", "u32": "u64", "i8": "i16",
              "i16": "i32", "f32": "f64"}


def build_confusables(corpus: list[dict]) -> list[dict]:
    """
    混淆对（论文 Sec. 5.1 confusable pairs）：

    对每个语料信号生成"孪生"查询——词法与语义证据均指向原信号、但 IO 类别或
    类型宽度被翻转。这类查询对应真实风险场景（测量/标定对象错位、记录布局
    错配），正确行为是**不得 AUTO**（必须人工确认）。仅结构证据通道可判别。
    """
    names = {e["name"] for e in corpus}
    out = []
    for e in corpus:
        n = e["name"]
        # IO 翻转: _M <-> _C
        m = re.sub(r"_([CM])$", lambda mm: "_C" if mm.group(1) == "M" else "_M", n)
        if m != n and m not in names:
            out.append({"query": m, "twin": n, "kind": "io_flip"})
        # 类型宽度翻转: u16 -> u32 等
        m2 = re.sub(r"_([a-z]\d+)_([CM])$",
                    lambda mm: f"_{_TYPE_FLIP.get(mm.group(1), mm.group(1))}_{mm.group(2)}", n)
        if m2 != n and m2 not in names:
            out.append({"query": m2, "twin": n, "kind": "type_flip"})
    return out


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  2. 基线方法（论文 Sec. 5.2）                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def lev_sim(a: str, b: str) -> float:
    """归一化 Levenshtein 相似度（baseline，Levenshtein 1966）。"""
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return 1.0 - prev[-1] / max(len(a), len(b))


def rank_exact(query: str, names: list[str]) -> list[str]:
    return [query] if query in set(names) else []


def rank_lev(query: str, names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: lev_sim(query, n), reverse=True)[:TOP_K]


class BM25Index:
    """Okapi BM25 词法检索基线（Robertson & Zaragoza 2009），索引与 Ours 同源文档。"""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self._doc_toks = [re.findall(r"[a-z0-9]+", d.lower()) for d in docs]
        self._df: dict[str, int] = {}
        for toks in self._doc_toks:
            for t in set(toks):
                self._df[t] = self._df.get(t, 0) + 1
        self._avgdl = sum(len(t) for t in self._doc_toks) / max(1, len(self._doc_toks))

    def rank(self, query: str, top_k: int = TOP_K) -> list[tuple[str, float]]:
        q_toks = re.findall(r"[a-z0-9]+", query.lower())
        n_docs = len(self._doc_toks)
        scores = []
        for i, toks in enumerate(self._doc_toks):
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            dl = len(toks)
            for t in q_toks:
                if t not in tf:
                    continue
                idf = math.log(1 + (n_docs - self._df.get(t, 0) + 0.5)
                               / (self._df.get(t, 0) + 0.5))
                s += idf * tf[t] * (self.k1 + 1) / (
                    tf[t] + self.k1 * (1 - self.b + self.b * dl / self._avgdl))
            scores.append(s)
        order = sorted(range(n_docs), key=lambda i: scores[i], reverse=True)[:top_k]
        return [(i, scores[i]) for i in order]


def rank_jaccard(query: str, names: list[str]) -> list[str]:
    """分解词元 Jaccard 排序基线：仅用确定性分解、不用知识库检索。"""
    q_tok = set(ra.decompose_signal_name(query)["semantic_tokens"])
    scored = sorted(
        names,
        key=lambda n: _jaccard(q_tok, set(ra.decompose_signal_name(n)["semantic_tokens"])),
        reverse=True)
    return scored[:TOP_K]


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def rank_ours(query: str, aligner: ra.SemanticAligner) -> list[str]:
    res = aligner.align(query, top_k=TOP_K)
    return [n for n, _ in res.candidates] or ([res.best_match] if res.best_match else [])


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  3. 指标与阈值校准（论文 Sec. 5.3）                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def ranking_metrics(ranked_fn, pairs: list[dict]) -> dict:
    top1 = top5 = 0
    rr_sum = 0.0
    for p in pairs:
        ranked = ranked_fn(p["query"])
        if not ranked:
            continue
        if ranked[0] == p["target"]:
            top1 += 1
        if p["target"] in ranked[:TOP_K]:
            top5 += 1
            rr_sum += 1.0 / (ranked.index(p["target"]) + 1)
    n = len(pairs)
    return {"top1": top1 / n, "top5": top5 / n, "mrr": rr_sum / n, "n": n}


def calibrate_thresholds(aligner: ra.SemanticAligner, calib_pairs: list[dict],
                         calib_distractors: list[str]) -> dict:
    """
    网格搜索置信度阈值（论文 Sec. 5.3 threshold calibration）。

    约束（同时作用于 AUTO 阈值）:
      (a) 正样本: AUTO 精度 >= 98%
      (b) 负样本: 负样本误入 AUTO 比例 <= 5%
    tau_suggest 仅约束 (AUTO∪SUGGEST) 精度 >= 85%（SUGGEST 供人工确认，容忍度高）。
    tau 从低到高扫描，首个满足约束的取值即最大覆盖点。
    """
    scored = []
    for p in calib_pairs:
        res = aligner.align(p["query"], top_k=TOP_K)
        scored.append((res.score, res.best_match == p["target"]))
    d_scores = [aligner.align(q, top_k=TOP_K).score for q in calib_distractors]

    def _precision(tau: float) -> tuple[float, int]:
        sel = [(s, ok) for s, ok in scored if s >= tau]
        if not sel:
            return 1.0, 0
        return sum(ok for _, ok in sel) / len(sel), len(sel)

    def _false_positive(tau: float) -> float:
        if not d_scores:
            return 0.0
        return sum(1 for s in d_scores if s >= tau) / len(d_scores)

    grid = [round(0.30 + 0.05 * i, 2) for i in range(14)]  # 0.30 ~ 0.95
    tau_auto = 0.95
    for tau in grid:
        prec, cnt = _precision(tau)
        if (cnt and prec >= TARGET_AUTO_PRECISION
                and _false_positive(tau) <= TARGET_AUTO_FALSE_POSITIVE):
            tau_auto = tau
            break
    tau_suggest = 0.50
    for tau in grid:
        prec, cnt = _precision(tau)
        if cnt and prec >= TARGET_SUGGEST_PRECISION:
            tau_suggest = tau
            break
    if tau_suggest > tau_auto:
        tau_suggest = tau_auto
    return {"tau_auto": tau_auto, "tau_suggest": tau_suggest,
            "auto_precision_at_tau": _precision(tau_auto)[0],
            "suggest_precision_at_tau": _precision(tau_suggest)[0],
            "auto_false_positive_at_tau": _false_positive(tau_auto)}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  4. 实验主体                                                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def run() -> dict:
    rng = random.Random(SEED)
    corpus = ra.load_corpus_from_main_source()
    assert corpus, "语料加载失败"
    pairs, distractors = build_benchmark(corpus)
    calib_pairs, test_pairs = split_by_signal(pairs)
    # 负样本同样 50/50 划分校准/测试
    rng.shuffle(distractors)
    half_d = len(distractors) // 2
    calib_distractors, test_distractors = distractors[:half_d], distractors[half_d:]
    names = [e["name"] for e in corpus]

    kb = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
    kb.add_semantic_corpus(corpus)
    query_text = ra.SemanticAligner._query_text

    # ---- 阈值校准（正/负校准集），测试阶段使用校准后阈值 --------------------------
    base_aligner = ra.SemanticAligner(kb)
    calib = calibrate_thresholds(base_aligner, calib_pairs, calib_distractors)
    aligner = ra.SemanticAligner(kb, tau_auto=calib["tau_auto"],
                                 tau_suggest=calib["tau_suggest"])

    # BM25 基线索引（与 Ours 同源文档文本，保证公平比较）
    bm25 = BM25Index([d.text() for d in kb.docs])
    bm25_names = [d.name for d in kb.docs]
    # 混淆对（安全评测集，不参与校准）
    confusables = build_confusables(corpus)

    results: dict = {
        "meta": {
            "corpus_size": len(corpus),
            "num_queries_total": len(pairs),
            "num_calib": len(calib_pairs),
            "num_test": len(test_pairs),
            "num_distractors": len(distractors),
            "num_distractors_calib": len(calib_distractors),
            "num_distractors_test": len(test_distractors),
            "num_confusables": len(confusables),
            "transforms": TRANSFORMS,
            "hard_transforms": sorted(HARD_TRANSFORMS),
            "retriever": type(kb.retriever).__name__,
            "seed": SEED,
            "top_k": TOP_K,
            "calibration": calib,
        },
        "by_transform": {},
        "hard_subset": {},
    }

    # ---- identity 对照：名字未改的常见情形，所有方法都应 100%（防稻草人质疑） ----
    id_exact = sum(1 for e in corpus if rank_exact(e["name"], names))
    id_ours = sum(1 for e in corpus
                  if (r := aligner.align(e["name"])).best_match == e["name"] and r.tier == "AUTO")
    results["identity_control"] = {
        "n": len(corpus),
        "exact_top1": id_exact / len(corpus),
        "ours_top1_auto": id_ours / len(corpus),
    }

    # ---- RQ1: 主对比（测试集） --------------------------------------------------
    def rank_retrieval(query: str) -> list[str]:
        """整库检索排序（= 只用检索通道时的排序）。"""
        return [n for n, _ in kb.retriever.retrieve(query_text(query), TOP_K)]

    methods = {
        "Exact": lambda q: rank_exact(q, names),
        "Levenshtein": lambda q: rank_lev(q, names),
        "Token-Jaccard (decomp.)": lambda q: rank_jaccard(q, names),
        "BM25 (KB docs)": lambda q: [bm25_names[i] for i, _ in bm25.rank(query_text(q))],
        "Retrieval-only": rank_retrieval,
        "Ours": lambda q: rank_ours(q, aligner),
    }
    results["rq1_alignment"] = {m: ranking_metrics(fn, test_pairs) for m, fn in methods.items()}

    # 多跳变换子集（字符串基线退化点，论文案例分析）
    hard_pairs = [p for p in test_pairs if p["transform"] in HARD_TRANSFORMS]
    if hard_pairs:
        results["hard_subset"] = {
            "n": len(hard_pairs),
            "Levenshtein": ranking_metrics(lambda q: rank_lev(q, names), hard_pairs),
            "Token-Jaccard (decomp.)": ranking_metrics(
                lambda q: rank_jaccard(q, names), hard_pairs),
            "BM25 (KB docs)": ranking_metrics(
                lambda q: [bm25_names[i] for i, _ in bm25.rank(query_text(q))], hard_pairs),
            "Retrieval-only": ranking_metrics(
                lambda q: [n for n, _ in kb.retriever.retrieve(query_text(q), TOP_K)], hard_pairs),
            "Ours": ranking_metrics(lambda q: rank_ours(q, aligner), hard_pairs),
        }

    # 按变换类型细分（测试集）
    for kind in TRANSFORMS:
        sub = [p for p in test_pairs if p["transform"] == kind]
        if sub:
            results["by_transform"][kind] = {
                "n": len(sub),
                "ours_top1": ranking_metrics(lambda q: rank_ours(q, aligner), sub)["top1"],
                "lev_top1": ranking_metrics(lambda q: rank_lev(q, names), sub)["top1"],
                "exact_top1": ranking_metrics(lambda q: rank_exact(q, names), sub)["top1"],
            }

    # ---- RQ2: 消融（测试集；证据通道 + 知识源两个维度） -----------------------------
    # 结构证据不进入融合分数（见 rag_align.W_STRUCTURE 的说明），它只驱动后融合
    # 契约检查，因此这里不再有 "w/o structure evidence" 变体——它与 Full 等价。
    #
    # 注意：消融必须真正"拿掉"一个通道，而不是只把它的权重置零——候选集仍由
    # 检索器给出时，排序结果几乎不变，消融就失去意义。因此这里用两个"单通道
    # 排序"函数直接在整个知识库上排序：
    #   token only   = 语义词元 Jaccard（= Token-Jaccard 基线）
    #   retrieval only = 检索器返回的整库排序（= Retrieval-only 基线）
    ablations = {
        "Full": dict(),
        "w/o token evidence (retrieval only)": dict(_ranker=rank_retrieval),
        "w/o retrieval (token only)": dict(_ranker=lambda q: rank_jaccard(q, names)),
        "w/o post-fusion constraint": dict(enable_type_penalty=False),
    }
    results["rq2_ablation"] = {}
    for label, kwargs in ablations.items():
        ranker = kwargs.pop("_ranker", None)
        if ranker is not None:
            results["rq2_ablation"][label] = ranking_metrics(lambda q: ranker(q), test_pairs)
            if hard_pairs:
                results["hard_subset"][label] = ranking_metrics(
                    lambda q: ranker(q), hard_pairs)
        else:
            abl_aligner = ra.SemanticAligner(kb, **kwargs)
            # 权重归一化已在 SemanticAligner.__init__ 内完成（任一通道被移除后，
            # 剩余通道按相对大小重新归一化），保证各消融配置的融合分数尺度一致，
            # 不会把"被删通道的权重缺失"混进消融对比。
            results["rq2_ablation"][label] = ranking_metrics(
                lambda q: rank_ours(q, abl_aligner), test_pairs)
            if hard_pairs:
                results["hard_subset"][label] = ranking_metrics(
                    lambda q: rank_ours(q, abl_aligner), hard_pairs)

    # 知识源消融: KB 仅含标识符（剥离语义描述/单位/分组, 即 K3 贡献隔离）
    kb_nameonly = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
    kb_nameonly.add_semantic_corpus(
        [{"name": e["name"], "short_name": e.get("short_name", "")} for e in corpus])
    no_aligner = ra.SemanticAligner(kb_nameonly, tau_auto=calib["tau_auto"],
                                    tau_suggest=calib["tau_suggest"])
    results["rq2_ablation"]["KB w/o descriptions (name-only)"] = ranking_metrics(
        lambda q: rank_ours(q, no_aligner), test_pairs)
    if hard_pairs:
        results["hard_subset"]["KB w/o descriptions (name-only)"] = ranking_metrics(
            lambda q: rank_ours(q, no_aligner), hard_pairs)

    # ---- RQ3: 置信度分层（测试集，校准后阈值） ------------------------------------
    tiers = {"AUTO": 0, "SUGGEST": 0, "MANUAL": 0}
    auto_correct = 0
    for p in test_pairs:
        res = aligner.align(p["query"], top_k=TOP_K)
        tiers[res.tier] += 1
        if res.tier == "AUTO":
            auto_correct += int(res.best_match == p["target"])
    auto_total = tiers["AUTO"]
    false_auto = sum(
        1 for q in test_distractors if aligner.align(q, top_k=TOP_K).tier == "AUTO")
    n_test = len(test_pairs)
    # 人工工作量模型（相对 exact-only 工作流: 每个改名查询 1 次完全人工匹配）:
    #   AUTO = 0 次人工; SUGGEST = 0.5 次(一键确认); MANUAL = 1 次完全人工
    confirmations_per_query = (tiers["SUGGEST"] * 0.5 + tiers["MANUAL"] * 1.0) / n_test
    results["rq3_tiers"] = {
        "distribution": tiers,
        "auto_precision": (auto_correct / auto_total) if auto_total else 0.0,
        "auto_coverage": auto_total / n_test,
        "suggest_coverage": tiers["SUGGEST"] / n_test,
        "manual_rate": tiers["MANUAL"] / n_test,
        "distractor_false_auto": false_auto,
        "distractor_total": len(test_distractors),
        "confirmations_per_query": confirmations_per_query,
        "workload_reduction_vs_exact": 1.0 - confirmations_per_query,
    }

    # ---- 阈值敏感性扫描（测试集）: coverage/precision vs 误报率随 tau 变化 ----------
    sweep_grid = [round(0.30 + 0.05 * i, 2) for i in range(14)]
    test_scores = [(aligner.align(p["query"], top_k=TOP_K).score,
                    aligner.align(p["query"], top_k=TOP_K).best_match == p["target"])
                   for p in test_pairs]
    d_test_scores = [aligner.align(q, top_k=TOP_K).score for q in test_distractors]
    c_test_scores = [aligner.align(c["query"], top_k=TOP_K).score for c in confusables]
    sweep = {"grid": sweep_grid, "coverage": [], "precision": [],
             "distractor_fp": [], "confusable_fp": []}
    for tau in sweep_grid:
        sel = [(s, ok) for s, ok in test_scores if s >= tau]
        sweep["coverage"].append(len(sel) / n_test)
        sweep["precision"].append((sum(ok for _, ok in sel) / len(sel)) if sel else 1.0)
        sweep["distractor_fp"].append(sum(1 for s in d_test_scores if s >= tau) / len(d_test_scores))
        sweep["confusable_fp"].append(sum(1 for s in c_test_scores if s >= tau) / len(c_test_scores))
    results["rq3_threshold_sweep"] = sweep

    # ---- 安全评测: 混淆对上误入 AUTO 的比例（融合/惩罚通道的安全增益） ----------------
    def _confusable_false_auto(al: ra.SemanticAligner) -> float:
        return sum(1 for c in confusables if al.align(c["query"], top_k=TOP_K).tier == "AUTO") \
            / max(1, len(confusables))

    def _valid_auto_coverage(al: ra.SemanticAligner) -> float:
        return sum(1 for p in test_pairs if al.align(p["query"], top_k=TOP_K).tier == "AUTO") \
            / n_test

    tau_a = calib["tau_auto"]
    safety_configs = {
        "Ours (full fusion + penalty)": aligner,
        "w/o type/IO penalty": ra.SemanticAligner(kb, enable_type_penalty=False,
                                                  tau_auto=tau_a, tau_suggest=calib["tau_suggest"]),
        # 反事实：把结构证据放回分数里（w_s=0.1，权重和仍为 1）。
        # 该配置的阈值在其自身校准下同样落在 (0.45, 0.30)，因此可与主模型
        # 直接比较；论文用这一行说明"结构证据为何只做检查输入"。
        "Ours + struct. score term": ra.SemanticAligner(kb, w_structure=0.1,
                                                         tau_auto=tau_a,
                                                         tau_suggest=calib["tau_suggest"]),
        "Retrieval-only (score>=tau)": None,   # 特判: 检索分数直接与阈值比较
        "Levenshtein (sim>=tau)": None,        # 特判: 归一化编辑距离与阈值比较
    }
    safety = {}
    for label, al in safety_configs.items():
        if al is not None:
            conf_fp = _confusable_false_auto(al)
            valid_cov = _valid_auto_coverage(al)
            dist_fp = sum(1 for q in test_distractors
                          if al.align(q, top_k=TOP_K).tier == "AUTO") / len(test_distractors)
        elif label.startswith("Retrieval"):
            conf_fp = sum(1 for c in confusables
                          if kb.retriever.retrieve(query_text(c["query"]), 1)[0][1] >= tau_a) \
                / len(confusables)
            valid_cov = sum(1 for p in test_pairs
                            if kb.retriever.retrieve(query_text(p["query"]), 1)[0][1] >= tau_a) / n_test
            dist_fp = sum(1 for q in test_distractors
                          if kb.retriever.retrieve(query_text(q), 1)[0][1] >= tau_a) \
                / len(test_distractors)
        else:  # Levenshtein
            conf_fp = sum(1 for c in confusables
                          if lev_sim(c["query"], c["twin"]) >= tau_a) / len(confusables)
            valid_cov = sum(1 for p in test_pairs
                            if lev_sim(p["query"], p["target"]) >= tau_a) / n_test
            dist_fp = sum(1 for q in test_distractors
                          if max(lev_sim(q, n) for n in names) >= tau_a) / len(test_distractors)
        safety[label] = {"valid_auto_coverage": valid_cov,
                         "confusable_false_auto": conf_fp,
                         "distractor_false_auto": dist_fp}
    results["safety"] = {"n_confusables": len(confusables), "configs": safety}

    # ---- RQ4a: 元数据补全（测试集） ------------------------------------------------
    completer = ra.MetadataCompleter(kb, aligner=aligner)
    unit_total = unit_hit = 0
    for p in test_pairs:
        if not p["unit"]:
            continue
        unit_total += 1
        sig = {"Name": p["query"], "Unit": "", "Description": ""}
        got = completer.complete(sig).get("Unit")
        unit_hit += int(got is not None and got["value"] == p["unit"])
    results["rq4_completion"] = {
        "eligible": unit_total,
        "unit_correct": unit_hit,
        "accuracy": (unit_hit / unit_total) if unit_total else 0.0,
    }

    # ---- RQ4b: 增量一致性检查（测试集信号） -----------------------------------------
    test_sigs = sorted({p["target"] for p in test_pairs})
    corpus_by_name = {e["name"]: e for e in corpus}
    type_targets = [corpus_by_name[n] for n in test_sigs][:30]
    drift_hit = 0
    for e in type_targets:
        wrong = rng.choice([t for t in ["u8", "u16", "u32", "f32", "f64", "i16"]
                            if not ra.DomainKnowledgeBase.type_compatible(t, e["c_type"])])
        rep = ra.check_incremental_consistency(
            [{"Name": e["name"], "DataType": wrong}], [e["name"]], aligner)
        drift_hit += int(bool(rep.type_conflicts))
    dup_hit = sum(
        1 for e in type_targets
        if ra.check_incremental_consistency(
            [{"Name": e["name"], "DataType": "u8"}, {"Name": e["name"], "DataType": "u8"}],
            [], aligner).duplicates)
    rename_total = rename_hit = 0
    for p in [p for p in test_pairs
              if p["transform"] in ("camel", "abbrev", "expand", "suffix_drop",
                                    "camel_expand", "camel_abbrev")]:
        rename_total += 1
        rep = ra.check_incremental_consistency(
            [{"Name": p["query"], "DataType": p["c_type"]}], [p["target"]], aligner)
        rename_hit += int(any(r["old"] == p["target"] for r in rep.aligned_renames))
    results["rq4_consistency"] = {
        "type_drift_recall": drift_hit / len(type_targets),
        "duplicate_recall": dup_hit / len(type_targets),
        "rename_recall": (rename_hit / rename_total) if rename_total else 0.0,
        "rename_total": rename_total,
    }

    # ---- RQ5: 漂移覆盖映射（论文 Tab. I 的"哪一层负责哪一类漂移"）------------------
    # 论文 Tab. I 列了 5 类漂移。本节逐类**测量**检出率，并指明由哪一层负责：
    #   类翻转 / 类型族翻转 -> 对齐层的后融合契约检查（IO 类别、类型族逐字段比较）
    #   位域丢失 / 换算丢失 -> 一致性检查层（掩码、换算系数/偏置）
    #   重复 / 孤儿         -> 一致性检查层（重名、消失对象）
    def _cover_confusable(kind: str) -> float:
        items = [c for c in confusables if c["kind"] == kind]
        if not items:
            return 0.0
        return sum(1 for c in items
                   if aligner.align(c["query"], top_k=TOP_K).tier != "AUTO") / len(items)

    # 位域丢失：把新对象的掩码改成不再隔离原本那些位的掩码（位被多算/少算）
    # 只有真正的数值位掩码才适用这一漂移类；枚举掩码（0="A" 1="B" ...）不是位域。
    masked = []
    for d in kb.docs:
        if not d.mask:
            continue
        try:
            int(d.mask, 0)
        except (TypeError, ValueError):
            continue
        masked.append(d)
    mask_hit = 0
    for d in masked:
        try:
            m = int(d.mask, 0)
        except (TypeError, ValueError):
            continue
        # 保证与原掩码不同：先尝试收窄到低 4 位，若本就没有高位则改为放宽
        broken = (m & 0x0F) if (m & ~0x0F) else (m | 0xF0)
        rep = ra.check_incremental_consistency(
            [{"Name": d.name, "DataType": d.c_type, "BitMask": "0x%x" % broken}],
            [d.name], aligner)
        mask_hit += int(any(c["name"] == d.name for c in rep.mask_conflicts))
    # 换算丢失：把新对象的换算系数改掉（物理值随之成比例偏移）
    scaled = [d for d in kb.docs if d.factor is not None]
    conv_hit = 0
    for d in scaled:
        f = float(d.factor)
        broken = f * 6.0 if f * 6.0 != f else f + 1.0     # 保证与原值不同
        rep = ra.check_incremental_consistency(
            [{"Name": d.name, "DataType": d.c_type, "Factor": broken}],
            [d.name], aligner)
        conv_hit += int(any(c["name"] == d.name for c in rep.conversion_conflicts))
    # 孤儿：上一版存在、本轮不再出现的对象（既有漏报也有误报两项指标）
    orphan_pool = [e["name"] for e in type_targets]
    orphan_drop = set(orphan_pool[:5])
    orphan_kept = orphan_pool[5:]
    rep_orphan = ra.check_incremental_consistency(
        [{"Name": n, "DataType": "u8"} for n in orphan_kept],
        set(orphan_pool), aligner)
    reported_orphans = set(rep_orphan.removed_orphans)
    orphan_hit = len(orphan_drop & reported_orphans)
    orphan_fp = len(reported_orphans - orphan_drop)
    results["rq5_drift_coverage"] = {
        "class_flip": _cover_confusable("io_flip"),
        "type_family_flip": _cover_confusable("type_flip"),
        "bit_layout_loss": (mask_hit / len(masked)) if masked else 0.0,
        "bit_layout_n": len(masked),
        "scaling_loss": (conv_hit / len(scaled)) if scaled else 0.0,
        "scaling_n": len(scaled),
        "duplicate": dup_hit / len(type_targets) if type_targets else 0.0,
        "orphan_recall": orphan_hit / len(orphan_drop) if orphan_drop else 0.0,
        "orphan_false_positive": orphan_fp / len(orphan_kept) if orphan_kept else 0.0,
        "orphan_n": len(orphan_drop),
    }

    # ---- 案例研究（论文 Sec. 5.7 qualitative analysis）----------------------------
    no_penalty = safety_configs["w/o type/IO penalty"]
    cases = []

    def _row(query, note):
        r = aligner.align(query, top_k=TOP_K)
        r_raw = no_penalty.align(query, top_k=TOP_K)
        ev = r.evidence
        return {"query": query, "best": r.best_match, "tier": r.tier,
                "score": r.score, "score_no_penalty": r_raw.score,
                "s_ret": ev.get("retrieval"), "s_tok": ev.get("token"),
                "s_str": ev.get("structure"),
                "penalty": bool(ev.get("type_penalty") or ev.get("io_penalty")),
                "note": note}

    for p in test_pairs:  # (1) 多跳改名 -> AUTO
        if p["transform"] == "camel_expand":
            r = aligner.align(p["query"], top_k=TOP_K)
            if r.tier == "AUTO" and r.best_match == p["target"]:
                cases.append(_row(p["query"], "multi-hop rename"))
                break
    for p in test_pairs:  # (2) 多跳改名 -> SUGGEST（分数不足，人工确认）
        if p["transform"] == "camel_abbrev":
            r = aligner.align(p["query"], top_k=TOP_K)
            if r.tier == "SUGGEST" and r.best_match == p["target"]:
                cases.append(_row(p["query"], "multi-hop rename (low score)"))
                break
    for c in confusables:  # (3) IO 翻转 -> 惩罚降层
        if c["kind"] == "io_flip":
            r = aligner.align(c["query"], top_k=TOP_K)
            r_raw = no_penalty.align(c["query"], top_k=TOP_K)
            if r.tier != "AUTO" and r_raw.tier == "AUTO":
                cases.append(_row(c["query"], "IO-flip confusable (penalized)"))
                break
    for c in confusables:  # (4) 类型翻转 -> 惩罚降层
        if c["kind"] == "type_flip":
            r = aligner.align(c["query"], top_k=TOP_K)
            r_raw = no_penalty.align(c["query"], top_k=TOP_K)
            if r.tier != "AUTO" and r_raw.tier == "AUTO":
                cases.append(_row(c["query"], "type-flip confusable (penalized)"))
                break
    for q in test_distractors:  # (5) 不存在信号 -> MANUAL
        r = aligner.align(q, top_k=TOP_K)
        if r.tier == "MANUAL":
            cases.append(_row(q, "non-existent signal"))
            break
    results["case_study"] = cases
    return results


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  5. LaTeX 表生成                                                              ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  6. 多种子稳健性（应对"单次划分偶然性"质疑）                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
ROBUSTNESS_SEEDS = list(range(1, 11))


def run_robustness(corpus: list[dict]) -> dict:
    """
    对 10 个不同随机划分重复"划分 -> 校准 -> 评测"全流程，报告关键指标的
    mean±std/min/max。校准阈值在每个划分内独立重新校准（无跨划分泄漏）。
    """
    pairs, distractors = build_benchmark(corpus)
    confusables = build_confusables(corpus)
    per_seed = []
    for seed in ROBUSTNESS_SEEDS:
        calib_pairs, test_pairs = split_by_signal(pairs, seed)
        rng = random.Random(seed)
        ds = list(distractors)
        rng.shuffle(ds)
        half = len(ds) // 2
        calib_d, test_d = ds[:half], ds[half:]

        kb = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        kb.add_semantic_corpus(corpus)
        cal = calibrate_thresholds(ra.SemanticAligner(kb), calib_pairs, calib_d)
        al = ra.SemanticAligner(kb, tau_auto=cal["tau_auto"],
                                tau_suggest=cal["tau_suggest"])

        m = ranking_metrics(lambda q: rank_ours(q, al), test_pairs)
        hard = [p for p in test_pairs if p["transform"] in HARD_TRANSFORMS]
        mh = ranking_metrics(lambda q: rank_ours(q, al), hard) if hard \
            else {"top1": 1.0}
        tiers = {"AUTO": 0, "SUGGEST": 0, "MANUAL": 0}
        correct = 0
        for p in test_pairs:
            r = al.align(p["query"], top_k=TOP_K)
            tiers[r.tier] += 1
            if r.tier == "AUTO":
                correct += int(r.best_match == p["target"])
        per_seed.append({
            "seed": seed,
            "tau_auto": cal["tau_auto"], "tau_suggest": cal["tau_suggest"],
            "top1": m["top1"], "hard_top1": mh["top1"],
            "auto_coverage": tiers["AUTO"] / len(test_pairs),
            "auto_precision": (correct / tiers["AUTO"]) if tiers["AUTO"] else 1.0,
            "confusable_fp": sum(1 for c in confusables
                                 if al.align(c["query"], top_k=TOP_K).tier == "AUTO")
            / len(confusables),
            "distractor_fp": sum(1 for q in test_d
                                 if al.align(q, top_k=TOP_K).tier == "AUTO") / len(test_d),
        })

    def _agg(key: str) -> dict:
        vals = [s[key] for s in per_seed]
        mu = sum(vals) / len(vals)
        sd = (sum((v - mu) ** 2 for v in vals) / len(vals)) ** 0.5
        return {"mean": mu, "std": sd, "min": min(vals), "max": max(vals)}

    return {
        "n_seeds": len(ROBUSTNESS_SEEDS),
        "seeds": ROBUSTNESS_SEEDS,
        "per_seed": per_seed,
        "aggregates": {k: _agg(k) for k in
                       ["top1", "hard_top1", "auto_coverage", "auto_precision",
                        "confusable_fp", "distractor_fp"]},
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}"


def _tex(s: str) -> str:
    """LaTeX 转义（信号名中的下划线等）。"""
    return (s or "").replace("_", r"\_").replace("&", r"\&").replace("%", r"\%")


def write_tables(results: dict) -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    m = results["meta"]
    cal = m["calibration"]

    # ---- Table: 漂移分类 + 实测覆盖映射（论文 Tab. I）---------------------------
    # 前三列是解析性的错误分类（工具作者撰写），第四列是**实测**的检出率与负责层：
    #   Check   = 对齐层的后融合契约检查（IO 类别、类型族逐字段比较）
    #   Consist.= 增量一致性检查（掩码、换算系数、重名、孤儿）
    cov = results["rq5_drift_coverage"]
    _cov_rows = [
        ("Class flip", "exposed as MEAS", "master writes const.",
         "Check (%s\\%%)" % _pct(cov["class_flip"])),
        ("Type-family", "layout no longer fits", "decodes to another",
         "Check (%s\\%%)" % _pct(cov["type_family_flip"])),
        ("Bit-layout", "mask stops isolating", "contaminated $2^{w}-2^{b}$",
         "Consist.\\ (%s\\%%)" % _pct(cov["bit_layout_loss"])),
        ("Scaling loss", "raw count, wrong $k$", "value off by $6\\times$",
         "Consist.\\ (%s\\%%)" % _pct(cov["scaling_loss"])),
        ("Duplicate", "two objects, one var", "master reads stale",
         "Consist.\\ (%s\\%%)" % _pct(cov["duplicate"])),
        ("Orphan", "object vanishes", "stale entry kept",
         "Consist.\\ (%s\\%%)" % _pct(cov["orphan_recall"])),
    ]
    _cov_body = "\n".join(
        f"{a} & {b} & {c} & {d} \\\\" for a, b, c, d in _cov_rows)
    (TABLES_DIR / "tab_contract.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Error taxonomy of a drifted measurement contract, with the "
        "measured detection rate of each class and the layer that owns it "
        "(Check = alignment contract check, Consist.\\ = incremental "
        "consistency check).}\n"
        "\\label{tab:contract}\n"
        "\\centering\n"
        "\\scriptsize\n"
        "\\setlength{\\tabcolsep}{2pt}\n"
        "\\begin{tabular}{>{\\raggedright\\arraybackslash}p{1.5cm}\n"
        "                >{\\raggedright\\arraybackslash}p{2.2cm}\n"
        "                >{\\raggedright\\arraybackslash}p{2.3cm}\n"
        "                >{\\raggedright\\arraybackslash}p{1.55cm}}\n"
        "\\toprule\n"
        "Drift class & At the target & Consequence & Owner \\\\\n"
        "\\midrule\n"
        f"{_cov_body}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 主对比 (RQ1) ----
    rq1 = results["rq1_alignment"]
    hard = results.get("hard_subset", {})

    def _hard_top1(name: str) -> str:
        return _pct(hard[name]["top1"]) if name in hard else "--"

    _disp = {"Token-Jaccard (decomp.)": "Token-Jaccard"}
    _ours_align = "Ours"

    def _align_row(name: str) -> str:
        cells = [f"{_disp.get(name, name)}",
                 _pct(rq1[name]["top1"]),
                 _pct(rq1[name]["top5"]),
                 f"{rq1[name]['mrr']:.3f}",
                 _hard_top1(name)]
        if name == _ours_align:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        return " & ".join(cells) + " \\\\"

    # 本方法统一置于表末并整行加粗（论文表格惯例）
    _names = [n for n in rq1 if n != _ours_align]
    if _ours_align in rq1:
        _names.append(_ours_align)
    rows = "\n".join(_align_row(n) for n in _names)
    (TABLES_DIR / "tab_alignment.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Test-split accuracy "
        f"(N={m['num_test']} queries, {m['corpus_size']} signals; Hard = "
        f"multi-hop, N={hard.get('n', 0)}); the check gates tiering. "
        "Retrieval-only and Ours share the identical ranking (the check "
        "changes the tier, not the order) and differ in Table~\\ref{tab:safety}.}\n"
        "\\label{tab:align}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{lcccc}\n"
        "\\toprule\n"
        "Method & Top-1 (\\%) & Top-5 (\\%) & MRR & Hard Top-1 \\\\\n"
        "\\midrule\n"
        f"{rows}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 消融 (RQ2) ----
    rq2 = results["rq2_ablation"]
    full_top1 = rq2["Full"]["top1"]
    _adisp = {"KB w/o descriptions (name-only)": "KB name-only"}
    rows = "\n".join(
        f"{_adisp.get(name, name)} & {_pct(mm['top1'])} & {_pct(mm['top5'])} & "
        f"{100 * (mm['top1'] - full_top1):+.1f} & {_hard_top1(name)} \\\\"
        for name, mm in rq2.items())
    (TABLES_DIR / "tab_ablation.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Ablation of evidence channels (percentages; Delta = "
        "drop vs.\\ the full model, Hard = multi-hop subset). A removed "
        "channel is removed for good: \\emph{token only} ranks the whole "
        "knowledge base by deterministic decomposition, \\emph{retrieval "
        "only} ranks it by the retriever, and the post-fusion constraint is "
        "switched off entirely.}\n"
        "\\label{tab:ablation}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{lcccc}\n"
        "\\toprule\n"
        "Configuration & Top-1 & Top-5 & Delta & Hard Top-1 \\\\\n"
        "\\midrule\n"
        f"{rows}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 安全评测 (confusables) ----
    safety = results["safety"]
    n_conf = safety["n_confusables"]
    _sdisp = {"Ours (full fusion + penalty)": "Ours (full fusion)",
              "Retrieval-only (score>=tau)": "Retrieval-only",
              "Levenshtein (sim>=tau)": "Levenshtein"}
    _ours_safety = "Ours (full fusion + penalty)"

    def _safety_row(name: str, cfg: dict) -> str:
        cells = [f"{_sdisp.get(name, name)}",
                 _pct(cfg["valid_auto_coverage"]),
                 _pct(cfg["confusable_false_auto"]),
                 _pct(cfg["distractor_false_auto"])]
        if name == _ours_safety:
            cells = [f"\\textbf{{{c}}}" for c in cells]
        return " & ".join(cells) + " \\\\"

    # 本方法统一置于表末并整行加粗（论文表格惯例）
    _names = [n for n in safety["configs"] if n != _ours_safety]
    if _ours_safety in safety["configs"]:
        _names.append(_ours_safety)
    rows = "\n".join(_safety_row(n, safety["configs"][n]) for n in _names)
    (TABLES_DIR / "tab_safety.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Safety at auto threshold "
        f"{cal['tau_auto']}: false auto-acceptance on {n_conf} "
        "confusable pairs and "
        f"{results['rq3_tiers']['distractor_total']} distractors.}}\n"
        "\\label{tab:safety}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{lccc}\n"
        "\\toprule\n"
        "Configuration & Valid cov. & Conf.\\ FP & Distr.\\ FP \\\\\n"
        "\\midrule\n"
        f"{rows}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 置信层分布与质量 (RQ3) ----
    tiers = results["rq3_tiers"]
    dist = tiers["distribution"]
    n_test = m["num_test"]
    rows = []
    for tier_name in ("AUTO", "SUGGEST", "MANUAL"):
        cnt = dist.get(tier_name, 0)
        rows.append(f"{tier_name} & {cnt} & {_pct(cnt / n_test)} \\\\")
    rows.append("\\midrule")
    rows.append(f"Auto precision & \\multicolumn{{2}}{{c}}{{{_pct(tiers['auto_precision'])}}} \\\\")
    rows.append(f"Confirmations per query & \\multicolumn{{2}}{{c}}{{{tiers['confirmations_per_query']:.3f}}} \\\\")
    rows.append(f"Workload reduction vs exact & \\multicolumn{{2}}{{c}}{{{_pct(tiers['workload_reduction_vs_exact'])}}} \\\\")
    rows.append(f"Distractor false auto-accept & \\multicolumn{{2}}{{c}}{{"
                f"{tiers['distractor_false_auto']} of {tiers['distractor_total']} "
                f"({_pct(tiers['distractor_false_auto'] / tiers['distractor_total'])})}} \\\\")
    (TABLES_DIR / "tab_tiers.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Confidence-tier distribution on the test split "
        f"(N={n_test} queries, {tiers['distractor_total']} distractors; "
        f"calibrated $\\tau_{{auto}}$={cal['tau_auto']}, "
        f"$\\tau_{{suggest}}$={cal['tau_suggest']}).}}\n"
        "\\label{tab:tiers}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{lcc}\n"
        "\\toprule\n"
        "Tier & Count & Share (\\%) \\\\\n"
        "\\midrule\n"
        f"{chr(10).join(rows)}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 下游任务 (RQ4) ----
    comp = results["rq4_completion"]
    cons = results["rq4_consistency"]
    d_rows = [
        f"Metadata completion & eligible queries & {comp['eligible']} \\\\",
        f" & correct unit & {comp['unit_correct']} \\\\",
        f" & accuracy (\\%) & {_pct(comp['accuracy'])} \\\\",
        "\\midrule",
        f"Incremental consistency & type-drift recall (\\%) & {_pct(cons['type_drift_recall'])} \\\\",
        f" & duplicate recall (\\%) & {_pct(cons['duplicate_recall'])} \\\\",
        f" & rename recall (\\%) & {_pct(cons['rename_recall'])} "
        f"({round(cons['rename_recall'] * cons['rename_total'])}/{cons['rename_total']}) \\\\",
    ]
    (TABLES_DIR / "tab_downstream.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Downstream tasks: metadata completion and incremental "
        "consistency checking (test split).}\n"
        "\\label{tab:downstream}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{llr}\n"
        "\\toprule\n"
        "Task & Metric & Value \\\\\n"
        "\\midrule\n"
        f"{chr(10).join(d_rows)}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 鲁棒性 (多随机划分) ----
    rob = results["robustness"]
    agg = rob["aggregates"]
    _rdisp = [("top1", "Top-1"), ("hard_top1", "Hard Top-1"),
              ("auto_coverage", "AUTO coverage"), ("auto_precision", "AUTO precision"),
              ("confusable_fp", "Confusable FP"), ("distractor_fp", "Distractor FP")]
    r_rows = []
    for key, label in _rdisp:
        a = agg.get(key)
        if not a:
            continue
        r_rows.append(f"{label} & {_pct(a['mean'])} & $\\pm${_pct(a['std'])} & "
                      f"{_pct(a['min'])} & {_pct(a['max'])} \\\\")
    (TABLES_DIR / "tab_robustness.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Robustness across "
        f"{rob['n_seeds']} random calibration/test splits (thresholds "
        "re-calibrated per split).}\n"
        "\\label{tab:robustness}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{lcccc}\n"
        "\\toprule\n"
        "Metric & Mean (\\%) & Std (\\%) & Min (\\%) & Max (\\%) \\\\\n"
        "\\midrule\n"
        f"{chr(10).join(r_rows)}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

    # ---- Table: 案例研究 ----
    cs_rows = []
    for c in results.get("case_study", []):
        cs_rows.append(
            f"\\texttt{{{_tex(c['query'])}}} & \\texttt{{{_tex(c['best'])}}} & "
            f"{c['tier']} & {c['s_ret']:.2f} & {c['s_tok']:.2f} & "
            f"{c['s_str']:.2f} & {c['score']:.2f} & {_tex(c.get('note', ''))} \\\\")
    (TABLES_DIR / "tab_casestudy.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Case study: per-channel evidence and fused score "
        "($s_r$=retrieval, $s_t$=token, $s_s$=structure, $s$=fused).}\n"
        "\\label{tab:casestudy}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{llllrrrl}\n"
        "\\toprule\n"
        "Query & Best match & Tier & $s_r$ & $s_t$ & $s_s$ & $s$ & Note \\\\\n"
        "\\midrule\n"
        f"{chr(10).join(cs_rows)}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")

def print_summary(results: dict) -> None:
    m = results["meta"]
    cal = m["calibration"]
    print("=" * 68)
    print(f"语料: {m['corpus_size']} | 查询: 总{m['num_queries_total']} "
          f"(校准{m['num_calib']}/测试{m['num_test']}) | 负样本: {m['num_distractors']}")
    print(f"校准阈值: tau_auto={cal['tau_auto']} (P={_pct(cal['auto_precision_at_tau'])}%) "
          f"tau_suggest={cal['tau_suggest']} (P={_pct(cal['suggest_precision_at_tau'])}%)")
    print("=" * 68)
    hard = results.get("hard_subset", {})
    print(f"{'RQ1 方法':<22}{'Top-1':>8}{'Top-5':>8}{'MRR':>8}{'Hard-1':>8}")
    for name, met in results["rq1_alignment"].items():
        h = _pct(hard[name]["top1"]) if name in hard else "--"
        print(f"{name:<22}{_pct(met['top1']):>8}{_pct(met['top5']):>8}{met['mrr']:>8.3f}{h:>8}")
    print("-" * 68)
    print(f"{'RQ2 消融':<30}{'Top-1':>8}{'Hard-1':>8}")
    for name, met in results["rq2_ablation"].items():
        h = _pct(hard[name]["top1"]) if name in hard else "--"
        print(f"{name:<30}{_pct(met['top1']):>8}{h:>8}")
    print("-" * 68)
    rq3 = results["rq3_tiers"]
    print(f"RQ3 分层: AUTO={_pct(rq3['auto_coverage'])}% (P={_pct(rq3['auto_precision'])}%) "
          f"SUGGEST={_pct(rq3['suggest_coverage'])}% MANUAL={_pct(rq3['manual_rate'])}% "
          f"| 负样本误报AUTO: {rq3['distractor_false_auto']}/{rq3['distractor_total']} "
          f"| 工作量节省(vs exact-only): {_pct(rq3['workload_reduction_vs_exact'])}%")
    idc = results["identity_control"]
    print(f"identity 对照: Exact={_pct(idc['exact_top1'])}% Ours(AUTO)={_pct(idc['ours_top1_auto'])}%")
    print("-" * 68)
    safety = results["safety"]
    print(f"安全评测 (混淆对 n={safety['n_confusables']}, 误入AUTO比例 / 有效AUTO覆盖):")
    for name, cfg in safety["configs"].items():
        print(f"  {name:<34} FP={_pct(cfg['confusable_false_auto']):>6}%  "
              f"cov={_pct(cfg['valid_auto_coverage']):>6}%  "
              f"distrFP={_pct(cfg['distractor_false_auto']):>6}%")
    print(f"RQ4 补全: unit acc={_pct(results['rq4_completion']['accuracy'])}% | "
          f"一致性: 漂移={_pct(results['rq4_consistency']['type_drift_recall'])}% "
          f"重名={_pct(results['rq4_consistency']['duplicate_recall'])}% "
          f"改名={_pct(results['rq4_consistency']['rename_recall'])}%")
    print("-" * 68)
    rb = results.get("robustness")
    if rb:
        ag = rb["aggregates"]
        print(f"稳健性 ({rb['n_seeds']} 种子): "
              f"Top-1 {_pct(ag['top1']['mean'])}±{_pct(ag['top1']['std'])} | "
              f"AUTO覆盖 {_pct(ag['auto_coverage']['mean'])}±{_pct(ag['auto_coverage']['std'])} | "
              f"混淆FP {_pct(ag['confusable_fp']['mean'])}±{_pct(ag['confusable_fp']['std'])}")
    print("-" * 68)
    print("按变换类型 (Ours / Levenshtein / Exact Top-1 %):")
    for kind, stat in results["by_transform"].items():
        mark = "*" if kind in HARD_TRANSFORMS else " "
        print(f" {mark}{kind:<15} n={stat['n']:<4} "
              f"{_pct(stat['ours_top1']):>6} / {_pct(stat['lev_top1']):>6} / {_pct(stat['exact_top1']):>6}")


if __name__ == "__main__":
    results = run()
    results["robustness"] = run_robustness(ra.load_corpus_from_main_source())
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    write_tables(results)
    print_summary(results)
    print(f"\n结果已写入: {RESULTS_DIR / 'results.json'}")
    print(f"LaTeX 表已写入: {TABLES_DIR}")

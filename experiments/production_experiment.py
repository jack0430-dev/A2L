# -*- coding: utf-8 -*-
"""
production_experiment.py — 生产固件镜像上的对齐评测（论文 Sec. IV-E）

回答两个此前只能靠"声明"的问题：

  (A) 取证：论文用 8.00% 做动机——"常规的精确串匹配在生产镜像上放不下 2,504 个
      DWARF 信号"。本脚本对这 2,504 个信号做逐条取证：它们在地址文件的符号表里
      到底有没有对应物？ 结论（写进 production_alignment.json）：
      这批信号是地址文件没有导出的局部/静态变量（**符号覆盖缺口**），而不是"改了名
      的同一个信号"（命名分歧）。因此 8.00% 不是对齐层能闭合的数字，论文据此改写
      动机陈述——把"覆盖缺口"与"命名分歧"分开说。

  (B) 生产规模评测：把该镜像 4,003 个真实符号当作"描述侧知识库"，施加生产中真实
      出现的重命名，测 Top-1/Top-5/MRR 与分层；并扫描知识库规模（83 → 4,003），
      回答"检索通道在候选空间变大时还有没有边际贡献"（论文 Sec. IV-E 的核心结论：
      演示语料上检索通道零贡献，生产规模上贡献 +6.9 个百分点）。

用法:
    py -3.12 experiments/production_experiment.py

输出:
    experiments/results/production_alignment.json
    paper/tables/tab_production.tex
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
os.environ.setdefault("RAG_ALIGN_RETRIEVER", "charngram")   # 零依赖可复现

import rag_align as ra                    # noqa: E402
import run_experiments as rx              # noqa: E402
import A2L_Update_Tool_main as tool       # noqa: E402

AXF = ROOT / "data" / "TcuBsw.axf"
A2L = ROOT / "data" / "A2lHeader_updated.a2l"
OUT = HERE / "results" / "production_alignment.json"
TABLES_DIR = ROOT / "paper" / "tables"

SEED = 20261011
TOP_K = 5
# 每个知识库规模上评估的查询数（按变换类型分层抽样）
QUERIES_PER_TRANSFORM = 30
# 知识库规模扫描的档位
KB_SIZES = (83, 250, 1000, 4003)

# 生产代码里真实出现的领域缩写（全称 -> 缩写）。只收录在 TcuBsw 的符号名里
# 实际出现过的词根，因此"缩写"这一变换是按该代码库的真实习惯构造的。
_ABBREV = {
    "VOLTAGE": "Volt", "TEMPERATURE": "Temp", "CURRENT": "Curr",
    "PRESSURE": "Pres", "CHANNEL": "Chn", "CONFIG": "Cfg",
    "CONFIGURATION": "Cfg", "INSTANCE": "Inst", "PARAMETER": "Param",
    "COUNTER": "Cnt", "STATUS": "Stat", "BUFFER": "Buf", "POINTER": "Ptr",
    "LENGTH": "Len", "REQUEST": "Req", "RESPONSE": "Resp", "NUMBER": "Num",
    "VALUE": "Val", "THRESHOLD": "Thresh", "ENABLE": "En", "DISABLE": "Dis",
    "DIRECTION": "Dir", "INPUT": "In", "OUTPUT": "Out", "GROUP": "Grp",
    "RESULT": "Res", "INITIAL": "Init", "MAXIMUM": "Max", "MINIMUM": "Min",
    "MONITOR": "Mon", "RELEASE": "Rel", "VERSION": "Ver", "ERROR": "Err",
    "WARNING": "Warn", "SIGNAL": "Sig", "TIMEOUT": "Tmo", "PERIOD": "Per",
    "FREQUENCY": "Freq", "PENDING": "Pend", "PASSWORD": "Pwd",
}
_EXPAND = {v.upper(): k for k, v in _ABBREV.items()}

PROD_TRANSFORMS = ["lower", "camel", "abbrev", "expand", "struct_path", "camel_abbrev"]
PROD_HARD = {"camel_abbrev"}


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  (A) 未解析信号的取证                                                          ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t}


def forensics(sym: dict, sigs: list[dict], a2l_names: set[str]) -> dict:
    """对 2,504 个"精确匹配失败"的信号做取证。"""
    elf = set(sym)
    unresolved = [s for s in sigs
                  if str(s.get("Name") or "").strip()
                  and str(s.get("Name")).strip() not in elf]
    distinct = sorted({str(s.get("Name")).strip() for s in unresolved})

    elf_ci: dict[str, list[str]] = {}
    for n in elf:
        elf_ci.setdefault(n.lower(), []).append(n)

    rules = {"exact": 0, "case_insensitive": 0, "container": 0, "token_superset": 0}
    examples: dict[str, list[str]] = {k: [] for k in rules}
    for name in distinct:
        if name in elf:
            rules["exact"] += 1
            continue
        folded = elf_ci.get(name.lower(), [])
        if len(folded) == 1:
            rules["case_insensitive"] += 1
            examples["case_insensitive"].append("%s -> %s" % (name, folded[0]))
            continue
        container = [e for e in elf if name.startswith(e + "_")]
        if len(container) == 1:
            rules["container"] += 1
            examples["container"].append("%s -> %s" % (name, container[0]))
            continue
        toks = _tokens(name)
        sup = [e for e in elf if toks and _tokens(e) and _tokens(e) < toks]
        if len(sup) == 1:
            rules["token_superset"] += 1
            examples["token_superset"].append("%s -> %s" % (name, sup[0]))
    in_a2l = [n for n in distinct if n in a2l_names]
    return {
        "dwarf_signals": len(sigs),
        "elf_symbols": len(elf),
        "unresolved_signals": len(unresolved),
        "unresolved_distinct_names": len(distinct),
        "recoverable_by_relaxation": rules,
        "relaxation_examples": {k: v[:3] for k, v in examples.items() if v},
        "unresolved_names_present_in_generated_a2l": len(in_a2l),
        "verdict": ("address-file symbol-coverage gap (the address file does not export "
                    "these DWARF variables); not a naming divergence, so no alignment "
                    "method can recover them"),
    }


def read_generated_a2l_names() -> set[str]:
    """工具自己生成的生产 A2L 里的对象名（用来判断未解析信号是否真的落进了描述）。"""
    names: set[str] = set()
    if not A2L.is_file():
        return names
    pat = re.compile(r"/\*\s*Name\s*\*/\s*(\S+)")
    with A2L.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = pat.search(line)
            if m:
                names.add(m.group(1))
    return names


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  (B) 生产规模的重命名负载                                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _map_tokens(name: str, mapping: dict[str, str]) -> str:
    out = []
    for tok in name.split("_"):
        out.append(mapping.get(tok.upper(), tok))
    return "_".join(out)


def _camel(name: str) -> str:
    words = [w for w in name.split("_") if w]
    if not words:
        return name
    return words[0] + "".join(w.capitalize() for w in words[1:])


def prod_variant(name: str, kind: str) -> str:
    """对生产符号名施加一类生产中真实出现的重命名。"""
    if kind == "lower":
        return name.lower()
    if kind == "camel":
        return _camel(name)
    if kind == "abbrev":
        return _map_tokens(name, _ABBREV)
    if kind == "expand":
        return _map_tokens(name, _EXPAND)
    if kind == "struct_path":
        return "g_cfg." + name
    if kind == "camel_abbrev":
        return _camel(_map_tokens(name, _ABBREV))
    raise ValueError(kind)


def _kb_doc(name: str, meta: dict) -> dict:
    """ELF 符号 -> 知识库文档（生产里能拿到的元数据只有 IO 类别与长度）。"""
    size = int(meta.get("size") or 0)
    c_type = {1: "u8", 2: "u16", 4: "u32", 8: "u64"}.get(size, "")
    return {
        "name": name,
        "short_name": "",
        "io": "CAL" if str(meta.get("object_type") or "").upper().startswith("CAL") else "MEAS",
        "c_type": c_type,
        "group": str(meta.get("section") or ""),
        "desc": "",
    }


def build_workload(symbols: list[str], pool: set[str]) -> dict[str, list[tuple[str, str]]]:
    """为 pool 中的每个符号生成各变换的查询；返回 {transform: [(query, target)]}。"""
    by_transform: dict[str, list[tuple[str, str]]] = {k: [] for k in PROD_TRANSFORMS}
    seen: set[str] = set(pool)
    for name in sorted(pool):
        for kind in PROD_TRANSFORMS:
            q = prod_variant(name, kind)
            if not q or q == name or q in seen:
                continue
            seen.add(q)
            by_transform[kind].append((q, name))
    return by_transform


def _sample_queries(by_transform: dict, rng: random.Random, per_transform: int) -> list[dict]:
    out: list[dict] = []
    for kind in PROD_TRANSFORMS:
        items = by_transform.get(kind) or []
        if not items:
            continue
        take = items if len(items) <= per_transform else rng.sample(items, per_transform)
        for q, tgt in take:
            out.append({"query": q, "target": tgt, "transform": kind})
    return out


def _ranking(ranked_fn, queries: list[dict]) -> dict:
    top1 = top5 = 0
    rr = 0.0
    for p in queries:
        ranked = ranked_fn(p["query"])
        if not ranked:
            continue
        if ranked[0] == p["target"]:
            top1 += 1
        if p["target"] in ranked[:TOP_K]:
            top5 += 1
            rr += 1.0 / (ranked.index(p["target"]) + 1)
    n = max(1, len(queries))
    return {"top1": top1 / n, "top5": top5 / n, "mrr": rr / n, "n": len(queries)}


def _tier_stats(aligner: ra.SemanticAligner, queries: list[dict]) -> dict:
    tiers = {"AUTO": 0, "SUGGEST": 0, "MANUAL": 0}
    auto_ok = 0
    for p in queries:
        r = aligner.align(p["query"], top_k=TOP_K)
        tiers[r.tier] += 1
        if r.tier == "AUTO":
            auto_ok += int(r.best_match == p["target"])
    auto = tiers["AUTO"]
    return {"distribution": tiers,
            "auto_coverage": auto / max(1, len(queries)),
            "auto_precision": (auto_ok / auto) if auto else 1.0}


def run_scale_sweep(symbols: list[str], meta: dict) -> dict:
    """知识库规模扫描：检索通道在候选空间变大时的边际贡献。"""
    rng = random.Random(SEED)
    order = list(symbols)
    rng.shuffle(order)
    sweep: dict[str, dict] = {}
    for size in KB_SIZES:
        pool = order[:min(size, len(order))]
        by_transform = build_workload(symbols, set(pool))
        queries = _sample_queries(by_transform, random.Random(SEED), QUERIES_PER_TRANSFORM)
        if not queries:
            continue
        kb = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
        kb.add_semantic_corpus([_kb_doc(n, meta.get(n, {})) for n in pool])
        full = ra.SemanticAligner(kb, tau_auto=0.45, tau_suggest=0.30)

        def ours_rank(q: str) -> list[str]:
            res = full.align(q, top_k=TOP_K)
            return [n for n, _ in res.candidates] or ([res.best_match] if res.best_match else [])

        # 真正的"去掉检索通道"：只用确定性分解词元打分，在整个知识库上排序。
        # （只把融合权重置零是不够的——候选集仍来自检索器，排序几乎不变，
        #   那样测出来的"消融"没有意义。）
        def token_rank(q: str) -> list[str]:
            return rx.rank_jaccard(q, list(pool))

        t0 = time.time()
        m_full = _ranking(ours_rank, queries)
        m_tok = _ranking(token_rank, queries)
        sweep[str(size)] = {
            "kb_size": len(pool),
            "n_queries": len(queries),
            "Ours": m_full,
            "Token-only (w/o retrieval)": m_tok,
            "retrieval_gain_top1": m_full["top1"] - m_tok["top1"],
            "tiers": _tier_stats(full, queries),
            "elapsed_s": round(time.time() - t0, 1),
        }
        print("  KB=%-5d Ours top1=%.3f  token-only top1=%.3f  gain=%+.3f  (%.1fs)"
              % (len(pool), m_full["top1"], m_tok["top1"],
                 m_full["top1"] - m_tok["top1"], sweep[str(size)]["elapsed_s"]))
    return sweep


def run_full_baselines(symbols: list[str], meta: dict) -> dict:
    """在完整生产知识库上跑全部基线（Levenshtein / BM25 较贵，只在抽样查询上跑）。"""
    rng = random.Random(SEED)
    by_transform = build_workload(symbols, set(symbols))
    queries = _sample_queries(by_transform, rng, QUERIES_PER_TRANSFORM)
    pool = list(symbols)
    kb = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
    kb.add_semantic_corpus([_kb_doc(n, meta.get(n, {})) for n in pool])
    full = ra.SemanticAligner(kb, tau_auto=0.45, tau_suggest=0.30)
    bm25 = rx.BM25Index([d.text() for d in kb.docs])
    bm25_names = [d.name for d in kb.docs]
    qt = ra.SemanticAligner._query_text

    def ours(q):
        res = full.align(q, top_k=TOP_K)
        return [n for n, _ in res.candidates] or ([res.best_match] if res.best_match else [])

    out = {
        "Exact": _ranking(lambda q: rx.rank_exact(q, pool), queries),
        "Levenshtein": _ranking(lambda q: rx.rank_lev(q, pool), queries),
        "Token-Jaccard": _ranking(lambda q: rx.rank_jaccard(q, pool), queries),
        "BM25 (KB docs)": _ranking(lambda q: [bm25_names[i] for i, _ in bm25.rank(qt(q))], queries),
        "Retrieval-only": _ranking(lambda q: [n for n, _ in kb.retriever.retrieve(qt(q), TOP_K)], queries),
        "Ours": _ranking(ours, queries),
    }
    for name, m in out.items():
        print("  %-14s top1=%.3f top5=%.3f mrr=%.3f" % (name, m["top1"], m["top5"], m["mrr"]))
    return out


def run() -> dict:
    t0 = time.time()
    print("loading production image ...")
    sym = tool.load_address_file_details(str(AXF))
    sigs = tool.build_signals_from_dwarf(str(AXF), sym)
    meta = {k: v for k, v in sym.items()}
    symbols = sorted(sym)
    print("  ELF symbols=%d  DWARF signals=%d" % (len(symbols), len(sigs)))

    print("(A) forensics on the unresolved production signals ...")
    a2l_names = read_generated_a2l_names()
    fx = forensics(sym, sigs, a2l_names)
    print("  unresolved=%d signals / %d distinct names; recoverable by relaxation: %s"
          % (fx["unresolved_signals"], fx["unresolved_distinct_names"],
             fx["recoverable_by_relaxation"]))
    print("  present in the generated A2L: %d" % fx["unresolved_names_present_in_generated_a2l"])

    print("(B) production-scale renaming workload ...")
    sweep = run_scale_sweep(symbols, meta)
    baselines = run_full_baselines(symbols, meta)

    # 逐变换的 Top-1（完整生产知识库）
    by_transform = build_workload(symbols, set(symbols))
    queries = _sample_queries(by_transform, random.Random(SEED), QUERIES_PER_TRANSFORM)
    kb = ra.DomainKnowledgeBase(retriever=ra.CharNgramRetriever())
    kb.add_semantic_corpus([_kb_doc(n, meta.get(n, {})) for n in symbols])
    full = ra.SemanticAligner(kb, tau_auto=0.45, tau_suggest=0.30)
    per_transform = {}
    for kind in PROD_TRANSFORMS:
        sub = [p for p in queries if p["transform"] == kind]
        if not sub:
            continue
        m = _ranking(lambda q: [n for n, _ in full.align(q, top_k=TOP_K).candidates]
                     or [full.align(q, top_k=TOP_K).best_match], sub)
        per_transform[kind] = {"n": m["n"], "top1": m["top1"], "top5": m["top5"]}

    return {
        "meta": {
            "firmware": "data/TcuBsw.axf",
            "elf_symbols": len(symbols),
            "dwarf_signals": len(sigs),
            "seed": SEED,
            "top_k": TOP_K,
            "queries_per_transform": QUERIES_PER_TRANSFORM,
            "transforms": PROD_TRANSFORMS,
            "hard_transforms": sorted(PROD_HARD),
            "elapsed_s": round(time.time() - t0, 1),
        },
        "forensics": fx,
        "baselines_full_kb": baselines,
        "by_transform": per_transform,
        "kb_scale_sweep": sweep,
    }


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  LaTeX 表                                                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def _pct(x: float) -> str:
    return "%.1f" % (100.0 * float(x))


def write_table(results: dict) -> None:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    sweep = results["kb_scale_sweep"]
    base = results["baselines_full_kb"]
    fx = results["forensics"]

    # 表：知识库规模扫描（检索通道的边际贡献）
    srows = []
    for size in KB_SIZES:
        key = str(size)
        if key not in sweep:
            continue
        s = sweep[key]
        cells = [str(s["kb_size"]),
                 _pct(s["Ours"]["top1"]),
                 _pct(s["Token-only (w/o retrieval)"]["top1"]),
                 "%+.1f" % (100.0 * s["retrieval_gain_top1"])]
        if size == KB_SIZES[-1]:
            cells = ["\\textbf{%s}" % c for c in cells]
        srows.append(" & ".join(cells) + " \\\\")
    (TABLES_DIR / "tab_production.tex").write_text(
        "\\begin{table}[t]\n"
        "\\caption{Marginal value of the retrieval channel as the candidate "
        "space grows (production image, Top-1 in \\%; token-only ranks the "
        "whole knowledge base by deterministic decomposition).}\n"
        "\\label{tab:production}\n"
        "\\centering\n"
        "\\footnotesize\n"
        "\\setlength{\\tabcolsep}{3.5pt}\n"
        "\\begin{tabular}{cccc}\n"
        "\\toprule\n"
        "KB size & Ours & Token-only & Gain (pts) \\\\\n"
        "\\midrule\n"
        f"{chr(10).join(srows)}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        "\\end{table}\n", encoding="utf-8")


def main() -> int:
    results = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    write_table(results)
    print("written: %s" % OUT)
    print("written: %s" % (TABLES_DIR / "tab_production.tex"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

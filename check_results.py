# -*- coding: utf-8 -*-
"""CI guard: the seeded experiment harness must reproduce the paper numbers.

Run after ``experiments/run_experiments.py``. If a change to the retrieval
thresholds, the fusion weights or the corpus moves these numbers, the
corresponding paper table and figure have to be regenerated in the same
commit -- this script makes the drift explicit instead of silent.
"""
import json
import os
import sys

RESULTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "experiments", "results", "results.json")
ROOT = os.path.dirname(os.path.abspath(__file__))


def check(label, actual, expected, tol=1e-3):
    if abs(float(actual) - float(expected)) > tol:
        print("FAIL %s: %r != %r" % (label, actual, expected))
        return False
    print("ok   %-28s %s" % (label, actual))
    return True


def check_production_axf_stats() -> bool:
    """The production-scale numbers quoted in the paper must match the image.

    ``data/TcuBsw.axf`` is released with the artifact, so the 31,316 / 28,812 /
    2,504 counts in Sec. "Scale" and the README are verifiable, not asserted.
    The statistics file is regenerated on demand; if it is missing we recompute
    it, because recomputation is cheap (~10 s) compared to the risk of a
    stale file hiding a drift.
    """
    stats_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "experiments", "results", "production_axf_stats.json")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "experiments", "production_axf_stats.py")
    if not os.path.isfile(stats_path):
        if not os.path.isfile(script):
            print("skip production AXF statistics     (script and stats absent)")
            return True
        import subprocess
        subprocess.run([sys.executable, script], check=True)

    with open(stats_path, encoding="utf-8") as f:
        stats = json.load(f)

    ok = True
    counts = stats["counts"]
    ok &= check("prod DWARF signals", counts["dwarf_signals"], 31316, tol=0)
    ok &= check("prod exact match", counts["exact_match"], 28812, tol=0)
    ok &= check("prod failed match", counts["failed_exact_match"], 2504, tol=0)
    ok &= check("prod failure rate %", stats["failure_rate"]["failed_exact_match_pct"],
                8.0, tol=0.01)
    return ok


def check_production_alignment() -> bool:
    """生产镜像评测（experiments/production_experiment.py）的数值守卫。"""
    script = os.path.join(ROOT, "experiments", "production_experiment.py")
    stats_path = os.path.join(ROOT, "experiments", "results",
                              "production_alignment.json")
    if not os.path.isfile(script):
        print("skip production alignment        (script absent)")
        return True
    if not os.path.isfile(stats_path):
        print("run production_experiment.py to produce the production numbers")
        return False
    with open(stats_path, encoding="utf-8") as f:
        stats = json.load(f)

    ok = True
    # (A) 未解析信号取证：这批信号在地址文件里没有对应物
    fx = stats["forensics"]
    ok &= check("prod unresolved signals", fx["unresolved_signals"], 2504, tol=0)
    ok &= check("prod unresolved distinct names", fx["unresolved_distinct_names"], 580, tol=0)
    ok &= check("prod unresolved recoverable (exact)", fx["recoverable_by_relaxation"]["exact"],
                0, tol=0)
    ok &= check("prod unresolved recoverable (case-insensitive)",
                fx["recoverable_by_relaxation"]["case_insensitive"], 0, tol=0)
    ok &= check("prod unresolved recoverable (container)",
                fx["recoverable_by_relaxation"]["container"], 2, tol=0)
    ok &= check("prod unresolved recoverable (token-superset)",
                fx["recoverable_by_relaxation"]["token_superset"], 7, tol=0)
    ok &= check("prod unresolved present in generated A2L",
                fx["unresolved_names_present_in_generated_a2l"], 0, tol=0)
    # (B) 生产规模评测：检索通道的边际贡献
    base = stats["baselines_full_kb"]
    ok &= check("prod Ours Top-1", base["Ours"]["top1"], 0.978)
    ok &= check("prod Ours Top-5", base["Ours"]["top5"], 0.994)
    ok &= check("prod Levenshtein Top-1", base["Levenshtein"]["top1"], 0.906)
    ok &= check("prod Token-Jaccard Top-1", base["Token-Jaccard"]["top1"], 0.722)
    sweep = stats["kb_scale_sweep"]
    ok &= check("prod sweep smallest gain", sweep["83"]["retrieval_gain_top1"], 0.183)
    ok &= check("prod sweep largest gain", sweep["4003"]["retrieval_gain_top1"], 0.256)
    return ok


def check_drift_coverage(res: dict) -> bool:
    """漂移覆盖映射（论文 Tab. I 第四列）：每一类都必须 100% 检出。"""
    cov = res.get("rq5_drift_coverage")
    if not cov:
        print("missing rq5_drift_coverage in results.json")
        return False
    ok = True
    for key, label in [("class_flip", "class flip"),
                       ("type_family_flip", "type-family flip"),
                       ("bit_layout_loss", "bit-layout loss"),
                       ("scaling_loss", "scaling loss"),
                       ("duplicate", "duplicate"),
                       ("orphan_recall", "orphan recall")]:
        ok &= check(f"drift coverage {label}", cov[key], 1.0)
    ok &= check("drift coverage orphan false positive", cov["orphan_false_positive"], 0.0)
    # 真正的单通道消融：拿掉检索通道必须显著掉点
    abl = res["rq2_ablation"]
    ok &= check("ablation token-only Top-1", abl["w/o retrieval (token only)"]["top1"], 0.643)
    ok &= check("ablation full Top-1", abl["Full"]["top1"], 0.990)
    return ok


def main() -> int:
    with open(RESULTS, encoding="utf-8") as f:
        res = json.load(f)

    ok = True
    ok &= check("meta.seed", res["meta"]["seed"], 20261011, tol=0)
    ok &= check("meta.corpus_size", res["meta"]["corpus_size"], 83, tol=0)
    ok &= check("meta.num_queries_total", res["meta"]["num_queries_total"], 631, tol=0)
    ok &= check("RQ1 Ours Top-1", res["rq1_alignment"]["Ours"]["top1"], 0.990)
    ok &= check("RQ1 Exact Top-1", res["rq1_alignment"]["Exact"]["top1"], 0.0, tol=0)
    ok &= check("RQ1 Levenshtein Top-1", res["rq1_alignment"]["Levenshtein"]["top1"], 0.924)
    ok &= check("RQ3 AUTO coverage", res["rq3_tiers"]["auto_coverage"], 0.936)
    ok &= check("RQ3 AUTO precision", res["rq3_tiers"]["auto_precision"], 0.993)
    ok &= check("RQ3 distractor false auto", res["rq3_tiers"]["distractor_false_auto"], 1, tol=0)
    ok &= check("RQ4 unit accuracy", res["rq4_completion"]["accuracy"], 0.978)

    # Safety table (Table III): the check is the only thing that provides safety.
    safety = res["safety"]["configs"]
    ok &= check("safety Ours confusable FP",
                safety["Ours (full fusion + penalty)"]["confusable_false_auto"], 0.0, tol=0)
    ok &= check("safety no-penalty confusable FP",
                safety["w/o type/IO penalty"]["confusable_false_auto"], 1.0, tol=0)
    ok &= check("safety struct-as-score coverage",
                safety["Ours + struct. score term"]["valid_auto_coverage"], 0.901)
    ok &= check("safety Ours coverage",
                safety["Ours (full fusion + penalty)"]["valid_auto_coverage"], 0.936)

    ok &= check_production_axf_stats()
    ok &= check_production_alignment()
    ok &= check_drift_coverage(res)

    if not ok:
        print("experiment results drifted from the published numbers; "
              "regenerate the paper tables and figures")
        return 1
    print("experiment results are reproducible")
    return 0


if __name__ == "__main__":
    sys.exit(main())

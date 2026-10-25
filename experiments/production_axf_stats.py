# -*- coding: utf-8 -*-
"""Reproduce the production-scale measurement statistics reported in the paper.

The paper motivates the whole approach with one number taken from a real
automotive firmware image: how many of the signals that the DWARF debug
information exposes survive the *exact string match* that the conventional
A2L update flow performs against the address-file symbol table.

With the firmware image released alongside the tool (``data/TcuBsw.axf``,
6.3 MB, ELF32/ARM, DWARF), that number is reproducible from a clean checkout:

    python experiments/production_axf_stats.py

The script does three things, all through the *public* API of the base tool
(``A2L_Update_Tool_main``):

1.  ``build_signals_from_dwarf``      -> the DWARF-derived signal set
2.  ``load_address_file_details``     -> the ELF symbol table of the image
3.  exact name lookup                 -> the signals that the deterministic
                                          chain can and cannot place

The output is written to ``experiments/results/production_axf_stats.json``
and is checked by ``check_results.py`` so that the published numbers cannot
drift away from the released artifact.
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import A2L_Update_Tool_main as tool  # noqa: E402

AXF = os.path.join(ROOT, "data", "TcuBsw.axf")
OUT = os.path.join(HERE, "results", "production_axf_stats.json")

# Values quoted in the paper (Sec. "Scale") and in the README.
EXPECTED = {
    "dwarf_signals": 31316,
    "exact_match": 28812,
    "failed_exact_match": 2504,
}


def main() -> int:
    if not os.path.isfile(AXF):
        print("missing %s -- the firmware image is part of the release" % AXF)
        return 2

    t0 = time.time()
    sym_details = tool.load_address_file_details(AXF)
    signals = tool.build_signals_from_dwarf(AXF, sym_details)
    elapsed = time.time() - t0

    hit = 0
    failed = 0
    failed_names = {}
    for sig in signals:
        name = str(sig.get("Name") or "").strip()
        if name and name in sym_details:
            hit += 1
        else:
            failed += 1
            failed_names[name] = failed_names.get(name, 0) + 1

    total = hit + failed
    stats = {
        "meta": {
            "firmware": "data/TcuBsw.axf",
            "architecture": "ELF32 / ARM (0x28), HighTec toolchain, DWARF",
            "elapsed_s": round(elapsed, 2),
        },
        "counts": {
            "elf_symbols": len(sym_details),
            "dwarf_signals": total,
            "exact_match": hit,
            "failed_exact_match": failed,
            "distinct_failed_names": len(failed_names),
        },
        "failure_rate": {
            "failed_exact_match_pct": round(100.0 * failed / max(1, total), 4),
        },
        "expected": EXPECTED,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1, ensure_ascii=False)

    c = stats["counts"]
    print("ELF symbols            : %d" % c["elf_symbols"])
    print("DWARF-derived signals  : %d" % c["dwarf_signals"])
    print("exact name match       : %d" % c["exact_match"])
    print("failed exact match     : %d  (%.2f %%)"
          % (c["failed_exact_match"], stats["failure_rate"]["failed_exact_match_pct"]))
    print("distinct failed names  : %d" % c["distinct_failed_names"])
    print("written                : %s" % OUT)

    ok = True
    for key, want in EXPECTED.items():
        got = c[key]
        flag = "ok  " if got == want else "FAIL"
        if got != want:
            ok = False
        print("%s %-24s %d (expected %d)" % (flag, key, got, want))

    if not ok:
        print("production statistics drifted from the published numbers")
        return 1
    print("production statistics are reproducible")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Check that every label in overview.pdf stays inside its box and canvas.

The box geometry is not guessed: experiments/make_figures.py writes the real
PDF coordinates of each box (derived from matplotlib's own data transform)
to paper/figures/overview_boxes.json, and this script validates the measured
text spans of overview.pdf against that file.  Running it after every figure
change catches labels that no longer fit their box.

Note: overview.pdf is currently NOT inserted in the paper (dropped for space;
see the figure comments in paper/main.tex).  It stays in the artifact as the
framework diagram, and this guard keeps it valid should it return.
"""
import json
import sys
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "paper" / "figures" / "overview.pdf"
META = ROOT / "paper" / "figures" / "overview_boxes.json"

TOL = 0.8  # pt, rasterisation/rounding slack


def main() -> int:
    meta = json.loads(META.read_text(encoding="utf-8"))
    fig_w, fig_h = meta["canvas_in"]
    doc = fitz.open(PDF)
    page = doc[0]
    pw, ph = page.rect.width, page.rect.height
    if abs(pw - fig_w * 72) > 1.0 or abs(ph - fig_h * 72) > 1.0:
        print(f"FAIL canvas {pw/72:.3f} x {ph/72:.3f} in != {fig_w} x {fig_h}")
        return 1

    boxes = {k: fitz.Rect(*v) for k, v in meta["boxes"].items()}
    title = fitz.Rect(*meta["title"])

    bad = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                r = fitz.Rect(span["bbox"])
                txt = span["text"].strip()
                if not txt:
                    continue
                if r.x0 < -TOL or r.x1 > pw + TOL or r.y0 < -TOL or r.y1 > ph + TOL:
                    bad.append(("CANVAS", txt, r, None))
                    continue
                if title.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)):
                    continue
                owner = None
                for name, br in boxes.items():
                    if br.contains(fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)):
                        owner = (name, br)
                        break
                if owner is None:
                    bad.append(("NO-BOX", txt, r, None))
                elif not (owner[1].x0 - TOL <= r.x0 and r.x1 <= owner[1].x1 + TOL
                          and owner[1].y0 - TOL <= r.y0 and r.y1 <= owner[1].y1 + TOL):
                    bad.append((owner[0], txt, r, owner[1]))

    for kind, txt, r, br in bad:
        extra = (f" box=({br.x0:.1f},{br.y0:.1f},{br.x1:.1f},{br.y1:.1f})"
                 if br else "")
        print(f"OVERFLOW [{kind}] {txt!r} "
              f"span=({r.x0:.1f},{r.y0:.1f},{r.x1:.1f},{r.y1:.1f}){extra}")

    sizes = sorted({round(s["size"], 2) for b in page.get_text("dict")["blocks"]
                    for l in b.get("lines", []) for s in l["spans"]})
    # Not inserted in the paper at present -- report canvas-relative sizes only.
    scale = 1.0
    print("canvas font sizes:", sizes,
          "-> paper sizes:", [round(s * scale, 2) for s in sizes])
    if bad:
        print(f"FAIL: {len(bad)} overflow(s)")
        return 1
    print("PASS: every label is inside its box and inside the canvas")
    return 0


if __name__ == "__main__":
    sys.exit(main())

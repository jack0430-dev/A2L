"""Geometric self-check of the signal-conversion figure."""
import sys
from collections import defaultdict
import fitz

PT = 2.54 / 72.0
path = sys.argv[1] if len(sys.argv) > 1 else "paper/figures/fig_signal_conversion.pdf"
page = fitz.open(path)[0]
print("page: %.3f x %.3f cm" % (page.rect.width * PT, page.rect.height * PT))

sizes = defaultdict(int)
spans = []
for b in page.get_text("dict")["blocks"]:
    for l in b.get("lines", []):
        for s in l["spans"]:
            sizes[round(s["size"], 1)] += 1
            spans.append(s)
print("type sizes:", dict(sorted(sizes.items())))
# text must be 6 pt or 8 pt; 5 pt is only allowed for math scripts
# (s_{ret} and friends), exactly as in the body of the paper.
text_sizes = {round(s["size"], 1) for s in spans
              if not s["font"].startswith(("CM", "CMSY"))}
math_sizes = {round(s["size"], 1) for s in spans
              if s["font"].startswith(("CM", "CMSY"))}
print("text sizes:", sorted(text_sizes), " math sizes:", sorted(math_sizes))
assert text_sizes <= {6.0, 8.0}, "unexpected text type sizes"
# math inherits the size of its context (8 pt headers / 6 pt cells); CM
# subscripts sit one step down the CM size ladder (8 -> 6 -> 5).
assert math_sizes <= {5.0, 6.0, 8.0}, "unexpected math type sizes"

boxes, rules = [], []
for d in page.get_drawings():
    r = d["rect"]
    if d.get("fill") is not None and r.width > 5 and r.height > 5:
        boxes.append((r.x0 * PT, r.y0 * PT, r.x1 * PT, r.y1 * PT))
    elif d.get("fill") is None and (r.height < 1.5 or r.width < 1.5):
        rules.append((r.x0 * PT, r.y0 * PT, r.x1 * PT, r.y1 * PT))
print("boxes: %d rules: %d" % (len(boxes), len(rules)))
maxx = max([b[2] for b in boxes] + [s["bbox"][2] * PT for s in spans])
minx = min([b[0] for b in boxes] + [s["bbox"][0] * PT for s in spans])
print("content extent x: %.3f .. %.3f" % (minx, maxx))


def report(name, sel, tol=0.02):
    sel = sorted(sel)
    if not sel:
        print("%-16s : NONE" % name)
        return
    tops = [b[1] for b in sel]
    bots = [b[3] for b in sel]
    hs = [b[3] - b[1] for b in sel]
    ws = [b[2] - b[0] for b in sel]
    print("%-16s : n=%d top %.3f..%.3f bottom %.3f..%.3f h %.3f..%.3f w %.3f..%.3f"
          % (name, len(sel), min(tops), max(tops), min(bots), max(bots),
             min(hs), max(hs), min(ws), max(ws)))
    assert max(tops) - min(tops) < tol, name + ": tops differ"
    assert max(bots) - min(bots) < tol, name + ": bottoms differ"
    assert max(hs) - min(hs) < tol, name + ": heights differ"


report("stage headers", [b for b in boxes if b[1] > 0.2 and b[3] < 2.6])
report("lower band", [b for b in boxes if b[1] > 4.0 and b[3] < 5.9 and b[2] - b[0] > 2.0])
report("rejected box", [b for b in boxes if b[1] > 5.2 and b[3] > 5.4 and b[2] - b[0] < 3.0])

lines = defaultdict(list)
for s in spans:
    y0 = s["bbox"][1] * PT
    if 1.5 < y0 < 4.0:
        lines[round(y0, 1)].append(s)
print("table text lines:")
for y in sorted(lines):
    tops = [s["bbox"][1] * PT for s in lines[y]]
    bots = [s["bbox"][3] * PT for s in lines[y]]
    print("   top %.3f n=%2d top-spread %.4f bottom-spread %.4f"
          % (y, len(lines[y]), max(tops) - min(tops), max(bots) - min(bots)))

vert = sorted({round(r[0], 2) for r in rules if r[2] - r[0] < 0.02 and r[3] - r[1] > 1.0})
# table rules only: the orthogonal stage-4 callout also draws long
# horizontals, but they sit below y = 4.0
horiz = sorted({round(r[1], 2) for r in rules
                if r[3] - r[1] < 0.02 and r[2] - r[0] > 5.0 and r[1] < 4.0})
print("vertical rules  :", vert)
print("horizontal rules:", horiz)

# the grid the source declares in its header comment
EXPECT_VERT = [0.80, 3.80, 6.32, 7.98, 11.53, 13.01, 15.93]
EXPECT_HORIZ = [1.68, 2.36, 3.04, 3.72]
assert vert == EXPECT_VERT, "column rules moved: %s" % vert
assert horiz == EXPECT_HORIZ, "table rules moved: %s" % horiz

# every text of the table must stay inside its column
COLS = [(0.00, 0.80), (0.80, 3.80), (3.80, 6.32), (6.32, 7.98),
        (7.98, 11.53), (11.53, 13.01), (13.01, 15.93), (15.93, 18.13)]
for s in spans:
    x0, x1 = s["bbox"][0] * PT, s["bbox"][2] * PT
    y0 = s["bbox"][1] * PT
    if not (1.5 < y0 < 3.7):          # table body only
        continue
    for a, b in COLS:
        if a <= x0 < b:
            assert x1 <= b - 0.02, "text crosses the column rule: %r" % s["text"]
            break
    else:
        raise AssertionError("text outside every column: %r" % s["text"])
print("grid + cell containment: OK")

# the stage-4 callout must be an orthogonal (axis-aligned) dashed polyline,
# never a diagonal "cone".  PyMuPDF reports dashes as a string of the form
# "[ on off ] phase"; solid strokes are "[] 0".
diag, n_dashed = [], 0
for d in page.get_drawings():
    dashes = d.get("dashes") or ""
    if not isinstance(dashes, str) or "[" not in dashes:
        continue
    inner = dashes.split("]", 1)[0].lstrip("[").strip()
    if not inner:
        continue
    n_dashed += 1
    for it in d.get("items", []):
        if not it:
            continue
        if it[0] == "l" and len(it) >= 3:
            p, q = it[1], it[2]
            dx = abs(p.x - q.x)
            dy = abs(p.y - q.y)
            if dx > 0.6 and dy > 0.6:
                diag.append(((p.x * PT, p.y * PT), (q.x * PT, q.y * PT)))
        elif it[0] == "c":
            # a dashed Bezier that is not a tiny rounded-corner would be a
            # diagonal; the callout itself is polyline-only
            pts = [x for x in it[1:] if hasattr(x, "x")]
            if len(pts) >= 2:
                p, q = pts[0], pts[-1]
                dx = abs(p.x - q.x)
                dy = abs(p.y - q.y)
                if dx > 2.0 and dy > 2.0:
                    diag.append("curve")
assert n_dashed >= 2, "expected dashed callout strokes, got %d" % n_dashed
assert not diag, "diagonal dashed segment: %s" % diag
print("orthogonal dashed callout: OK (%d dashed strokes)" % n_dashed)
print("OK")

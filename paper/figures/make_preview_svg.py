"""SVG preview of the fig_workflow_compare TikZ figure.

Mirrors the geometry of the LaTeX source fig_workflow_compare.tex so
that the layout can be inspected without a TeX installation.  One
 SVG user unit is 1/CM cm, exactly like the TikZ picture.
"""
from pathlib import Path

CM = 40.0
SERIF = "Times New Roman, Liberation Serif, Nimbus Roman, serif"
FS, FSH, FSE = 11.3, 11.3, 11.3
LH, PAD = 13.4, 4.2
CELL_W = (2.10 + 0.211) * CM
COLS = [3.6, 6.2, 8.8, 11.4, 14.0, 16.6]
ROW_A, ROW_B = 1.0, -1.0
CELL_H = 48.0
STYLE = {
    "man": ("#f5f5f5", "#9a9a9a", "#333333", "#666666"),
    "rag": ("#eef4fc", "#4a7ebb", "#1a1a1a", "#555555"),
    "core": ("#dfeaf9", "#2a5fa8", "#1a1a1a", "#555555"),
    "bandm": ("#f7f7f7", "#b0b0b0", "#444444", None),
    "bandr": ("#f0f5fc", "#8aabdd", "#1a1a1a", None),
}
HEADERS = [("search", "1.  Candidates"), ("database", "2.  Evidence"),
           ("shield", "3.  Consistency"), ("route", "4.  Decision"),
           ("report", "5.  Report"), ("expert", "6.  Review")]
GROUPS = [(3.6, 6.2, "Search & Evidence"),
          (8.8, 11.4, "Check & Decide"),
          (14.0, 16.6, "Audit & Review")]
ROW_A_CELLS = [
    [("Manual lookup", 1), ("A2L + sheets", 0), ("unordered", 0)],
    [("Name reading", 1), ("strings only", 0), ("unscored", 0)],
    [("Implicit rule", 1), ("unit & type", 0), ("unrecorded", 0)],
    [("Each signal", 1), ("accept / redo", 0), ("no rubric", 0)],
    [("Sheet log", 1), ("hand-kept", 0), ("post hoc", 0)],
    [("Full sign-off", 1), ("every signal", 0), ("serial queue", 0)],
]
ROW_B_CELLS = [
    [("Query decomp.", 1), ("name, type, I/O", 0), ("structured", 0)],
    [("RAG fusion", 1), ("3 chan. fused", 0), ("ranked, scored", 0)],
    [("Post-fusion", 1), ("IO + type check", 0), ("conflict cap", 0)],
    [("Tier routing", 1), ("auto / human", 0), ("calibrated", 0)],
    [("Align. report", 1), ("query-level", 0), ("reproducible", 0)],
    [("Targeted rev.", 1), ("ambiguous only", 0), ("auditable", 0)],
]


def X(x_cm):
    return x_cm * CM


def Y(y_cm):
    return (3.6 - y_cm) * CM + 15


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def icon(o, name, x_cm, y_cm, color):
    """Draw the TikZ pics/<name> macro, centred on the given y."""
    cx, cy = X(x_cm), Y(y_cm)

    def P(x, y):
        return (cx + x * CM, cy - y * CM)

    def ln(x1, y1, x2, y2, w=1.6):
        a, b = P(x1, y1), P(x2, y2)
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                 'stroke-width="%.1f" stroke-linecap="round"/>' % (a[0], a[1], b[0], b[1], color, w))

    def ci(x, y, r, fill="none", w=1.6):
        p = P(x, y)
        o.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" '
                 'stroke-width="%.1f"/>' % (p[0], p[1], r * CM, fill, color, w))

    def el(x, y, rx, ry, w=1.6):
        p = P(x, y)
        o.append('<ellipse cx="%.1f" cy="%.1f" rx="%.1f" ry="%.1f" fill="none" '
                 'stroke="%s" stroke-width="%.1f"/>' % (p[0], p[1], rx * CM, ry * CM, color, w))

    def path(d, w=1.6):
        o.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f" '
                 'stroke-linejoin="round" stroke-linecap="round"/>' % (d, color, w))

    def poly(pts, w=2.4):
        d = " ".join("%.1f,%.1f" % P(x, y) for x, y in pts)
        o.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="%.1f" '
                 'stroke-linecap="round" stroke-linejoin="round"/>' % (d, color, w))

    if name == "search":
        ci(-0.04, 0.05, 0.15)
        ln(0.07, -0.06, 0.20, -0.19)
    elif name == "database":
        el(0, 0.13, 0.20, 0.085)
        ln(-0.20, 0.13, -0.20, -0.14)
        ln(0.20, 0.13, 0.20, -0.14)
        el(0, -0.02, 0.20, 0.085)
        el(0, -0.15, 0.20, 0.085)
    elif name == "shield":
        a = P(-0.19, 0.19); b = P(0.19, 0.19); c = P(0.19, -0.02)
        d = P(0.0, -0.21); e = P(-0.19, -0.02)
        path("M%.1f,%.1f L%.1f,%.1f L%.1f,%.1f L%.1f,%.1f L%.1f,%.1f Z"
             % (a[0], a[1], b[0], b[1], c[0], c[1], d[0], d[1], e[0], e[1]))
    elif name == "route":
        ln(-0.20, 0.0, 0.0, 0.0)
        ln(0.0, 0.0, 0.16, 0.15)
        ln(0.0, 0.0, 0.20, 0.0)
        ln(0.0, 0.0, 0.16, -0.15)
        ci(0.19, 0.16, 0.05)
        ci(0.22, 0.0, 0.05)
        ci(0.19, -0.16, 0.05)
    elif name == "report":
        a, b = P(-0.16, -0.21), P(0.16, 0.21)
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
                 'stroke="%s" stroke-width="1.6"/>'
                 % (a[0], a[1], (b[0] - a[0]), (b[1] - a[1]), color))
        ln(0.05, 0.21, 0.05, 0.10)
        ln(0.05, 0.10, 0.16, 0.10)
        ln(-0.09, 0.02, 0.09, 0.02)
        ln(-0.09, -0.07, 0.09, -0.07)
        ln(-0.09, -0.16, 0.01, -0.16)
    elif name == "expert":
        ci(-0.05, 0.13, 0.085)
        p1, p2 = P(-0.17, -0.21), P(-0.17, -0.03)
        p3, p4 = P(0.0, 0.01), P(0.17, -0.03)
        p5, p6 = P(0.17, -0.21), P(-0.17, -0.21)
        path("M%.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f Z"
             % (p1[0], p1[1], p2[0], p2[1], p2[0] - 0.07 * CM, p2[1] + 0.05 * CM, p3[0], p3[1],
                p4[0] + 0.07 * CM, p4[1] + 0.05 * CM, p4[0], p4[1], p5[0], p5[1]))
        poly([(0.05, -0.05), (0.12, -0.13), (0.25, -0.01)])
    elif name == "person":
        ci(-0.09, 0.15, 0.095)
        a = P(-0.24, -0.22); b = P(-0.24, -0.03); c = P(-0.18, 0.02)
        d = P(-0.07, 0.02); e = P(0.04, 0.02); f = P(0.10, -0.03)
        g = P(0.10, -0.22)
        path("M%.1f,%.1f C%.1f,%.1f %.1f,%.1f %.1f,%.1f "
             "C%.1f,%.1f %.1f,%.1f %.1f,%.1f"
             % (a[0], a[1], b[0], b[1], c[0], c[1], d[0], d[1],
                e[0], e[1], f[0], f[1], g[0], g[1]))
        ci(0.16, 0.08, 0.10)
        ln(0.23, 0.01, 0.32, -0.08)
    elif name == "robot":
        k = 0.8
        ln(0, 0.24 * k, 0, 0.32 * k)
        ci(0, 0.385 * k, 0.045 * k)
        a, b = P(-0.17 * k, 0.24 * k), P(0.17 * k, 0.04 * k)
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="none" '
                 'stroke="%s" stroke-width="1.6"/>'
                 % (a[0], a[1], (b[0] - a[0]), (b[1] - a[1]), color))
        ci(-0.085 * k, 0.14 * k, 0.032 * k)
        ci(0.085 * k, 0.14 * k, 0.032 * k)
        ln(-0.07 * k, 0.08 * k, 0.07 * k, 0.08 * k)
        poly([(-0.21 * k, 0.04 * k), (-0.21 * k, -0.24 * k),
              (0.21 * k, -0.24 * k), (0.21 * k, 0.04 * k)])
        ln(-0.21 * k, 0.0, -0.32 * k, -0.12 * k)
        ln(0.21 * k, 0.0, 0.32 * k, -0.12 * k)


def htext(o, x_cm, y_cm, txt, size, weight, fill, anchor="middle"):
    # SVG text y is the baseline, a TikZ node is centred: offset by
    # the cap height (~0.35em) to line the two up.
    o.append('<text x="%.1f" y="%.1f" font-size="%.1f" font-weight="%s" '
             'text-anchor="%s" fill="%s" font-family="%s">%s</text>'
             % (X(x_cm), Y(y_cm) + size * 0.35, size, weight, anchor, fill,
                SERIF, esc(txt)))


def cell(o, x_cm, y_cm, lines, kind):
    f, st, tc, sc = STYLE[kind]
    x, y = X(x_cm) - CELL_W / 2.0, Y(y_cm) - CELL_H / 2.0
    o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" ry="4" '
             'fill="%s" stroke="%s" stroke-width="0.8"/>'
             % (x, y, CELL_W, CELL_H, f, st))
    for i, (txt, bold) in enumerate(lines):
        o.append('<text x="%.1f" y="%.1f" font-size="%.1f" font-weight="%s" '
                 'text-anchor="middle" fill="%s" font-family="%s">%s</text>'
                 % (X(x_cm), y + PAD + LH * 0.8 + i * LH, FS,
                    "bold" if bold else "normal", tc, SERIF, esc(txt)))


def line(o, x1, y1, x2, y2, color, w=1.0, arrow=False):
    m = ' marker-end="url(#ah)"' if arrow else ""
    o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
             'stroke-width="%.1f"%s/>' % (X(x1), Y(y1), X(x2), Y(y2), color, w, m))


def build():
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.2fcm" height="%.2fcm" '
         'viewBox="0 0 730 250" font-family="Segoe UI,Helvetica,Arial,sans-serif">'
         % (730 / CM, 250 / CM),
         '<defs><marker id="ah" markerWidth="7" markerHeight="7" refX="6.5" refY="2.6" '
         'orient="auto"><path d="M0,0 L6.5,2.6 L0,5.2 z" fill="#666"/></marker></defs>',
         '<rect width="730" height="250" fill="#ffffff"/>']
    for x1, x2, lbl in GROUPS:
        c = (x1 + x2) / 2.0
        half = (len(lbl) * 5.3 + 6) / 2.0 / CM
        line(o, x1, 3.45, c - half, 3.45, "#8a8a8a", 1.3)
        line(o, c + half, 3.45, x2, 3.45, "#8a8a8a", 1.3)
        line(o, x1, 3.45, x1, 3.28, "#8a8a8a", 1.3)
        line(o, x2, 3.45, x2, 3.28, "#8a8a8a", 1.3)
        w = len(lbl) * 5.3 + 6
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="13" fill="#ffffff"/>'
                 % (X(c) - w / 2.0, Y(3.45) - 9, w))
        htext(o, c, 3.45, lbl, FSH, "bold", "#444444")
    for x, (ic, t) in zip(COLS, HEADERS):
        icon(o, ic, x, 2.92, "#8a8a8a")
        htext(o, x, 2.45, t, FSH, "bold", "#666666")
    line(o, 0.2, 2.12, 17.8, 2.12, "#cccccc", 1.0)
    # ---- row A: traditional ----
    icon(o, "person", 1.2, 1.55, "#9a9a9a")
    htext(o, 1.2, 0.92, "Traditional", FSH, "bold", "#333333")
    htext(o, 1.2, 0.58, "manual, per signal", FSE, "normal", "#666666")
    for i, lines in enumerate(ROW_A_CELLS):
        cell(o, COLS[i], ROW_A, lines, "man")
    for i in range(5):
        line(o, COLS[i] + CELL_W / 2.0 / CM, ROW_A, COLS[i + 1] - CELL_W / 2.0 / CM,
             ROW_A, "#9a9a9a", 1.0, True)
    line(o, 0.2, 0.05, 17.8, 0.05, "#cccccc", 1.0)
    # ---- row B: ours ----
    icon(o, "robot", 1.2, -0.35, "#4a7ebb")
    htext(o, 1.2, -0.98, "A2L-RAGAlign", FSH, "bold", "#2a5fa8")
    htext(o, 1.2, -1.32, "(Ours), automatic", FSE, "normal", "#555555")
    for i, lines in enumerate(ROW_B_CELLS):
        cell(o, COLS[i], ROW_B, lines, "core" if i == 1 else "rag")
    for i in range(5):
        line(o, COLS[i] + CELL_W / 2.0 / CM, ROW_B, COLS[i + 1] - CELL_W / 2.0 / CM,
             ROW_B, "#4a7ebb", 1.0, True)
    o.append("</svg>")
    return "\n".join(o)


out = Path(__file__).resolve().parent / "fig_workflow_compare_preview.svg"
out.write_text(build(), encoding="utf-8")
print("SVG written:", out)

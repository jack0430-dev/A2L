r"""Build paper/figures/fig_workflow_compare.pdf from the TikZ source.

Usage:
    py -3.12 paper/figures/build_workflow_figure.py [--preview]

Order of preference:
  1. A TeX distribution (latexmk / pdflatex / xelatex) compiles
     fig_workflow_compare_standalone.tex, which \inputs the TikZ source.
     The result is typographically identical to the main document.
  2. Without TeX, the vector SVG preview is converted to PDF with
     CairoSVG.  The result is a vector PDF with the same layout and the
     same Times metrics, but it is a fallback: regenerate it with
     step 1 before submitting if a TeX distribution is available.

The main document (paper/main.tex) includes the PDF when it exists and
falls back to \\input of the TikZ source otherwise, so the paper always
compiles.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "fig_workflow_compare.tex"
WRAPPER = HERE / "fig_workflow_compare_standalone.tex"
SVG = HERE / "fig_workflow_compare_preview.svg"
PNG = HERE / "fig_workflow_compare_preview.png"
PDF = HERE / "fig_workflow_compare.pdf"


def _run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def build_with_tex():
    """Compile the standalone wrapper with the available TeX engine."""
    if not WRAPPER.exists():
        print("[ERROR] missing wrapper: %s" % WRAPPER.name)
        return None
    if shutil.which("latexmk"):
        cmd = ["latexmk", "-pdf", "-interaction=nonstopmode",
               "-halt-on-error", WRAPPER.name]
        runs = 1
    else:
        engine = shutil.which("pdflatex") or shutil.which("xelatex")
        if not engine:
            return None
        cmd = [engine, "-interaction=nonstopmode", "-halt-on-error",
               WRAPPER.name]
        runs = 2
    for i in range(runs):
        code, out = _run(cmd, HERE)
        if code != 0:
            print(out[-2500:])
            print("[ERROR] %s failed (exit %d)" % (cmd[0], code))
            return None
    for aux in (".aux", ".out", ".toc"):
        p = WRAPPER.with_suffix(aux)
        if p.exists():
            p.unlink()
    overfull = []
    log = HERE / (WRAPPER.stem + ".log")
    if log.exists():
        overfull = re.findall(r"^Overfull.*$", log.read_text(errors="ignore"),
                              re.M)
    return "TeX (%s)" % cmd[0], overfull


def build_with_tectonic():
    """Compile the standalone wrapper with the bundled Tectonic engine."""
    tectonic = HERE.parent / "_tools" / "tectonic.exe"
    if not tectonic.exists():
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [str(tectonic), "-X", "compile", WRAPPER.name,
               "--outdir", tmp, "--keep-logs"]
        code, out = _run(cmd, HERE)
        produced = Path(tmp) / (WRAPPER.stem + ".pdf")
        if code != 0 or not produced.exists():
            print(out[-2000:])
            print("[ERROR] tectonic failed (exit %d)" % code)
            return None
        shutil.copyfile(produced, PDF)
        log = Path(tmp) / (WRAPPER.stem + ".log")
        overfull = []
        if log.exists():
            overfull = re.findall(r"^Overfull.*$", log.read_text(errors="ignore"),
                                  re.M)
        return "Tectonic (%s)" % tectonic.name, overfull


def build_with_cairosvg():
    """Convert the vector SVG preview into a PDF (fallback, no TeX needed)."""
    try:
        import cairosvg
    except ImportError:
        print("[ERROR] cairosvg is not installed")
        return None
    if not SVG.exists():
        print("[ERROR] missing %s -- run make_preview_svg.py first" % SVG.name)
        return None
    cairosvg.svg2pdf(url=str(SVG), write_to=str(PDF))
    return "CairoSVG (vector SVG fallback)", []


def update_preview():
    """Refresh the PNG preview from the SVG."""
    try:
        import cairosvg
    except ImportError:
        return False
    cairosvg.svg2png(url=str(SVG), write_to=str(PNG), scale=1.9,
                      background_color="white")
    return True


def pdf_page_size(path):
    data = path.read_bytes()
    m = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
                  data)
    if not m:
        return None
    w, h = float(m.group(3)), float(m.group(4))
    return w / 72.0 * 2.54, h / 72.0 * 2.54   # pt -> cm


def main(argv):
    if not SRC.exists():
        print("[ERROR] missing TikZ source: %s" % SRC)
        return 1
    if "--preview" in argv:
        code, out = _run([sys.executable, str(HERE / "make_preview_svg.py")],
                         HERE)
        print(out.strip() or "preview regenerated")
        if code != 0:
            return code
        if update_preview():
            print("PNG preview updated: %s" % PNG.name)
        return 0

    info = build_with_tex()
    if info is None:
        info = build_with_tectonic()
    if info is None:
        info = build_with_cairosvg()
    if info is None:
        print("[ERROR] no way to build the PDF (no TeX, no cairosvg)")
        return 1
    how, overfull = info

    if not PDF.exists():
        print("[ERROR] PDF was not produced")
        return 1
    size = pdf_page_size(PDF)
    print("PDF: %s (%.1f KB)" % (PDF, PDF.stat().st_size / 1024.0))
    print("built by: %s" % how)
    if size:
        print("page size: %.2f x %.2f cm  (main document uses \\textwidth = 18.19 cm)"
              % size)
    if overfull:
        print("\nLaTeX warnings (paste these if the figure looks wrong):")
        for line in overfull[:10]:
            print("  " + line)
    else:
        print("no Overfull warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

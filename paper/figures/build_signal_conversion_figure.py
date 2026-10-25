r"""Build paper/figures/fig_signal_conversion.pdf from the TikZ source.

Usage:
    py -3.12 paper/figures/build_signal_conversion_figure.py

Order of preference:
  1. A TeX distribution (latexmk / pdflatex / xelatex) compiles
     fig_signal_conversion_standalone.tex, which \inputs the TikZ source.
     The result is typographically identical to the main document.
  2. The bundled Tectonic engine (paper/_tools/tectonic.exe) compiles the
     same wrapper.  Tectonic is XeTeX based, so it emits harmless
     "Font shape TU/ptm/... undefined" warnings for IEEEtran-style
     documents, but the vector output is the same.

The main document (paper/main.tex) includes the generated PDF.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "fig_signal_conversion.tex"
WRAPPER = HERE / "fig_signal_conversion_standalone.tex"
PDF = HERE / "fig_signal_conversion.pdf"
TECTONIC = HERE.parent / "_tools" / "tectonic.exe"


def _run(cmd, cwd):
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def build_with_tex():
    """Compile the standalone wrapper with the available TeX engine."""
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
    for _ in range(runs):
        code, out = _run(cmd, HERE)
        if code != 0:
            print(out[-2500:])
            print("[ERROR] %s failed (exit %d)" % (cmd[0], code))
            return None
    for aux in (".aux", ".out", ".toc", ".log"):
        p = WRAPPER.with_suffix(aux)
        if p.exists():
            p.unlink()
    return "TeX (%s)" % cmd[0], _overfull(HERE / (WRAPPER.stem + ".log"))


def build_with_tectonic():
    """Compile the standalone wrapper with the bundled Tectonic engine."""
    if not TECTONIC.exists():
        return None
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [str(TECTONIC), "-X", "compile", WRAPPER.name,
               "--outdir", tmp, "--keep-logs"]
        code, out = _run(cmd, HERE)
        produced = Path(tmp) / (WRAPPER.stem + ".pdf")
        if code != 0 or not produced.exists():
            print(out[-2500:])
            print("[ERROR] tectonic failed (exit %d)" % code)
            return None
        shutil.copyfile(produced, PDF)
        log = Path(tmp) / (WRAPPER.stem + ".log")
        return "Tectonic (%s)" % TECTONIC.name, _overfull(log)


def _overfull(log: Path):
    if not log.exists():
        return []
    return re.findall(r"^Overfull.*$", log.read_text(errors="ignore"), re.M)


def pdf_page_size(path: Path):
    """Page size in cm; the /MediaBox may sit inside a compressed object
    stream, so the streams are inflated as well."""
    data = path.read_bytes()
    chunks = [data]
    for m in re.finditer(rb"stream\r?\n", data):
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        try:
            chunks.append(zlib.decompress(data[start:end]))
        except zlib.error:
            pass
    for chunk in chunks:
        m = re.search(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+"
                      rb"([\d.\-]+)\s+([\d.\-]+)", chunk)
        if m:
            w = float(m.group(3)) - float(m.group(1))
            h = float(m.group(4)) - float(m.group(2))
            return w / 72.0 * 2.54, h / 72.0 * 2.54   # pt -> cm
    return None


def main(argv):
    if not SRC.exists():
        print("[ERROR] missing TikZ source: %s" % SRC)
        return 1
    info = build_with_tex() or build_with_tectonic()
    if info is None:
        print("[ERROR] no way to build the PDF (no TeX, no tectonic)")
        return 1
    how, overfull = info
    if not PDF.exists():
        print("[ERROR] PDF was not produced")
        return 1
    size = pdf_page_size(PDF)
    print("PDF: %s (%.1f KB)" % (PDF, PDF.stat().st_size / 1024.0))
    print("built by: %s" % how)
    if size:
        print("page size: %.3f x %.3f cm  (IEEEtran \\textwidth = 18.136 cm, "
              "so width=\\textwidth scales the figure by %.4f)"
              % (size[0], size[1], 18.1356 / size[0]))
    if overfull:
        print("\nLaTeX warnings (paste these if the figure looks wrong):")
        for line in overfull[:10]:
            print("  " + line)
    else:
        print("no Overfull warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

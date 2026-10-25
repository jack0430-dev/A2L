# Contributing to A2L Update Tool

Thanks for taking the time to contribute. This project is a research-grade
tool: the same code base backs the GUI, the headless CLI, and the
experiments behind the accompanying ISCAS 2027 submission.

## Getting set up

```bash
git clone https://github.com/jack0430-dev/A2L-RAGAlign_2026.git
cd A2L-RAGAlign_2026
python -m pip install -r requirements.txt
python -m pytest -q
```

The test suite must pass before you open a pull request. It runs without a
display (`QT_QPA_PLATFORM=offscreen`) and without network access.

## Ground rules

1. **The original A2L header is sacred.** The tool must never rewrite the
   user's A2L header unless auto-repair was explicitly requested. Changes
   that break this guarantee will not be merged.
2. **Failures must be visible.** A signal that cannot be resolved has to
   appear in the match report with a reason. Silently dropping signals is
   the bug this tool exists to fix -- do not reintroduce it.
3. **Semantic alignment never mutates output.** The retrieval stage reports
   candidates for human review; it must not silently pick one.
4. **Add a test with every behaviour change.** The suite covers A2L
   generation, header preservation, struct/array expansion, reports, the
   i18n layer, the CLI and the RAG bridge.
5. **Keep the i18n table complete.** Every new user-visible string needs an
   entry in `i18n.py`. `test_i18n.py` fails the build otherwise, and the
   English string is the source of truth for the translation key.

## Internationalisation

- User-visible strings go through `tr()` / `tf()`.
- Placeholders are always `{n1}`, `{n2}`, ... and are passed as keyword
  arguments to `tf()`.
- `i18n.py` holds the translation table; entries are kept in sorted order.
- The GUI is English by default with a runtime switch to Chinese.

## Reproducing the paper experiments

```bash
python experiments/run_experiments.py
python experiments/make_figures.py
```

The harness is seeded (`SEED = 20261011`); the same command must produce the
same `experiments/results/results.json`. If you change the retrieval
thresholds, the fusion weights, or the corpus, regenerate the tables and
figures and commit them together with the code change.

## Reporting bugs

Please include:

- the tool version (`python A2L_Update_Tool_main.py --version`),
- the address file type (AXF / ELF / MAP) and toolchain,
- the exact command line or the GUI steps,
- the match report (`--report`) with any proprietary names removed.

## Pull requests

- One logical change per pull request.
- Update `README.md` when behaviour or CLI options change.
- Update the version in `A2L_Update_Tool_main.py` (`APP_VERSION`) and
  `A2L_Update_Tool_V*.spec` when you cut a release.

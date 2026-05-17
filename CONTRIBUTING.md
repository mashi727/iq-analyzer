# Contributing to iq-analyzer

Thanks for considering a contribution! This project is small enough that the
day-to-day flow is straightforward.

## Development setup

The recommended toolchain is [uv](https://docs.astral.sh/uv/) — it manages the
virtual environment, the Python toolchain, and the dev/build extras.

```bash
git clone https://github.com/mashi727/iq-analyzer.git
cd iq-analyzer
uv sync               # creates .venv/, installs runtime + dev deps + this package (editable)
```

After this, the following commands work out of the box:

```bash
uv run iq-analyzer    # launch the GUI
uv run pytest -q      # full test suite (~3 s, runs offscreen)
uv run ruff check .   # lint
uv run ruff check --fix .   # auto-fix what's safe
```

## Project layout

See the [Package structure](README.md#パッケージ構造) section of the README
for the module map. As a rule of thumb:

- **`iq_analyzer/core/`** — pure-Python signal processing. Should never import
  PySide6 or pyqtgraph. Easy to test in isolation.
- **`iq_analyzer/loaders/`** — file-format adapters. Each implements the
  `IQLoader` protocol (`header`, `get_iq_data()`, `close()`).
- **`iq_analyzer/widgets/`** — self-contained Qt widgets. They communicate
  with the main window only via Qt signals.
- **`iq_analyzer/ui/`** — the main window, which orchestrates everything.
- **`tests/`** — pytest. Widget tests use a session-wide offscreen
  `QApplication` from `conftest.py`.

## Coding conventions

- Python 3.11+ syntax (PEP 604 unions, structural typing).
- Type hints on new functions; rely on Pyright through the repo's default
  config (false positives from PySide6/pyqtgraph dynamic attrs are tolerated).
- Format & lint via **ruff** — the config lives in `pyproject.toml`. No
  separate formatter; `ruff format` is fine if you need it.
- Comments only when the *why* isn't obvious from the code. Don't restate
  the *what*.
- Tests live next to the matching module (`tests/test_<module>.py`). Add a
  test for any pure-Python change in `core/` or `loaders/`.

## Commit messages

Loose convention: short imperative subject under ~70 characters, optional
body explaining the *why*. Examples in `git log`:

- `Fix spectrogram blanking on first calculation; default to 10% region`
- `Add Keysight N5110A (.bin + .bin.txt) loader`
- `Refactor: extract UI panels into iq_analyzer.widgets`

If you used Claude Code, please keep the `Co-Authored-By:` trailer.

## Pull request checklist

- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check .` is clean
- [ ] New behaviour has a test (where reasonably possible without a live IQ
      file)
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] If the change touches the GUI, a quick manual test with one of the
      sample files in `data/` (gitignored — see the project owner)

## Releasing

Pushes that match the `v*` tag pattern trigger
`.github/workflows/release.yml`, which builds a single-file Windows EXE with
PyInstaller and attaches it to a GitHub Release. To cut a release:

1. Update the version in `pyproject.toml` and `src/iq_analyzer/__init__.py`.
2. Move the `## [Unreleased]` notes to a new dated section in `CHANGELOG.md`.
3. `git tag v0.x.y && git push --tags` — the workflow does the rest.

## Reporting bugs

Open an issue with:

- OS / Python version / iq-analyzer version (`pip show iq-analyzer`)
- File format that triggered the bug
- The output captured in the in-app log panel (it tees real stdout)
- Steps to reproduce

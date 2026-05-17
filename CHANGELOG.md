# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-05-17

First public release. The project started life as a single-file
`rs_iq_viewer.py` script (3,595 lines) and has been refactored into a proper
src-layout package with full test coverage.

### Added

- **Three IQ data formats** supported through a unified `open_iq_file()` factory:
  - Rohde & Schwarz WVH/WVD (RAW16LE, int16 interleaved)
  - Rohde & Schwarz iq.tar (float32 / float64 in a tar archive)
  - Keysight N5110A (.bin + .bin.txt metadata, 16-bit LE with YScale)
- **Spectrogram widget** with adaptive color scaling, max-pool downsampling
  and four built-in colormaps (plasma / viridis / inferno / magma).
- **Min-Max envelope decimation** for the overview waveform, preserves
  narrow pulses that simple stride decimation would drop.
- **Auto STFT parameter selection** driven by a duration → settings lookup
  table, capped by an estimated 4 GB memory budget.
- **Console entry points**: `iq-analyzer` (via project.scripts) and
  `python -m iq_analyzer`.
- **75 pytest cases** covering loaders, signal-processing helpers, widget
  signal contracts and CLI surface. Runs headlessly via offscreen Qt.
- **CI** on GitHub Actions: ruff + pytest across Linux / macOS / Windows ×
  Python 3.11 / 3.12 / 3.13.
- **Windows single-file EXE** built via PyInstaller on release tags.

### Architecture

- `iq_analyzer.core` — UI-independent signal processing (decimation,
  spectrogram STFT, memory monitoring, stdout-to-Qt-signal redirector).
- `iq_analyzer.loaders` — format-specific loaders sharing an `IQLoader`
  Protocol.
- `iq_analyzer.widgets` — self-contained PySide6 panels communicating with
  the main window only via Qt signals.
- `iq_analyzer.ui.main_window` — orchestrates the three plots and the
  region/ROI synchronisation.
- `iq_analyzer.cli` — `QApplication` lifecycle.

### Fixed (during pre-1.0 development)

- Spectrogram appeared blank on the first calculation because `setImage()` and
  `setLevels()` were called sequentially; Qt repainted once in between with
  the stale levels from the previous capture. Now passes levels in the same
  `setImage()` call.
- Reduced default initial Region span from 50% to 10% (middle-centred) so the
  first STFT on a multi-GB recording finishes in seconds rather than minutes.
- Replaced ~24 bare `except:` clauses in cleanup paths with `except Exception`
  / `contextlib.suppress(Exception)` so Ctrl+C / SystemExit are no longer
  swallowed during teardown.

[Unreleased]: https://github.com/mashi727/iq-analyzer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mashi727/iq-analyzer/releases/tag/v0.1.0

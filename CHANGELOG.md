# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Rohde & Schwarz ARB waveform files (`.wv`, `{TYPE: SMU-WV}`) via the new
  `SMUWVLoader`. The single-file layout (ASCII tags + `{WAVEFORM-n:#...}` int16
  payload) is memory-mapped at the payload offset. Both `WAVEFORM` and
  `WWAVEFORM` tags are accepted; when the `SAMPLES` tag disagrees with the
  payload length, the payload length wins.
- Precomputed amplitude envelope (`core.envelope`) for very large files: one
  background pass stores min/max amplitude per 4096 samples (~49 MB for
  100 GB) in the per-user cache directory (never next to the data). Overview
  and zoomed waveforms are then served from it in milliseconds, with every
  sample contributing. Measured on a 17 GB file: first open shows a preview in
  1.5 s and the envelope completes in ~8 s; reopening takes ~0.2 s.
- Streaming spectrogram (`compute_spectrogram_streaming`): the region is read
  and transformed in batches, and frames beyond 4096 columns are max-pooled in
  time, so memory no longer grows with region length.
- File browser rebuilt as an Explorer-style tree, ported from book-viewer:
  `..` (re-root one level up), the start folder, ホーム, and この Mac / PC
  listing the boot disk and external/network drives (`/Volumes` is hidden on
  macOS, so external drives were previously unreachable). Folders load lazily
  and are watched for changes; drive plug/unplug updates the list. Only
  folders and IQ files are shown. Single click previews the header, double
  click loads. Tree and header preview share a vertical splitter.
- The output panel now also shows `sys.stderr` (tracebacks, Python warnings)
  and `iq_analyzer` log records at WARNING and above, in red. Previously only
  stdout was captured, so failures such as an envelope-cache write error were
  invisible — and lost entirely in the `--windowed` EXE, where `sys.stderr` is
  `None`. Qt's own C++ warnings are intentionally not routed (they are emitted
  mid-paint, and echoing them would re-enter the event loop).
- Payloads whose byte entropy is indistinguishable from uniform random data
  (> 7.99 bit/byte, typical of encrypted waveforms) trigger a warning dialog,
  because the displayed waveform would not represent the recorded signal.

### Changed

- Removed the breadcrumb bar; the tree's `..` and top-level entries replace it.
- The initial region is the central 10% capped at 2^28 samples (1 GB of int16),
  so the first spectrogram of a 100 GB file no longer takes minutes.

### Fixed

- Spectrogram frequency axis read "kMHz" for RF recordings (3.1 GHz): values
  were plotted in MHz with units fixed to "MHz", and pyqtgraph's SI prefix
  stacked on top. The axis is now in Hz and shows GHz or MHz as appropriate.
- Output panel: after the first stderr line, every later stdout line was also
  red (appended HTML carried its colour forward). Each line now gets an
  explicit character format.
- Envelope progress signal was declared `Signal(int, int)`, a 32-bit C int in
  PySide6; files beyond 2^31 samples (~8.6 GB) flooded stderr with
  `OverflowError` and the overview/progress never updated. Now `qlonglong`.
- Opening a 121 GB WVD on an external drive took 273 s: the initial region
  (1 GB) was read in full while the envelope build streamed from another
  offset of the same drive. Ranges the envelope has not reached yet now use
  the bounded preview, and the build pauses during previews, spectrogram
  computation and region save. Measured: open 1.55 s; spectrogram of the
  initial region mid-build 10.6 s.
- WVH/WVD pairs with different names (e.g. `X_header.wvh` + `X_data.wvd`)
  failed with "WVDファイルが見つかりません". The data file is now matched by
  same stem, else by exact size (`SAMPLES × 4`), else as the folder's only
  pair; ambiguous folders list the candidates. `.wvd` files are shown in the
  file browser and can be opened directly (the header is found the same way).
- Overview decimation read the *whole* file on every full-span redraw: its
  "sub-sampling" branch read each bin in full before sub-sampling. Wide ranges
  without an envelope now use a bounded preview (≤ 512 reads of 64 Ki samples).
- Intermittent segfault: `print()` → `append_stdout` → `processEvents()` could
  re-enter the event loop from inside a repaint. Re-entry is now guarded.
- Spectrogram rendered as a single flat colour on recordings with recurring
  strong bursts. Three compounding causes: the auto colour range used
  `max - 30 dB` as its lower bound, which rose above the whole noise floor when
  bursts were frequent (now capped at the median); max-pool downsampling read
  the viewport size in data units (s × MHz) instead of pixels, so it never ran
  and short bursts were dropped by the painter; and levels were computed on the
  raw STFT rather than the pooled image actually shown.

## [0.1.1] — 2026-05-26

### Fixed

- `pyqtdarktheme` is now an optional `[darktheme]` extra instead of a hard
  dependency. Its only PyPI release (2.1.0) pins `Requires-Python <3.12`, so
  `pip install iq-analyzer` failed on Python 3.12/3.13 even though the dark
  theme is purely cosmetic and already imported behind a try/except. Core
  installs now have no Python-version-gated dependencies.

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

[Unreleased]: https://github.com/mashi727/iq-analyzer/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/mashi727/iq-analyzer/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/mashi727/iq-analyzer/releases/tag/v0.1.0

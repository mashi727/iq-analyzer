"""Smoke tests for :class:`iq_analyzer.ui.main_window.RSIQViewer` and the CLI.

The viewer wires together every panel; the tests below mainly verify that the
window *can* be constructed and shown headlessly, which catches the bulk of
import / signal-wiring regressions without needing a real IQ file.
"""

from __future__ import annotations

import pytest


def test_main_window_constructs_and_aliases_attributes(qapp) -> None:
    from iq_analyzer.ui.main_window import RSIQViewer

    viewer = RSIQViewer()
    try:
        # Subwidgets exist
        assert viewer.file_browser is not None
        assert viewer.control_panel is not None
        assert viewer.adjust_panel is not None
        assert viewer.spectrogram_widget is not None

        # Legacy attribute aliases that the remaining methods rely on
        assert viewer.nfft_spin is viewer.adjust_panel.nfft_spin
        assert viewer.overlap_spin is viewer.adjust_panel.overlap_spin
        assert viewer.calc_spec_btn is viewer.control_panel.calc_spec_btn
        assert viewer.save_btn is viewer.control_panel.save_btn

        # Initial state — no file loaded
        assert viewer.wv_loader is None
        assert viewer.total_samples == 0
    finally:
        viewer.close()


def test_main_window_show_and_close(qapp) -> None:
    from iq_analyzer.ui.main_window import RSIQViewer

    viewer = RSIQViewer()
    viewer.show()
    viewer.close()
    # No assertion needed — surviving construction + close cycle is the test.


def test_cli_main_is_callable_and_exposes_helpers() -> None:
    """We do not drive the actual event loop here — Qt's closeEvent cleanup
    chain has historically been brittle to invoke from inside pytest. Instead
    verify the public surface: ``main`` is callable and the small helpers it
    relies on are pure-enough to assert against directly."""
    from iq_analyzer import cli

    assert callable(cli.main)
    # platform font helper returns a positive int
    size = cli._platform_font_size()
    assert isinstance(size, int) and size > 0


def test_legacy_shim_re_exports(qapp) -> None:
    """``rs_iq_viewer`` must keep RSIQViewer and main importable."""
    import importlib.util
    from pathlib import Path

    shim_path = Path(__file__).resolve().parents[1] / "rs_iq_viewer.py"
    spec = importlib.util.spec_from_file_location("rs_iq_viewer_shim", shim_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(module, "RSIQViewer")
    assert hasattr(module, "main")
    assert callable(module.main)


@pytest.mark.parametrize("entry", ["iq_analyzer.cli", "iq_analyzer.__main__"])
def test_package_entry_points_importable(entry: str) -> None:
    """Both ``iq-analyzer`` and ``python -m iq_analyzer`` resolve to live code."""
    import importlib

    module = importlib.import_module(entry)
    assert hasattr(module, "main")

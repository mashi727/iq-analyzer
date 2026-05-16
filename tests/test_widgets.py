"""Behaviour tests for the extracted panel widgets.

These spin up a real (offscreen) QApplication so we exercise the Qt signal
wiring end-to-end rather than just the Python data structures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iq_analyzer.widgets import (
    AVAILABLE_COLORMAPS,
    AdjustmentPanel,
    BreadcrumbBar,
    ControlPanel,
    FileBrowserPanel,
)


def test_breadcrumb_set_path_emits_clicks_per_segment(qapp) -> None:
    bar = BreadcrumbBar(font_size_pt=10)
    bar.set_path(Path("/tmp/a/b"))
    assert bar.current_path == Path("/tmp/a/b")

    received: list[Path] = []
    bar.path_clicked.connect(received.append)

    # Each segment is a QPushButton sitting in the layout (separators are QLabel).
    from PySide6.QtWidgets import QPushButton

    buttons = [
        bar.layout().itemAt(i).widget()
        for i in range(bar.layout().count())
        if isinstance(bar.layout().itemAt(i).widget(), QPushButton)
    ]
    # Path has 4 segments on POSIX: /, tmp, a, b
    assert len(buttons) == 4
    buttons[-1].click()
    buttons[0].click()
    assert received == [Path("/tmp/a/b"), Path("/")]


def test_breadcrumb_set_path_rebuilds_cleanly(qapp) -> None:
    bar = BreadcrumbBar()
    bar.set_path("/tmp/a")
    first_count = bar.layout().count()
    bar.set_path("/tmp/a/b/c")
    second_count = bar.layout().count()
    assert second_count > first_count  # more segments now


def test_adjustment_panel_signals(qapp) -> None:
    panel = AdjustmentPanel()

    cmap_seen: list[str] = []
    nfft_seen: list[int] = []
    overlap_seen: list[int] = []
    cutoff_seen: list[int] = []
    auto_seen: list[bool] = []

    panel.colormap_changed.connect(cmap_seen.append)
    panel.nfft_changed.connect(nfft_seen.append)
    panel.overlap_changed.connect(overlap_seen.append)
    panel.cutoff_changed.connect(cutoff_seen.append)
    panel.auto_update_changed.connect(auto_seen.append)

    assert "plasma" in AVAILABLE_COLORMAPS
    panel.colormap_combo.setCurrentText("viridis")
    panel.nfft_spin.setValue(1024)
    panel.overlap_spin.setValue(70)
    panel.lower_cutoff_spin.setValue(5)
    panel.auto_update_checkbox.setChecked(True)

    assert cmap_seen[-1] == "viridis"
    assert nfft_seen[-1] == 1024
    assert overlap_seen[-1] == 70
    assert cutoff_seen[-1] == 5
    assert auto_seen[-1] is True


def test_adjustment_panel_accessors(qapp) -> None:
    panel = AdjustmentPanel()
    panel.nfft = 2048
    panel.overlap_percent = 25
    assert panel.nfft == 2048
    assert panel.overlap_percent == 25
    assert panel.colormap == "plasma"
    assert panel.auto_update is False
    assert panel.lower_cutoff_percent == 0


def test_control_panel_button_signals(qapp) -> None:
    panel = ControlPanel()
    panel.set_calculate_enabled(True)
    panel.set_save_enabled(True)

    calc: list[bool] = []
    save: list[bool] = []
    quit_: list[bool] = []

    panel.calculate_clicked.connect(lambda: calc.append(True))
    panel.save_clicked.connect(lambda: save.append(True))
    panel.exit_clicked.connect(lambda: quit_.append(True))

    panel.calc_spec_btn.click()
    panel.save_btn.click()
    panel.exit_btn.click()

    assert calc and save and quit_


def test_control_panel_busy_state_round_trip(qapp) -> None:
    panel = ControlPanel()
    panel.set_calculate_enabled(True)
    panel.set_calculate_busy(True)
    assert not panel.calc_spec_btn.isEnabled()
    assert "計算中" in panel.calc_spec_btn.text()
    panel.set_calculate_busy(False)
    assert panel.calc_spec_btn.isEnabled()
    assert "スペクトログラム" in panel.calc_spec_btn.text()


def test_control_panel_breadcrumb_forwards_clicks(qapp) -> None:
    panel = ControlPanel()
    received: list[Path] = []
    panel.breadcrumb_path_clicked.connect(received.append)
    # Drive the inner bar directly — we're verifying the forwarding hookup.
    panel.breadcrumb.path_clicked.emit(Path("/some/where"))
    assert received == [Path("/some/where")]


def test_file_browser_set_root_dir_emits_signal(qapp, tmp_path: Path) -> None:
    panel = FileBrowserPanel()
    seen_dirs: list[Path] = []
    seen_msgs: list[str] = []
    panel.current_dir_changed.connect(seen_dirs.append)
    panel.status_message.connect(seen_msgs.append)

    panel.set_root_dir(tmp_path)

    assert panel.current_root_dir == tmp_path
    assert seen_dirs == [tmp_path]
    assert any(str(tmp_path) in msg for msg in seen_msgs)


def test_file_browser_set_root_dir_ignores_invalid_path(qapp, tmp_path: Path) -> None:
    panel = FileBrowserPanel()
    seen: list[Path] = []
    panel.current_dir_changed.connect(seen.append)

    missing = tmp_path / "does-not-exist"
    panel.set_root_dir(missing)
    assert seen == []


def test_file_browser_header_preview_renders_wvh(qapp, tmp_path: Path) -> None:
    """Selecting a real WVH file should populate the preview without raising."""
    import numpy as np

    from iq_analyzer.loaders import WVFileLoader

    samples = 128
    rng = np.random.default_rng(0)
    interleaved = rng.integers(-100, 100, size=samples * 2, dtype=np.int16)
    (tmp_path / "demo.wvd").write_bytes(interleaved.tobytes())
    WVFileLoader.write_wvh_header(
        tmp_path / "demo.wvh",
        {
            "TYPE": "RAW16LE",
            "COMPONENTS": "IQ",
            "CLOCK": 32_000_000.0,
            "RESOLUTION": 16,
            "FREQUENCY": 1e6,
            "REFLEVEL": -10.0,
            "SAMPLES": samples,
        },
    )

    panel = FileBrowserPanel()
    panel._show_header(tmp_path / "demo.wvh")  # internal helper kept stable
    text = panel.header_info_text.toPlainText()
    assert "demo.wvh" in text
    assert "中心周波数" in text


@pytest.mark.parametrize("name", ["plasma", "viridis", "inferno", "magma"])
def test_adjustment_panel_lists_every_known_colormap(qapp, name) -> None:
    panel = AdjustmentPanel()
    items = [panel.colormap_combo.itemText(i) for i in range(panel.colormap_combo.count())]
    assert name in items

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
    ControlPanel,
    FileBrowserPanel,
)


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


def _tree_labels(item) -> list[str]:
    return [item.child(i).text(0) for i in range(item.childCount())]


def _make_iq_folder(root: Path) -> Path:
    folder = root / "rec"
    (folder / "sub").mkdir(parents=True)
    (folder / "a.wvh").write_text("{TYPE:RAW16LE}{CLOCK:1}{SAMPLES:1}")
    (folder / "a.wvd").write_bytes(b"\0" * 4)
    (folder / "b.wv").write_bytes(b"{TYPE: SMU-WV,0}")
    (folder / "notes.txt").write_text("not IQ")
    (folder / "firmware.bin").write_bytes(b"\0")  # no .bin.txt -> not IQ
    (folder / ".hidden.wvh").write_text("")
    return folder


def test_file_browser_top_level_structure(qapp, tmp_path: Path) -> None:
    from iq_analyzer.widgets.file_browser import _COMPUTER_LABEL, mounted_volumes

    panel = FileBrowserPanel(tmp_path)
    tops = [panel.tree.topLevelItem(i).text(0) for i in range(panel.tree.topLevelItemCount())]
    assert tops == ["..", tmp_path.name, "ホーム", _COMPUTER_LABEL]
    assert _tree_labels(panel._mac) == [name for name, _ in mounted_volumes()]
    assert panel._mac.isExpanded() and not panel._start_item.isExpanded()


def test_file_browser_lists_folders_then_iq_files_only(qapp, tmp_path: Path) -> None:
    folder = _make_iq_folder(tmp_path)
    panel = FileBrowserPanel(folder)
    panel._start_item.setExpanded(True)
    assert _tree_labels(panel._start_item) == ["sub", "a.wvd", "a.wvh", "b.wv"]


def test_file_browser_go_up_reroots_and_selects_previous(qapp, tmp_path: Path) -> None:
    folder = _make_iq_folder(tmp_path)
    panel = FileBrowserPanel(folder)
    seen: list[Path] = []
    panel.current_dir_changed.connect(seen.append)

    panel._on_clicked(panel._up_item, 0)

    assert panel.current_root_dir == tmp_path
    assert seen == [tmp_path]
    assert panel.tree.currentItem().text(0) == "rec"


def test_file_browser_click_previews_double_click_opens(qapp, tmp_path: Path) -> None:
    folder = _make_iq_folder(tmp_path)
    panel = FileBrowserPanel(folder)
    opened: list[Path] = []
    panel.file_open_requested.connect(opened.append)

    assert panel.reveal(folder / "b.wv")
    item = panel.tree.currentItem()
    assert item.text(0) == "b.wv"
    assert "b.wv" in panel.header_info_text.toPlainText()  # preview on selection
    assert opened == []

    panel._on_double_clicked(item, 0)
    assert opened == [folder / "b.wv"]


def test_file_browser_picks_up_new_files(qapp, tmp_path: Path) -> None:
    folder = _make_iq_folder(tmp_path)
    panel = FileBrowserPanel(folder)
    panel._start_item.setExpanded(True)
    (folder / "c.wv").write_bytes(b"")
    panel._on_dir_changed(str(folder))
    panel._refresh_dirty()
    assert "c.wv" in _tree_labels(panel._start_item)


def test_file_browser_reveal_through_symlinked_path(qapp, tmp_path: Path) -> None:
    """/tmp vs /private/tmp on macOS: reveal must match the real path."""
    folder = _make_iq_folder(tmp_path / "real")
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real")
    panel = FileBrowserPanel(folder)
    assert panel.reveal(link / "rec" / "b.wv")
    assert panel.tree.currentItem().text(0) == "b.wv"

"""Display-adjustment panel: colormap, NFFT/overlap, cutoff, auto-update toggle.

Every user-facing control is exposed both as a Qt signal (for live updates)
and as a property (for the main window to read the current value when
triggering manual operations).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from iq_analyzer.widgets.spectrogram import AVAILABLE_COLORMAPS


class AdjustmentPanel(QWidget):
    """Compact panel for spectrogram display settings."""

    colormap_changed = Signal(str)
    nfft_changed = Signal(int)
    overlap_changed = Signal(int)
    cutoff_changed = Signal(int)
    auto_update_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._wire_signals()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        # Two columns of (label, control) so the panel is only three rows
        # tall and the bottom strip (log + this panel) stays ~160 px.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("表示調整")
        grid = QGridLayout(group)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(list(AVAILABLE_COLORMAPS))

        self.nfft_spin = QSpinBox()
        self.nfft_spin.setRange(64, 8192)
        self.nfft_spin.setSingleStep(64)
        self.nfft_spin.setValue(256)
        self.nfft_spin.setToolTip("スペクトログラムのFFTサイズ")

        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 90)
        self.overlap_spin.setSingleStep(10)
        self.overlap_spin.setValue(50)
        self.overlap_spin.setSuffix("%")
        self.overlap_spin.setToolTip("スペクトログラムのオーバーラップ率")

        self.lower_cutoff_spin = QSpinBox()
        self.lower_cutoff_spin.setRange(0, 50)
        self.lower_cutoff_spin.setSingleStep(1)
        self.lower_cutoff_spin.setValue(0)
        self.lower_cutoff_spin.setSuffix(" %")
        self.lower_cutoff_spin.setToolTip(
            "下位何%をカットするか\n"
            "0% = カットなし（全表示）\n"
            "1% = 軽度のノイズ除去\n"
            "3-5% = 中程度のノイズ除去\n"
            "10%以上 = 強力なノイズ除去"
        )
        cutoff_label = QLabel("下位カット:")
        cutoff_label.setToolTip("ノイズフロアをカットして小信号を強調\n大きい値でノイズを除去")

        self.auto_update_checkbox = QCheckBox("Region変更時に自動更新")
        self.auto_update_checkbox.setChecked(False)
        self.auto_update_checkbox.setToolTip("ONにするとRegion範囲変更時にスペクトログラムも自動計算")

        grid.addWidget(QLabel("カラーマップ:"), 0, 0)
        grid.addWidget(self.colormap_combo, 0, 1)
        grid.addWidget(QLabel("NFFT:"), 0, 2)
        grid.addWidget(self.nfft_spin, 0, 3)
        grid.addWidget(QLabel("オーバーラップ:"), 1, 0)
        grid.addWidget(self.overlap_spin, 1, 1)
        grid.addWidget(cutoff_label, 1, 2)
        grid.addWidget(self.lower_cutoff_spin, 1, 3)
        grid.addWidget(self.auto_update_checkbox, 2, 0, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        grid.setRowStretch(3, 1)
        outer.addWidget(group)

    def _wire_signals(self) -> None:
        self.colormap_combo.currentTextChanged.connect(self.colormap_changed.emit)
        self.nfft_spin.valueChanged.connect(self.nfft_changed.emit)
        self.overlap_spin.valueChanged.connect(self.overlap_changed.emit)
        self.lower_cutoff_spin.valueChanged.connect(self.cutoff_changed.emit)
        self.auto_update_checkbox.toggled.connect(self.auto_update_changed.emit)

    # ------------------------------------------------------------ accessors

    @property
    def nfft(self) -> int:
        return int(self.nfft_spin.value())

    @nfft.setter
    def nfft(self, value: int) -> None:
        self.nfft_spin.setValue(int(value))

    @property
    def overlap_percent(self) -> int:
        return int(self.overlap_spin.value())

    @overlap_percent.setter
    def overlap_percent(self, value: int) -> None:
        self.overlap_spin.setValue(int(value))

    @property
    def lower_cutoff_percent(self) -> int:
        return int(self.lower_cutoff_spin.value())

    @property
    def colormap(self) -> str:
        return str(self.colormap_combo.currentText())

    @property
    def auto_update(self) -> bool:
        return bool(self.auto_update_checkbox.isChecked())

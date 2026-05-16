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
    QFrame,
    QGroupBox,
    QHBoxLayout,
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("表示調整")
        layout = QVBoxLayout(group)

        self.auto_update_checkbox = QCheckBox("Region変更時に自動更新")
        self.auto_update_checkbox.setChecked(False)
        self.auto_update_checkbox.setToolTip(
            "ONにするとRegion範囲変更時にスペクトログラムも自動計算"
        )
        layout.addWidget(self.auto_update_checkbox)

        layout.addWidget(_separator())

        cmap_row = QHBoxLayout()
        cmap_row.addWidget(QLabel("カラーマップ:"))
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(list(AVAILABLE_COLORMAPS))
        cmap_row.addWidget(self.colormap_combo)
        layout.addLayout(cmap_row)

        nfft_row = QHBoxLayout()
        nfft_row.addWidget(QLabel("NFFT:"))
        self.nfft_spin = QSpinBox()
        self.nfft_spin.setRange(64, 8192)
        self.nfft_spin.setSingleStep(64)
        self.nfft_spin.setValue(256)
        self.nfft_spin.setToolTip("スペクトログラムのFFTサイズ")
        nfft_row.addWidget(self.nfft_spin)
        layout.addLayout(nfft_row)

        overlap_row = QHBoxLayout()
        overlap_row.addWidget(QLabel("オーバーラップ:"))
        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 90)
        self.overlap_spin.setSingleStep(10)
        self.overlap_spin.setValue(50)
        self.overlap_spin.setSuffix("%")
        self.overlap_spin.setToolTip("スペクトログラムのオーバーラップ率")
        overlap_row.addWidget(self.overlap_spin)
        layout.addLayout(overlap_row)

        layout.addWidget(_separator())

        cutoff_label = QLabel("下位カットオフ:")
        cutoff_label.setToolTip(
            "ノイズフロアをカットして小信号を強調\n大きい値でノイズを除去"
        )
        layout.addWidget(cutoff_label)

        cutoff_row = QHBoxLayout()
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
        cutoff_row.addWidget(self.lower_cutoff_spin)
        layout.addLayout(cutoff_row)

        layout.addStretch()
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


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line

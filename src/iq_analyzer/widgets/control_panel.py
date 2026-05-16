"""Top row of the main window: breadcrumb + action buttons."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from iq_analyzer.widgets.breadcrumb import BreadcrumbBar


class ControlPanel(QWidget):
    """Breadcrumb on the left, the three main action buttons on the right."""

    breadcrumb_path_clicked = Signal(Path)
    calculate_clicked = Signal()
    save_clicked = Signal()
    exit_clicked = Signal()

    def __init__(
        self,
        *,
        font_size_large: int = 12,
        button_height: int = 30,
        max_height: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._font_size_large = font_size_large
        self._button_height = button_height

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.breadcrumb = BreadcrumbBar(font_size_pt=font_size_large)
        self.breadcrumb.path_clicked.connect(self.breadcrumb_path_clicked.emit)
        layout.addWidget(self.breadcrumb, 0)

        layout.addStretch()

        self.calc_spec_btn = self._make_button(
            "📊 スペクトログラム計算",
            "選択したRegion範囲のスペクトログラムを手動計算\n自動更新がOFFの場合に使用",
            background="#4CAF50",
            hover="#45A049",
        )
        self.calc_spec_btn.setEnabled(False)
        self.calc_spec_btn.clicked.connect(self.calculate_clicked.emit)
        layout.addWidget(self.calc_spec_btn)

        self.save_btn = self._make_button(
            "💾 保存",
            "選択したRegion範囲をWVH/WVD形式で保存",
            background="#00BCD4",
            hover="#0097A7",
        )
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.save_btn)

        self.exit_btn = self._make_button(
            "🚪 終了",
            "アプリケーションを終了 (Ctrl+Q / Cmd+Q)",
            background="#f44336",
            hover="#d32f2f",
        )
        self.exit_btn.clicked.connect(self.exit_clicked.emit)
        layout.addWidget(self.exit_btn)

        if max_height is not None:
            self.setMaximumHeight(max_height)

    def set_calculate_enabled(self, enabled: bool) -> None:
        self.calc_spec_btn.setEnabled(enabled)

    def set_calculate_busy(self, busy: bool) -> None:
        self.calc_spec_btn.setEnabled(not busy)
        self.calc_spec_btn.setText("計算中..." if busy else "📊 スペクトログラム計算")

    def set_save_enabled(self, enabled: bool) -> None:
        self.save_btn.setEnabled(enabled)

    def set_breadcrumb_path(self, path: Path | str) -> None:
        self.breadcrumb.set_path(path)

    def _make_button(self, label: str, tooltip: str, *, background: str, hover: str) -> QPushButton:
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.setFixedHeight(self._button_height)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {background};
                color: white;
                font-weight: bold;
                font-size: {self._font_size_large}pt;
                padding: 5px 15px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:disabled {{
                background-color: #cccccc;
                color: #666666;
            }}
            """
        )
        return btn

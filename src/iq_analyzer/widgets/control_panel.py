"""Top row of the main window: the main action buttons."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from iq_analyzer.ui.style import action_button


class ControlPanel(QWidget):
    """The main action buttons, right-aligned."""

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
        # Size and colours come from ui.style.action_button (shared with the
        # playback panel); the two size
        # arguments are kept for API compatibility.
        del font_size_large, button_height

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addStretch()

        self.calc_spec_btn = action_button(
            "📊 スペクトログラム計算",
            "選択したRegion範囲のスペクトログラムを手動計算\n自動更新がOFFの場合に使用",
            role="run",
        )
        self.calc_spec_btn.setEnabled(False)
        self.calc_spec_btn.clicked.connect(self.calculate_clicked.emit)
        layout.addWidget(self.calc_spec_btn)

        self.save_btn = action_button(
            "💾 保存",
            "選択したRegion範囲をWVH/WVD形式で保存",
            role="save",
        )
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.save_btn)

        self.exit_btn = action_button(
            "🚪 終了",
            "アプリケーションを終了 (Ctrl+Q / Cmd+Q)",
            role="stop",
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

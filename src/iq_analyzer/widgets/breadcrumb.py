"""Clickable breadcrumb bar showing the active directory path.

Each path segment becomes a flat button; clicking one emits
:attr:`BreadcrumbBar.path_clicked` with the corresponding :class:`Path`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class BreadcrumbBar(QWidget):
    """Path → row of clickable segments."""

    path_clicked = Signal(Path)

    def __init__(self, font_size_pt: int = 12, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._font_size_pt = font_size_pt

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 5, 0, 5)
        self._layout.setSpacing(0)
        self._layout.setAlignment(Qt.AlignLeft)

        self._current_path: Path | None = None

    def set_path(self, path: Path | str) -> None:
        """Rebuild the breadcrumb so it reflects *path*."""
        path = Path(path)
        self._current_path = path
        self._clear()

        parts: list[Path] = []
        cursor = path
        while True:
            parts.insert(0, cursor)
            if cursor.parent == cursor:
                break
            cursor = cursor.parent

        for i, segment in enumerate(parts):
            label = self._segment_label(i, segment)
            btn = QPushButton(label)
            btn.setFlat(True)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: transparent;
                    border: none;
                    color: #0066cc;
                    text-align: left;
                    padding: 2px 5px;
                    font-size: {self._font_size_pt}pt;
                }}
                QPushButton:hover {{
                    background-color: #e6f2ff;
                    text-decoration: underline;
                }}
                """
            )
            # Capture the path in the default arg to avoid the usual late-binding
            # lambda gotcha.
            btn.clicked.connect(lambda _checked=False, p=segment: self.path_clicked.emit(p))
            self._layout.addWidget(btn)

            if i < len(parts) - 1:
                separator = QLabel("/")
                separator.setStyleSheet(
                    f"color: #888888; font-size: {self._font_size_pt}pt;"
                )
                self._layout.addWidget(separator)

    @property
    def current_path(self) -> Path | None:
        return self._current_path

    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _segment_label(index: int, segment: Path) -> str:
        if index != 0:
            return segment.name
        # Root looks different on Windows vs POSIX — show the drive letter or "/".
        return str(segment) if sys.platform == "win32" else "/"

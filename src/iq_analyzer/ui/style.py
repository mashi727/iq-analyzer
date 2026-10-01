"""Application-wide flat dark theme.

One set of colour tokens drives the Qt palette, the stylesheet and the
pyqtgraph defaults, so panes, text areas and plots share the same near-black
surfaces. Separators and axes are low-contrast greys: on a black background
the default white frame lines and axes were the brightest thing on screen
and competed with the signal.

The Fusion style is used because it is flat and draws combo/spin-box arrows
from the palette on every platform (a stylesheet border on the native macOS
widgets made Qt drop the arrows, and stylesheets cannot draw triangles).
"""

from __future__ import annotations

from typing import Any

# --- colour tokens -------------------------------------------------------
WINDOW = "#141414"  # window and panel background
SURFACE = "#0c0c0c"  # text areas, trees, tables, inputs ("black")
SURFACE_ALT = "#121212"  # alternate table rows
BUTTON = "#232323"
BUTTON_HOVER = "#2e2e2e"
BUTTON_PRESSED = "#383838"
LINE = "#262626"  # separators and frames: visible but quiet
TEXT = "#d4d4d4"
TEXT_MUTED = "#8a8a8a"
TEXT_DISABLED = "#5a5a5a"
ACCENT = "#3b78c2"  # selection, focus, progress
PLOT_BG = "#0a0a0a"
PLOT_AXIS = "#4a4a4a"  # axis lines and ticks
PLOT_TEXT = "#9a9a9a"  # tick labels and titles
# Grid lines are drawn in the (already dim) axis colour at this opacity;
# the default near-white axis at 0.3 was too bright on black.
GRID_ALPHA = 0.5
ERROR = "#ff6b6b"

# Gaps between panes (px). Kept here so every layout uses the same rhythm.
PANE_MARGIN = 4
PANE_SPACING = 4
SPLITTER_HANDLE = 2

FLAT_STYLESHEET = f"""
QGroupBox {{
    border: none;
    margin-top: 1.1em;
    font-weight: bold;
    color: {TEXT_MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 2px;
    padding: 0;
}}
QPushButton {{
    border: none;
    border-radius: 3px;
    padding: 3px 12px;
    background: {BUTTON};
    color: {TEXT};
}}
QPushButton:hover {{ background: {BUTTON_HOVER}; }}
QPushButton:pressed {{ background: {BUTTON_PRESSED}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    border: none;
    padding: 3px 12px;
    background: transparent;
    color: {TEXT_MUTED};
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTableView, QTableWidget, QTreeView, QTreeWidget, QTextEdit, QPlainTextEdit {{
    border: 1px solid {LINE};
    background: {SURFACE};
    gridline-color: {LINE};
}}
QHeaderView::section {{
    border: none;
    border-right: 1px solid {LINE};
    border-bottom: 1px solid {LINE};
    padding: 2px 6px;
    background: {WINDOW};
    color: {TEXT_MUTED};
}}
QSplitter::handle {{ background: {LINE}; }}
QProgressBar {{
    border: none;
    background: {SURFACE};
    color: {TEXT};
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; }}
QScrollBar:vertical {{ background: {SURFACE}; border: none; width: 10px; }}
QScrollBar:horizontal {{ background: {SURFACE}; border: none; height: 10px; }}
QScrollBar::handle {{ background: {BUTTON_HOVER}; border-radius: 3px; min-height: 20px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QToolTip {{ background: {BUTTON}; color: {TEXT}; border: 1px solid {LINE}; }}
"""


def dark_palette() -> Any:
    from PySide6.QtGui import QColor, QPalette

    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: WINDOW,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: SURFACE,
        QPalette.ColorRole.AlternateBase: SURFACE_ALT,
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.Button: BUTTON,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: ACCENT,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: BUTTON,
        QPalette.ColorRole.ToolTipText: TEXT,
        QPalette.ColorRole.PlaceholderText: TEXT_MUTED,
        QPalette.ColorRole.Mid: LINE,
        QPalette.ColorRole.Midlight: BUTTON_HOVER,
        QPalette.ColorRole.Dark: "#000000",
        QPalette.ColorRole.Light: BUTTON_PRESSED,
        QPalette.ColorRole.Link: ACCENT,
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(TEXT_DISABLED))
    return p


_PLATFORM_FONT_CLASSES = (
    "QLabel",
    "QCheckBox",
    "QRadioButton",
    "QGroupBox",
    "QAbstractItemView",
    "QTableView",
    "QTableWidget",
    "QTreeView",
    "QTreeWidget",
    "QListView",
    "QHeaderView",
    "QMenu",
    "QMenuBar",
    "QMessageBox",
    "QToolTip",
    "QTipLabel",
)


def apply_theme(app: Any, extra_stylesheet: str = "") -> None:
    """Fusion + dark palette + flat stylesheet + pyqtgraph colours.

    Must run before any plot widget is created: pyqtgraph reads its
    background/foreground options at construction.
    """
    import pyqtgraph as pg

    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(extra_stylesheet + FLAT_STYLESHEET)
    pg.setConfigOptions(background=PLOT_BG, foreground=PLOT_TEXT)
    # macOS assigns its own fonts per widget class (labels/check boxes 13 pt,
    # item views 13 pt, headers 11 pt measured under Cocoa), overriding
    # QApplication.setFont(): labels and tables came out far smaller than the
    # 20 pt buttons and combos. Pin those classes to the application font.
    # Must come last: setStyle() and setStyleSheet() both reset the
    # per-class fonts to the platform's (verified under Cocoa).
    base = app.font()
    for cls in _PLATFORM_FONT_CLASSES:
        app.setFont(base, cls)


def style_plot(plot_item: Any) -> None:
    """Dim axis lines, readable tick labels, faint grid.

    pyqtgraph draws axis line and labels with one foreground colour; split
    them so the frame recedes and the numbers stay legible.
    """
    import pyqtgraph as pg

    for name in ("left", "bottom"):
        axis = plot_item.getAxis(name)
        axis.setPen(pg.mkPen(PLOT_AXIS))
        axis.setTextPen(pg.mkPen(PLOT_TEXT))
    plot_item.showGrid(x=True, y=True, alpha=GRID_ALPHA)


# --- action buttons ------------------------------------------------------
# Every action button in the app (main window, playback panel)
# comes from :func:`action_button`, so size and colour mean the same thing
# everywhere: green runs, purple analyses, teal saves, red stops/exits.
ACTION_ROLES = {
    "run": ("#2e7d4f", "#36915c"),
    "analyze": ("#5b4a9e", "#6a58b3"),
    "save": ("#1f7a8c", "#258da1"),
    "stop": ("#a8413b", "#bd4b44"),
}
_ACTION_DISABLED = ("#1e1e1e", "#555555")  # background, text


def action_metrics() -> tuple[int, int]:
    """(font pt, height px): compact on Windows so the UI fits 1080p."""
    import sys

    return (9, 35) if sys.platform == "win32" else (20, 50)


def action_button(label: str, tooltip: str = "", role: str = "run", parent: Any = None) -> Any:
    from PySide6.QtWidgets import QPushButton

    font_pt, height = action_metrics()
    background, hover = ACTION_ROLES[role]
    btn = QPushButton(label, parent)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setFixedHeight(height)
    btn.setStyleSheet(
        f"""
        QPushButton {{
            background-color: {background};
            color: white;
            font-weight: bold;
            font-size: {font_pt}pt;
            padding: 5px 15px;
            border: none;
            border-radius: 3px;
        }}
        QPushButton:hover {{ background-color: {hover}; }}
        QPushButton:disabled {{
            background-color: {_ACTION_DISABLED[0]};
            color: {_ACTION_DISABLED[1]};
        }}
        """
    )
    return btn

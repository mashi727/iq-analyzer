"""PySide6/PyQtGraph widgets used by the viewer."""

from __future__ import annotations

from iq_analyzer.widgets.adjustment_panel import AdjustmentPanel
from iq_analyzer.widgets.control_panel import ControlPanel
from iq_analyzer.widgets.file_browser import FileBrowserPanel
from iq_analyzer.widgets.spectrogram import (
    AVAILABLE_COLORMAPS,
    SpectrogramWidget,
    auto_color_levels,
    max_pool_2d,
)

__all__ = [
    "AVAILABLE_COLORMAPS",
    "AdjustmentPanel",
    "ControlPanel",
    "FileBrowserPanel",
    "SpectrogramWidget",
    "auto_color_levels",
    "max_pool_2d",
]

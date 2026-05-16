"""PySide6/PyQtGraph widgets used by the viewer."""

from __future__ import annotations

from iq_analyzer.widgets.spectrogram import (
    AVAILABLE_COLORMAPS,
    SpectrogramWidget,
    auto_color_levels,
    max_pool_2d,
)

__all__ = [
    "AVAILABLE_COLORMAPS",
    "SpectrogramWidget",
    "auto_color_levels",
    "max_pool_2d",
]

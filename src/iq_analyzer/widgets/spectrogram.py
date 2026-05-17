"""2D spectrogram widget with ROI support and adaptive color scaling."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QVBoxLayout, QWidget

logger = logging.getLogger(__name__)


# Five-stop colormaps stored as (position, RGBA) tuples. Pulled out of the
# class so they can be inspected/extended without instantiating Qt.
_COLORMAPS: dict[str, list[tuple[float, tuple[int, int, int, int]]]] = {
    "plasma": [
        (0.0, (12, 7, 134, 255)),
        (0.25, (126, 3, 167, 255)),
        (0.5, (203, 71, 119, 255)),
        (0.75, (248, 149, 64, 255)),
        (1.0, (239, 248, 33, 255)),
    ],
    "viridis": [
        (0.0, (68, 1, 84, 255)),
        (0.25, (59, 82, 139, 255)),
        (0.5, (33, 145, 140, 255)),
        (0.75, (94, 201, 98, 255)),
        (1.0, (253, 231, 37, 255)),
    ],
    "inferno": [
        (0.0, (0, 0, 4, 255)),
        (0.25, (87, 16, 110, 255)),
        (0.5, (188, 55, 84, 255)),
        (0.75, (249, 142, 9, 255)),
        (1.0, (252, 255, 164, 255)),
    ],
    "magma": [
        (0.0, (0, 0, 4, 255)),
        (0.25, (80, 18, 123, 255)),
        (0.5, (182, 54, 121, 255)),
        (0.75, (251, 136, 97, 255)),
        (1.0, (252, 253, 191, 255)),
    ],
}

AVAILABLE_COLORMAPS: tuple[str, ...] = tuple(_COLORMAPS)

# Color-scaling tuning constants.
_CUTOFF_DB_FROM_MAX = 30.0  # ~typical SNR for a strong signal vs noise floor
_MIN_DISPLAY_RANGE_DB = 10.0


def max_pool_2d(
    data: NDArray[Any], target_width: int, target_height: int
) -> NDArray[Any]:
    """2-D max-pool ``data`` down to roughly ``(target_height, target_width)``.

    Pooling preserves peaks (important for spectrograms — we never want to lose
    a narrow signal to averaging). The implementation is fully vectorised via
    ``reshape + max`` along the pooled axes, which is dramatically faster than
    nested Python loops for typical 4k+ point spectrograms.

    Any trailing rows/columns that don't fit evenly into a pooling window are
    discarded, matching the behaviour of the original loop-based implementation.
    """
    data_height, data_width = data.shape
    pool_height = max(1, data_height // target_height)
    pool_width = max(1, data_width // target_width)

    out_height = data_height // pool_height
    out_width = data_width // pool_width

    # Trim the trailing fractional window before reshaping.
    trimmed = data[: out_height * pool_height, : out_width * pool_width]
    return trimmed.reshape(out_height, pool_height, out_width, pool_width).max(axis=(1, 3))


def auto_color_levels(sxx_db: NDArray[np.floating]) -> tuple[float, float]:
    """Choose ``(min_level, max_level)`` for the histogram LUT.

    Strategy: set the upper bound at the 99th percentile (clips occasional
    outliers without losing real signal peaks), and the lower bound at
    ``max - 30 dB``. This keeps a generous dynamic range for the displayed
    image while suppressing the noise floor.

    A 10 dB minimum range is enforced so the colormap never collapses to a
    single shade on signals with very narrow distributions.
    """
    data_min = float(np.min(sxx_db))
    data_max = float(np.max(sxx_db))
    upper = float(np.percentile(sxx_db, 99))
    lower = max(data_max - _CUTOFF_DB_FROM_MAX, data_min)

    if upper - lower < _MIN_DISPLAY_RANGE_DB:
        center = (upper + lower) / 2
        lower = center - _MIN_DISPLAY_RANGE_DB / 2
        upper = center + _MIN_DISPLAY_RANGE_DB / 2

    logger.debug(
        "auto color levels: range=[%.1f, %.1f] dB (data=[%.1f, %.1f] dB)",
        lower,
        upper,
        data_min,
        data_max,
    )
    return lower, upper


class SpectrogramWidget(QWidget):
    """Spectrogram display with adaptive color scaling and downsampling.

    The widget owns a :class:`pyqtgraph.PlotItem` and a
    :class:`pyqtgraph.HistogramLUTItem`; callers feed it ``(frequencies, times,
    sxx_db)`` via :meth:`update_spectrogram` and the widget handles the rest
    (downsampling for the current viewport, color scaling, axis units).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frequencies: NDArray[np.floating] | None = None
        self.times: NDArray[np.floating] | None = None
        self.time_unit: str = "s"
        self.spectrogram_data: NDArray[np.floating] | None = None
        self.current_sxx_db: NDArray[np.floating] | None = None
        self.current_min_level: float | None = None
        self.current_max_level: float | None = None
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        self.plot_item = self.graphics_widget.addPlot()
        self.plot_item.setLabel("left", "周波数 (MHz)")
        self.plot_item.setLabel("bottom", "時間")
        self.plot_item.showGrid(x=True, y=True, alpha=0.3)

        self.img_item = pg.ImageItem()
        self.plot_item.addItem(self.img_item)

        self.hist = pg.HistogramLUTItem()
        self.hist.setImageItem(self.img_item)

        self.set_colormap("plasma")

        layout.addWidget(self.graphics_widget)
        self.setLayout(layout)

    def set_colormap(self, colormap_name: str) -> None:
        """Apply one of :data:`AVAILABLE_COLORMAPS`. Unknown names are ignored."""
        stops = _COLORMAPS.get(colormap_name)
        if stops is None:
            logger.warning("Unknown colormap %r; keeping current.", colormap_name)
            return
        self.hist.gradient.restoreState({"mode": "rgb", "ticks": stops})

    def update_spectrogram(
        self,
        frequencies: NDArray[np.floating],
        times: NDArray[np.floating],
        sxx_db: NDArray[np.floating],
        center_freq: float = 0.0,
    ) -> None:
        """Repaint the spectrogram with new data.

        ``frequencies`` is in Hz (baseband); ``center_freq`` is added before
        display so the y-axis shows absolute frequency in MHz. The widget picks
        a sensible time-axis unit (s/ms/μs/ns) based on the maximum value.
        """
        freq_mhz = (frequencies + center_freq) / 1e6

        if times[-1] < 1e-6:
            time_scale = times * 1e9
            time_unit = "ns"
        elif times[-1] < 1e-3:
            time_scale = times * 1e6
            time_unit = "μs"
        elif times[-1] < 1:
            time_scale = times * 1e3
            time_unit = "ms"
        else:
            time_scale = times
            time_unit = "s"

        self.frequencies = freq_mhz
        self.times = time_scale
        self.time_unit = time_unit
        self.spectrogram_data = sxx_db

        sxx_display = self._maybe_downsample(sxx_db)
        min_level, max_level = auto_color_levels(sxx_db)

        # Pass levels alongside the image data so the very first paint uses
        # the correct dynamic range. If we called setImage() and then setLevels()
        # separately, Qt could (and would) repaint once in between with the
        # stale levels from the previous capture, showing the spectrogram as
        # a blank black field for a single frame.
        # PyQtGraph expects (x, y); our data is (freq, time) so we transpose.
        self.img_item.setImage(
            sxx_display.T,
            autoLevels=False,
            levels=(min_level, max_level),
        )

        if len(time_scale) > 1 and len(freq_mhz) > 1:
            rect = QRectF(
                float(time_scale[0]),
                float(freq_mhz[0]),
                float(time_scale[-1] - time_scale[0]),
                float(freq_mhz[-1] - freq_mhz[0]),
            )
            self.img_item.setRect(rect)

        # Keep the histogram LUT in sync with the displayed levels.
        self.hist.setLevels(min_level, max_level)
        self.current_min_level = min_level
        self.current_max_level = max_level
        self.current_sxx_db = sxx_db

        self.plot_item.setLabel("bottom", "時間", units=time_unit)

        time_duration = float(time_scale[-1] - time_scale[0])
        freq_range = float(freq_mhz[-1] - freq_mhz[0])
        # Stretch the time axis for very short pulses so the spectrogram
        # doesn't collapse into a vertical sliver.
        min_time_width = freq_range * 0.1
        if time_duration < min_time_width:
            time_center = (float(time_scale[0]) + float(time_scale[-1])) / 2
            self.plot_item.setRange(
                xRange=(time_center - min_time_width / 2, time_center + min_time_width / 2),
                yRange=(float(freq_mhz[0]), float(freq_mhz[-1])),
                padding=0,
            )
            logger.debug(
                "short pulse: time width %.3f %s extended to %.3f %s",
                time_duration,
                time_unit,
                min_time_width,
                time_unit,
            )
        else:
            self.plot_item.autoRange()

    def _maybe_downsample(self, sxx_db: NDArray[np.floating]) -> NDArray[np.floating]:
        """Apply :func:`max_pool_2d` if the data is much bigger than the viewport."""
        viewbox_rect = self.plot_item.getViewBox().viewRect()
        widget_width = viewbox_rect.width()
        widget_height = viewbox_rect.height()

        # Reject implausible viewport sizes (e.g. widget not yet realised).
        valid_viewport = (
            widget_width
            and widget_height
            and 10 < widget_width < 10000
            and 10 < widget_height < 10000
        )
        if not valid_viewport:
            return sxx_db

        target_width = int(widget_width)
        target_height = int(widget_height)
        data_height, data_width = sxx_db.shape

        need_w = data_width > target_width * 2
        need_h = data_height > target_height * 2
        if not (need_w or need_h):
            return sxx_db

        logger.debug(
            "max-pool downsampling: data=%dx%d -> display=%dx%d",
            data_width,
            data_height,
            target_width,
            target_height,
        )
        return max_pool_2d(
            sxx_db,
            target_width if need_w else data_width,
            target_height if need_h else data_height,
        )

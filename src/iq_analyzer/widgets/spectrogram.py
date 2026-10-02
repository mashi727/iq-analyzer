"""2D spectrogram widget with ROI support and adaptive color scaling."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pyqtgraph as pg
from numpy.typing import NDArray
from PySide6.QtCore import QRectF, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget

from iq_analyzer.ui.style import style_plot

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
    # ``max - 30 dB`` sits near the floor only when the peaks are isolated.
    # With recurring strong bursts (e.g. RFI in a sky recording) it climbs above
    # the 99th percentile and the whole floor falls below the colormap, so the
    # image renders as a single flat shade. The median is a robust noise-floor
    # estimate; never let the lower bound rise above it.
    floor = float(np.median(sxx_db))
    lower = max(min(data_max - _CUTOFF_DB_FROM_MAX, floor), data_min)

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


_TIME_FACTORS = {"s": 1.0, "ms": 1e3, "μs": 1e6, "ns": 1e9}


class SpectrogramWidget(QWidget):
    """Spectrogram display with adaptive color scaling and downsampling.

    The widget owns a :class:`pyqtgraph.PlotItem` and a
    :class:`pyqtgraph.HistogramLUTItem`; callers feed it ``(frequencies, times,
    sxx_db)`` via :meth:`update_spectrogram` and the widget handles the rest
    (downsampling for the current viewport, color scaling, axis units).

    A rectangular selection (``selection_roi``) marks a time × frequency
    range, e.g. for envelope playback; :meth:`selection` returns it in
    absolute seconds and Hz.
    """

    selection_changed = Signal()

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

    # ------------------------------------------------------------ selection

    def _init_selection(self) -> None:
        # Plain ROI rather than RectROI: RectROI always puts its corner
        # handle at the top-right. The corner handle sits at the bottom-right
        # (ROI-relative (1, 0): the frequency axis points up) and scales about
        # the top-left corner.
        self.selection_roi = pg.ROI(
            [0, 0], [1, 1], pen=pg.mkPen((0, 255, 255), width=2), hoverPen=pg.mkPen((255, 255, 255), width=2)
        )
        self.selection_roi.addScaleHandle([1, 0], [0, 1])
        # Edge handles so time and band can be adjusted independently.
        for pos, center in (([0, 0.5], [1, 0.5]), ([1, 0.5], [0, 0.5]), ([0.5, 0], [0.5, 1]), ([0.5, 1], [0.5, 0])):
            self.selection_roi.addScaleHandle(pos, center)
        self.selection_roi.setZValue(20)
        self.selection_roi.hide()
        self.selection_roi.sigRegionChangeFinished.connect(lambda *_: self.selection_changed.emit())
        self.plot_item.addItem(self.selection_roi)

        self.playhead = pg.InfiniteLine(angle=90, pen=pg.mkPen((255, 255, 255), width=2))
        self.playhead.setZValue(30)
        self.playhead.hide()
        self.plot_item.addItem(self.playhead)

    def _image_extent(self) -> tuple[float, float, float, float] | None:
        """(x0, x1, y0, y1) of the current image in display units."""
        if self.times is None or self.frequencies is None or len(self.times) < 2:
            return None
        return (float(self.times[0]), float(self.times[-1]), float(self.frequencies[0]), float(self.frequencies[-1]))

    def _place_selection(self, prev: tuple[float, float, float, float] | None) -> None:
        """Keep the user's rectangle if it still lies on the new image, else
        reset it to the whole image (after a new Region, the old one is stale)."""
        ext = self._image_extent()
        if ext is None:
            self.selection_roi.hide()
            return
        x0, x1, y0, y1 = ext
        k = _TIME_FACTORS.get(self.time_unit, 1.0)
        if prev is not None and x0 <= prev[0] * k and prev[1] * k <= x1 and y0 <= prev[2] and prev[3] <= y1:
            # Re-express in the (possibly changed) time unit.
            t0, t1, f0, f1 = prev
            self.selection_roi.setPos([t0 * k, f0], finish=False)
            self.selection_roi.setSize([(t1 - t0) * k, f1 - f0], finish=False)
        else:
            self.selection_roi.setPos([x0, y0], finish=False)
            self.selection_roi.setSize([x1 - x0, y1 - y0], finish=False)
        self.selection_roi.show()
        self.selection_changed.emit()

    def selection(self) -> tuple[float, float, float, float] | None:
        """(t0 s, t1 s, f0 Hz, f1 Hz), absolute and clipped to the image; None if empty."""
        ext = self._image_extent()
        if ext is None:
            return None
        x0, x1, y0, y1 = ext
        pos = self.selection_roi.pos()
        size = self.selection_roi.size()
        ax0, ax1 = sorted((pos.x(), pos.x() + size.x()))
        ay0, ay1 = sorted((pos.y(), pos.y() + size.y()))
        ax0, ax1 = max(ax0, x0), min(ax1, x1)
        ay0, ay1 = max(ay0, y0), min(ay1, y1)
        if ax1 <= ax0 or ay1 <= ay0:
            return None
        k = _TIME_FACTORS.get(self.time_unit, 1.0)
        return ax0 / k, ax1 / k, ay0, ay1

    def set_playhead(self, t_s: float | None) -> None:
        """Show a vertical line at absolute time ``t_s`` (None hides it)."""
        if t_s is None or self.times is None:
            self.playhead.hide()
            return
        self.playhead.setPos(t_s * _TIME_FACTORS.get(self.time_unit, 1.0))
        self.playhead.show()

    def select_all(self) -> None:
        """Stretch the selection over the whole spectrogram image."""
        ext = self._image_extent()
        if ext is None:
            return
        x0, x1, y0, y1 = ext
        self._set_selection_rect(x0, x1, y0, y1)

    def select_view(self) -> None:
        """Fit the selection to the visible (zoomed) part of the image."""
        ext = self._image_extent()
        if ext is None:
            return
        (vx0, vx1), (vy0, vy1) = self.plot_item.getViewBox().viewRange()
        x0, x1, y0, y1 = ext
        nx0, nx1 = max(x0, vx0), min(x1, vx1)
        ny0, ny1 = max(y0, vy0), min(y1, vy1)
        if nx1 > nx0 and ny1 > ny0:
            self._set_selection_rect(nx0, nx1, ny0, ny1)

    def _set_selection_rect(self, x0: float, x1: float, y0: float, y1: float) -> None:
        self.selection_roi.setPos([x0, y0], finish=False)
        self.selection_roi.setSize([x1 - x0, y1 - y0], finish=False)
        self.selection_roi.show()
        self.selection_changed.emit()

    def _init_selection_menu(self) -> None:
        """Two entries in the plot's right-click menu (the ViewBox menu also
        opens over the ROI, which does not take right clicks)."""
        menu = self.plot_item.getViewBox().menu
        menu.addSeparator()
        self.select_all_action = menu.addAction("選択枠を全体に", self.select_all)
        self.select_view_action = menu.addAction("選択枠を表示範囲に", self.select_view)

        def refresh() -> None:
            has_image = self._image_extent() is not None
            self.select_all_action.setEnabled(has_image)
            self.select_view_action.setEnabled(has_image)

        menu.aboutToShow.connect(refresh)

    def _init_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        self.graphics_widget = pg.GraphicsLayoutWidget()
        # No outer margin, so the plot's view box can line up with the
        # waveform plots below it (see ui.style.time_axis_left_width).
        self.graphics_widget.ci.setContentsMargins(0, 0, 0, 0)
        self.plot_item = self.graphics_widget.addPlot()
        # Time values are already scaled to s/ms/μs/ns by update_spectrogram, so
        # pyqtgraph must not stack its own SI prefix on top ("kms", "(x0.001)").
        self.plot_item.getAxis("bottom").enableAutoSIPrefix(False)
        self.clear_axes()
        style_plot(self.plot_item)

        self.img_item = pg.ImageItem()
        self.plot_item.addItem(self.img_item)

        self.hist = pg.HistogramLUTItem()
        self.hist.setImageItem(self.img_item)

        self.set_colormap("plasma")

        self._init_selection()
        self._init_selection_menu()

        layout.addWidget(self.graphics_widget)
        self.setLayout(layout)

    def clear_axes(self) -> None:
        """Axis labels for the empty state: no units, no SI prefix.

        With units set, the default ±0.5 view of an empty plot is rendered as
        "周波数 (mHz)" / "時間 (x0.001)", which reads as a real (and absurd) scale.
        """
        if hasattr(self, "selection_roi"):
            self.selection_roi.hide()
            self.playhead.hide()
        left = self.plot_item.getAxis("left")
        left.enableAutoSIPrefix(False)
        self.plot_item.setLabel("left", "周波数")
        self.plot_item.setLabel("bottom", "時間")

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
        display so the y-axis shows absolute frequency (in Hz, SI-prefixed). The widget picks
        a sensible time-axis unit (s/ms/μs/ns) based on the maximum value.
        """
        # Selection in absolute units, read before the axes change.
        prev = self.selection() if self.selection_roi.isVisible() else None
        freq_hz = frequencies + center_freq
        # Plot in Hz and let pyqtgraph pick the SI prefix: GHz for an RF centre
        # frequency, MHz for baseband. (Fixing units="MHz" made the auto prefix
        # render "kMHz" for 3.1 GHz recordings.)
        self.plot_item.getAxis("left").enableAutoSIPrefix(True)
        self.plot_item.setLabel("left", "周波数", units="Hz")

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

        self.frequencies = freq_hz
        self.times = time_scale
        self.time_unit = time_unit
        self.spectrogram_data = sxx_db

        sxx_display = self._maybe_downsample(sxx_db)
        # Levels must come from what is actually shown: max-pooling shifts the
        # noise-floor distribution up by several dB (the max of N noise cells),
        # so levels taken from the raw STFT would saturate the whole image.
        min_level, max_level = auto_color_levels(sxx_display)

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

        if len(time_scale) > 1 and len(freq_hz) > 1:
            rect = QRectF(
                float(time_scale[0]),
                float(freq_hz[0]),
                float(time_scale[-1] - time_scale[0]),
                float(freq_hz[-1] - freq_hz[0]),
            )
            self.img_item.setRect(rect)

        # Keep the histogram LUT in sync with the displayed levels.
        self.hist.setLevels(min_level, max_level)
        self.current_min_level = min_level
        self.current_max_level = max_level
        self.current_sxx_db = sxx_db
        self.current_display_db = sxx_display

        self.plot_item.setLabel("bottom", "時間", units=time_unit)
        self._place_selection(prev)

        time_duration = float(time_scale[-1] - time_scale[0])
        # The stretch heuristic below was tuned with the frequency span in MHz
        # against the time span in its display unit; keep it in MHz.
        freq_range = float(freq_hz[-1] - freq_hz[0]) / 1e6
        # Stretch the time axis for very short pulses so the spectrogram
        # doesn't collapse into a vertical sliver.
        min_time_width = freq_range * 0.1
        if time_duration < min_time_width:
            time_center = (float(time_scale[0]) + float(time_scale[-1])) / 2
            self.plot_item.setRange(
                xRange=(time_center - min_time_width / 2, time_center + min_time_width / 2),
                yRange=(float(freq_hz[0]), float(freq_hz[-1])),
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
        # Size on screen in pixels. (viewRect() would be in data units, i.e.
        # seconds × MHz, which made pooling silently never trigger and let
        # short bursts get dropped by the painter's nearest-neighbour scaling.)
        viewbox = self.plot_item.getViewBox()
        widget_width = viewbox.width()
        widget_height = viewbox.height()

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

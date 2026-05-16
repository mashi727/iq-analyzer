"""Tests for the pure-Python helpers in ``iq_analyzer.widgets.spectrogram``.

The :class:`SpectrogramWidget` itself isn't covered here because instantiating
it requires a live ``QApplication``. The helpers below are extracted so the
heavy lifting can be exercised without Qt.
"""

from __future__ import annotations

import numpy as np

from iq_analyzer.widgets.spectrogram import (
    AVAILABLE_COLORMAPS,
    auto_color_levels,
    max_pool_2d,
)


def _reference_max_pool_2d(
    data: np.ndarray, target_width: int, target_height: int
) -> np.ndarray:
    """Naive loop-based pooling kept around as the spec for the fast version."""
    data_height, data_width = data.shape
    pool_height = max(1, data_height // target_height)
    pool_width = max(1, data_width // target_width)
    out_height = data_height // pool_height
    out_width = data_width // pool_width

    pooled = np.zeros((out_height, out_width), dtype=data.dtype)
    for i in range(out_height):
        for j in range(out_width):
            h_start = i * pool_height
            h_end = min((i + 1) * pool_height, data_height)
            w_start = j * pool_width
            w_end = min((j + 1) * pool_width, data_width)
            pooled[i, j] = np.max(data[h_start:h_end, w_start:w_end])
    return pooled


def test_max_pool_matches_reference_implementation() -> None:
    rng = np.random.default_rng(0)
    data = rng.standard_normal((123, 217)).astype(np.float32)

    for target_w, target_h in [(10, 10), (50, 30), (217, 123), (1, 1)]:
        expected = _reference_max_pool_2d(data, target_w, target_h)
        actual = max_pool_2d(data, target_w, target_h)
        np.testing.assert_array_equal(actual, expected)


def test_max_pool_preserves_peaks() -> None:
    """Pooling must preserve the maximum, not average it away."""
    data = np.zeros((100, 100), dtype=np.float32)
    data[50, 50] = 1000.0
    pooled = max_pool_2d(data, 10, 10)
    assert pooled.max() == 1000.0


def test_auto_color_levels_typical_signal() -> None:
    """Strong outliers must saturate: the upper bound stays near the floor.

    The widget intentionally focuses contrast on the noise/signal floor and
    lets isolated peaks clip — this matches the convention of conventional
    spectrum-analyzer displays.
    """
    rng = np.random.default_rng(1)
    data = rng.normal(loc=-80.0, scale=2.0, size=(64, 64)).astype(np.float32)
    data[10, 10] = -10.0  # lone strong peak

    lower, upper = auto_color_levels(data)
    assert lower < upper
    # The lone peak at -10 dB should saturate (i.e. sit above the upper bound).
    assert upper < -10.0
    # Both bounds should hover around the noise floor.
    assert -90.0 < lower < -50.0
    assert -90.0 < upper < -50.0


def test_auto_color_levels_uniform_signal_uses_cutoff() -> None:
    """Without an outlier, lower must sit at ``max - 30 dB``."""
    rng = np.random.default_rng(2)
    data = rng.normal(loc=-40.0, scale=5.0, size=(64, 64)).astype(np.float32)
    lower, upper = auto_color_levels(data)
    assert lower == float(data.max()) - 30.0
    assert upper > lower


def test_auto_color_levels_enforces_minimum_range() -> None:
    """When the data is nearly flat the helper widens to at least 10 dB."""
    flat = np.full((32, 32), -50.0, dtype=np.float32)
    lower, upper = auto_color_levels(flat)
    assert upper - lower >= 10.0 - 1e-6


def test_available_colormaps_constant() -> None:
    assert "plasma" in AVAILABLE_COLORMAPS
    assert set(AVAILABLE_COLORMAPS) == {"plasma", "viridis", "inferno", "magma"}

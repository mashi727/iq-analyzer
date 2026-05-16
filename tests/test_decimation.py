"""Tests for ``iq_analyzer.core.decimation.min_max_downsample``."""

from __future__ import annotations

import numpy as np
import pytest

from iq_analyzer.core.decimation import RAW_WAVEFORM_THRESHOLD, min_max_downsample


def _amp_getter(amplitudes: np.ndarray):
    """Build a ``get_iq_data`` callback that returns ``amplitudes[s:e] + 0j``."""

    def get(start: int, end: int) -> np.ndarray:
        return (amplitudes[start:end].astype(np.float32) + 0j).astype(np.complex64)

    return get


def test_raw_passthrough_under_threshold() -> None:
    """Below the raw threshold we get one sample per input sample, in seconds."""
    amplitudes = np.linspace(0.0, 1.0, 1000, dtype=np.float32)
    sample_rate = 1000.0

    x, y = min_max_downsample(_amp_getter(amplitudes), 0, 1000, target_pixels=100, sample_rate=sample_rate)

    assert x.shape == (1000,)
    assert y.shape == (1000,)
    np.testing.assert_array_equal(y, amplitudes)
    # X axis is samples / sample_rate
    np.testing.assert_allclose(x, np.arange(1000) / sample_rate)


def test_minmax_bins_when_above_threshold() -> None:
    """Over the threshold we produce 2 * target_pixels alternating (min, max) pairs."""
    n = RAW_WAVEFORM_THRESHOLD + 1000
    amplitudes = np.arange(n, dtype=np.float32)  # monotonic — min and max per bin trivially known
    target_pixels = 200

    x, y = min_max_downsample(_amp_getter(amplitudes), 0, n, target_pixels=target_pixels, sample_rate=1.0)

    assert x.shape == y.shape
    assert len(x) == 2 * target_pixels
    # Min and max in a bin sit at the same x (bin center) by design.
    np.testing.assert_array_equal(x[0::2], x[1::2])
    # Mins must be <= maxes in every bin.
    assert np.all(y[0::2] <= y[1::2])


def test_minmax_preserves_envelope() -> None:
    """A solitary spike in an otherwise quiet region must survive decimation."""
    n = RAW_WAVEFORM_THRESHOLD + 10_000
    amplitudes = np.zeros(n, dtype=np.float32)
    amplitudes[n // 2] = 5000.0  # lone peak

    _, y = min_max_downsample(_amp_getter(amplitudes), 0, n, target_pixels=500, sample_rate=1.0)
    assert y.max() == pytest.approx(5000.0)


def test_x_axis_uses_sample_rate() -> None:
    """X output must be in seconds (i.e. divided by sample_rate)."""
    n = RAW_WAVEFORM_THRESHOLD + 4_000
    sample_rate = 2_000.0
    amplitudes = np.ones(n, dtype=np.float32)

    x, _ = min_max_downsample(_amp_getter(amplitudes), 0, n, target_pixels=100, sample_rate=sample_rate)
    assert x[0] == pytest.approx((n / 100) / 2 / sample_rate, rel=0.05)
    assert x[-1] < n / sample_rate

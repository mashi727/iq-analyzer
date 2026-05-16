"""Tests for ``iq_analyzer.core.spectrogram``."""

from __future__ import annotations

import numpy as np
import pytest

from iq_analyzer.core.spectrogram import (
    SpectrogramParams,
    auto_optimize_params,
    compute_spectrogram,
)


@pytest.mark.parametrize(
    ("duration_s", "expected_nfft", "expected_window"),
    [
        (0.5e-6, 64, "blackmanharris"),  # extreme short pulse
        (5e-6, 128, "blackmanharris"),
        (50e-6, 256, "blackmanharris"),
        (0.5e-3, 512, "hann"),
        (5e-3, 1024, "hann"),
        (50e-3, 2048, "hann"),
        (500e-3, 4096, "hann"),  # long signal
    ],
)
def test_auto_optimize_picks_expected_profile(
    duration_s: float, expected_nfft: int, expected_window: str
) -> None:
    sample_rate = 32e6
    params = auto_optimize_params(int(duration_s * sample_rate), sample_rate)
    assert isinstance(params, SpectrogramParams)
    assert params.nfft == expected_nfft
    assert params.window == expected_window


def test_auto_optimize_respects_memory_budget() -> None:
    """Huge regions must trigger NFFT growth so the estimate stays bounded."""
    sample_rate = 1e9
    # 1 second @ 1 GHz = 1e9 samples → tens of GB of spectrogram if untouched.
    params = auto_optimize_params(int(1 * sample_rate), sample_rate)
    assert params.nfft >= 4096
    assert "メモリ制約により調整" in params.message


def test_compute_spectrogram_shape_and_freq_axis() -> None:
    sample_rate = 32e6
    nfft = 256
    overlap_percent = 50.0
    iq = np.random.default_rng(0).standard_normal(2048).astype(np.complex64)

    freqs, times, sxx_db = compute_spectrogram(iq, nfft, overlap_percent, "hann", sample_rate)

    assert sxx_db.shape[0] == nfft
    assert sxx_db.shape[1] == len(times)
    # Frequencies are fftshifted: span -fs/2 .. +fs/2
    assert freqs[0] == pytest.approx(-sample_rate / 2, rel=0.01)
    assert freqs[-1] == pytest.approx(sample_rate / 2 - sample_rate / nfft, rel=0.01)
    assert sxx_db.dtype == np.float32


def test_compute_spectrogram_recovers_tone_frequency() -> None:
    """A pure sinusoid should produce a peak at the expected frequency bin."""
    sample_rate = 1_000_000.0
    nfft = 1024
    tone_hz = 100_000.0
    n_samples = 8192
    t = np.arange(n_samples) / sample_rate
    iq = np.exp(1j * 2 * np.pi * tone_hz * t).astype(np.complex64)

    freqs, _, sxx_db = compute_spectrogram(iq, nfft, 50.0, "hann", sample_rate)

    peak_freq_per_frame = freqs[np.argmax(sxx_db, axis=0)]
    # All time frames should peak near +100 kHz (within one frequency bin).
    bin_hz = sample_rate / nfft
    assert np.all(np.abs(peak_freq_per_frame - tone_hz) <= bin_hz)


def test_compute_spectrogram_progress_callback() -> None:
    calls: list[tuple[int, int]] = []
    iq = np.zeros(4096, dtype=np.complex64)
    compute_spectrogram(
        iq, 256, 50.0, "hann", 1.0, progress=lambda done, total: calls.append((done, total))
    )
    assert calls, "progress callback should fire at least once"
    done, total = calls[-1]
    assert done == total  # final call reports completion


def test_compute_spectrogram_rejects_excessive_overlap() -> None:
    iq = np.zeros(512, dtype=np.complex64)
    with pytest.raises(ValueError):
        compute_spectrogram(iq, 256, 100.0, "hann", 1.0)

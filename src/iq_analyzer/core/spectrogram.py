"""Spectrogram parameter selection and STFT computation.

These helpers contain the actual signal-processing maths; the GUI side only
needs to hand over the IQ slice and display the result. Keeping the FFT
work out of the main window also makes regression testing tractable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


# Memory budget for spectrogram + IQ buffers on the target 8 GB machine.
_MAX_MEMORY_MB = 4_000


class SpectrogramParams(NamedTuple):
    """Result of :func:`auto_optimize_params`."""

    nfft: int
    overlap_percent: float
    window: str
    message: str


@dataclass(frozen=True)
class _ProfileEntry:
    """A single row in the duration → STFT-config lookup table."""

    duration_threshold_s: float
    nfft: int
    overlap_percent: float
    window: str
    label: str


# Higher time resolution for short pulses, higher frequency resolution for
# long captures. The values were tuned against typical radar / wideband
# recordings and preserved verbatim from the original viewer.
_PROFILES: tuple[_ProfileEntry, ...] = (
    _ProfileEntry(1e-6, 64, 90.0, "blackmanharris", "極短パルス検出: 最高時間分解能モード"),
    _ProfileEntry(10e-6, 128, 87.5, "blackmanharris", "短パルス検出: 高時間分解能モード"),
    _ProfileEntry(100e-6, 256, 85.0, "blackmanharris", "中短パルス検出: 高時間分解能モード"),
    _ProfileEntry(1e-3, 512, 80.0, "hann", "中間長信号: バランスモード"),
    _ProfileEntry(10e-3, 1024, 75.0, "hann", "中長信号: 標準モード"),
    _ProfileEntry(100e-3, 2048, 70.0, "hann", "長信号: 高周波数分解能モード"),
)
_LONG_SIGNAL = _ProfileEntry(
    float("inf"), 4096, 65.0, "hann", "超長信号: 最高周波数分解能モード"
)


def _estimate_memory_mb(region_samples: int, nfft: int, overlap_percent: float) -> float:
    """Rough peak memory: complex64 IQ buffer + float32 spectrogram."""
    iq_data_mb = (region_samples * 8) / 1024 / 1024
    hop = nfft * (1 - overlap_percent / 100)
    time_frames = max(1, int(region_samples / hop))
    spectrogram_mb = (nfft * time_frames * 4) / 1024 / 1024
    return iq_data_mb + spectrogram_mb


def auto_optimize_params(region_samples: int, sample_rate: float) -> SpectrogramParams:
    """Choose ``(NFFT, overlap, window)`` based on the region's time length.

    The default profile is picked from a duration → settings lookup table; if
    the resulting buffer exceeds :data:`_MAX_MEMORY_MB`, ``NFFT`` is doubled
    (and the overlap shrunk by 5%) iteratively until the estimate fits.
    """
    duration_sec = region_samples / sample_rate

    profile = next(
        (p for p in _PROFILES if duration_sec < p.duration_threshold_s),
        _LONG_SIGNAL,
    )

    nfft = profile.nfft
    overlap = profile.overlap_percent
    message = profile.label

    total_mb = _estimate_memory_mb(region_samples, nfft, overlap)
    if total_mb > _MAX_MEMORY_MB:
        logger.info(
            "memory optimisation: estimated %.0f MB > %d MB budget", total_mb, _MAX_MEMORY_MB
        )
        while total_mb > _MAX_MEMORY_MB and nfft < 16_384:
            nfft *= 2
            overlap = max(50.0, overlap - 5)
            total_mb = _estimate_memory_mb(region_samples, nfft, overlap)
        message += " (メモリ制約により調整)"
        logger.info("memory optimisation done: NFFT=%d, est=%.0f MB", nfft, total_mb)

    return SpectrogramParams(
        nfft=nfft, overlap_percent=overlap, window=profile.window, message=message
    )


def _build_window(name: str, nfft: int) -> NDArray[np.floating]:
    """Return the window array used by the original viewer.

    Note: the legacy code mapped ``'blackmanharris'`` to :func:`numpy.blackman`,
    which is actually a *Blackman* window rather than the (different)
    Blackman-Harris one. We preserve that behaviour for bit-for-bit output
    compatibility; switching to scipy's true Blackman-Harris is left for a
    follow-up so it can be reviewed deliberately.
    """
    if name == "blackmanharris":
        return np.blackman(nfft)
    # Fall through covers 'hann' and any unknown name.
    return np.hanning(nfft)


def compute_spectrogram(
    iq_data: NDArray[np.complexfloating],
    nfft: int,
    overlap_percent: float,
    window: str,
    sample_rate: float,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[NDArray[np.floating], NDArray[np.floating], NDArray[np.float32]]:
    """Compute a baseband-centred dB spectrogram.

    Returns ``(frequencies_hz, times_seconds, sxx_db)``. ``frequencies`` is
    centred on 0 Hz via ``fftshift``; the caller is expected to add the IQ
    center frequency before display.

    ``progress(i, n)`` is invoked once per ~100 frames so the UI can update a
    busy indicator without polluting this module with Qt knowledge.
    """
    nfft = int(nfft)
    noverlap = int(nfft * overlap_percent / 100)
    hop_length = nfft - noverlap
    if hop_length <= 0:
        raise ValueError("overlap too large: hop_length must be positive")

    num_frames = 1 + max(0, (len(iq_data) - nfft)) // hop_length

    frequencies = np.fft.fftshift(np.fft.fftfreq(nfft, d=1 / sample_rate))
    times = np.arange(num_frames) * hop_length / sample_rate

    win = _build_window(window, nfft)
    sxx = np.zeros((nfft, num_frames), dtype=np.float32)

    # The frame-by-frame loop is kept because the spectrograms involved can be
    # multi-GB and a single vectorised matrix would blow the memory budget.
    for i in range(num_frames):
        start_idx = i * hop_length
        end_idx = start_idx + nfft
        frame = iq_data[start_idx:end_idx]
        windowed = frame * win
        fft_result = np.fft.fft(windowed)
        sxx[:, i] = np.fft.fftshift(np.abs(fft_result) ** 2)

        if progress is not None and ((i + 1) % 100 == 0 or i == num_frames - 1):
            progress(i + 1, num_frames)

    sxx_db = (10 * np.log10(sxx + 1e-12)).astype(np.float32)
    return frequencies, times, sxx_db

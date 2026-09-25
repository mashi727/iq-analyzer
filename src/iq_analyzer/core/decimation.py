"""Waveform decimation helpers for the overview plot.

For 20 GB IQ recordings we cannot render every sample, so the overview plot
shows a *Min-Max* envelope: each pixel column is fed two points — the minimum
and maximum amplitude in the corresponding sample window — which preserves
narrow pulses that a simple stride decimation would miss.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# Below this sample count we just return the raw amplitudes — the cost of
# computing min/max bins outweighs the saving and the user prefers seeing the
# true waveform when possible.
RAW_WAVEFORM_THRESHOLD = 3_200_000

# When a single Min-Max bin would span more than this many samples, reading
# it whole is too slow (a 100 GB overview would read the entire file). Such
# requests become a *preview*: at most ``PREVIEW_MAX_READS`` bins, each fed by
# one contiguous ``PREVIEW_BLOCK`` read at the bin centre. The cap keeps the
# seek count tolerable on external HDDs (~512 × 10 ms). A preview can miss
# short pulses; the precomputed envelope (core.envelope) replaces it.
_CHUNK_SIZE = 1_000_000
PREVIEW_MAX_READS = 512
PREVIEW_BLOCK = 65_536


IQGetter = Callable[[int, int], NDArray[np.complexfloating]]


def min_max_downsample(
    get_iq_data: IQGetter,
    start_sample: int,
    end_sample: int,
    target_pixels: int,
    sample_rate: float,
) -> tuple[NDArray[np.float64], NDArray[np.float32]]:
    """Return ``(x_seconds, amplitude)`` arrays sized for ``target_pixels``.

    Parameters
    ----------
    get_iq_data:
        Callable identical to ``loader.get_iq_data(start, end)`` — returns the
        complex IQ slice for a half-open sample range. Passed as a callback so
        this function stays independent of any particular loader class.
    start_sample, end_sample:
        Half-open sample range to render.
    target_pixels:
        Approximate number of horizontal pixels available; each pixel becomes
        two output points (min and max).
    sample_rate:
        Sampling clock in Hz. Used to convert sample indices to seconds.

    Returns
    -------
    x_seconds, amplitude:
        Two arrays of length ``2 * target_pixels`` (downsampled case) or
        ``end_sample - start_sample`` (raw case).
    """
    total_samples = end_sample - start_sample

    if total_samples <= RAW_WAVEFORM_THRESHOLD:
        logger.debug(
            "raw waveform: %d samples <= %d threshold", total_samples, RAW_WAVEFORM_THRESHOLD
        )
        iq_data = get_iq_data(start_sample, end_sample)
        x_data = np.arange(start_sample, end_sample, dtype=np.float64) / sample_rate
        y_data = np.abs(iq_data).astype(np.float32)
        return x_data, y_data

    logger.debug(
        "min-max decimation: %d samples -> %d target pixels", total_samples, target_pixels
    )

    if total_samples // max(1, target_pixels) > _CHUNK_SIZE:
        return _preview_downsample(get_iq_data, start_sample, end_sample, target_pixels, sample_rate)

    bin_size = max(1, total_samples // target_pixels)
    x_mins: list[float] = []
    x_maxs: list[float] = []
    y_mins: list[float] = []
    y_maxs: list[float] = []

    for bin_idx in range(target_pixels):
        bin_start = start_sample + bin_idx * bin_size
        bin_end = min(start_sample + (bin_idx + 1) * bin_size, end_sample)

        if bin_end <= bin_start:
            break

        iq_bin_data = get_iq_data(bin_start, bin_end)

        amplitudes = np.abs(iq_bin_data)
        bin_center = (bin_start + bin_end) / 2 / sample_rate

        x_mins.append(bin_center)
        x_maxs.append(bin_center)
        y_mins.append(float(np.min(amplitudes)))
        y_maxs.append(float(np.max(amplitudes)))

    # Interleave (min, max) so the line renderer draws a vertical bar per bin —
    # this is what gives the overview its characteristic envelope look.
    x_data = np.empty(len(x_mins) * 2, dtype=np.float64)
    y_data = np.empty(len(y_mins) * 2, dtype=np.float32)
    x_data[0::2] = x_mins
    x_data[1::2] = x_maxs
    y_data[0::2] = y_mins
    y_data[1::2] = y_maxs
    return x_data, y_data


def _preview_downsample(
    get_iq_data: IQGetter,
    start_sample: int,
    end_sample: int,
    target_pixels: int,
    sample_rate: float,
) -> tuple[NDArray[np.float64], NDArray[np.float32]]:
    """Sparse min/max preview: one short contiguous read per output bin."""
    total_samples = end_sample - start_sample
    n_bins = max(1, min(target_pixels, PREVIEW_MAX_READS))
    edges = start_sample + (np.arange(n_bins + 1, dtype=np.int64) * total_samples) // n_bins
    block = min(PREVIEW_BLOCK, total_samples // n_bins)

    x_data = np.empty(2 * n_bins, dtype=np.float64)
    y_data = np.empty(2 * n_bins, dtype=np.float32)
    for i in range(n_bins):
        center = (int(edges[i]) + int(edges[i + 1])) // 2
        s = max(start_sample, center - block // 2)
        amplitudes = np.abs(get_iq_data(s, min(end_sample, s + block)))
        x_data[2 * i : 2 * i + 2] = center / sample_rate
        y_data[2 * i] = amplitudes.min()
        y_data[2 * i + 1] = amplitudes.max()
    return x_data, y_data

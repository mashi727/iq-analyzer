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

# When a single Min-Max bin would span more than this many samples, fall back
# to ``linspace`` sub-sampling to keep memory bounded.
_CHUNK_SIZE = 1_000_000


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

        if bin_end - bin_start > _CHUNK_SIZE:
            sample_indices = np.linspace(
                bin_start,
                bin_end - 1,
                min(_CHUNK_SIZE, bin_end - bin_start),
                dtype=np.int64,
            )
            chunk_start = int(sample_indices[0])
            chunk_end = int(sample_indices[-1]) + 1
            chunk_data = get_iq_data(chunk_start, chunk_end)
            iq_bin_data = chunk_data[sample_indices - chunk_start]
        else:
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

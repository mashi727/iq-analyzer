"""Core signal-processing and memory utilities (UI-independent)."""

from __future__ import annotations

from iq_analyzer.core.decimation import RAW_WAVEFORM_THRESHOLD, min_max_downsample
from iq_analyzer.core.memory import (
    COLOR_CRITICAL,
    COLOR_NORMAL,
    COLOR_WARNING,
    MemoryStatus,
    color_for_percent,
    memory_status,
)
from iq_analyzer.core.spectrogram import (
    SpectrogramParams,
    auto_optimize_params,
    compute_spectrogram,
)
from iq_analyzer.core.stdout_redirector import StdoutRedirector

__all__ = [
    "COLOR_CRITICAL",
    "COLOR_NORMAL",
    "COLOR_WARNING",
    "RAW_WAVEFORM_THRESHOLD",
    "MemoryStatus",
    "SpectrogramParams",
    "StdoutRedirector",
    "auto_optimize_params",
    "color_for_percent",
    "compute_spectrogram",
    "memory_status",
    "min_max_downsample",
]

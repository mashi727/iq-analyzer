"""Core signal-processing and memory utilities (UI-independent)."""

from __future__ import annotations

from iq_analyzer.core.memory import (
    COLOR_CRITICAL,
    COLOR_NORMAL,
    COLOR_WARNING,
    MemoryStatus,
    color_for_percent,
    memory_status,
)
from iq_analyzer.core.stdout_redirector import StdoutRedirector

__all__ = [
    "COLOR_CRITICAL",
    "COLOR_NORMAL",
    "COLOR_WARNING",
    "MemoryStatus",
    "StdoutRedirector",
    "color_for_percent",
    "memory_status",
]

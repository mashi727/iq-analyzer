"""Cross-platform system memory monitoring helpers.

The viewer is targeted at 8 GB Windows 11 machines processing files up to
20 GB, so a small always-on memory readout doubles as a safety indicator.
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil

# Status thresholds — kept as module-level constants so the UI and tests can
# agree on the cut-offs without hard-coding them in multiple places.
WARNING_PERCENT = 60.0  # orange
CRITICAL_PERCENT = 80.0  # red

# Hex colors consumed by the Qt style sheet.
COLOR_NORMAL = "#888888"
COLOR_WARNING = "#ffa500"
COLOR_CRITICAL = "#ff6b6b"


@dataclass(frozen=True)
class MemoryStatus:
    """Snapshot of system memory usage suitable for a status label."""

    used_gb: float
    total_gb: float
    percent: float
    color: str

    def label(self) -> str:
        return f"メモリ: {self.used_gb:.1f} / {self.total_gb:.1f} GB ({self.percent:.0f}%)"


def color_for_percent(percent: float) -> str:
    """Pick a status color from a percent usage value."""
    if percent >= CRITICAL_PERCENT:
        return COLOR_CRITICAL
    if percent >= WARNING_PERCENT:
        return COLOR_WARNING
    return COLOR_NORMAL


def memory_status() -> MemoryStatus:
    """Return the current system memory snapshot.

    Wraps :func:`psutil.virtual_memory` so the rest of the application stays
    independent of platform-specific quirks.
    """
    memory = psutil.virtual_memory()
    used_gb = memory.used / (1024**3)
    total_gb = memory.total / (1024**3)
    percent = float(memory.percent)
    return MemoryStatus(
        used_gb=used_gb,
        total_gb=total_gb,
        percent=percent,
        color=color_for_percent(percent),
    )

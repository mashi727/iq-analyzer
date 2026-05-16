"""Tests for ``iq_analyzer.core.memory``."""

from __future__ import annotations

from collections import namedtuple

import pytest

from iq_analyzer.core import memory as memory_mod
from iq_analyzer.core.memory import (
    COLOR_CRITICAL,
    COLOR_NORMAL,
    COLOR_WARNING,
    color_for_percent,
    memory_status,
)

_VMem = namedtuple("VirtualMemory", "used total percent")


@pytest.mark.parametrize(
    ("percent", "expected"),
    [
        (0.0, COLOR_NORMAL),
        (59.9, COLOR_NORMAL),
        (60.0, COLOR_WARNING),
        (79.9, COLOR_WARNING),
        (80.0, COLOR_CRITICAL),
        (100.0, COLOR_CRITICAL),
    ],
)
def test_color_for_percent_thresholds(percent: float, expected: str) -> None:
    assert color_for_percent(percent) == expected


def test_memory_status_formats_label(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _VMem(used=4 * 1024**3, total=16 * 1024**3, percent=25.0)
    monkeypatch.setattr(memory_mod.psutil, "virtual_memory", lambda: fake)

    status = memory_status()
    assert status.used_gb == pytest.approx(4.0)
    assert status.total_gb == pytest.approx(16.0)
    assert status.percent == pytest.approx(25.0)
    assert status.color == COLOR_NORMAL
    assert status.label() == "メモリ: 4.0 / 16.0 GB (25%)"


def test_memory_status_warning_color(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _VMem(used=10 * 1024**3, total=16 * 1024**3, percent=70.0)
    monkeypatch.setattr(memory_mod.psutil, "virtual_memory", lambda: fake)
    assert memory_status().color == COLOR_WARNING


def test_memory_status_critical_color(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _VMem(used=14 * 1024**3, total=16 * 1024**3, percent=90.0)
    monkeypatch.setattr(memory_mod.psutil, "virtual_memory", lambda: fake)
    assert memory_status().color == COLOR_CRITICAL

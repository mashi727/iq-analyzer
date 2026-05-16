"""Smoke tests: package metadata is importable and version is set."""

from __future__ import annotations


def test_version_is_exposed() -> None:
    import iq_analyzer

    assert iq_analyzer.__version__
    assert isinstance(iq_analyzer.__version__, str)

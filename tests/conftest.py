"""Pytest fixtures shared across the suite.

Widget tests need a live :class:`QApplication`; we create a single one for the
whole session against the ``offscreen`` Qt platform so the tests don't require
a display server (works on CI and headless dev machines).
"""

from __future__ import annotations

import os
import sys

import pytest

# Must be set *before* PySide6 imports the Qt platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> object:
    """Lazy session-wide ``QApplication`` for widget tests."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app

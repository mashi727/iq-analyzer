#!/usr/bin/env python3
"""Legacy entry point — kept so ``python rs_iq_viewer.py`` still works.

All implementation now lives in the :mod:`iq_analyzer` package. New code
should prefer the ``iq-analyzer`` console script (installed by ``uv sync``)
or ``python -m iq_analyzer``. The :class:`RSIQViewer` symbol is re-exported
for any external scripts that imported it from here.
"""

from __future__ import annotations

from iq_analyzer.cli import main
from iq_analyzer.ui.main_window import RSIQViewer

__all__ = ["RSIQViewer", "main"]


if __name__ == "__main__":
    raise SystemExit(main())

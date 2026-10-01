"""Console entry point for the IQ analyzer.

Exposed as the ``iq-analyzer`` script via ``[project.scripts]`` in
``pyproject.toml`` and as ``python -m iq_analyzer`` via :mod:`__main__`.
"""

from __future__ import annotations

import gc
import logging
import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def _platform_font_size() -> int:
    """Smaller font on Windows so the dense UI fits 1080p, larger on macOS/Linux."""
    return 9 if sys.platform == "win32" else 20


def main(argv: list[str] | None = None) -> int:
    """Launch the GUI and run the Qt event loop.

    Returns the application exit code. Wrapped by both the
    ``iq-analyzer`` console script and ``python -m iq_analyzer``.
    """
    if argv is None:
        argv = sys.argv

    app = QApplication.instance() or QApplication(argv)

    font = QFont()
    font.setPointSize(_platform_font_size())
    app.setFont(font)

    # Built-in flat dark theme (ui.style). qdarktheme is no longer layered
    # underneath: when installed, its sheet set backgrounds (#202124), frame
    # lines (#3f4042) and group-box/splitter padding that ours did not
    # override, which left 5–7 px gaps around every pane.
    from iq_analyzer.ui.style import apply_theme

    apply_theme(app)

    # Imported lazily so ``--help`` (etc., in the future) is cheap and so
    # widget creation only happens after the QApplication exists.
    from iq_analyzer.ui.main_window import RSIQViewer

    viewer = RSIQViewer()
    viewer.show()

    print("=" * 60)
    print("Rohde & Schwarz IQ Data Viewer")
    print("Target: Windows 11, Core i3, 8GB RAM")
    print("Supported formats: WVH/WVD, .wv (SMU-WV), iq.tar, Keysight .bin")
    print("=" * 60)
    print("アプリケーションが起動しました。")
    print("左側のファイルブラウザからファイルをダブルクリックしてください。")

    exit_code = int(app.exec())

    # closeEvent has already done the heavy cleanup; this is a final sweep so
    # the (memmapped) loader buffers definitely release before the process exits.
    gc.collect()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

"""Console entry point for the IQ analyzer.

Exposed as the ``iq-analyzer`` script via ``[project.scripts]`` in
``pyproject.toml`` and as ``python -m iq_analyzer`` via :mod:`__main__`.
"""

from __future__ import annotations

import argparse
import gc
import logging
import re
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def _platform_font_size() -> int:
    """Smaller font on Windows so the dense UI fits 1080p, larger on macOS/Linux."""
    return 9 if sys.platform == "win32" else 20


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose --help / usage errors stay visible in the windowed EXE.

    argparse writes to sys.stdout / sys.stderr, which are None in a PyInstaller
    ``--windowed`` build; writing there raises instead of showing anything.
    """

    def _print_message(self, message: str, file=None) -> None:  # noqa: ANN001
        if message and (file is None or file is sys.stderr) and sys.stderr is None:
            _report_error(message)
        elif message and file is not None:
            file.write(message)
        elif message and sys.stdout is not None:
            sys.stdout.write(message)
        elif message:
            _report_error(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="iq-analyzer",
        description="R&S / Keysight IQ データビューア",
    )
    parser.add_argument(
        "start_dir",
        nargs="?",
        default=None,
        metavar="DIR",
        help="ファイルブラウザの起点にするドライブまたはディレクトリ（例: D:  D:\\data  ~/iq）。"
        "省略時はカレントディレクトリ。",
    )
    return parser


def resolve_start_dir(arg: str | None) -> Path:
    """Turn the command-line argument into an absolute, existing directory.

    ``None`` means the current directory. A bare Windows drive ("D:") is taken
    as that drive's root: on its own, "D:" means "the current directory *on* D:",
    which is rarely what someone typing a drive letter wants.
    Raises ``ValueError`` with a user-facing message when the path is unusable.
    """
    if arg is None:
        return Path.cwd()
    text = arg.strip().strip('"')
    if re.fullmatch(r"[A-Za-z]:", text):
        text += "\\"
    path = Path(text).expanduser()
    if not path.exists():
        raise ValueError(f"指定されたパスが見つかりません: {arg}")
    if not path.is_dir():
        raise ValueError(f"ドライブまたはディレクトリを指定してください（ファイルは不可）: {arg}")
    return path.resolve()


def _report_error(message: str) -> None:
    """Show *message* where the user can see it.

    The Windows EXE is built with ``--windowed``: there is no console and
    ``sys.stderr`` is None, so a printed error would vanish and the app would
    just not start. Fall back to a dialog in that case.
    """
    if sys.stderr is not None:
        print(f"iq-analyzer: {message}", file=sys.stderr)
        return
    from PySide6.QtWidgets import QMessageBox

    QMessageBox.critical(None, "IQ Analyzer", message)


def main(argv: list[str] | None = None) -> int:
    """Launch the GUI and run the Qt event loop.

    Returns the application exit code. Wrapped by both the
    ``iq-analyzer`` console script and ``python -m iq_analyzer``.
    """
    if argv is None:
        argv = sys.argv

    app = QApplication.instance() or QApplication(argv)

    # Parse after QApplication: Qt removes its own options (-style, -platform …)
    # from app.arguments(), so they are not mistaken for the start directory.
    # When main() is called with an explicit argv (tests), use that instead.
    args_in = list(argv[1:]) if argv is not sys.argv else list(app.arguments()[1:])
    args = _build_parser().parse_args(args_in)
    try:
        start_dir = resolve_start_dir(args.start_dir)
    except ValueError as exc:
        _report_error(str(exc))
        return 2

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

    viewer = RSIQViewer(start_dir=start_dir)
    viewer.show()

    print("=" * 60)
    print("Rohde & Schwarz IQ Data Viewer")
    print("Target: Windows 11, Core i3, 8GB RAM")
    print("Supported formats: WVH/WVD, .wv (SMU-WV), iq.tar, Keysight .bin")
    print("=" * 60)
    print("アプリケーションが起動しました。")
    print(f"起点フォルダ: {start_dir}")
    print("左側のファイルブラウザからファイルをダブルクリックしてください。")

    exit_code = int(app.exec())

    # closeEvent has already done the heavy cleanup; this is a final sweep so
    # the (memmapped) loader buffers definitely release before the process exits.
    gc.collect()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

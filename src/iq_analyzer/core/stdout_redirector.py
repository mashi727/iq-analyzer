"""Redirect ``sys.stdout`` writes into a Qt signal.

Plugged into ``sys.stdout`` so any ``print()`` from the viewer (or the loaders
underneath it) lands in the in-app log panel while still appearing on the real
terminal for live debugging.
"""

from __future__ import annotations

import sys
from typing import TextIO

from PySide6.QtCore import QObject, Signal


class StdoutRedirector(QObject):
    """Tee :data:`sys.stdout` into a Qt signal.

    Connect :attr:`text_written` to e.g. a ``QTextEdit.append`` slot, then
    assign the instance to :data:`sys.stdout`::

        redirector = StdoutRedirector(sys.stdout)
        redirector.text_written.connect(text_edit.append)
        sys.stdout = redirector

    The original file-like object (typically the real terminal) keeps
    receiving writes so external debuggers / log captures continue to work.

    Implementation note: :meth:`write` does **not** call
    ``QCoreApplication.processEvents``. Doing so during early app initialisation
    triggers a re-entrant event loop that has historically caused crashes on
    macOS — the consumer side is responsible for any UI refresh.
    """

    text_written = Signal(str)

    def __init__(self, original_stdout: TextIO | None = None) -> None:
        super().__init__()
        self.original_stdout: TextIO = original_stdout or sys.stdout

    def write(self, text: str) -> int:
        if text.strip():
            self.text_written.emit(text)
        if self.original_stdout is not None:
            self.original_stdout.write(text)
        return len(text)

    def flush(self) -> None:
        if self.original_stdout is not None:
            self.original_stdout.flush()

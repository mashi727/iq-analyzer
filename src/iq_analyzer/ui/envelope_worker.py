"""Background thread that builds (or loads) the amplitude envelope."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from PySide6.QtCore import QThread, Signal

from iq_analyzer.core.envelope import Envelope, build_envelope, source_path

logger = logging.getLogger(__name__)

# Minimum spacing between progress signals; each one triggers an overview
# redraw on the GUI thread, which should stay cheap relative to the I/O.
_PROGRESS_INTERVAL_S = 0.5


class EnvelopeWorker(QThread):
    """Fills an :class:`Envelope` from a loader without blocking the GUI.

    The loader's memmap is read directly from this thread. Callers must call
    :meth:`stop` (which joins the thread) *before* closing the loader.
    """

    # qlonglong, not int: Signal(int) is a 32-bit C int in PySide6, and sample
    # counts pass 2^31 at ~8.6 GB of int16 IQ (a 121 GB WVD has 3.0e10 samples).
    progress = Signal("qlonglong", "qlonglong")  # (samples_done, samples_total)
    completed = Signal(bool)  # True if a cache file was written

    def __init__(self, loader: Any, envelope: Envelope, parent: Any = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self.envelope = envelope
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._last_emit = 0.0

    def stop(self) -> None:
        self._cancel.set()
        self._resume.set()
        self.wait()

    def pause(self) -> None:
        """Hold the build between chunks (returns immediately)."""
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def _on_progress(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done >= total or now - self._last_emit >= _PROGRESS_INTERVAL_S:
            self._last_emit = now
            self.progress.emit(done, total)

    def run(self) -> None:
        try:
            finished = build_envelope(
                self._loader,
                self.envelope,
                progress=self._on_progress,
                cancel=self._cancel,
                resume=self._resume,
            )
        except Exception:  # pragma: no cover - reported, never fatal for the viewer
            logger.exception("envelope build failed")
            return
        if not finished:
            return
        src = source_path(self._loader)
        saved = src is not None and self.envelope.save(src) is not None
        self.completed.emit(saved)

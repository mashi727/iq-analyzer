"""Tests for ``iq_analyzer.core.stdout_redirector.StdoutRedirector``.

Signals from a QObject can be exercised without a running QApplication as long
as they are connected to plain Python callables, so these tests stay
GUI-free.
"""

from __future__ import annotations

import io

from iq_analyzer.core import StdoutRedirector


def test_write_emits_signal_and_echoes_original() -> None:
    sink = io.StringIO()
    redirector = StdoutRedirector(sink)
    received: list[str] = []
    redirector.text_written.connect(received.append)

    redirector.write("hello\n")
    redirector.write("world\n")

    assert received == ["hello\n", "world\n"]
    assert sink.getvalue() == "hello\nworld\n"


def test_whitespace_only_writes_are_dropped_from_signal() -> None:
    sink = io.StringIO()
    redirector = StdoutRedirector(sink)
    received: list[str] = []
    redirector.text_written.connect(received.append)

    redirector.write("   \n")
    redirector.write("\t")

    # Whitespace is still passed through to the original stream so console
    # formatting survives, but it doesn't spam the in-app log.
    assert received == []
    assert sink.getvalue() == "   \n\t"


def test_flush_delegates_to_original_stream() -> None:
    flushed = {"count": 0}

    class Recording(io.StringIO):
        def flush(self) -> None:  # type: ignore[override]
            flushed["count"] += 1

    redirector = StdoutRedirector(Recording())
    redirector.flush()
    redirector.flush()
    assert flushed["count"] == 2


def test_write_returns_length() -> None:
    """``sys.stdout.write`` is expected to return the number of bytes written."""
    redirector = StdoutRedirector(io.StringIO())
    assert redirector.write("abcd") == 4

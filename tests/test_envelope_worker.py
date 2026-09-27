"""Tests for ``iq_analyzer.ui.envelope_worker.EnvelopeWorker``."""

from __future__ import annotations

import numpy as np

from iq_analyzer.core.envelope import BASE_BIN, Envelope
from iq_analyzer.ui.envelope_worker import EnvelopeWorker


def test_progress_signal_carries_sample_counts_beyond_32_bit(qapp) -> None:
    """A 121 GB WVD holds 30,299,652,096 samples — far past INT32_MAX."""
    worker = EnvelopeWorker(loader=None, envelope=Envelope(1))
    received: list[tuple[int, int]] = []
    worker.progress.connect(lambda done, total: received.append((done, total)))

    worker.progress.emit(8_334_082_048, 30_299_652_096)

    assert received == [(8_334_082_048, 30_299_652_096)]


def test_worker_runs_to_completion_in_thread(qapp, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IQ_ANALYZER_CACHE_DIR", str(tmp_path / "cache"))

    class Loader:
        def __init__(self) -> None:
            self.data_memmap = None
            self.wv_path = tmp_path / "x.wv"
            self.wv_path.write_bytes(b"x")

        def get_iq_data(self, s: int, e: int) -> np.ndarray:
            return np.full(e - s, 3 + 4j, dtype=np.complex64)

    env = Envelope(3 * BASE_BIN)
    worker = EnvelopeWorker(Loader(), env)
    done: list[bool] = []
    worker.completed.connect(done.append)
    worker.start()
    assert worker.wait(10_000)
    qapp.processEvents()

    assert env.complete
    assert done == [True]
    np.testing.assert_allclose(env.amp_max, 5.0)

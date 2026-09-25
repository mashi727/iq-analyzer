"""Tests for the precomputed amplitude envelope (``iq_analyzer.core.envelope``)."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.core.envelope import BASE_BIN, Envelope, amplitude_scale_of, build_envelope
from iq_analyzer.loaders import open_iq_file


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IQ_ANALYZER_CACHE_DIR", str(tmp_path / "cache"))


def _write_wv(path: Path, raw: np.ndarray) -> Path:
    payload = raw.astype("<i2").tobytes()
    head = f"{{TYPE: SMU-WV,0}}{{CLOCK:1000000}}{{SAMPLES:{len(raw) // 2}}}{{WAVEFORM-{len(payload) + 1}:#".encode()
    path.write_bytes(head + payload + b"}")
    return path


def _recording(tmp_path: Path, n: int) -> tuple[Path, np.ndarray]:
    rng = np.random.default_rng(0)
    raw = np.clip(rng.normal(0, 300, 2 * n), -32768, 32767).astype(np.int16)
    raw[2 * (n // 3)] = -32768  # extreme values must not overflow the integer path
    raw[2 * (n // 3) + 1] = -32768
    raw[2 * (n // 2)] = 20000  # one-sample pulse
    return _write_wv(tmp_path / "rec.wv", raw), raw


def _reference_bins(raw: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    amp = np.abs(raw[0::2].astype(np.float64) + 1j * raw[1::2].astype(np.float64))
    bins = [amp[i : i + BASE_BIN] for i in range(0, len(amp), BASE_BIN)]
    return np.array([b.min() for b in bins]), np.array([b.max() for b in bins])


def test_build_matches_exact_amplitudes_including_partial_tail(tmp_path: Path) -> None:
    n = 5 * 1024 * BASE_BIN // 4 + 123  # several chunks + a partial last bin
    path, raw = _recording(tmp_path, n)
    loader = open_iq_file(path)
    env = Envelope(n, amplitude_scale_of(loader))
    assert build_envelope(loader, env)
    assert env.complete

    ref_min, ref_max = _reference_bins(raw)
    np.testing.assert_allclose(env.amp_min, ref_min, rtol=1e-6)
    np.testing.assert_allclose(env.amp_max, ref_max, rtol=1e-6)
    assert env.amp_max.max() == pytest.approx(np.hypot(32768, 32768), rel=1e-6)
    loader.close()


def test_minmax_query_keeps_single_sample_pulse(tmp_path: Path) -> None:
    n = 2000 * BASE_BIN
    path, _ = _recording(tmp_path, n)
    loader = open_iq_file(path)
    env = Envelope(n)
    build_envelope(loader, env)

    x, y = env.minmax(0, n, 500, sample_rate=1e6)
    assert x.shape == y.shape == (1000,)
    assert np.all(y[0::2] <= y[1::2])
    assert y.max() >= 20000
    assert np.all(np.diff(x[0::2]) > 0)
    loader.close()


def test_cache_round_trip_and_invalidation(tmp_path: Path) -> None:
    n = 50 * BASE_BIN
    path, _ = _recording(tmp_path, n)
    loader = open_iq_file(path)
    env = Envelope(n)
    build_envelope(loader, env)
    assert env.save(path) is not None

    cached = Envelope.load_cached(path, n)
    assert cached is not None and cached.complete
    np.testing.assert_array_equal(cached.amp_max, env.amp_max)
    assert Envelope.load_cached(path, n + 1) is None  # different length → miss
    loader.close()

    path.write_bytes(path.read_bytes() + b"\0")  # file changed → new key
    assert Envelope.load_cached(path, n) is None


def test_cancel_stops_early_and_reports_prefix(tmp_path: Path) -> None:
    n = 4 * 1024 * BASE_BIN
    path, _ = _recording(tmp_path, n)
    loader = open_iq_file(path)
    env = Envelope(n)
    cancel = threading.Event()

    def progress(done: int, total: int) -> None:
        cancel.set()

    assert not build_envelope(loader, env, progress=progress, cancel=cancel)
    assert 0 < env.valid_samples < n
    assert env.covers(0, env.valid_samples)
    assert not env.covers(0, n)
    loader.close()


def test_generic_path_for_float_loaders() -> None:
    class FloatLoader:
        def __init__(self) -> None:
            self.data_memmap = None

        def get_iq_data(self, s: int, e: int) -> np.ndarray:
            return np.full(e - s, 3 + 4j, dtype=np.complex64)

    env = Envelope(3 * BASE_BIN)
    build_envelope(FloatLoader(), env)
    np.testing.assert_allclose(env.amp_max, 5.0)

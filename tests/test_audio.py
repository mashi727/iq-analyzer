"""Tests for envelope audio of a time–frequency selection (``iq_analyzer.core.audio``)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.core.audio import EnvelopeAudioParams, envelope_audio, write_wav
from iq_analyzer.loaders import open_iq_file

FS = 10e6
RATE = 48_000


class ArrayLoader:
    def __init__(self, x: np.ndarray) -> None:
        self.x = x.astype(np.complex64)
        self.header = {"SAMPLES": x.size, "CLOCK": FS}

    def get_iq_data(self, start: int = 0, end: int | None = None) -> np.ndarray:
        return self.x[start:end].copy()


def _trains(seconds: float = 0.2, seed: int = 0) -> np.ndarray:
    """+2 MHz pulses at PRF 1 kHz and −2 MHz pulses at PRF 3 kHz, 2 µs wide."""
    rng = np.random.default_rng(seed)
    n = int(seconds * FS)
    x = 3.0 * (rng.standard_normal(n) + 1j * rng.standard_normal(n))
    t = np.arange(n) / FS
    # Phase offset so the two trains never coincide.
    for prf, f0, phase in ((1000.0, 2e6, 0.1e-3), (3000.0, -2e6, 0.0)):
        on = ((t - phase) * prf) % 1.0 < 2e-6 * prf
        x[on] += 300.0 * np.exp(2j * np.pi * f0 * t[on])
    return x


def _autocorr(audio: np.ndarray) -> np.ndarray:
    a = audio - audio.mean()
    spec = np.fft.rfft(a, 2 * a.size)
    return np.fft.irfft(np.abs(spec) ** 2)[: a.size]


def _period_samples(audio: np.ndarray, lo: int = 4, hi: int = 2000) -> int:
    """Fundamental repetition period: the smallest lag whose autocorrelation
    reaches 90 % of the maximum (a period-16 train peaks equally at 16, 32, 48…)."""
    ac = _autocorr(audio)[lo:hi]
    return lo + int(np.flatnonzero(ac >= 0.9 * ac.max())[0])


def test_band_selection_isolates_each_pulse_train() -> None:
    x = _trains()
    loader = ArrayLoader(x)
    hi = envelope_audio(loader, 0, x.size, FS, EnvelopeAudioParams(1e6, 3e6))
    lo = envelope_audio(loader, 0, x.size, FS, EnvelopeAudioParams(-3e6, -1e6))
    assert hi.filtered and lo.filtered
    assert _period_samples(hi.audio) == pytest.approx(RATE / 1000, abs=1)  # 48 samples
    assert _period_samples(lo.audio) == pytest.approx(RATE / 3000, abs=1)  # 16 samples
    # The 3 kHz train (−2 MHz) is attenuated in the +2 MHz band. It does not
    # vanish: the sinc sidelobes of a 2 µs pulse 4 MHz away are ≈ −20 dB.
    ones = hi.audio[int(0.1e-3 * RATE) :: RATE // 1000]  # on the 1 kHz train
    threes = hi.audio[RATE // 3000 :: RATE // 1000]  # on 3 kHz pulses only
    assert np.median(threes) < 0.2 * np.median(ones)


def test_full_band_hears_both_and_slowdown_lowers_pitch() -> None:
    x = _trains()
    loader = ArrayLoader(x)
    full = envelope_audio(loader, 0, x.size, FS, EnvelopeAudioParams(-FS, FS))
    assert not full.filtered
    assert full.audio.size == int(0.2 * RATE)
    slow = envelope_audio(loader, 0, x.size, FS, EnvelopeAudioParams(1e6, 3e6, slowdown=4.0))
    assert slow.audio.size == pytest.approx(4 * 0.2 * RATE, abs=2)
    assert _period_samples(slow.audio) == pytest.approx(4 * RATE / 1000, abs=2)


def test_block_size_does_not_change_the_result() -> None:
    x = _trains(0.1)
    loader = ArrayLoader(x)
    p = EnvelopeAudioParams(1e6, 3e6)
    a = envelope_audio(loader, 0, x.size, FS, p, block=1 << 21).audio
    b = envelope_audio(loader, 0, x.size, FS, p, block=1 << 16).audio
    assert a.size == b.size
    assert np.corrcoef(a, b)[0, 1] > 0.99


def test_int16_fast_path_matches_generic_path(tmp_path: Path) -> None:
    x = _trains(0.05)
    raw = np.empty(2 * x.size, dtype=np.int16)
    raw[0::2] = np.clip(x.real, -32768, 32767)
    raw[1::2] = np.clip(x.imag, -32768, 32767)
    head = f"{{TYPE: SMU-WV,0}}{{CLOCK:{FS:.0f}}}{{SAMPLES:{x.size}}}{{WAVEFORM-{raw.nbytes + 1}:#".encode()
    path = tmp_path / "t.wv"
    path.write_bytes(head + raw.astype("<i2").tobytes() + b"}")
    loader = open_iq_file(path)
    params = EnvelopeAudioParams(-FS, FS)
    fast = envelope_audio(loader, 0, x.size, FS, params).audio
    generic = envelope_audio(ArrayLoader(loader.get_iq_data(0, x.size)), 0, x.size, FS, params).audio
    np.testing.assert_allclose(fast, generic, atol=1e-4)
    loader.close()


def test_too_long_audio_is_refused() -> None:
    x = np.zeros(10_000_000, dtype=np.complex64)
    with pytest.raises(ValueError, match="audio would last"):
        envelope_audio(ArrayLoader(x), 0, x.size, FS, EnvelopeAudioParams(-FS, FS, slowdown=1000.0))


def test_write_wav_roundtrip(tmp_path: Path) -> None:
    audio = np.sin(np.linspace(0, 100, 4800)).astype(np.float32) * 0.5
    path = tmp_path / "a.wav"
    write_wav(path, audio, RATE)
    with wave.open(str(path)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, RATE, 4800)
        pcm = np.frombuffer(w.readframes(4800), dtype="<i2")
    np.testing.assert_allclose(pcm / 32767, audio, atol=1e-4)


def test_spectrogram_selection_tracks_unit_changes(qapp) -> None:
    from iq_analyzer.widgets.spectrogram import SpectrogramWidget

    w = SpectrogramWidget()
    f = np.linspace(-5e6, 5e6, 64)
    w.update_spectrogram(f, np.linspace(0.5, 1.5, 100), np.zeros((64, 100)), center_freq=1e9)
    sel = w.selection()
    assert sel is not None
    t0, t1, f0, f1 = sel
    assert (t0, t1) == pytest.approx((0.5, 1.5)) and (f0, f1) == pytest.approx((1e9 - 5e6, 1e9 + 5e6))

    # Narrow the selection, then recompute a spectrogram that is shown in ms.
    w.selection_roi.setPos([0.6, 1e9 - 1e6])
    w.selection_roi.setSize([0.2, 2e6])
    w.update_spectrogram(f, np.linspace(0.5, 0.9, 100), np.zeros((64, 100)), center_freq=1e9)
    assert w.time_unit == "ms"
    assert w.selection() == pytest.approx((0.6, 0.8, 1e9 - 1e6, 1e9 + 1e6))

    w.clear_axes()
    assert not w.selection_roi.isVisible()


def test_selection_menu_selects_all_or_visible_range(qapp) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from iq_analyzer.widgets.spectrogram import SpectrogramWidget

    w = SpectrogramWidget()
    w.resize(900, 500)
    w.show()
    w.update_spectrogram(np.linspace(-5e6, 5e6, 64), np.linspace(0.5, 1.5, 100), np.zeros((64, 100)), center_freq=1e9)
    qapp.processEvents()

    # Right-clicking on the ROI (which covers the image) must still open the menu.
    menu = w.plot_item.getViewBox().menu
    centre = w.graphics_widget.mapFromScene(w.selection_roi.sceneBoundingRect().center())
    QTest.mouseClick(w.graphics_widget.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, centre)
    qapp.processEvents()
    assert menu.isVisible()
    assert w.select_all_action.isEnabled() and w.select_view_action.isEnabled()
    menu.hide()

    w.selection_roi.setPos([0.8, 1e9 - 1e6])
    w.selection_roi.setSize([0.1, 2e6])
    w.select_all()
    assert w.selection() == pytest.approx((0.5, 1.5, 1e9 - 5e6, 1e9 + 5e6))

    w.plot_item.getViewBox().setRange(xRange=(0.9, 1.2), yRange=(1e9 - 2e6, 1e9 + 3e6), padding=0)
    w.select_view()
    assert w.selection() == pytest.approx((0.9, 1.2, 1e9 - 2e6, 1e9 + 3e6))
    w.close()

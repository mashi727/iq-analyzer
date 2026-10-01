"""Envelope of a time–frequency selection, rendered as audio.

Listening to the detected envelope is the classic way to recognise a pulse
train: a PRF of 2.5 kHz is heard as a 2.5 kHz buzz, a stagger as a rough
tone, a scanning beam as a periodic swell.

Envelope
--------
The recording is already complex baseband IQ, i.e. the analytic signal of
the real passband signal. The Hilbert envelope of the band ``[f_lo, f_hi]``
is therefore ``|x_band(t)|`` where ``x_band`` is the IQ restricted to that
band — no explicit Hilbert transform of a real signal is needed (it would
only reconstruct the quadrature component we already have). The band is cut
with an FFT mask with raised-cosine edges, applied block-wise by
overlap-discard so memory stays flat for any selection length.

Audio
-----
Each audio sample covers ``fs / (audio_rate · slowdown)`` IQ samples (≈ 5,200
at 250 MS/s in real time). The *peak* detector keeps the maximum envelope of
that span, so a 1 µs pulse still produces a full-height click; the *mean*
detector averages and favours continuous signals. The median (noise floor)
is subtracted, and the result is normalised on the 99.9th
percentile so a single spike cannot make everything else inaudible.
"""

from __future__ import annotations

import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

AUDIO_RATE = 48_000
# Longest audio rendered in one go (float32 mono: 10 min ≈ 115 MB).
MAX_AUDIO_S = 600.0


@dataclass
class EnvelopeAudioParams:
    f_lo_hz: float  # band edges as offsets from the recording centre
    f_hi_hz: float
    slowdown: float = 1.0  # 10 → the selection plays 10× longer (pitch ÷ 10)
    detector: str = "peak"  # "peak" | "mean"
    log_scale: bool = False  # compress the envelope in dB before playback
    audio_rate: int = AUDIO_RATE


@dataclass
class EnvelopeAudio:
    audio: NDArray[np.float32]  # mono, −1..1
    rate: int
    span: tuple[int, int]  # IQ samples
    band_hz: tuple[float, float]
    filtered: bool  # False when the band was the full IQ bandwidth
    cancelled: bool = False


def _band_mask(n: int, fs: float, f_lo: float, f_hi: float) -> NDArray[np.float32]:
    """FFT-bin weights: 1 inside the band, raised-cosine skirts outside."""
    f = np.fft.fftfreq(n, 1.0 / fs)
    width = f_hi - f_lo
    # Transition: 5 % of the band, at least 4 bins (avoids long ringing on
    # narrow selections, where a brick wall would ring for ~fs/Δf samples).
    tr = max(0.05 * width, 4 * fs / n)
    m = np.zeros(n, dtype=np.float32)
    m[(f >= f_lo) & (f <= f_hi)] = 1.0
    lo = (f > f_lo - tr) & (f < f_lo)
    m[lo] = 0.5 * (1 + np.cos(np.pi * (f_lo - f[lo]) / tr))
    hi = (f > f_hi) & (f < f_hi + tr)
    m[hi] = 0.5 * (1 + np.cos(np.pi * (f[hi] - f_hi) / tr))
    return m


def _filter_margin(fs: float, f_lo: float, f_hi: float) -> int:
    """Samples discarded at each block edge: ≈ 4× the filter's time spread."""
    tr = max(0.05 * (f_hi - f_lo), 1.0)
    return int(np.clip(4 * fs / tr, 4096, 1 << 20))


class _Accumulator:
    """Folds envelope samples, stamped with their IQ sample position, into
    audio samples (max for the peak detector, mean otherwise). Spans split
    across blocks merge correctly because values are combined, not assigned."""

    def __init__(self, n_out: int, start: int, ratio: float, detector: str) -> None:
        self.start, self.ratio, self.peak = start, ratio, detector != "mean"
        self.val = np.zeros(n_out)
        self.cnt = np.zeros(n_out)
        self.n_out = n_out

    def add(self, pos0: float, step: float, env: NDArray[np.floating]) -> None:
        """``env[i]`` was taken at IQ position ``pos0 + i·step``."""
        if env.size == 0:
            return
        if step < self.ratio:  # several envelope samples per audio sample
            k = np.floor((pos0 + np.arange(env.size) * step - self.start) / self.ratio).astype(np.int64)
            ok = (k >= 0) & (k < self.n_out)
            k, v = k[ok], env[ok].astype(np.float64)
            if k.size == 0:
                return
            first = np.flatnonzero(np.concatenate(([True], np.diff(k) != 0)))
            kk = k[first]
            if self.peak:
                np.maximum.at(self.val, kk, np.maximum.reduceat(v, first))
            else:
                np.add.at(self.val, kk, np.add.reduceat(v, first))
            np.add.at(self.cnt, kk, np.diff(np.concatenate((first, [k.size]))))
        else:  # slowed down below the envelope rate: interpolate
            t = self.start + np.arange(self.n_out) * self.ratio
            lo, hi = pos0, pos0 + (env.size - 1) * step
            sel = (t >= lo) & (t <= hi) & (self.cnt == 0)
            if sel.any():
                self.val[sel] = np.interp(t[sel], lo + np.arange(env.size) * step, env)
                self.cnt[sel] = 1

    def result(self) -> NDArray[np.float64]:
        return self.val if self.peak else self.val / np.maximum(self.cnt, 1)


def envelope_audio(
    loader: Any,
    start: int,
    end: int,
    fs: float,
    params: EnvelopeAudioParams,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    block: int = 1 << 21,
) -> EnvelopeAudio:
    from scipy import fft as sfft

    start, end = max(0, int(start)), int(end)
    if end <= start:
        raise ValueError("empty selection")
    f_lo = max(params.f_lo_hz, -fs / 2)
    f_hi = min(params.f_hi_hz, fs / 2)
    if f_hi <= f_lo:
        raise ValueError("empty band")
    filtered = (f_hi - f_lo) < 0.99 * fs

    # IQ samples per audio sample. Slowing down by N stretches the audio N×,
    # so each audio sample covers N× less signal time (< 1 IQ sample when
    # slowed down strongly).
    ratio = fs / (params.audio_rate * params.slowdown)
    n_out = int(np.floor((end - start) / ratio))
    if n_out < 2:
        raise ValueError("selection shorter than two audio samples")
    if n_out / params.audio_rate > MAX_AUDIO_S:
        raise ValueError(f"audio would last {n_out / params.audio_rate:.0f} s (limit {MAX_AUDIO_S:.0f} s)")

    acc = _Accumulator(n_out, start, ratio, params.detector)
    n_total = int(loader.header["SAMPLES"])
    margin = _filter_margin(fs, f_lo, f_hi) if filtered else 0
    block = max(block, 4 * margin)
    # Full band, peak detector, int16 source: read |x|² in integer arithmetic
    # (as the envelope cache does, ~20× faster than complex64 conversion); the
    # max commutes with the square root, taken after reduction.
    power_reader = None
    if not filtered:
        from iq_analyzer.core.envelope import _power_reader, amplitude_scale_of

        mm = getattr(loader, "data_memmap", None)
        if mm is not None and mm.dtype == np.int16:
            power_reader = _power_reader(loader)
            scale = amplitude_scale_of(loader)

    total = end - start
    cancelled = False
    mask_cache: dict[int, tuple[NDArray[np.float32], int, int, int]] = {}
    s = start
    while s < end:
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        e = min(end, s + block)
        if filtered:
            # Overlap-discard: read a margin on both sides, filter, keep the middle.
            a = max(0, s - margin)
            b = min(n_total, e + margin)
            x = loader.get_iq_data(a, b)
            n = x.size
            if n not in mask_cache:
                mask_cache[n] = _decimating_mask(n, fs, f_lo, f_hi)
            mask, i0, i1, m = mask_cache[n]
            # Keep only the band's bins and inverse-transform them at a reduced
            # length: the envelope comes out at ≈ 4× the band width instead of
            # fs (a 20 MHz band at 250 MS/s is ~3× less work and memory).
            X = sfft.fft(x, workers=-1)
            Y = np.zeros(m, dtype=np.complex64)
            Y[: i1 - i0] = np.fft.fftshift(X)[i0:i1] * mask
            y = sfft.ifft(Y, workers=-1)
            step = n / m
            j0 = int(np.ceil((s - a) / step))
            j1 = int(np.ceil((e - a) / step))
            env = np.abs(y[j0:j1]) * (m / n)
            acc.add(a + j0 * step, step, env)
        elif power_reader is not None:
            p = power_reader(s, e).astype(np.float32)
            if acc.peak and ratio >= 1:
                acc.add(s, 1.0, p)  # max of power; sqrt below
            else:
                acc.add(s, 1.0, np.sqrt(p) * scale)
        else:
            acc.add(s, 1.0, np.abs(loader.get_iq_data(s, e)))
        s = e
        if progress is not None:
            progress(s - start, total)

    env_out = acc.result()
    if power_reader is not None and acc.peak and ratio >= 1:
        env_out = np.sqrt(env_out) * scale
    return EnvelopeAudio(
        audio=_to_audio(env_out, params),
        rate=params.audio_rate,
        span=(start, end),
        band_hz=(f_lo, f_hi),
        filtered=filtered,
        cancelled=cancelled,
    )


def _decimating_mask(n: int, fs: float, f_lo: float, f_hi: float) -> tuple[NDArray[np.float32], int, int, int]:
    """(weights, i0, i1, m): the band (with skirts) occupies fftshift bins
    [i0, i1); ``m`` is the reduced IFFT length (≥ 4× the band, fast size).

    The complex band signal itself needs only ~1× its width, but its
    *magnitude* has up to that bandwidth again and the peak detector samples
    it: at 1.25× the peak of a 2 µs pulse moved with the grid phase and the
    result depended on the block length (r = 0.95 between block sizes)."""
    from scipy.fft import next_fast_len

    full = np.fft.fftshift(_band_mask(n, fs, f_lo, f_hi))
    nz = np.flatnonzero(full > 0)
    i0, i1 = int(nz[0]), int(nz[-1]) + 1
    m = next_fast_len(max(16, int(np.ceil(4 * (i1 - i0)))))
    return full[i0:i1], i0, i1, m


def _to_audio(env: NDArray[np.float64], params: EnvelopeAudioParams) -> NDArray[np.float32]:
    y = env
    if params.log_scale:
        floor = max(float(np.median(y)), 1e-12)
        y = np.clip(20 * np.log10(np.maximum(y, 1e-12) / floor), 0, None)
    # Remove the noise floor by its median, not a high-pass: a zero-phase
    # IIR high-pass rang for tens of ms after a pulse at the selection edge
    # (odd-extension padding), and anything it would remove — scan
    # modulation at < 20 Hz — is inaudible anyway.
    y = y - float(np.median(y))
    ref = float(np.percentile(np.abs(y), 99.9)) if y.size else 0.0
    if ref <= 0:
        return np.zeros(y.size, dtype=np.float32)
    out = np.clip(0.9 * y / ref, -1.0, 1.0)
    # 5 ms fades so start/stop do not click.
    nf = min(out.size // 4, int(0.005 * params.audio_rate))
    if nf > 1:
        ramp = np.linspace(0.0, 1.0, nf)
        out[:nf] *= ramp
        out[-nf:] *= ramp[::-1]
    return out.astype(np.float32)


def write_wav(path: Path, audio: NDArray[np.float32], rate: int) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())

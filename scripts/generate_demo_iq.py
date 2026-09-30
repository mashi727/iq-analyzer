"""Generate a synthetic R&S WVH/WVD recording for trying out the viewer.

No instrument data is needed: the capture is built from signals whose shape is
easy to recognise in a spectrogram.

* noise floor
* two CW carriers (one slowly amplitude-modulated)
* a pulsed LFM radar: 80 µs up-chirps every 2.5 ms, amplitude following a
  rotating-antenna scan pattern (visible in the overview as a slow envelope)
* a frequency hopper: 1 ms dwells over 16 channels, with gaps
* short wideband bursts every ~37 ms

Usage::

    uv run python scripts/generate_demo_iq.py [OUT_DIR] [--seconds 1.0]

writes ``OUT_DIR/demo_capture.wvh`` + ``.wvd`` (default ``demo_data/``,
100 MS/s × 1 s = 400 MB). Generation runs in chunks, so memory stays small.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from iq_analyzer.loaders import WVFileLoader

FS = 100e6  # sample rate [Hz]
CENTER = 5.8e9  # nominal RF centre frequency [Hz] (metadata only)
CHUNK = 1 << 22  # samples per generation chunk

# Pulsed LFM radar
PRI = 2.5e-3
PULSE_WIDTH = 80e-6
CHIRP_F0, CHIRP_F1 = -42e6, -24e6
SCAN_PERIOD = 0.25  # antenna rotation period [s]

# Frequency hopper
HOP_DWELL = 1e-3
HOP_CHANNELS = np.linspace(4e6, 26e6, 16)

# Wideband bursts
BURST_PERIOD = 37e-3
BURST_WIDTH = 300e-6


def _chunk(n0: int, n: int, rng: np.random.Generator) -> np.ndarray:
    t = (n0 + np.arange(n)) / FS
    x = (rng.normal(0, 60, n) + 1j * rng.normal(0, 60, n)).astype(np.complex128)

    # CW carriers
    x += 400 * np.exp(2j * np.pi * 32e6 * t)
    x += 250 * (1 + 0.8 * np.sin(2 * np.pi * 3.0 * t)) * np.exp(2j * np.pi * -18e6 * t)

    # Pulsed LFM radar with scan-pattern amplitude
    tau = np.mod(t, PRI)
    on = tau < PULSE_WIDTH
    k = (CHIRP_F1 - CHIRP_F0) / PULSE_WIDTH
    # Main beam passes at t = 0.5 s (+ every SCAN_PERIOD), i.e. in the middle of
    # the viewer's default region.
    scan = 0.08 + 0.92 * np.sinc(4 * (np.mod(t + SCAN_PERIOD / 2, SCAN_PERIOD) / SCAN_PERIOD - 0.5)) ** 2
    phase = 2 * np.pi * (CHIRP_F0 * tau + 0.5 * k * tau**2)
    x[on] += 9000 * scan[on] * np.exp(1j * phase[on])

    # Frequency hopper (pseudo-random channel per dwell, ~25% of dwells silent)
    dwell = np.floor(t / HOP_DWELL).astype(np.int64)
    h = (dwell * 2654435761) & 0xFFFFFFFF
    active = (h >> 7) % 4 != 0
    freq = HOP_CHANNELS[h % len(HOP_CHANNELS)]
    x[active] += 1500 * np.exp(2j * np.pi * freq[active] * t[active])

    # Wideband bursts (band-limited noise across ±40 MHz)
    bpos = np.mod(t, BURST_PERIOD)
    burst = bpos < BURST_WIDTH
    if burst.any():
        wb = rng.normal(0, 1, burst.sum()) + 1j * rng.normal(0, 1, burst.sum())
        spec = np.fft.fft(wb)
        f = np.fft.fftfreq(wb.size, 1 / FS)
        spec[np.abs(f) > 40e6] = 0
        x[burst] += 2500 * np.fft.ifft(spec) * np.sqrt(wb.size / max(1, (np.abs(f) <= 40e6).sum()))

    return x


def generate(out_dir: Path, seconds: float, seed: int = 7) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    wvh = out_dir / "demo_capture.wvh"
    wvd = out_dir / "demo_capture.wvd"
    total = int(FS * seconds)
    rng = np.random.default_rng(seed)

    with wvd.open("wb") as fh:
        for n0 in range(0, total, CHUNK):
            n = min(CHUNK, total - n0)
            x = _chunk(n0, n, rng)
            iq = np.empty(2 * n, dtype="<i2")
            iq[0::2] = np.clip(np.round(x.real), -32768, 32767)
            iq[1::2] = np.clip(np.round(x.imag), -32768, 32767)
            iq.tofile(fh)

    WVFileLoader.write_wvh_header(
        wvh,
        {
            "TYPE": "RAW16LE",
            "COMPONENTS": "IQ",
            "CLOCK": FS,
            "RESOLUTION": 16,
            "FREQUENCY": CENTER,
            "REFLEVEL": -20.0,
            "SAMPLES": total,
            "CHANNAME0": "SYNTHETIC DEMO",
        },
    )
    return wvh


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out_dir", nargs="?", default="demo_data", type=Path)
    ap.add_argument("--seconds", type=float, default=1.0)
    args = ap.parse_args()
    wvh = generate(args.out_dir, args.seconds)
    size = wvh.with_suffix(".wvd").stat().st_size / 1e6
    print(f"wrote {wvh} (+ .wvd, {size:,.0f} MB)")


if __name__ == "__main__":
    main()

"""Precomputed amplitude envelope for multi-GB recordings.

Rendering the overview of a 100 GB file by reading it on every redraw costs
minutes. Instead, one sequential pass stores the min/max amplitude of every
``BASE_BIN`` samples (≈49 MB for 100 GB of int16 IQ). Any later overview or
zoomed waveform whose pixel width spans at least ``2 * BASE_BIN`` samples is
answered from that array in milliseconds, and — unlike stride sampling — every
sample has contributed, so no pulse is lost.

The envelope is cached on local disk (never next to the data: external
volumes may be read-only or slow, and a Dropbox folder would sync it), keyed
by path, size and mtime of the source file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

BASE_BIN = 4096  # samples per envelope bin
# Bins processed per read. 1024 bins × 4096 samples × 4 B = 16 MB of int16,
# small enough to keep peak memory flat and large enough for sequential I/O.
_BINS_PER_CHUNK = 1024
_CACHE_VERSION = 1


def cache_dir() -> Path:
    """Per-user cache directory for envelope files."""
    override = os.environ.get("IQ_ANALYZER_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "iq-analyzer" / "envelope"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "iq-analyzer" / "envelope"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "iq-analyzer" / "envelope"


def source_path(loader: Any) -> Path | None:
    """The file that identifies the recording (the archive, not a temp extract)."""
    for attr in ("tar_path", "wv_path", "wvd_path", "bin_path"):
        p = getattr(loader, attr, None)
        if p:
            return Path(p)
    return None


def _cache_file(src: Path) -> Path:
    st = src.stat()
    key = f"{_CACHE_VERSION}|{BASE_BIN}|{src.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return cache_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".npz")


def _power_int16(raw: NDArray[np.int16]) -> NDArray[np.uint32]:
    """|I|²+|Q|² of interleaved int16 in integer arithmetic (each square ≤ 2³⁰)."""
    sq = raw.astype(np.int32)
    sq *= sq
    pair = sq.view(np.uint32).reshape(-1, 2)
    return pair[:, 0] + pair[:, 1]


def _make_file_reader(fh: Any, offset: int) -> Callable[[int, int], NDArray[np.uint32]]:
    def read(start: int, end: int) -> NDArray[np.uint32]:
        raw = np.empty(2 * (end - start), dtype="<i2")
        fh.seek(offset + 4 * start)
        got = fh.readinto(memoryview(raw).cast("B"))
        if got != raw.nbytes:
            raise OSError(f"short read at sample {start}: {got} of {raw.nbytes} bytes")
        return _power_int16(raw)

    return read


def _power_reader(loader: Any) -> Callable[[int, int], NDArray[Any]]:
    """Return ``read(start, end) -> |IQ|²`` for a sample range.

    For interleaved int16 sources (WVD, .wv, Keysight .bin) the power is
    computed straight from the memmap in integer arithmetic: the complex64
    conversion done by ``get_iq_data`` is ~20× slower than the disk read. Each
    square is ≤ 2³⁰ so the sum of two fits in uint32 without overflow.
    """
    mm = getattr(loader, "data_memmap", None)
    if mm is not None and mm.dtype == np.int16:
        # Read with plain file I/O rather than through the memmap: pages touched
        # via a mapping stay in the process RSS, so one pass over a 17 GB file
        # measured 18 GB peak RSS. Buffered reads leave only the OS page cache.
        filename = getattr(mm, "filename", None)
        offset = int(getattr(mm, "offset", 0))
        if filename:
            fh = open(filename, "rb")  # noqa: SIM115 - closed when the reader is dropped
            reader = _make_file_reader(fh, offset)
            weakref.finalize(reader, fh.close)
            return reader

        def read_int16(start: int, end: int) -> NDArray[np.uint32]:
            return _power_int16(np.asarray(mm[2 * start : 2 * end]))

        return read_int16

    def read_generic(start: int, end: int) -> NDArray[np.float32]:
        x = loader.get_iq_data(start, end)
        return (x.real * x.real + x.imag * x.imag).astype(np.float32)

    return read_generic


class Envelope:
    """Min/max amplitude per ``BASE_BIN`` samples, possibly still being filled.

    ``valid_samples`` grows monotonically while :func:`build_envelope` runs, so
    the GUI thread may query the already-covered prefix concurrently.
    """

    def __init__(self, n_samples: int, amplitude_scale: float = 1.0) -> None:
        self.n_samples = int(n_samples)
        self.n_bins = -(-self.n_samples // BASE_BIN)
        self.amp_min = np.zeros(self.n_bins, dtype=np.float32)
        self.amp_max = np.zeros(self.n_bins, dtype=np.float32)
        self.amplitude_scale = float(amplitude_scale)
        self.valid_samples = 0

    @property
    def complete(self) -> bool:
        return self.valid_samples >= self.n_samples

    def covers(self, start: int, end: int) -> bool:
        return 0 <= start < end <= self.valid_samples

    def minmax(
        self, start: int, end: int, target_pixels: int, sample_rate: float
    ) -> tuple[NDArray[np.float64], NDArray[np.float32]]:
        """Same output shape as :func:`min_max_downsample`: interleaved (min, max).

        Pixel edges are rounded to envelope bins, so each pixel may include up
        to one bin (``BASE_BIN`` samples) outside its exact range; callers only
        use this when a pixel spans ≥ 2 bins.
        """
        b0 = start // BASE_BIN
        b1 = -(-end // BASE_BIN)
        n = b1 - b0
        pixels = max(1, min(int(target_pixels), n))
        edges = b0 + (np.arange(pixels + 1, dtype=np.int64) * n) // pixels
        idx = edges[:-1] - b0
        y_min = np.minimum.reduceat(self.amp_min[b0:b1], idx)
        y_max = np.maximum.reduceat(self.amp_max[b0:b1], idx)
        centers = (edges[:-1] + edges[1:]) * (BASE_BIN / 2) / sample_rate

        x = np.repeat(centers, 2).astype(np.float64)
        y = np.empty(2 * pixels, dtype=np.float32)
        y[0::2] = y_min
        y[1::2] = y_max
        return x, y

    # ------------------------------------------------------------------ cache

    def save(self, src: Path) -> Path | None:
        try:
            path = _cache_file(src)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp.npz")
            np.savez(
                tmp,
                amp_min=self.amp_min,
                amp_max=self.amp_max,
                n_samples=np.int64(self.n_samples),
                amplitude_scale=np.float64(self.amplitude_scale),
            )
            os.replace(tmp, path)
            return path
        except OSError as exc:
            logger.warning("envelope cache write failed: %s", exc)
            return None

    @classmethod
    def load_cached(cls, src: Path, n_samples: int) -> Envelope | None:
        try:
            path = _cache_file(src)
            if not path.exists():
                return None
            with np.load(path) as data:
                if int(data["n_samples"]) != int(n_samples):
                    return None
                env = cls(n_samples, float(data["amplitude_scale"]))
                env.amp_min[:] = data["amp_min"]
                env.amp_max[:] = data["amp_max"]
            env.valid_samples = env.n_samples
            return env
        except (OSError, KeyError, ValueError) as exc:
            logger.warning("envelope cache read failed: %s", exc)
            return None


def build_envelope(
    loader: Any,
    envelope: Envelope,
    *,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    resume: threading.Event | None = None,
) -> bool:
    """Fill ``envelope`` with one sequential pass. Returns False if cancelled.

    While ``resume`` is cleared the pass waits between chunks. The GUI clears
    it around its own bulk reads (spectrogram, save): two concurrent streams
    at distant offsets on one external drive measured 90× slower than either
    alone (273 s to read a 1 GB region during a build).
    """
    read_power = _power_reader(loader)
    scale = envelope.amplitude_scale
    n = envelope.n_samples
    chunk = _BINS_PER_CHUNK * BASE_BIN

    for start in range(0, n, chunk):
        while resume is not None and not resume.wait(0.1):
            if cancel is not None and cancel.is_set():
                return False
        if cancel is not None and cancel.is_set():
            return False
        end = min(start + chunk, n)
        power = read_power(start, end)

        full = (end - start) // BASE_BIN
        b0 = start // BASE_BIN
        if full:
            blocks = power[: full * BASE_BIN].reshape(full, BASE_BIN)
            envelope.amp_max[b0 : b0 + full] = np.sqrt(blocks.max(axis=1).astype(np.float64)) * scale
            envelope.amp_min[b0 : b0 + full] = np.sqrt(blocks.min(axis=1).astype(np.float64)) * scale
        tail = power[full * BASE_BIN :]
        if tail.size:  # only the very last bin can be partial
            envelope.amp_max[b0 + full] = np.sqrt(float(tail.max())) * scale
            envelope.amp_min[b0 + full] = np.sqrt(float(tail.min())) * scale

        envelope.valid_samples = end
        if progress is not None:
            progress(end, n)
    return True


def amplitude_scale_of(loader: Any) -> float:
    """Scale that ``get_iq_data`` applies on top of the raw int16 samples."""
    mm = getattr(loader, "data_memmap", None)
    if mm is not None and mm.dtype == np.int16:
        return float(getattr(loader, "y_scale", 1.0))
    return 1.0

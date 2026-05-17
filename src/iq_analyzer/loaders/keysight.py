"""Keysight N5110A (.bin + .bin.txt) loader.

The recording is split across two files that live side by side:

* ``<name>.bin`` — 16-bit signed-integer little-endian, I and Q interleaved.
* ``<name>.bin.txt`` — small tab-separated metadata file. Keys we care about:

    - ``XDelta``      seconds per sample (sample_rate = 1 / XDelta)
    - ``InputCenter`` carrier / center frequency in Hz
    - ``YScale``      amplitude scale factor applied to every I/Q sample
    - ``InputRange``  full-scale input range (V) — kept verbatim
    - ``InputRefImped`` reference impedance (Ω)
    - ``FreqValidMin`` / ``FreqValidMax``  guaranteed in-band range (Hz)
    - ``TimeUtcString``                    recording timestamp (UTC)

The binary file can be many GB on real captures, so we memory-map it instead
of slurping it into RAM. The YScale multiplication is applied lazily inside
:meth:`get_iq_data` so the on-disk samples stay as int16.
"""

from __future__ import annotations

import gc
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_BYTES_PER_SAMPLE = 4  # int16 × (I + Q)


def _parse_metadata(txt_path: Path) -> dict[str, str]:
    """Read the tab-separated ``.bin.txt`` file into a plain ``dict``.

    Lines starting with ``#`` are ignored (the Keysight tooling sometimes
    repeats a key as a comment). Blank lines are skipped too.
    """
    metadata: dict[str, str] = {}
    with txt_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                metadata[parts[0]] = parts[1]
    return metadata


class KeysightBinLoader:
    """Lazy loader for Keysight N5110A IQ recordings.

    Implements the :class:`iq_analyzer.loaders.base.IQLoader` protocol: the
    rest of the viewer can treat a Keysight capture exactly like a R&S WVH/WVD
    file.
    """

    def __init__(self) -> None:
        self.header: dict[str, Any] = {}
        self.data_memmap: np.memmap | None = None
        self.bin_path: Path | None = None
        self.txt_path: Path | None = None
        self.y_scale: float = 1.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ API

    def parse_bin_txt(self, bin_path: str | Path) -> dict[str, Any]:
        """Locate and parse the metadata file that accompanies *bin_path*."""
        bin_path = Path(bin_path)
        if not bin_path.exists():
            raise FileNotFoundError(f"binファイルが見つかりません: {bin_path}")
        self.bin_path = bin_path

        # Keysight always names it <stem>.bin.txt — append, not replace.
        txt_path = bin_path.with_suffix(bin_path.suffix + ".txt")
        if not txt_path.exists():
            raise FileNotFoundError(
                f"対応するメタデータファイルが見つかりません: {txt_path}"
            )
        self.txt_path = txt_path

        raw = _parse_metadata(txt_path)
        if "XDelta" not in raw:
            raise ValueError("メタデータに XDelta が見つかりません")

        x_delta = float(raw["XDelta"])
        if x_delta <= 0:
            raise ValueError(f"XDelta は正の値である必要があります: {x_delta}")

        clock_hz = 1.0 / x_delta
        center_hz = float(raw.get("InputCenter", 0.0))
        self.y_scale = float(raw.get("YScale", 1.0))

        # Derive sample count from the binary file size — the metadata file
        # doesn't carry it explicitly. This matches the WV loader's defensive
        # behaviour against header/data mismatches.
        bin_size = bin_path.stat().st_size
        if bin_size % _BYTES_PER_SAMPLE != 0:
            logger.warning(
                "bin size %d is not a multiple of %d — trailing bytes will be ignored",
                bin_size,
                _BYTES_PER_SAMPLE,
            )
        samples = bin_size // _BYTES_PER_SAMPLE

        # Compose the header in the same shape the rest of the viewer expects
        # (SAMPLES, CLOCK, FREQUENCY, REFLEVEL guaranteed; everything else
        # forwarded for the preview pane).
        header: dict[str, Any] = {
            "TYPE": "Keysight N5110A (16bit LE IQ)",
            "COMPONENTS": "IQ",
            "RESOLUTION": 16,
            "SAMPLES": int(samples),
            "CLOCK": clock_hz,
            "FREQUENCY": center_hz,
            "REFLEVEL": 0.0,  # not present in the Keysight metadata
            "YSCALE": self.y_scale,
            "XDELTA": x_delta,
        }
        # Forward optional metadata fields verbatim so the preview can show them.
        for key in (
            "InputRange",
            "InputRefImped",
            "FreqValidMin",
            "FreqValidMax",
            "InputZoom",
            "XStart",
            "XDomain",
            "XUnit",
            "TimeUtcString",
        ):
            if key in raw:
                header[key] = raw[key]

        self.header = header
        return header

    def open_bin(self) -> np.memmap:
        """Memory-map the ``.bin`` file as int16."""
        if self.bin_path is None:
            raise RuntimeError("先に parse_bin_txt() を実行してください")

        bin_size = self.bin_path.stat().st_size
        elements = (bin_size // _BYTES_PER_SAMPLE) * 2  # each sample = (I, Q)

        self.data_memmap = np.memmap(
            self.bin_path,
            dtype=np.int16,
            mode="r",
            shape=(elements,),
        )
        return self.data_memmap

    def get_iq_data(
        self,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> NDArray[np.complex64]:
        """Return ``[start_sample, end_sample)`` as ``complex64`` (YScale applied)."""
        with self._lock:
            if self.data_memmap is None:
                raise RuntimeError("先に open_bin() を実行してください")

            total = int(self.header["SAMPLES"])
            end = total if end_sample is None else int(end_sample)
            start = max(0, int(start_sample))
            end = min(total, end)

            if start >= end:
                raise ValueError(f"無効な範囲: start={start}, end={end}")

            start_idx = start * 2
            end_idx = end * 2
            interleaved = np.array(self.data_memmap[start_idx:end_idx], dtype=np.int16)

        # YScale lets the user recover absolute IQ amplitudes; matches the
        # reference Keysight viewer behaviour bit-for-bit.
        i = interleaved[0::2].astype(np.float32) * np.float32(self.y_scale)
        q = interleaved[1::2].astype(np.float32) * np.float32(self.y_scale)
        return (i + 1j * q).astype(np.complex64)

    def close(self) -> None:
        """Release the memmap and reset state. Idempotent."""
        if self.data_memmap is not None:
            memmap_ref = self.data_memmap
            self.data_memmap = None
            try:
                inner = getattr(memmap_ref, "_mmap", None)
                if inner is not None:
                    try:
                        inner.close()
                    except OSError as exc:  # pragma: no cover - platform-specific
                        logger.debug("memmap close failed: %s", exc)
                del memmap_ref
                gc.collect()
            except Exception:  # pragma: no cover - defensive
                logger.exception("KeysightBinLoader.close: memmap teardown failed")

        self.header = {}
        self.bin_path = None
        self.txt_path = None
        self.y_scale = 1.0

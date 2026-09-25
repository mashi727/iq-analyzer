"""Rohde & Schwarz ARB waveform (``.wv``, "SMU-WV") loader.

Unlike the WVH/WVD pair, a ``.wv`` file is self-contained: a sequence of
ASCII ``{TAG:value}`` blocks followed by one binary waveform block::

    {TYPE: SMU-WV,<checksum>}{COMMENT:...}{CLOCK:250000000}
    {LEVEL OFFS:<rms dB>,<peak dB>}{SAMPLES:<n>}
    {WAVEFORM-<len>:#<I0 Q0 I1 Q1 ... as int16 LE>}

``<len>`` counts the bytes from ``#`` up to (not including) the closing ``}``,
so the payload is ``len - 1`` bytes of interleaved int16 I/Q. Some tools emit
``WWAVEFORM`` instead of ``WAVEFORM``; both spellings are accepted.

The payload is memory-mapped with a byte offset, so multi-GB files cost only
the pages actually touched by :meth:`get_iq_data`.

The format carries no center frequency or reference level. ``FREQUENCY`` and
``REFLEVEL`` default to 0.0, or are taken from a same-stem ``.wvh`` recording
header if one sits next to the file.
"""

from __future__ import annotations

import gc
import logging
import re
import threading
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_BYTES_PER_SAMPLE = 4  # int16 × (I + Q)
# Header tags (comments, marker lists) are small, but marker lists on long
# waveforms can reach a few MB. Scan up to this many bytes for the waveform tag.
_MAX_HEADER_BYTES = 16 * 1024 * 1024
_SCAN_CHUNK = 64 * 1024

_TAG_PATTERN = re.compile(rb"\{([^:{}]+):([^{}]*)\}")
_WAVEFORM_PATTERN = re.compile(rb"\{(W?WAVEFORM)-(\d+):\s*#")


def _payload_randomness(memmap: np.memmap, n_windows: int = 4, window: int = 1 << 20) -> float:
    """Return the minimum byte entropy (bits/byte) over a few payload windows.

    A physical IQ recording stored as int16 has a peaked (roughly Gaussian)
    amplitude distribution, so its byte entropy stays well below 8. A payload
    that is encrypted or otherwise scrambled is indistinguishable from uniform
    random bytes (≈8.0 bits/byte). Several windows are sampled so that a zero
    padded lead-in does not dominate the estimate.
    """
    raw = memmap.view(np.uint8)
    size = raw.shape[0]
    if size < window:
        window = size
    if window == 0:
        return 0.0
    entropies = []
    for k in range(n_windows):
        start = (size - window) * (k + 1) // (n_windows + 1)
        counts = np.bincount(np.asarray(raw[start : start + window]), minlength=256)
        p = counts[counts > 0] / window
        entropies.append(float(-(p * np.log2(p)).sum()))
    return min(entropies)


class SMUWVLoader:
    """Lazy loader for Rohde & Schwarz ARB ``.wv`` waveform files.

    Implements the :class:`iq_analyzer.loaders.base.IQLoader` protocol.
    """

    # Above this byte entropy the payload is treated as "not plain IQ".
    RANDOM_ENTROPY_THRESHOLD = 7.99

    def __init__(self) -> None:
        self.header: dict[str, Any] = {}
        self.data_memmap: np.memmap | None = None
        self.wv_path: Path | None = None
        self.payload_offset: int = 0
        self.payload_bytes: int = 0
        self.header_mismatch: bool = False
        self.payload_entropy: float | None = None
        self._lock = threading.Lock()

    @property
    def payload_looks_random(self) -> bool:
        """True when the payload is statistically indistinguishable from random bytes.

        This typically means an encrypted waveform: it can be displayed, but
        the samples do not represent the recorded signal.
        """
        return (
            self.payload_entropy is not None
            and self.payload_entropy > self.RANDOM_ENTROPY_THRESHOLD
        )

    def parse_wv(self, wv_path: str | Path) -> dict[str, Any]:
        """Parse the ASCII tags and locate the binary waveform block."""
        wv_path = Path(wv_path)
        if not wv_path.exists():
            raise FileNotFoundError(f"WVファイルが見つかりません: {wv_path}")
        self.wv_path = wv_path
        file_size = wv_path.stat().st_size

        head = b""
        match = None
        with wv_path.open("rb") as fh:
            while len(head) < _MAX_HEADER_BYTES:
                chunk = fh.read(_SCAN_CHUNK)
                if not chunk:
                    break
                head += chunk
                match = _WAVEFORM_PATTERN.search(head)
                if match:
                    break
        if not head.startswith(b"{TYPE:"):
            raise ValueError(f"SMU-WV形式ではありません（{{TYPE:}} で始まらない）: {wv_path.name}")
        if match is None:
            raise ValueError(f"WAVEFORMタグが見つかりません: {wv_path.name}")

        header: dict[str, Any] = {}
        for key, value in _TAG_PATTERN.findall(head[: match.start()]):
            header[key.decode("latin-1").strip()] = value.decode("latin-1").strip()

        if "CLOCK" not in header:
            raise ValueError("必須ヘッダー情報が不足: CLOCK")

        declared_len = int(match.group(2))
        self.payload_offset = match.end()  # first byte after '#'
        # The declared length includes the '#'. Never trust it beyond the file
        # end (minus the closing '}'), in case the file was truncated.
        available = max(0, file_size - self.payload_offset - 1)
        self.payload_bytes = min(declared_len - 1, available)
        payload_samples = self.payload_bytes // _BYTES_PER_SAMPLE
        if payload_samples == 0:
            raise ValueError(f"波形データが空です: {wv_path.name}")

        level = [s.strip() for s in header.get("LEVEL OFFS", "").split(",")]
        # "{TYPE: SMU-WV,<checksum>}" — split the checksum off the type name.
        type_name, _, checksum = header.get("TYPE", "SMU-WV").partition(",")
        header["TYPE"] = type_name.strip()
        if checksum.strip():
            header["CHECKSUM"] = checksum.strip()
        header["WAVEFORM_TAG"] = match.group(1).decode("ascii")
        header["CLOCK"] = float(header["CLOCK"])
        header["SAMPLES_DECLARED"] = int(header["SAMPLES"]) if "SAMPLES" in header else None
        header["SAMPLES"] = payload_samples
        header["RESOLUTION"] = 16
        header["COMPONENTS"] = "IQ"
        header["FREQUENCY"] = 0.0
        header["REFLEVEL"] = 0.0
        if len(level) == 2 and all(level):
            header["RMS_OFFSET_DB"] = float(level[0])
            header["PEAK_OFFSET_DB"] = float(level[1])

        # The ARB format has no RF metadata. When the recording's own .wvh sits
        # next to the file under the same stem, borrow center frequency and
        # reference level from it.
        companion = wv_path.with_suffix(".wvh")
        if companion.exists():
            text = companion.read_text(encoding="utf-8", errors="ignore")
            rec = {k: v for k, v in re.findall(r"\{([^:]+):([^}]+)\}", text)}
            if "FREQUENCY" in rec:
                header["FREQUENCY"] = float(rec["FREQUENCY"])
            if "REFLEVEL" in rec:
                header["REFLEVEL"] = float(rec["REFLEVEL"])

        declared = header["SAMPLES_DECLARED"]
        self.header_mismatch = declared is not None and declared != payload_samples
        if self.header_mismatch:
            logger.warning(
                "SAMPLESタグ(%d)と波形ブロックの実サンプル数(%d)が一致しません。実データ長を採用します",
                declared,
                payload_samples,
            )

        self.header = header
        return header

    def open_wv(self) -> np.memmap:
        """Memory-map the waveform payload and estimate whether it is plain IQ."""
        if self.wv_path is None:
            raise RuntimeError("先にparse_wv()を実行してください")

        n_elements = int(self.header["SAMPLES"]) * 2
        self.data_memmap = np.memmap(
            self.wv_path,
            dtype="<i2",
            mode="r",
            offset=self.payload_offset,
            shape=(n_elements,),
        )
        self.payload_entropy = _payload_randomness(self.data_memmap)
        if self.payload_looks_random:
            logger.warning(
                "波形データのバイトエントロピーが %.5f bit/byte で一様乱数と区別できません"
                "（暗号化波形の可能性）",
                self.payload_entropy,
            )
        return self.data_memmap

    def get_iq_data(
        self,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> NDArray[np.complex64]:
        """Return IQ samples in ``[start_sample, end_sample)`` as ``complex64``."""
        with self._lock:
            if self.data_memmap is None:
                raise RuntimeError("先にopen_wv()を実行してください")

            total_samples = int(self.header["SAMPLES"])
            end = total_samples if end_sample is None else int(end_sample)
            start = max(0, int(start_sample))
            end = min(total_samples, end)
            if start >= end:
                raise ValueError(f"無効な範囲: start={start}, end={end}")

            # Copy out of the memmap so we don't keep a reference into the file.
            iq_interleaved = np.array(self.data_memmap[start * 2 : end * 2], dtype=np.int16)

        out = np.empty(end - start, dtype=np.complex64)
        out.real = iq_interleaved[0::2]
        out.imag = iq_interleaved[1::2]
        return out

    def close(self) -> None:
        """Release the memmap and reset state. Safe to call repeatedly."""
        if self.data_memmap is not None:
            memmap_ref = self.data_memmap
            self.data_memmap = None
            try:
                inner = getattr(memmap_ref, "_mmap", None)
                if inner is not None:
                    try:
                        inner.close()
                    except (OSError, BufferError) as exc:  # pragma: no cover - platform-specific
                        logger.debug("memmap close failed: %s", exc)
                del memmap_ref
                gc.collect()
            except Exception:  # pragma: no cover - defensive
                logger.exception("SMUWVLoader.close: memmap teardown failed")

        self.header = {}
        self.wv_path = None
        self.payload_entropy = None

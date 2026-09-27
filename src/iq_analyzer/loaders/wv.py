"""Rohde & Schwarz WVH/WVD (RAW16LE) loader.

The WVH file is a small text file containing ``{KEY:VALUE}`` metadata pairs.
The companion WVD file holds the IQ data as 16-bit little-endian integers
with I and Q components interleaved::

    [I0, Q0, I1, Q1, ..., In, Qn]

For very large data sets the WVD file is opened via :func:`numpy.memmap` so
that only the slices actually touched are paged into RAM.
"""

from __future__ import annotations

import gc
import logging
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


_HEADER_PATTERN = re.compile(r"\{([^:]+):([^}]+)\}")
_REQUIRED_KEYS = ("TYPE", "SAMPLES", "CLOCK")
_BYTES_PER_SAMPLE = 4  # int16 I + int16 Q


def _declared_samples(wvh_path: Path) -> int | None:
    try:
        header = dict(_HEADER_PATTERN.findall(wvh_path.read_text(encoding="utf-8", errors="ignore")))
        return int(header["SAMPLES"])
    except (OSError, KeyError, ValueError):
        return None


def _pick(candidates: list[Path], what: str, anchor: Path) -> Path:
    if len(candidates) == 1:
        return candidates[0]
    names = ", ".join(sorted(c.name for c in candidates)) or "なし"
    raise FileNotFoundError(
        f"{anchor.name} に対応する{what}を特定できません（候補: {names}）。"
        f"同じファイル名にそろえてください"
    )


def find_wvd_for_wvh(wvh_path: Path, samples: int) -> Path:
    """Locate the data file belonging to *wvh_path*.

    The instrument writes ``name.wvh`` + ``name.wvd``, but renamed recordings
    (e.g. ``X_header.wvh`` + ``X_data.wvd``) are common. Rules, in order:

    1. same stem;
    2. the only ``.wvd`` in the folder whose size is exactly ``SAMPLES × 4``;
    3. the folder holds exactly one ``.wvh`` and one ``.wvd`` (size mismatch is
       then reported by :meth:`WVFileLoader.open_wvd` as usual).
    """
    same = wvh_path.with_suffix(".wvd")
    if same.exists():
        return same
    folder = wvh_path.parent
    wvds = [p for p in folder.glob("*.wvd") if p.is_file()]
    by_size = [p for p in wvds if p.stat().st_size == samples * _BYTES_PER_SAMPLE]
    if len(by_size) == 1:
        return by_size[0]
    if len(wvds) == 1 and len(list(folder.glob("*.wvh"))) == 1:
        return wvds[0]
    return _pick(by_size or wvds, "WVDファイル", wvh_path)


def find_wvh_for_wvd(wvd_path: Path) -> Path:
    """Inverse of :func:`find_wvd_for_wvh`: the header describing *wvd_path*."""
    same = wvd_path.with_suffix(".wvh")
    if same.exists():
        return same
    folder = wvd_path.parent
    size = wvd_path.stat().st_size
    wvhs = [p for p in folder.glob("*.wvh") if p.is_file()]
    by_size = [p for p in wvhs if (_declared_samples(p) or -1) * _BYTES_PER_SAMPLE == size]
    if len(by_size) == 1:
        return by_size[0]
    if len(wvhs) == 1 and len(list(folder.glob("*.wvd"))) == 1:
        return wvhs[0]
    return _pick(by_size or wvhs, "WVHファイル", wvd_path)


def resolve_wvh(path: str | Path) -> Path:
    """Accept either half of a pair and return the ``.wvh`` to parse."""
    path = Path(path)
    return find_wvh_for_wvd(path) if path.suffix.lower() == ".wvd" else path


class WVFileLoader:
    """Lazy loader for Rohde & Schwarz WVH/WVD pairs.

    The companion ``.wvd`` file is opened with :class:`numpy.memmap`, so the
    process RSS stays small even for multi-GB recordings — the OS pages in
    only the chunks actually accessed via :meth:`get_iq_data`.

    The loader is safe to call from multiple threads: a :class:`threading.Lock`
    guards every memmap access.
    """

    def __init__(self) -> None:
        self.header: dict[str, Any] = {}
        self.data_memmap: np.memmap | None = None
        self.wvd_path: Path | None = None
        self.wvh_path: Path | None = None
        self.header_mismatch: bool = False
        self._lock = threading.Lock()

    def parse_wvh(self, wvh_path: str | Path) -> dict[str, Any]:
        """Parse a WVH header file and remember the companion WVD path."""
        wvh_path = Path(wvh_path)
        self.wvh_path = wvh_path

        if not wvh_path.exists():
            raise FileNotFoundError(f"WVHファイルが見つかりません: {wvh_path}")

        content = wvh_path.read_text(encoding="utf-8")
        header: dict[str, Any] = dict(_HEADER_PATTERN.findall(content))

        for key in _REQUIRED_KEYS:
            if key not in header:
                raise ValueError(f"必須ヘッダー情報が不足: {key}")

        header["SAMPLES"] = int(header["SAMPLES"])
        header["CLOCK"] = float(header["CLOCK"])
        header["FREQUENCY"] = float(header.get("FREQUENCY", 0.0))
        header["RESOLUTION"] = int(header.get("RESOLUTION", 16))
        header["REFLEVEL"] = float(header.get("REFLEVEL", 0.0))

        self.header = header
        self.wvd_path = find_wvd_for_wvh(wvh_path, header["SAMPLES"])
        if self.wvd_path.stem != wvh_path.stem:
            logger.info("WVHとWVDの名前が異なるため、サイズ一致で対応付けました: %s ↔ %s",
                        wvh_path.name, self.wvd_path.name)

        return header

    def open_wvd(self) -> np.memmap:
        """Open the WVD as a memory map.

        Some recordings have a ``SAMPLES`` value in the WVH that doesn't match
        the actual data size — we trust the file size and patch the header
        in-memory, raising :attr:`header_mismatch` for the UI to optionally
        rewrite the WVH later.
        """
        if self.wvd_path is None:
            raise RuntimeError("先にparse_wvh()を実行してください")

        if self.header["TYPE"] != "RAW16LE":
            raise ValueError(f"未対応のデータ型: {self.header['TYPE']}")

        dtype = np.int16
        bytes_per_element = 2

        actual_file_size = self.wvd_path.stat().st_size
        actual_elements = actual_file_size // bytes_per_element
        header_elements = self.header["SAMPLES"] * 2  # I, Q interleaved

        if actual_elements != header_elements:
            logger.info(
                "WVH/WVDサンプル数不一致を検出。WVDの実サイズに合わせて補正します "
                "(header=%d, actual=%d)",
                header_elements // 2,
                actual_elements // 2,
            )
            self.header["SAMPLES"] = actual_elements // 2
            self.header_mismatch = True
        else:
            self.header_mismatch = False

        self.data_memmap = np.memmap(
            self.wvd_path,
            dtype=dtype,
            mode="r",
            shape=(actual_elements,),
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
                raise RuntimeError("先にopen_wvd()を実行してください")

            total_samples = int(self.header["SAMPLES"])
            end = total_samples if end_sample is None else int(end_sample)
            start = max(0, int(start_sample))
            end = min(total_samples, end)

            if start >= end:
                raise ValueError(f"無効な範囲: start={start}, end={end}")

            start_idx = start * 2
            end_idx = end * 2

            # Copy out of the memmap so we don't keep a reference into the file.
            iq_interleaved = np.array(self.data_memmap[start_idx:end_idx], dtype=np.int16)

        i_data = iq_interleaved[0::2].astype(np.float32)
        q_data = iq_interleaved[1::2].astype(np.float32)
        return (i_data + 1j * q_data).astype(np.complex64)

    def fix_wvh_header(self) -> None:
        """Rewrite the WVH file with the loader's current ``SAMPLES`` value.

        The original WVH is preserved as ``<name>.wvh.bak``.
        """
        if self.wvh_path is None:
            raise RuntimeError("WVHファイルが読み込まれていません")

        backup_path = self.wvh_path.with_suffix(".wvh.bak")
        shutil.copy2(self.wvh_path, backup_path)
        self.write_wvh_header(self.wvh_path, self.header)

    @staticmethod
    def write_wvh_header(wvh_path: str | Path, header: dict[str, Any]) -> None:
        """Serialize ``header`` to a Rohde & Schwarz-compatible WVH file."""
        wvh_path = Path(wvh_path)
        parts: list[str] = []

        parts.append("{COPYRIGHT:2025 Rohde&Schwarz IQW}")
        parts.append("{FWVERSION:" + str(header.get("FWVERSION", "3.2.3")) + "}")

        if "DATE" in header:
            parts.append("{DATE:" + str(header["DATE"]) + "}")
        else:
            parts.append("{DATE:" + datetime.now().strftime("%Y-%m-%d;%H:%M:%S") + "}")

        parts.append("{TYPE:" + str(header.get("TYPE", "RAW16LE")) + "}")
        parts.append("{COMPONENTS:" + str(header.get("COMPONENTS", "IQ")) + "}")
        parts.append(f"{{CLOCK:{float(header['CLOCK']):.6f}}}")

        if "CHANNAME0" in header:
            parts.append("{CHANNAME0:" + str(header["CHANNAME0"]) + "}")

        parts.append("{RESOLUTION:" + str(header.get("RESOLUTION", 16)) + "}")
        parts.append(f"{{FREQUENCY:{float(header.get('FREQUENCY', 0.0)):.6f}}}")

        reflevel = header.get("REFLEVEL", 0.0)
        if isinstance(reflevel, (int, float)):
            parts.append(f"{{REFLEVEL:{float(reflevel):.6f}}}")
        else:
            parts.append("{REFLEVEL:0.000000}")

        parts.append("{SAMPLES:" + str(int(header["SAMPLES"])) + "}")

        wvh_path.write_text("".join(parts), encoding="utf-8")

    @staticmethod
    def save_region_as_wv(
        save_path: str | Path,
        iq_data: NDArray[np.complexfloating],
        header_template: dict[str, Any],
    ) -> tuple[Path, Path]:
        """Save a complex IQ slice as a fresh WVH/WVD pair.

        ``save_path`` may include either suffix (``.wvh``/``.wvd``) or none.
        Returns the resulting ``(wvh_path, wvd_path)``.
        """
        save_path = Path(save_path)
        base_path = save_path.with_suffix("") if save_path.suffix in {".wvh", ".wvd"} else save_path

        wvh_path = base_path.with_suffix(".wvh")
        wvd_path = base_path.with_suffix(".wvd")

        new_header = dict(header_template)
        new_header["SAMPLES"] = len(iq_data)

        i_data = np.real(iq_data).astype(np.int16)
        q_data = np.imag(iq_data).astype(np.int16)

        interleaved = np.empty(len(iq_data) * 2, dtype=np.int16)
        interleaved[0::2] = i_data
        interleaved[1::2] = q_data

        with wvd_path.open("wb") as f:
            interleaved.tofile(f)

        WVFileLoader.write_wvh_header(wvh_path, new_header)
        return wvh_path, wvd_path

    def close(self) -> None:
        """Release the memmap and reset state. Safe to call repeatedly."""
        if self.data_memmap is not None:
            memmap_ref = self.data_memmap
            self.data_memmap = None
            try:
                # The private ``_mmap`` attribute is the most reliable way to
                # release the underlying OS handle promptly; on Windows this
                # matters because the file stays locked otherwise.
                inner = getattr(memmap_ref, "_mmap", None)
                if inner is not None:
                    try:
                        inner.close()
                    except OSError as exc:  # pragma: no cover - platform-specific
                        logger.debug("memmap close failed: %s", exc)
                del memmap_ref
                gc.collect()
            except Exception:  # pragma: no cover - defensive
                logger.exception("WVFileLoader.close: memmap teardown failed")

        self.header = {}
        self.wvd_path = None
        self.wvh_path = None

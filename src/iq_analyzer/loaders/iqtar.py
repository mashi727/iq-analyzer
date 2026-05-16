"""Rohde & Schwarz ``iq.tar`` loader.

``iq.tar`` is a tar archive that bundles:

* an XML metadata file (``*.xml``)
* an interleaved IQ binary (``*.complexNch.float32`` / ``...float64``)
* an optional XSLT stylesheet for browser preview

The archive is extracted to a temporary directory on parse, and the binary
data is opened via :class:`numpy.memmap` so multi-GB recordings stay out of
RSS.
"""

from __future__ import annotations

import gc
import logging
import shutil
import tarfile
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


_DTYPE_BY_NAME: dict[str, tuple[type, int]] = {
    "float32": (np.float32, 4),
    "float64": (np.float64, 8),
}


class IQTarLoader:
    """Lazy loader for Rohde & Schwarz ``iq.tar`` archives.

    The on-disk archive is extracted into a :class:`tempfile.mkdtemp` directory
    on :meth:`parse_iqtar`; the directory is removed in :meth:`close`. Concurrent
    access to the memmap is guarded by a :class:`threading.Lock`.
    """

    def __init__(self) -> None:
        self.header: dict[str, Any] = {}
        self.data_memmap: np.memmap | None = None
        self.tar_path: Path | None = None
        self.temp_dir: str | None = None
        self.data_file_path: Path | None = None
        self.xml_file_path: Path | None = None
        self._lock = threading.Lock()

    def parse_iqtar(self, tar_path: str | Path) -> dict[str, Any]:
        """Extract the archive and parse the XML metadata."""
        tar_path = Path(tar_path)
        self.tar_path = tar_path

        if not tar_path.exists():
            raise FileNotFoundError(f"iq.tarファイルが見つかりません: {tar_path}")

        self.temp_dir = tempfile.mkdtemp(prefix="iqtar_")

        try:
            with tarfile.open(tar_path, "r") as tar:
                # Python 3.12+ requires an explicit filter; ``data`` rejects
                # absolute paths and links escaping the temp dir.
                tar.extractall(self.temp_dir, filter="data")
        except tarfile.TarError as exc:
            raise RuntimeError(f"tarファイル展開エラー: {exc}") from exc

        temp_path = Path(self.temp_dir)
        xml_files = sorted(temp_path.glob("*.xml"))
        if not xml_files:
            raise FileNotFoundError("XMLメタデータファイルが見つかりません")
        self.xml_file_path = xml_files[0]

        try:
            tree = ET.parse(self.xml_file_path)
        except ET.ParseError as exc:
            raise RuntimeError(f"XML解析エラー: {exc}") from exc

        header = self._extract_metadata(tree.getroot())
        self.header = header

        data_filename = header["DATAFILENAME"]
        data_file_path = temp_path / data_filename
        if not data_file_path.exists():
            raise FileNotFoundError(
                f"バイナリデータファイルが見つかりません: {data_file_path}"
            )
        self.data_file_path = data_file_path

        return header

    @staticmethod
    def _extract_metadata(root: ET.Element) -> dict[str, Any]:
        """Pull the subset of XML fields the viewer cares about."""

        def _text(elem: ET.Element | None, default: str = "") -> str:
            return (elem.text or default) if elem is not None else default

        header: dict[str, Any] = {
            "NAME": _text(root.find("Name"), "Unknown"),
            "SAMPLES": int(_text(root.find("Samples"), "0")),
            "CLOCK": float(_text(root.find("Clock"), "1.0")),
            "FORMAT": _text(root.find("Format"), "complex"),
            "DATATYPE": _text(root.find("DataType"), "float32"),
        }

        datafile_elem = root.find("DataFilename")
        if datafile_elem is None or not datafile_elem.text:
            raise ValueError("DataFilename要素が見つかりません")
        header["DATAFILENAME"] = datafile_elem.text

        center_freq = 0.0
        reflevel = 0.0
        userdata = root.find(".//UserData")
        if userdata is not None:
            rs_elem = userdata.find(".//RohdeSchwarz")
            if rs_elem is not None:
                mandatory = rs_elem.find(".//DataImportExport_MandatoryData")
                if mandatory is not None:
                    cf_elem = mandatory.find(".//CenterFrequency")
                    if cf_elem is not None and cf_elem.text:
                        center_freq = float(cf_elem.text)

                if center_freq == 0.0:
                    sa_elem = rs_elem.find(".//SpectrumAnalyzer")
                    if sa_elem is not None:
                        cf_elem = sa_elem.find(".//CenterFrequency")
                        if cf_elem is not None and cf_elem.text:
                            center_freq = float(cf_elem.text)

                optional = rs_elem.find(".//DataImportExport_OptionalData")
                if optional is not None:
                    for key_elem in optional.findall(".//Key"):
                        if key_elem.get("name") == "Ch1_RefLevel[dBm]" and key_elem.text:
                            reflevel = float(key_elem.text)
                            break

        header["FREQUENCY"] = center_freq
        header["REFLEVEL"] = reflevel
        return header

    def open_data(self) -> np.memmap:
        """Open the binary data file as a memmap."""
        if self.data_file_path is None:
            raise RuntimeError("先にparse_iqtar()を実行してください")

        datatype_name = self.header["DATATYPE"]
        if datatype_name not in _DTYPE_BY_NAME:
            raise ValueError(f"未対応のデータ型: {datatype_name}")
        dtype, bytes_per_element = _DTYPE_BY_NAME[datatype_name]

        format_type = self.header["FORMAT"]
        actual_file_size = self.data_file_path.stat().st_size
        actual_elements = actual_file_size // bytes_per_element

        if format_type == "complex":
            expected_elements = self.header["SAMPLES"] * 2
        else:
            expected_elements = self.header["SAMPLES"]

        if actual_elements != expected_elements:
            corrected = actual_elements // 2 if format_type == "complex" else actual_elements
            logger.info(
                "iq.tarサンプル数不一致を検出。実ファイルサイズに合わせて補正します "
                "(header=%d, actual=%d)",
                self.header["SAMPLES"],
                corrected,
            )
            self.header["SAMPLES"] = corrected

        self.data_memmap = np.memmap(
            self.data_file_path,
            dtype=dtype,
            mode="r",
            shape=(actual_elements,),
        )
        return self.data_memmap

    def get_iq_data(
        self,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> NDArray[np.complexfloating]:
        """Return IQ samples in ``[start_sample, end_sample)`` as complex array."""
        with self._lock:
            if self.data_memmap is None:
                raise RuntimeError("先にopen_data()を実行してください")

            total_samples = int(self.header["SAMPLES"])
            end = total_samples if end_sample is None else int(end_sample)
            start = max(0, int(start_sample))
            end = min(total_samples, end)

            if start >= end:
                raise ValueError(f"無効な範囲: start={start}, end={end}")

            format_type = self.header["FORMAT"]
            if format_type != "complex":
                raise ValueError(f"未対応のフォーマット: {format_type}")

            start_idx = start * 2
            end_idx = end * 2
            iq_interleaved = np.array(self.data_memmap[start_idx:end_idx])

        i_data = iq_interleaved[0::2]
        q_data = iq_interleaved[1::2]
        return i_data + 1j * q_data

    def close(self) -> None:
        """Release the memmap and remove the temporary directory."""
        if self.data_memmap is not None:
            memmap_ref = self.data_memmap
            self.data_memmap = None
            try:
                inner = getattr(memmap_ref, "_mmap", None)
                if inner is not None:
                    try:
                        inner.close()
                    except OSError as exc:  # pragma: no cover
                        logger.debug("memmap close failed: %s", exc)
                del memmap_ref
                gc.collect()
            except Exception:  # pragma: no cover - defensive
                logger.exception("IQTarLoader.close: memmap teardown failed")

        if self.temp_dir is not None and Path(self.temp_dir).exists():
            try:
                shutil.rmtree(self.temp_dir)
            except OSError as exc:  # pragma: no cover
                logger.debug("failed to remove temp dir %s: %s", self.temp_dir, exc)

        self.header = {}
        self.tar_path = None
        self.temp_dir = None
        self.data_file_path = None
        self.xml_file_path = None

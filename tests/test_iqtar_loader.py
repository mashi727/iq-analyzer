"""Tests for ``iq_analyzer.loaders.iqtar.IQTarLoader``."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.loaders import IQTarLoader, open_iq_file

_XML_TEMPLATE = """<?xml version='1.0' encoding='UTF-8'?>
<RS_IQ_TAR_FileFormat fileFormatVersion="3">
  <Name>FSW-43</Name>
  <DateTime>2017-10-25 23:42:23</DateTime>
  <Samples>{samples}</Samples>
  <Clock unit="Hz">{clock}</Clock>
  <Format>complex</Format>
  <DataType>float32</DataType>
  <DataFilename>{data_filename}</DataFilename>
  <UserData>
    <RohdeSchwarz>
      <DataImportExport_MandatoryData>
        <CenterFrequency unit="Hz">{center_freq}</CenterFrequency>
      </DataImportExport_MandatoryData>
      <DataImportExport_OptionalData>
        <Key name="Ch1_RefLevel[dBm]">{reflevel}</Key>
      </DataImportExport_OptionalData>
    </RohdeSchwarz>
  </UserData>
</RS_IQ_TAR_FileFormat>
"""


def _build_iqtar(
    tar_path: Path,
    *,
    samples: int,
    clock: float = 2_500_000_000.0,
    center_freq: float = 9_000_000_000.0,
    reflevel: float = 0.0,
) -> np.ndarray:
    """Create a minimal ``iq.tar`` and return the interleaved complex64 array."""
    rng = np.random.default_rng(123)
    i = rng.standard_normal(samples).astype(np.float32)
    q = rng.standard_normal(samples).astype(np.float32)
    interleaved = np.empty(samples * 2, dtype=np.float32)
    interleaved[0::2] = i
    interleaved[1::2] = q

    data_filename = "data.complex1ch.float32"
    xml = _XML_TEMPLATE.format(
        samples=samples,
        clock=f"{clock:.6f}",
        center_freq=f"{center_freq:.6f}",
        reflevel=reflevel,
        data_filename=data_filename,
    ).encode("utf-8")

    data_bytes = interleaved.tobytes()

    with tarfile.open(tar_path, "w") as tar:
        xml_info = tarfile.TarInfo(name="meta.xml")
        xml_info.size = len(xml)
        tar.addfile(xml_info, io.BytesIO(xml))

        data_info = tarfile.TarInfo(name=data_filename)
        data_info.size = len(data_bytes)
        tar.addfile(data_info, io.BytesIO(data_bytes))

    return (i + 1j * q).astype(np.complex64)


def test_parse_and_read(tmp_path: Path) -> None:
    tar_path = tmp_path / "sample.iq.tar"
    expected = _build_iqtar(tar_path, samples=512)

    loader = IQTarLoader()
    header = loader.parse_iqtar(tar_path)

    assert header["SAMPLES"] == 512
    assert header["FORMAT"] == "complex"
    assert header["DATATYPE"] == "float32"
    assert header["CLOCK"] == pytest.approx(2_500_000_000.0)
    assert header["FREQUENCY"] == pytest.approx(9_000_000_000.0)

    loader.open_data()
    iq = loader.get_iq_data()
    np.testing.assert_allclose(iq, expected, rtol=0, atol=0)
    loader.close()
    assert loader.temp_dir is None


def test_open_iq_file_dispatches_to_iqtar(tmp_path: Path) -> None:
    tar_path = tmp_path / "dispatch.iq.tar"
    expected = _build_iqtar(tar_path, samples=64)
    loader = open_iq_file(tar_path)
    try:
        np.testing.assert_allclose(loader.get_iq_data(), expected)
    finally:
        loader.close()


def test_close_removes_temp_dir(tmp_path: Path) -> None:
    tar_path = tmp_path / "cleanup.iq.tar"
    _build_iqtar(tar_path, samples=32)
    loader = IQTarLoader()
    loader.parse_iqtar(tar_path)
    temp_dir = loader.temp_dir
    assert temp_dir is not None and Path(temp_dir).exists()
    loader.open_data()
    loader.close()
    assert not Path(temp_dir).exists()


def test_invalid_range_raises(tmp_path: Path) -> None:
    tar_path = tmp_path / "range.iq.tar"
    _build_iqtar(tar_path, samples=128)
    loader = IQTarLoader()
    loader.parse_iqtar(tar_path)
    loader.open_data()
    with pytest.raises(ValueError):
        loader.get_iq_data(start_sample=100, end_sample=10)
    loader.close()

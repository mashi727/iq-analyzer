"""Tests for ``iq_analyzer.loaders.wv.WVFileLoader``."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.loaders import WVFileLoader, open_iq_file


def _write_wv_pair(
    base: Path,
    *,
    samples: int,
    clock: float = 32_000_000.0,
    center_freq: float = 1_000_000.0,
    reflevel: float = -10.0,
) -> tuple[Path, Path, np.ndarray]:
    """Create a tiny ``.wvh`` / ``.wvd`` pair and return the synthetic IQ array."""
    rng = np.random.default_rng(42)
    i = rng.integers(-1000, 1000, size=samples, dtype=np.int16)
    q = rng.integers(-1000, 1000, size=samples, dtype=np.int16)

    wvd_path = base.with_suffix(".wvd")
    interleaved = np.empty(samples * 2, dtype=np.int16)
    interleaved[0::2] = i
    interleaved[1::2] = q
    interleaved.tofile(wvd_path)

    wvh_path = base.with_suffix(".wvh")
    WVFileLoader.write_wvh_header(
        wvh_path,
        {
            "TYPE": "RAW16LE",
            "COMPONENTS": "IQ",
            "CLOCK": clock,
            "RESOLUTION": 16,
            "FREQUENCY": center_freq,
            "REFLEVEL": reflevel,
            "SAMPLES": samples,
        },
    )

    iq = (i.astype(np.float32) + 1j * q.astype(np.float32)).astype(np.complex64)
    return wvh_path, wvd_path, iq


def test_round_trip_parse_and_read(tmp_path: Path) -> None:
    wvh_path, _, expected = _write_wv_pair(tmp_path / "sample", samples=1024)

    loader = WVFileLoader()
    header = loader.parse_wvh(wvh_path)

    assert header["SAMPLES"] == 1024
    assert header["TYPE"] == "RAW16LE"
    assert header["CLOCK"] == pytest.approx(32_000_000.0)
    assert header["FREQUENCY"] == pytest.approx(1_000_000.0)
    assert header["REFLEVEL"] == pytest.approx(-10.0)

    loader.open_wvd()
    iq = loader.get_iq_data()
    np.testing.assert_array_equal(iq, expected)
    loader.close()


def test_get_iq_data_range(tmp_path: Path) -> None:
    wvh_path, _, expected = _write_wv_pair(tmp_path / "range", samples=256)

    loader = WVFileLoader()
    loader.parse_wvh(wvh_path)
    loader.open_wvd()

    chunk = loader.get_iq_data(start_sample=10, end_sample=42)
    np.testing.assert_array_equal(chunk, expected[10:42])

    with pytest.raises(ValueError):
        loader.get_iq_data(start_sample=100, end_sample=50)

    loader.close()


def test_header_mismatch_corrects_samples(tmp_path: Path) -> None:
    """If the WVH ``SAMPLES`` lies, ``open_wvd`` must trust the WVD file size."""
    wvh_path, _, _ = _write_wv_pair(tmp_path / "mismatch", samples=128)

    # Overwrite the WVH with an inflated SAMPLES value.
    WVFileLoader.write_wvh_header(
        wvh_path,
        {
            "TYPE": "RAW16LE",
            "CLOCK": 32_000_000.0,
            "RESOLUTION": 16,
            "FREQUENCY": 0.0,
            "REFLEVEL": 0.0,
            "SAMPLES": 999_999,
        },
    )

    loader = WVFileLoader()
    loader.parse_wvh(wvh_path)
    loader.open_wvd()
    assert loader.header_mismatch is True
    assert loader.header["SAMPLES"] == 128

    loader.fix_wvh_header()
    assert wvh_path.with_suffix(".wvh.bak").exists()

    # Reloading the fixed header should agree with the WVD again.
    fresh = WVFileLoader()
    fresh.parse_wvh(wvh_path)
    fresh.open_wvd()
    assert fresh.header_mismatch is False
    assert fresh.header["SAMPLES"] == 128
    fresh.close()
    loader.close()


def test_save_region_round_trip(tmp_path: Path) -> None:
    wvh_path, _, _ = _write_wv_pair(tmp_path / "src", samples=64)
    loader = WVFileLoader()
    loader.parse_wvh(wvh_path)
    loader.open_wvd()
    region = loader.get_iq_data(start_sample=10, end_sample=50)

    out_wvh, out_wvd = WVFileLoader.save_region_as_wv(
        tmp_path / "out", region, loader.header
    )
    assert out_wvh.exists() and out_wvd.exists()

    reloaded = WVFileLoader()
    reloaded.parse_wvh(out_wvh)
    reloaded.open_wvd()
    assert reloaded.header["SAMPLES"] == 40
    np.testing.assert_array_equal(reloaded.get_iq_data(), region)
    reloaded.close()
    loader.close()


def test_open_iq_file_dispatches_to_wv(tmp_path: Path) -> None:
    wvh_path, _, expected = _write_wv_pair(tmp_path / "dispatch", samples=32)
    loader = open_iq_file(wvh_path)
    try:
        iq = loader.get_iq_data()  # type: ignore[attr-defined]
        np.testing.assert_array_equal(iq, expected)
    finally:
        loader.close()


def test_parse_rejects_missing_required_keys(tmp_path: Path) -> None:
    bad = tmp_path / "bad.wvh"
    bad.write_text("{TYPE:RAW16LE}{CLOCK:1.0}", encoding="utf-8")
    # Missing SAMPLES — and the companion WVD doesn't exist either, but the
    # SAMPLES check happens first.
    loader = WVFileLoader()
    with pytest.raises(ValueError, match="SAMPLES"):
        loader.parse_wvh(bad)


def test_close_is_idempotent(tmp_path: Path) -> None:
    wvh_path, _, _ = _write_wv_pair(tmp_path / "idempotent", samples=16)
    loader = WVFileLoader()
    loader.parse_wvh(wvh_path)
    loader.open_wvd()
    loader.close()
    loader.close()  # must not raise
    assert loader.data_memmap is None

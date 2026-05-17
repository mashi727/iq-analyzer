"""Tests for ``iq_analyzer.loaders.keysight.KeysightBinLoader``."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.loaders import KeysightBinLoader, open_iq_file


def _write_keysight_pair(
    base: Path,
    *,
    samples: int,
    x_delta: float = 1 / 32_000_000,
    center_hz: float = 1.0e9,
    y_scale: float = 1.0e-6,
    extra_metadata: dict[str, str] | None = None,
) -> tuple[Path, Path, np.ndarray, np.ndarray]:
    """Create a tiny ``.bin`` + ``.bin.txt`` pair.

    Returns ``(bin_path, txt_path, raw_int16_iq, scaled_complex_iq)`` so tests
    can verify both the on-disk bytes and the expected ``get_iq_data`` output.
    """
    rng = np.random.default_rng(0)
    i = rng.integers(-2000, 2000, size=samples, dtype=np.int16)
    q = rng.integers(-2000, 2000, size=samples, dtype=np.int16)

    bin_path = base.with_suffix(".bin")
    interleaved = np.empty(samples * 2, dtype=np.int16)
    interleaved[0::2] = i
    interleaved[1::2] = q
    bin_path.write_bytes(interleaved.tobytes())

    txt_path = bin_path.with_suffix(".bin.txt")
    lines = [
        f"XDelta\t{x_delta:.12E}",
        f"InputCenter\t{center_hz:.0f}",
        f"YScale\t{y_scale:.6E}",
        "XStart\t0",
        "XDomain\t2",
        "XUnit\tSec",
        "InputRefImped\t50",
        "InputRange\t0.02903687168",
        f"FreqValidMin\t{center_hz - 1.0e9:.0f}",
        f"FreqValidMax\t{center_hz + 1.0e9:.0f}",
        "TimeUtcString\t2025-01-28T05:21:52.045Z",
    ]
    if extra_metadata:
        for k, v in extra_metadata.items():
            lines.append(f"{k}\t{v}")
    txt_path.write_text("\n".join(lines), encoding="utf-8")

    expected = (
        i.astype(np.float32) * np.float32(y_scale)
        + 1j * q.astype(np.float32) * np.float32(y_scale)
    ).astype(np.complex64)
    return bin_path, txt_path, interleaved, expected


def test_parse_extracts_required_keys(tmp_path: Path) -> None:
    bin_path, _, _, _ = _write_keysight_pair(
        tmp_path / "sample", samples=512, x_delta=1 / 100e6, center_hz=2.4e9, y_scale=1e-5
    )

    loader = KeysightBinLoader()
    header = loader.parse_bin_txt(bin_path)

    assert header["SAMPLES"] == 512
    assert header["CLOCK"] == pytest.approx(100e6, rel=1e-6)
    assert header["FREQUENCY"] == pytest.approx(2.4e9)
    assert header["YSCALE"] == pytest.approx(1e-5)
    assert header["RESOLUTION"] == 16
    assert "TYPE" in header and "Keysight" in header["TYPE"]


def test_round_trip_applies_yscale(tmp_path: Path) -> None:
    bin_path, _, _, expected = _write_keysight_pair(
        tmp_path / "round", samples=1024, y_scale=2.5e-7
    )
    loader = KeysightBinLoader()
    loader.parse_bin_txt(bin_path)
    loader.open_bin()
    np.testing.assert_array_equal(loader.get_iq_data(), expected)
    loader.close()


def test_get_iq_data_range(tmp_path: Path) -> None:
    bin_path, _, _, expected = _write_keysight_pair(tmp_path / "rng", samples=256)
    loader = KeysightBinLoader()
    loader.parse_bin_txt(bin_path)
    loader.open_bin()
    np.testing.assert_array_equal(loader.get_iq_data(10, 42), expected[10:42])

    with pytest.raises(ValueError):
        loader.get_iq_data(100, 50)
    loader.close()


def test_open_iq_file_dispatches_to_keysight(tmp_path: Path) -> None:
    bin_path, _, _, expected = _write_keysight_pair(tmp_path / "dispatch", samples=64)
    loader = open_iq_file(bin_path)
    try:
        np.testing.assert_array_equal(loader.get_iq_data(), expected)  # type: ignore[attr-defined]
    finally:
        loader.close()


def test_open_iq_file_rejects_bin_without_metadata(tmp_path: Path) -> None:
    """A ``.bin`` file with no sibling ``.bin.txt`` must not be claimed."""
    orphan = tmp_path / "orphan.bin"
    orphan.write_bytes(b"\x00" * 16)
    with pytest.raises(ValueError, match="未対応"):
        open_iq_file(orphan)


def test_parse_rejects_missing_xdelta(tmp_path: Path) -> None:
    bin_path = tmp_path / "bad.bin"
    bin_path.write_bytes(b"\x00\x00\x00\x00")
    bin_path.with_suffix(".bin.txt").write_text("InputCenter\t1000\n", encoding="utf-8")

    loader = KeysightBinLoader()
    with pytest.raises(ValueError, match="XDelta"):
        loader.parse_bin_txt(bin_path)


def test_close_is_idempotent(tmp_path: Path) -> None:
    bin_path, _, _, _ = _write_keysight_pair(tmp_path / "close", samples=16)
    loader = KeysightBinLoader()
    loader.parse_bin_txt(bin_path)
    loader.open_bin()
    loader.close()
    loader.close()  # must not raise
    assert loader.data_memmap is None


def test_size_inferred_from_bin_overrides_anything_else(tmp_path: Path) -> None:
    """SAMPLES comes from the binary file, not from the metadata file."""
    bin_path, _, _, _ = _write_keysight_pair(tmp_path / "size", samples=200)
    loader = KeysightBinLoader()
    header = loader.parse_bin_txt(bin_path)
    assert header["SAMPLES"] == 200

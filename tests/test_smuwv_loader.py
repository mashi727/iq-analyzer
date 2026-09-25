"""Tests for ``iq_analyzer.loaders.smuwv.SMUWVLoader``."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from iq_analyzer.loaders import SMUWVLoader, open_iq_file


def _write_wv(
    path: Path,
    iq_interleaved: np.ndarray,
    *,
    clock: float = 250e6,
    declared_samples: int | None = None,
    tag: str = "WAVEFORM",
    extra_tags: str = "",
) -> Path:
    """Write a minimal SMU-WV file around an interleaved int16 payload."""
    payload = iq_interleaved.astype("<i2").tobytes()
    samples = len(iq_interleaved) // 2 if declared_samples is None else declared_samples
    head = (
        "{TYPE: SMU-WV,0}{COMMENT:test}"
        f"{{CLOCK:{clock:.6f}}}{{LEVEL OFFS:3.0103,0}}{{SAMPLES:{samples}}}{extra_tags}"
        f"{{{tag}-{len(payload) + 1}: #"
    ).encode("ascii")
    path.write_bytes(head + payload + b"}")
    return path


def _gaussian_iq(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(rng.normal(0, 2000, size=2 * n), -32768, 32767).astype(np.int16)


def test_round_trip(tmp_path: Path) -> None:
    raw = _gaussian_iq(4096)
    path = _write_wv(tmp_path / "a.wv", raw)

    loader = SMUWVLoader()
    header = loader.parse_wv(path)
    assert header["TYPE"] == "SMU-WV"
    assert header["CHECKSUM"] == "0"
    assert header["SAMPLES"] == 4096
    assert header["CLOCK"] == pytest.approx(250e6)
    assert header["RMS_OFFSET_DB"] == pytest.approx(3.0103)
    assert header["FREQUENCY"] == 0.0
    assert not loader.header_mismatch

    loader.open_wv()
    iq = loader.get_iq_data()
    np.testing.assert_array_equal(iq.real, raw[0::2])
    np.testing.assert_array_equal(iq.imag, raw[1::2])
    np.testing.assert_array_equal(loader.get_iq_data(10, 20).real, raw[20:40:2])
    # Gaussian IQ is far from uniform random bytes.
    assert not loader.payload_looks_random
    loader.close()
    loader.close()  # idempotent


def test_payload_with_odd_offset_and_braces_in_data(tmp_path: Path) -> None:
    # '{' and '}' bytes inside the payload must not confuse the tag parser, and
    # an odd header length must not misalign the int16 view.
    raw = np.full(64, ord("}") | (ord("{") << 8), dtype=np.int16)
    path = _write_wv(tmp_path / "b.wv", raw, extra_tags="{X:1}")
    loader = open_iq_file(path)
    assert isinstance(loader, SMUWVLoader)
    np.testing.assert_array_equal(loader.get_iq_data().real, raw[0::2])
    loader.close()


def test_samples_tag_mismatch_trusts_payload(tmp_path: Path) -> None:
    raw = _gaussian_iq(100)
    path = _write_wv(tmp_path / "c.wv", raw, declared_samples=96, tag="WWAVEFORM")
    loader = SMUWVLoader()
    header = loader.parse_wv(path)
    assert header["SAMPLES"] == 100
    assert header["SAMPLES_DECLARED"] == 96
    assert header["WAVEFORM_TAG"] == "WWAVEFORM"
    assert loader.header_mismatch


def test_random_payload_is_flagged(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    raw = rng.integers(-32768, 32767, size=2 * 600_000, dtype=np.int16)
    loader = open_iq_file(_write_wv(tmp_path / "d.wv", raw))
    assert loader.payload_looks_random
    loader.close()


def test_companion_wvh_supplies_rf_metadata(tmp_path: Path) -> None:
    path = _write_wv(tmp_path / "e.wv", _gaussian_iq(16))
    (tmp_path / "e.wvh").write_text("{TYPE:RAW16LE}{FREQUENCY:31750000000.0}{REFLEVEL:-36}")
    header = SMUWVLoader().parse_wv(path)
    assert header["FREQUENCY"] == pytest.approx(31.75e9)
    assert header["REFLEVEL"] == pytest.approx(-36.0)


def test_rejects_non_wv(tmp_path: Path) -> None:
    bad = tmp_path / "x.wv"
    bad.write_bytes(b"not a waveform")
    with pytest.raises(ValueError):
        SMUWVLoader().parse_wv(bad)

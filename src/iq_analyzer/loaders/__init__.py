"""IQ file format loaders.

Use :func:`open_iq_file` to load an IQ recording without caring whether it is
stored as a Rohde & Schwarz WVH/WVD pair, a modern ``iq.tar`` archive, or a
Keysight N5110A ``.bin`` + ``.bin.txt`` pair.
"""

from __future__ import annotations

from pathlib import Path

from iq_analyzer.loaders.base import IQLoader
from iq_analyzer.loaders.iqtar import IQTarLoader
from iq_analyzer.loaders.keysight import KeysightBinLoader
from iq_analyzer.loaders.wv import WVFileLoader

__all__ = [
    "IQLoader",
    "IQTarLoader",
    "KeysightBinLoader",
    "WVFileLoader",
    "open_iq_file",
]


def open_iq_file(path: str | Path) -> IQLoader:
    """Detect the format of *path* and return a fully initialized loader.

    Recognized extensions:

    * ``.wvh`` / ``.wvd`` — :class:`WVFileLoader` (parses the ``.wvh`` and
      memory-maps the ``.wvd``).
    * ``.iq.tar`` (or just ``.tar``) — :class:`IQTarLoader`.
    * ``.bin`` accompanied by ``<name>.bin.txt`` — :class:`KeysightBinLoader`.
    """
    path = Path(path)
    name = path.name.lower()

    if name.endswith(".iq.tar") or name.endswith(".tar"):
        iqtar_loader = IQTarLoader()
        iqtar_loader.parse_iqtar(path)
        iqtar_loader.open_data()
        return iqtar_loader

    if path.suffix.lower() in {".wvh", ".wvd"}:
        wv_loader = WVFileLoader()
        wv_loader.parse_wvh(path.with_suffix(".wvh"))
        wv_loader.open_wvd()
        return wv_loader

    if path.suffix.lower() == ".bin" and path.with_suffix(".bin.txt").exists():
        ks_loader = KeysightBinLoader()
        ks_loader.parse_bin_txt(path)
        ks_loader.open_bin()
        return ks_loader

    raise ValueError(f"未対応のファイル形式: {path}")

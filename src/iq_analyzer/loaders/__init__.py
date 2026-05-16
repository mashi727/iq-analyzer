"""IQ file format loaders.

Use :func:`open_iq_file` to load an IQ recording without caring whether it is
stored as a Rohde & Schwarz WVH/WVD pair or a modern ``iq.tar`` archive.
"""

from __future__ import annotations

from pathlib import Path

from iq_analyzer.loaders.base import IQLoader
from iq_analyzer.loaders.iqtar import IQTarLoader
from iq_analyzer.loaders.wv import WVFileLoader

__all__ = [
    "IQLoader",
    "IQTarLoader",
    "WVFileLoader",
    "open_iq_file",
]


def open_iq_file(path: str | Path) -> IQLoader:
    """Detect the format of *path* and return a fully initialized loader.

    Recognized extensions:

    * ``.wvh`` / ``.wvd`` — :class:`WVFileLoader` (parses the ``.wvh`` and
      memory-maps the ``.wvd``).
    * ``.iq.tar`` (or just ``.tar``) — :class:`IQTarLoader`.
    """
    path = Path(path)
    name = path.name.lower()

    if name.endswith(".iq.tar") or name.endswith(".tar"):
        loader = IQTarLoader()
        loader.parse_iqtar(path)
        loader.open_data()
        return loader

    if path.suffix.lower() in {".wvh", ".wvd"}:
        wv_loader = WVFileLoader()
        wv_loader.parse_wvh(path.with_suffix(".wvh"))
        wv_loader.open_wvd()
        return wv_loader

    raise ValueError(f"未対応のファイル形式: {path}")

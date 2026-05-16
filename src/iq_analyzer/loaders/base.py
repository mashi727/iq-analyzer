"""Common contract for IQ data loaders.

A loader exposes a small surface area used by the rest of the viewer:

* ``header`` — a ``dict`` of parsed metadata. The exact keys depend on the
  source format, but the following keys are guaranteed to be present after a
  successful load:

    - ``SAMPLES`` (``int``)   total number of IQ samples available
    - ``CLOCK``   (``float``) sampling clock in Hz
    - ``FREQUENCY`` (``float``) center frequency in Hz (``0.0`` if unknown)
    - ``REFLEVEL`` (``float``) reference level in dBm (``0.0`` if unknown)

* ``get_iq_data(start_sample, end_sample)`` — return a ``numpy`` complex array
  for the requested half-open sample range. The returned dtype is at least
  ``complex64``.

* ``close()`` — release the underlying ``numpy.memmap`` and any temporary
  resources. Must be safe to call multiple times.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import numpy as np
from numpy.typing import NDArray


@runtime_checkable
class IQLoader(Protocol):
    """Structural type implemented by every IQ loader."""

    header: dict[str, Any]

    def get_iq_data(
        self,
        start_sample: int = 0,
        end_sample: int | None = None,
    ) -> NDArray[np.complexfloating]:
        """Return IQ samples in ``[start_sample, end_sample)`` as complex array."""
        ...

    def close(self) -> None:
        """Release memory maps and temporary resources."""
        ...

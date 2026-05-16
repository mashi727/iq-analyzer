"""Command-line entry point.

The actual viewer implementation currently lives in the legacy single-file
script (``rs_iq_viewer.py`` at the project root). During the refactor this
module simply delegates to it so that the packaged ``iq-analyzer`` command
remains usable. Subsequent refactoring steps will gradually move the
implementation into the :mod:`iq_analyzer` package.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _legacy_script_path() -> Path | None:
    """Locate the legacy ``rs_iq_viewer.py`` next to the project root.

    Returns ``None`` once the legacy script has been removed (post-migration).
    """
    # src/iq_analyzer/cli.py -> project root is two levels up from ``src``
    candidates = [
        Path(__file__).resolve().parents[2] / "rs_iq_viewer.py",
        Path.cwd() / "rs_iq_viewer.py",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def main() -> int:
    """Launch the IQ viewer.

    Returns a process exit code so that callers can propagate it.
    """
    script = _legacy_script_path()
    if script is None:
        sys.stderr.write(
            "iq-analyzer: the legacy entry script rs_iq_viewer.py was not found, "
            "and the package refactor has not yet wired up a native entry point.\n"
        )
        return 1

    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

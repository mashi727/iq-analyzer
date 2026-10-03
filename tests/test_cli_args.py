"""Command-line start directory: ``iq-analyzer [DIR]``."""

from __future__ import annotations

from pathlib import Path

import pytest

from iq_analyzer import cli


def test_no_argument_uses_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_start_dir(None) == Path.cwd()


def test_directory_argument_is_made_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "data").mkdir()
    monkeypatch.chdir(tmp_path)
    assert cli.resolve_start_dir("data") == (tmp_path / "data").resolve()


def test_quoted_argument_with_trailing_quote(tmp_path: Path) -> None:
    # cmd.exe turns "D:\data\" into  D:\data"  (the backslash escapes the quote).
    assert cli.resolve_start_dir(f'{tmp_path}"') == tmp_path.resolve()


def test_bare_drive_letter_means_drive_root(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    class FakePath:
        def __init__(self, text: str) -> None:
            seen.append(text)

        def expanduser(self) -> "FakePath":
            return self

        def exists(self) -> bool:
            return True

        def is_dir(self) -> bool:
            return True

        def resolve(self) -> str:
            return seen[-1]

    monkeypatch.setattr(cli, "Path", FakePath)
    assert cli.resolve_start_dir("D:") == "D:\\"


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="見つかりません"):
        cli.resolve_start_dir(str(tmp_path / "nope"))


def test_file_is_rejected(tmp_path: Path) -> None:
    f = tmp_path / "a.wvh"
    f.write_text("x")
    with pytest.raises(ValueError, match="ファイルは不可"):
        cli.resolve_start_dir(str(f))


def test_main_returns_2_for_bad_path(qapp, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["iq-analyzer", str(tmp_path / "nope")]) == 2
    assert "見つかりません" in capsys.readouterr().err


def test_viewer_file_browser_starts_at_given_dir(qapp, tmp_path: Path) -> None:
    from iq_analyzer.ui.main_window import RSIQViewer

    viewer = RSIQViewer(start_dir=tmp_path)
    try:
        assert viewer.file_browser.current_root_dir == tmp_path
    finally:
        viewer.close()

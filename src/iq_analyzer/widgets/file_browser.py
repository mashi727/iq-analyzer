"""File-browser panel: a tree view of WVH / iq.tar / Keysight .bin files plus
a header preview.

The panel is fully self-contained: it owns its model, view and preview text
area. It communicates with the rest of the app exclusively through Qt signals,
so the main window doesn't need to reach inside it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QDir, Qt, Signal
from PySide6.QtWidgets import (
    QFileSystemModel,
    QGroupBox,
    QLabel,
    QTextEdit,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from iq_analyzer.loaders import IQTarLoader, KeysightBinLoader, WVFileLoader


def _format_duration(samples: int, clock_hz: float) -> str | None:
    if clock_hz <= 0:
        return None
    duration = samples / clock_hz
    if duration < 1e-3:
        return f"{duration * 1e6:.2f} μs"
    if duration < 1:
        return f"{duration * 1e3:.2f} ms"
    return f"{duration:.3f} s"


def _format_wvh_header(file_path: Path, header: dict) -> str:
    text: list[str] = []
    wvd_path = file_path.with_suffix(".wvd")
    if wvd_path.exists():
        size_gb = wvd_path.stat().st_size / 1e9
        text.append(f"WVDサイズ:        {size_gb:.2f} GB")
    text.append(f"フォーマット:     {header.get('TYPE', 'N/A')}")
    text.append(f"コンポーネント:   {header.get('COMPONENTS', 'N/A')}")
    text.append(f"分解能:           {header.get('RESOLUTION', 'N/A')} bit\n")

    samples = int(header.get("SAMPLES", 0))
    clock = float(header.get("CLOCK", 0))
    text.append(f"サンプル数:       {samples:,}")
    text.append(f"サンプリング周波数: {clock / 1e6:.2f} MHz")
    duration = _format_duration(samples, clock)
    if duration:
        text.append(f"継続時間:         {duration}\n")

    frequency = float(header.get("FREQUENCY", 0))
    text.append(f"中心周波数:       {frequency / 1e6:.2f} MHz")
    if clock > 0:
        bw = clock
        text.append(f"瞬時帯域幅:       {bw / 1e6:.2f} MHz")
        text.append(
            f"周波数範囲:       {(frequency - bw / 2) / 1e6:.2f} - "
            f"{(frequency + bw / 2) / 1e6:.2f} MHz\n"
        )

    for key, label in (("DATE", "日付"), ("FWVERSION", "ファームウェア"), ("CHANNAME0", "チャンネル名")):
        if key in header:
            text.append(f"{label}:             {header[key]}")

    return "\n".join(text)


def _format_iqtar_header(file_path: Path, header: dict) -> str:
    text: list[str] = []
    size_gb = file_path.stat().st_size / 1e9
    text.append(f"ファイルサイズ:   {size_gb:.2f} GB\n")
    text.append("フォーマット:     iq.tar (float32)")
    text.append("コンポーネント:   IQ\n")

    samples = int(header.get("SAMPLES", 0))
    clock = float(header.get("CLOCK", 0))
    text.append(f"サンプル数:       {samples:,}")
    text.append(f"サンプリング周波数: {clock / 1e6:.2f} MHz")
    duration = _format_duration(samples, clock)
    if duration:
        text.append(f"継続時間:         {duration}\n")

    frequency = float(header.get("FREQUENCY", 0))
    text.append(f"中心周波数:       {frequency / 1e6:.2f} MHz")
    if clock > 0:
        bw = clock
        text.append(f"瞬時帯域幅:       {bw / 1e6:.2f} MHz")
        text.append(
            f"周波数範囲:       {(frequency - bw / 2) / 1e6:.2f} - "
            f"{(frequency + bw / 2) / 1e6:.2f} MHz"
        )
    return "\n".join(text)


def _format_keysight_header(file_path: Path, header: dict) -> str:
    text: list[str] = []
    size_gb = file_path.stat().st_size / 1e9
    text.append(f"binサイズ:        {size_gb:.2f} GB")
    text.append(f"フォーマット:     {header.get('TYPE', 'Keysight N5110A')}")
    text.append(f"コンポーネント:   {header.get('COMPONENTS', 'IQ')}")
    text.append(f"分解能:           {header.get('RESOLUTION', 16)} bit\n")

    samples = int(header.get("SAMPLES", 0))
    clock = float(header.get("CLOCK", 0))
    text.append(f"サンプル数:       {samples:,}")
    text.append(f"サンプリング周波数: {clock / 1e6:.2f} MHz")
    duration = _format_duration(samples, clock)
    if duration:
        text.append(f"継続時間:         {duration}\n")

    frequency = float(header.get("FREQUENCY", 0))
    text.append(f"中心周波数:       {frequency / 1e6:.2f} MHz")
    if "FreqValidMin" in header and "FreqValidMax" in header:
        fmin = float(header["FreqValidMin"]) / 1e6
        fmax = float(header["FreqValidMax"]) / 1e6
        text.append(f"有効周波数範囲:   {fmin:.2f} - {fmax:.2f} MHz")

    yscale = header.get("YSCALE")
    if yscale is not None:
        text.append(f"\nYScale:           {float(yscale):.6e}")
    if "InputRange" in header:
        text.append(f"InputRange:       {header['InputRange']} V")
    if "InputRefImped" in header:
        text.append(f"参照インピーダンス: {header['InputRefImped']} Ω")
    if "TimeUtcString" in header:
        text.append(f"記録時刻:         {header['TimeUtcString']}")
    return "\n".join(text)


def _display_width(name: str) -> int:
    """Rough fixed-width column count: non-ASCII counts as 2, ASCII as 1."""
    return sum(2 if ord(c) > 127 else 1 for c in name)


class FileBrowserPanel(QWidget):
    """Tree view + header preview for WVH / iq.tar files."""

    # Emitted whenever the displayed root directory changes (user double-clicks
    # into a sub-folder, programmatic set_root_dir, etc.).
    current_dir_changed = Signal(Path)
    # Emitted on double-click of a recognised IQ file (caller is expected to load it).
    file_open_requested = Signal(Path)
    # Short messages destined for a status bar (one-line).
    status_message = Signal(str)

    def __init__(
        self,
        *,
        font_size_large: int = 12,
        font_size_small: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._font_size_large = font_size_large
        self._font_size_small = font_size_small

        self._current_root_dir = Path.cwd()
        self._build_ui()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        group = QGroupBox("ファイルブラウザ")
        layout = QVBoxLayout(group)

        dir_label = QLabel(f"📁 起動ディレクトリ: {self._current_root_dir.name}")
        dir_label.setStyleSheet("font-weight: bold; padding: 5px;")
        dir_label.setToolTip(str(self._current_root_dir))
        layout.addWidget(dir_label)
        self._startup_dir_label = dir_label

        self.file_model = QFileSystemModel()
        self.file_model.setRootPath(str(self._current_root_dir))
        self.file_model.setNameFilters(["*.wvh", "*.iq.tar", "*.bin"])
        self.file_model.setNameFilterDisables(False)
        self.file_model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDot | QDir.NoDotDot)

        self.file_tree = QTreeView()
        self.file_tree.setModel(self.file_model)
        self.file_tree.setSortingEnabled(True)
        self.file_model.sort(0, Qt.AscendingOrder)
        self.file_tree.setRootIndex(self.file_model.index(str(self._current_root_dir)))
        self.file_tree.setColumnWidth(0, 250)
        self.file_tree.setColumnHidden(2, True)
        self.file_tree.setColumnHidden(3, True)
        self.file_tree.setStyleSheet(
            f"QTreeView {{ font-size: {self._font_size_large}pt; }}"
        )
        self.file_tree.clicked.connect(self._on_clicked)
        self.file_tree.doubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self.file_tree)

        header_label = QLabel("ファイル情報:")
        header_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        layout.addWidget(header_label)

        self.header_info_text = QTextEdit()
        self.header_info_text.setReadOnly(True)
        self.header_info_text.setMinimumHeight(300)
        self.header_info_text.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: #f5f5f5;
                color: #333333;
                font-family: 'SF Mono', 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: {self._font_size_small}pt;
                border: 1px solid #cccccc;
                padding: 8px;
                line-height: 1.0;
            }}
            """
        )
        self.header_info_text.setPlaceholderText(
            "ファイルを選択するとヘッダー情報が表示されます..."
        )
        layout.addWidget(self.header_info_text)

        outer.addWidget(group)

    # ------------------------------------------------------------------ API

    @property
    def current_root_dir(self) -> Path:
        return self._current_root_dir

    def set_root_dir(self, path: Path | str) -> None:
        """Re-root the tree view at *path* and notify listeners."""
        target = Path(path)
        if not (target.exists() and target.is_dir()):
            return
        self._current_root_dir = target
        self.file_tree.setRootIndex(self.file_model.index(str(target)))
        self.current_dir_changed.emit(target)
        self.status_message.emit(f"📁 {target}")

    # ------------------------------------------------------------- signals

    def _on_clicked(self, index) -> None:
        file_path = Path(self.file_model.filePath(index))
        if file_path.is_dir():
            self.header_info_text.clear()
            self.header_info_text.setPlaceholderText("ディレクトリが選択されています...")
            return
        if self._is_iq_file(file_path):
            self._show_header(file_path)

    def _on_double_clicked(self, index) -> None:
        file_path = Path(self.file_model.filePath(index))
        if file_path.is_dir():
            self.set_root_dir(file_path)
            return
        if self._is_iq_file(file_path):
            # Re-root at the file's parent so the breadcrumb stays in sync.
            self.set_root_dir(file_path.parent)
            self.status_message.emit(f"📄 読み込み中: {file_path.name}")
            self.file_open_requested.emit(file_path)

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _is_iq_file(path: Path) -> bool:
        if path.suffix in {".wvh", ".tar"} or path.name.endswith(".iq.tar"):
            return True
        # Keysight: .bin only counts if the matching .bin.txt sits next to it,
        # otherwise we'd pick up arbitrary firmware blobs that happen to share
        # the extension.
        return path.suffix == ".bin" and path.with_suffix(".bin.txt").exists()

    def _show_header(self, file_path: Path) -> None:
        display_name = f"📄 {file_path.name}"
        rule = "=" * _display_width(display_name)
        try:
            if file_path.suffix == ".wvh":
                wv_loader = WVFileLoader()
                header = wv_loader.parse_wvh(str(file_path))
                body = _format_wvh_header(file_path, header)
            elif file_path.name.endswith(".iq.tar"):
                tar_loader = IQTarLoader()
                header = tar_loader.parse_iqtar(str(file_path))
                try:
                    body = _format_iqtar_header(file_path, header)
                finally:
                    tar_loader.close()
            elif file_path.suffix == ".bin" and file_path.with_suffix(".bin.txt").exists():
                ks_loader = KeysightBinLoader()
                header = ks_loader.parse_bin_txt(file_path)
                body = _format_keysight_header(file_path, header)
            else:
                return
        except Exception as exc:
            self.header_info_text.setPlainText(
                f"❌ ヘッダー読み込みエラー\n\nファイル: {file_path.name}\nエラー: {exc}"
            )
            return

        self.header_info_text.setPlainText(f"{display_name}\n{rule}\n\n{body}")

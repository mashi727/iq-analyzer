"""File-browser panel: an Explorer-style folder tree plus a header preview.

  ↑ ..               (re-roots the start folder one level up)
  📁 <start folder>
  🏠 ホーム
  💻 この Mac / PC
      boot disk, external and network drives

Ported from book-viewer's shelf panel so both tools behave the same. Several
top-level entries means a :class:`QTreeWidget` rather than a
:class:`QFileSystemModel` (which has a single root); folders are read lazily
when expanded and watched for changes, and ``/Volumes`` is watched so plugging
in a drive updates the list. Icons come from :class:`QFileIconProvider`, so
drives look as they do in Finder / Explorer.

Only folders and recognised IQ files are listed; hidden entries (dot files,
macOS ``UF_HIDDEN`` such as ``/Volumes`` or ``~/Library``, Windows hidden
attribute) are skipped. A single click previews a file's header; a double
click asks the main window to load it (loading a 100 GB recording is not
something to trigger by accident).
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from PySide6.QtCore import (
    QCollator,
    QFileInfo,
    QFileSystemWatcher,
    QSize,
    QStorageInfo,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QGroupBox,
    QLabel,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iq_analyzer.loaders import IQTarLoader, KeysightBinLoader, SMUWVLoader, WVFileLoader
from iq_analyzer.loaders.wv import resolve_wvh

_PATH_ROLE = Qt.ItemDataRole.UserRole  # item path (str); None for ".." and "この Mac"
_LOADED_ROLE = Qt.ItemDataRole.UserRole + 1  # folder children already read
_PLACEHOLDER = "…"  # dummy child so unread folders show an expander

# macOS keeps system snapshots / helper volumes under /Volumes as well.
_HIDDEN_VOLUME_NAMES = {"Recovery", "Preboot", "VM", "Update", "xarts", "iSCPreboot", "Hardware"}
_NETWORK_FS = {"smbfs", "afpfs", "nfs", "webdav", "cifs", "smb3", "fuse.sshfs"}
_COMPUTER_LABEL = "この Mac" if sys.platform == "darwin" else "PC"


def _format_duration(samples: int, clock_hz: float) -> str | None:
    if clock_hz <= 0:
        return None
    duration = samples / clock_hz
    if duration < 1e-3:
        return f"{duration * 1e6:.2f} μs"
    if duration < 1:
        return f"{duration * 1e3:.2f} ms"
    return f"{duration:.3f} s"


def _format_wvh_header(wvh_path: Path, wvd_path: Path | None, header: dict) -> str:
    text: list[str] = []
    if wvd_path is not None and wvd_path.exists():
        size_gb = wvd_path.stat().st_size / 1e9
        if wvd_path.stem != wvh_path.stem:
            text.append(f"ヘッダー:         {wvh_path.name}")
            text.append(f"データ:           {wvd_path.name}")
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


def _format_smuwv_header(file_path: Path, header: dict) -> str:
    text: list[str] = []
    size_gb = file_path.stat().st_size / 1e9
    text.append(f"ファイルサイズ:   {size_gb:.2f} GB")
    text.append(f"フォーマット:     {header.get('TYPE', 'SMU-WV')} (int16 IQ)")
    if "COMMENT" in header:
        text.append(f"コメント:         {header['COMMENT']}")
    text.append("")

    samples = int(header.get("SAMPLES", 0))
    clock = float(header.get("CLOCK", 0))
    text.append(f"サンプル数:       {samples:,}")
    declared = header.get("SAMPLES_DECLARED")
    if declared is not None and declared != samples:
        text.append(f"  (SAMPLESタグ:   {declared:,})")
    text.append(f"サンプリング周波数: {clock / 1e6:.2f} MHz")
    duration = _format_duration(samples, clock)
    if duration:
        text.append(f"継続時間:         {duration}\n")

    frequency = float(header.get("FREQUENCY", 0))
    text.append(f"中心周波数:       {frequency / 1e6:.2f} MHz")
    if "RMS_OFFSET_DB" in header:
        text.append(f"レベルオフセット: RMS {header['RMS_OFFSET_DB']:.4f} dB / "
                    f"Peak {header['PEAK_OFFSET_DB']:.4f} dB")
    if "DATE" in header:
        text.append(f"日付:             {header['DATE']}")
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


def mounted_volumes() -> list[tuple[str, Path]]:
    """Volumes listed under "この Mac": ``[(label, root)]``, boot disk first."""
    boot: list[tuple[str, Path]] = []
    others: list[tuple[str, Path]] = []
    infos = list(QStorageInfo.mountedVolumes())
    if sys.platform == "darwin" and Path("/Volumes").is_dir():
        # mountedVolumes() can come back empty (e.g. under a restricted
        # sandbox); the mount points under /Volumes are the ground truth.
        known = {Path(i.rootPath()) for i in infos}
        with os.scandir("/Volumes") as it:
            for e in it:
                if Path(e.path) not in known and os.path.ismount(e.path):
                    infos.append(QStorageInfo(e.path))
        if Path("/") not in known:
            infos.append(QStorageInfo("/"))
    for info in infos:
        if not (info.isValid() and info.isReady()):
            continue
        root = Path(info.rootPath())
        if sys.platform == "darwin":
            if root != Path("/") and (root.parent != Path("/Volumes") or root.name in _HIDDEN_VOLUME_NAMES):
                continue
        elif sys.platform != "win32":
            if root != Path("/") and not str(root).startswith(("/media/", "/mnt/", "/run/media/")):
                continue
        name = info.displayName() or root.name or str(root)
        if sys.platform == "win32" and not name.endswith(")"):
            name = f"{name} ({str(root).rstrip(chr(92)).rstrip('/')})"
        fs = bytes(info.fileSystemType().data()).decode(errors="ignore").lower()
        if fs in _NETWORK_FS:
            device = bytes(info.device().data()).decode(errors="ignore")
            name += f" ({device.lstrip('/')})"
        (boot if root == Path("/") else others).append((name, root))
    return boot + sorted(others, key=lambda v: v[0].lower())


def is_iq_file(path: Path) -> bool:
    """Files the viewer can open (by name; content is checked on load)."""
    name = path.name.lower()
    if path.suffix.lower() in {".wvh", ".wvd", ".wv"} or name.endswith(".iq.tar"):
        return True
    # Keysight: .bin only counts with its .bin.txt next to it, otherwise any
    # firmware blob sharing the extension would show up.
    return path.suffix.lower() == ".bin" and path.with_suffix(".bin.txt").exists()


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    st = entry.stat(follow_symlinks=False)
    if sys.platform == "win32":
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & (stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM))
    # Finder hides UF_HIDDEN entries (/Volumes, /bin, ~/Library ...).
    return bool(getattr(st, "st_flags", 0) & getattr(stat, "UF_HIDDEN", 0))


def _display_width(name: str) -> int:
    """Rough fixed-width column count: non-ASCII counts as 2, ASCII as 1."""
    return sum(2 if ord(c) > 127 else 1 for c in name)


class FileBrowserPanel(QWidget):
    """Folder tree + header preview for IQ recordings."""

    # Emitted when ".." (or set_root_dir) moves the start folder.
    current_dir_changed = Signal(Path)
    # Emitted on double-click of a recognised IQ file (caller loads it).
    file_open_requested = Signal(Path)
    # Short messages destined for a status bar (one-line).
    status_message = Signal(str)

    def __init__(
        self,
        start_dir: Path | str | None = None,
        *,
        font_size_large: int = 12,
        font_size_small: int = 10,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._font_size_large = font_size_large
        self._font_size_small = font_size_small
        self._start_dir = Path(start_dir) if start_dir else Path.cwd()
        self._icons = QFileIconProvider()
        self._collator = QCollator()
        self._collator.setNumericMode(True)  # "file2" < "file10"
        self._collator.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

        # Expanded folders are watched; changed ones are re-read in one batch.
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._dirty: set[str] = set()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(200)
        self._refresh_timer.timeout.connect(self._refresh_dirty)

        self._build_ui()
        self._build_roots()
        if Path("/Volumes").is_dir():
            self._watcher.addPath("/Volumes")  # drives plugged in / ejected

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        group = QGroupBox("ファイルブラウザ")
        layout = QVBoxLayout(group)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setExpandsOnDoubleClick(False)  # folders toggle on single click
        size = self._font_size_large + 4
        self.tree.setIconSize(QSize(size, size))
        self.tree.setStyleSheet(f"QTreeWidget {{ font-size: {self._font_size_large}pt; }}")
        self.tree.itemExpanded.connect(self._ensure_loaded)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double_clicked)
        self.tree.currentItemChanged.connect(self._on_current_changed)

        # Tree and header preview share the column through a splitter so the
        # user can trade one for the other; the tree gets most of it.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.tree)
        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)

        header_label = QLabel("ファイル情報:")
        header_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        info_layout.addWidget(header_label)

        self.header_info_text = QTextEdit()
        self.header_info_text.setReadOnly(True)
        self.header_info_text.setMinimumHeight(80)
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
        info_layout.addWidget(self.header_info_text)
        splitter.addWidget(info)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        outer.addWidget(group)

    def _build_roots(self) -> None:
        up = QTreeWidgetItem([".."])
        up.setIcon(0, self._up_arrow_icon())
        up.setData(0, _PATH_ROLE, None)
        self._up_item = up
        self._start_item = self._make_start_item()
        home = self._dir_item(Path.home(), "ホーム")
        mac = QTreeWidgetItem([_COMPUTER_LABEL])
        mac.setIcon(0, self._icons.icon(QFileIconProvider.IconType.Computer))
        mac.setData(0, _PATH_ROLE, None)
        self._mac = mac
        self.tree.addTopLevelItems([up, self._start_item, home, mac])
        self._update_up_item()
        self._fill_volumes()
        # The start folder starts collapsed so the top-level structure
        # (start / home / this computer) is visible at a glance.
        mac.setExpanded(True)

    def _up_arrow_icon(self) -> QIcon:
        """Thin-line ↑ like Windows 11 Explorer's "Up" (macOS's ▲ reads as Eject)."""
        size = self.tree.iconSize().height()
        dpr = 2.0
        pm = QPixmap(round(size * dpr), round(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self.tree.palette().color(QPalette.ColorRole.Text), max(1.5, size / 14))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        cx, top, bottom, wing = size / 2, size * 0.18, size * 0.84, size * 0.30
        path = QPainterPath()
        path.moveTo(cx, bottom)
        path.lineTo(cx, top)
        path.moveTo(cx - wing, top + wing)
        path.lineTo(cx, top)
        path.lineTo(cx + wing, top + wing)
        painter.drawPath(path)
        painter.end()
        return QIcon(pm)

    # ------------------------------------------------------------------ API

    @property
    def current_root_dir(self) -> Path:
        return self._start_dir

    def set_root_dir(self, path: Path | str) -> None:
        """Replace the start folder with *path* and notify listeners."""
        target = Path(path)
        if not (target.exists() and target.is_dir()):
            return
        self._replace_start(target)
        self.current_dir_changed.emit(target)
        self.status_message.emit(f"📁 {target}")

    def go_up(self) -> None:
        """Re-root the start folder one level up, keeping the old one selected."""
        old = self._start_dir
        parent = old.parent
        if parent == old:
            return
        self._replace_start(parent)
        self.reveal(old, under=self._start_item)
        self.current_dir_changed.emit(parent)

    def reveal(self, path: str | Path, under: QTreeWidgetItem | None = None) -> bool:
        """Expand the tree down to *path* and select it.

        Without *under*, walks from the deepest top-level entry containing it.
        """
        # Compare real paths: on macOS /tmp is /private/tmp, /Volumes/MacHD is /,
        # and the same folder may be reached through either spelling.
        target = Path(os.path.realpath(path))
        best: tuple[int, QTreeWidgetItem] | None = None
        for item in [under] if under is not None else self._all_roots():
            p = item.data(0, _PATH_ROLE)
            if p is None:
                continue
            base = Path(os.path.realpath(p))
            if target == base or base in target.parents:
                depth = len(base.parts)
                if best is None or depth > best[0]:
                    best = (depth, item)
        if best is None:
            return False
        item = best[1]
        base = Path(os.path.realpath(item.data(0, _PATH_ROLE)))
        for part in target.relative_to(base).parts:
            self._ensure_loaded(item)
            item.setExpanded(True)
            nxt = self._child_by_name(item, part)
            if nxt is None:
                break
            item = nxt
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        return True

    # ------------------------------------------------------------ building

    def _replace_start(self, new_dir: Path) -> None:
        index = self.tree.indexOfTopLevelItem(self._start_item)
        self.tree.takeTopLevelItem(index)
        self._start_dir = new_dir
        self._start_item = self._make_start_item()
        self.tree.insertTopLevelItem(index, self._start_item)
        self._update_up_item()

    def _make_start_item(self) -> QTreeWidgetItem:
        item = self._dir_item(self._start_dir, self._start_dir.name or str(self._start_dir))
        item.setToolTip(0, str(self._start_dir))
        return item

    def _update_up_item(self) -> None:
        parent = self._start_dir.parent
        self._up_item.setHidden(parent == self._start_dir)  # hidden at the root
        self._up_item.setToolTip(0, f"1 つ上へ: {parent}")

    def _fill_volumes(self) -> None:
        expanded = self._expanded_paths(self._mac)
        self._mac.takeChildren()
        for name, root in mounted_volumes():
            self._mac.addChild(self._dir_item(root, name))
        self._restore_expanded(self._mac, expanded)

    def _dir_item(self, path: Path, label: str | None = None) -> QTreeWidgetItem:
        item = QTreeWidgetItem([label or path.name])
        item.setIcon(0, self._icons.icon(QFileInfo(str(path))))
        item.setData(0, _PATH_ROLE, str(path))
        item.setData(0, _LOADED_ROLE, False)
        item.addChild(QTreeWidgetItem([_PLACEHOLDER]))  # shows the expander
        return item

    def _file_item(self, path: Path) -> QTreeWidgetItem:
        item = QTreeWidgetItem([path.name])
        item.setIcon(0, self._icons.icon(QFileInfo(str(path))))
        item.setData(0, _PATH_ROLE, str(path))
        try:
            size_gb = path.stat().st_size / 1e9
            item.setToolTip(0, f"{path.name}\n{size_gb:,.2f} GB")
        except OSError:
            pass
        return item

    def _ensure_loaded(self, item: QTreeWidgetItem) -> None:
        if item is self._mac or item.data(0, _LOADED_ROLE) or item.data(0, _PATH_ROLE) is None:
            return
        self._fill(item)

    def _fill(self, item: QTreeWidgetItem) -> None:
        """Re-create a folder's children, keeping expansion and selection."""
        path = item.data(0, _PATH_ROLE)
        expanded = self._expanded_paths(item)
        current = self.tree.currentItem()
        selected = current.data(0, _PATH_ROLE) if current else None
        dirs: list[Path] = []
        files: list[Path] = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        if _is_hidden(e):
                            continue
                        if e.is_dir():
                            dirs.append(Path(e.path))
                        elif e.is_file() and is_iq_file(Path(e.path)):
                            files.append(Path(e.path))
                    except OSError:
                        continue
        except OSError:
            pass

        def key(p: Path) -> object:
            return self._collator.sortKey(p.name)

        item.takeChildren()
        for d in sorted(dirs, key=key):
            item.addChild(self._dir_item(d))
        for f in sorted(files, key=key):
            item.addChild(self._file_item(f))
        item.setData(0, _LOADED_ROLE, True)
        if path not in self._watcher.directories():
            self._watcher.addPath(path)
        self._restore_expanded(item, expanded)
        if selected:
            for it in self._items_for(selected):
                self.tree.setCurrentItem(it)
                break

    # ------------------------------------------------------------ watching

    def _on_dir_changed(self, path: str) -> None:
        self._dirty.add(path)
        self._refresh_timer.start()

    def _refresh_dirty(self) -> None:
        dirty, self._dirty = self._dirty, set()
        if "/Volumes" in dirty:
            self._fill_volumes()
        for path in dirty:
            for item in self._items_for(path):
                if item.data(0, _LOADED_ROLE):
                    self._fill(item)

    # ------------------------------------------------------------- helpers

    def _all_roots(self) -> list[QTreeWidgetItem]:
        tops = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        return tops + [self._mac.child(i) for i in range(self._mac.childCount())]

    def _items_for(self, path: str) -> list[QTreeWidgetItem]:
        """Items showing *path* (a folder may appear under several roots)."""
        out = []
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item.data(0, _PATH_ROLE) == path:
                out.append(item)
            if item is self._mac or item.data(0, _LOADED_ROLE):
                stack.extend(item.child(i) for i in range(item.childCount()))
        return out

    @staticmethod
    def _child_by_name(item: QTreeWidgetItem, name: str) -> QTreeWidgetItem | None:
        for i in range(item.childCount()):
            child = item.child(i)
            p = child.data(0, _PATH_ROLE)
            if p and Path(p).name == name:
                return child
        return None

    def _expanded_paths(self, item: QTreeWidgetItem) -> set[str]:
        out: set[str] = set()
        stack = [item.child(i) for i in range(item.childCount())]
        while stack:
            it = stack.pop()
            if it.isExpanded() and it.data(0, _PATH_ROLE):
                out.add(it.data(0, _PATH_ROLE))
                stack.extend(it.child(i) for i in range(it.childCount()))
        return out

    def _restore_expanded(self, item: QTreeWidgetItem, expanded: set[str]) -> None:
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, _PATH_ROLE) in expanded:
                self._ensure_loaded(child)
                child.setExpanded(True)
                self._restore_expanded(child, expanded)

    # ------------------------------------------------------------- signals

    def _on_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        if item is self._up_item:
            self.go_up()
            return
        path = item.data(0, _PATH_ROLE)
        if path and item.childCount() and not is_iq_file(Path(path)):
            # Like Explorer: clicking a folder name toggles it too.
            self._ensure_loaded(item)
            item.setExpanded(not item.isExpanded())

    def _on_current_changed(self, item: QTreeWidgetItem | None, _prev: object) -> None:
        path = item.data(0, _PATH_ROLE) if item else None
        if path and is_iq_file(Path(path)) and Path(path).is_file():
            self._show_header(Path(path))
        elif path:
            self.header_info_text.clear()
            self.header_info_text.setPlaceholderText("ディレクトリが選択されています...")

    def _on_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path = item.data(0, _PATH_ROLE)
        if path and is_iq_file(Path(path)) and Path(path).is_file():
            self.status_message.emit(f"📄 読み込み中: {Path(path).name}")
            self.file_open_requested.emit(Path(path))

    _is_iq_file = staticmethod(is_iq_file)  # kept for callers of the old name

    def _show_header(self, file_path: Path) -> None:
        display_name = f"📄 {file_path.name}"
        rule = "=" * _display_width(display_name)
        try:
            if file_path.suffix in (".wvh", ".wvd"):
                wv_loader = WVFileLoader()
                header = wv_loader.parse_wvh(resolve_wvh(file_path))
                body = _format_wvh_header(wv_loader.wvh_path, wv_loader.wvd_path, header)
            elif file_path.suffix == ".wv":
                smu_loader = SMUWVLoader()
                header = smu_loader.parse_wv(file_path)
                body = _format_smuwv_header(file_path, header)
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

#!/usr/bin/env python3
"""
Rohde & Schwarz IQデータビューワー
大容量IQデータ（最大20GB）を効率的に処理するメモリ最適化版

ターゲット環境: Windows 11, Core i3, RAM 8GB
データ形式: WVH/WVDファイル（Rohde & Schwarz IQW）
"""

import sys
import threading
from pathlib import Path

import numpy as np

# Loaders, widgets and core utilities have moved into the iq_analyzer package.
# Keep the old names available at module scope so the rest of this script keeps
# working without change during the ongoing refactor.
from iq_analyzer.core import (
    StdoutRedirector,
    auto_optimize_params,
    compute_spectrogram,
    memory_status,
    min_max_downsample,
)
from iq_analyzer.loaders import IQTarLoader, WVFileLoader
from iq_analyzer.widgets import (
    AdjustmentPanel,
    ControlPanel,
    FileBrowserPanel,
    SpectrogramWidget,
)

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QFileDialog, QLabel, QSplitter, QDoubleSpinBox,
    QComboBox, QGroupBox, QMessageBox, QSpinBox, QCheckBox, QTextEdit,
    QTreeView, QFileSystemModel, QScrollArea, QSizePolicy, QMenu
)
from PySide6.QtCore import Qt, QRectF, Signal, QObject, QDir, QTimer, QCoreApplication
from PySide6.QtGui import QScreen, QKeySequence, QShortcut, QTextCursor

# PyQtGraphのインポート
import pyqtgraph as pg
from scipy import signal as scipy_signal

# ダークテーマ（オプショナル）
try:
    import qdarktheme
    HAS_DARKTHEME = True
except ImportError:
    HAS_DARKTHEME = False



class RSIQViewer(QMainWindow):
    """
    Rohde & Schwarz IQデータビューワー メインウィンドウ

    3つの表示エリア：
    1. 最上段: Region範囲のスペクトログラム
    2. 中段左: Region範囲の詳細波形（間引きなし）
    3. 最下段: 全体波形（間引きあり）+ Region選択
    """

    def __init__(self):
        super().__init__()
        self.wv_loader = None  # WVFileLoader or IQTarLoader
        self.file_type = None  # 'wv' or 'iqtar'
        self.total_samples = 0
        self.sample_rate = 1.0
        self.center_frequency = 0
        self.region_start = 0
        self.region_end = 100000

        # 間引きパラメータ
        self.decimation_threshold = 0.5  # 閾値以上の信号は間引かない（将来の拡張用）

        # 自動更新フラグ
        self.auto_update_spectrogram = False  # スペクトログラム自動更新

        # イベント管理の統合
        # これらのフラグを使って複雑なイベントチェーンを防止
        self._event_state = {
            'spectrogram_calculating': False,    # スペクトログラム計算中
            'region_updating': False,            # Region更新中
            'viewbox_updating': False,           # ViewBox更新中
            'programmatic_update': False,        # プログラム制御中
            'closing': False                      # 終了処理中
        }

        # 後方互換性のための旧フラグエイリアス
        self._spectrogram_calculating = False
        self.is_closing = False


        # プラットフォーム別フォントサイズ設定
        if sys.platform == 'win32':
            # Windows: 9ptに統一（コンパクト表示）
            self.font_size_large = 9
            self.font_size_small = 9
            # Windows: UI要素の高さ設定
            self.button_height = 35          # ボタン高さ
            self.control_panel_height = 45   # コントロールパネル高さ
            # 標準出力: 10行分の高さ（行間1.5倍を考慮）
            self.stdout_lines = 10
            self.stdout_min_height = int(self.font_size_large * 1.5 * self.stdout_lines)
            self.stdout_max_height = int(self.font_size_large * 1.5 * self.stdout_lines)
        else:
            # macOS/Linux: 大きめのフォント
            self.font_size_large = 20
            self.font_size_small = 16
            # macOS/Linux: UI要素の高さ設定
            self.button_height = 50
            self.control_panel_height = 60
            # 標準出力: 10行分の高さ（行間1.5倍を考慮）
            self.stdout_lines = 10
            self.stdout_min_height = int(self.font_size_large * 1.5 * self.stdout_lines)
            self.stdout_max_height = int(self.font_size_large * 1.5 * self.stdout_lines)

        self.init_ui()

    def init_ui(self):
        """UI初期化"""
        self.setWindowTitle("Rohde & Schwarz IQデータビューワー - [左側でファイル選択 | Ctrl+S: 保存 | Ctrl+Q: 終了]")

        # スクリーンサイズに応じてウィンドウサイズ調整
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()

        # 高さを画面いっぱい（95%）に設定し、アスペクト比を維持して横幅を計算
        # 元のアスペクト比: 1200:900 = 4:3
        aspect_ratio = 4.0 / 3.0

        # 高さを画面の95%に設定（タスクバー等を考慮）
        target_height = int(screen_geometry.height() * 0.95)

        # アスペクト比を維持して横幅を計算
        target_width = int(target_height * aspect_ratio / 1.333)  # 1.333 = 4/3の逆数

        # プラットフォームに応じた調整
        if sys.platform == 'win32':
            # Windows: DPIスケーリング対応
            dpi = screen.logicalDotsPerInch()
            scale_factor = dpi / 96.0
            if scale_factor > 1.0:
                # 高DPI環境では若干縮小
                target_width = int(target_width / scale_factor * 0.9)
                target_height = int(target_height / scale_factor * 0.9)

        # 画面幅を超えないように制限
        max_width = int(screen_geometry.width() * 0.85)
        if target_width > max_width:
            target_width = max_width
            # 幅が制限された場合、高さもアスペクト比に合わせて調整
            target_height = int(target_width * 1.333)

        # ウィンドウ位置を画面中央に配置
        x_pos = (screen_geometry.width() - target_width) // 2
        y_pos = (screen_geometry.height() - target_height) // 2

        self.setGeometry(x_pos, y_pos, target_width, target_height)

        # 中央ウィジェット
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # === コントロールパネル ===
        self.control_panel = ControlPanel(
            font_size_large=self.font_size_large,
            button_height=self.button_height,
            max_height=self.control_panel_height,
        )
        self.control_panel.calculate_clicked.connect(self.calculate_spectrogram)
        self.control_panel.save_clicked.connect(self.save_region)
        self.control_panel.exit_clicked.connect(self.safe_exit)
        self.control_panel.breadcrumb_path_clicked.connect(self._on_breadcrumb_clicked)
        # 旧 attribute alias（既存メソッドが直接参照しているため）
        self.calc_spec_btn = self.control_panel.calc_spec_btn
        self.save_btn = self.control_panel.save_btn
        main_layout.addWidget(self.control_panel)

        # === メインエリア（水平分割：ファイルブラウザ | プロット表示） ===
        main_splitter = QSplitter(Qt.Horizontal)

        # === 左側：ファイルブラウザ ===
        self.file_browser = FileBrowserPanel(
            font_size_large=self.font_size_large,
            font_size_small=self.font_size_small,
        )
        self.file_browser.current_dir_changed.connect(self.control_panel.set_breadcrumb_path)
        self.file_browser.file_open_requested.connect(
            lambda p: self.load_file(str(p))
        )
        self.file_browser.status_message.connect(
            lambda msg: self.status_label.setText(msg)
        )
        # 初期パンくず
        self.control_panel.set_breadcrumb_path(self.file_browser.current_root_dir)
        main_splitter.addWidget(self.file_browser)

        # === 右側：プロット表示エリア（垂直分割） ===
        plot_container = QWidget()
        plot_layout = QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(0, 0, 0, 0)

        # 上段: スペクトログラム表示のみ
        self.spectrogram_widget = SpectrogramWidget()

        # 中段: Region範囲の時間-振幅波形（スペクトログラムと横軸連動）
        self.region_plot = pg.PlotWidget()
        self.region_plot.setLabel('left', '振幅')
        self.region_plot.setLabel('bottom', '時間', units='s')
        self.region_plot.showGrid(x=True, y=True, alpha=0.3)
        self.region_plot.setTitle("Region範囲 時間-振幅波形")
        self.region_plot.setMinimumHeight(150)  # 最小高さ150px（マウス拡大で潰れないように）
        self.region_curve = self.region_plot.plot(pen=pg.mkPen('c', width=1))

        # スペクトログラムと横軸を連動（X軸同期、Y軸は自動調整）
        self.region_plot.setXLink(self.spectrogram_widget.plot_item)
        region_viewbox = self.region_plot.getViewBox()
        region_viewbox.enableAutoRange(axis='y', enable=True)

        # Y軸の移動・拡大縮小を無効化（ユーザー要望）
        region_viewbox.setMouseEnabled(x=True, y=False)  # X軸のみマウス操作可能

        # Region plotのViewBox範囲変更時に波形を再計算（デバウンス付き）
        self.region_viewbox = region_viewbox  # ViewBoxの参照を保存（シグナル切断用）
        self.region_update_timer = QTimer()
        self.region_update_timer.setSingleShot(True)
        self.region_update_timer.timeout.connect(self.on_region_viewbox_changed)
        self.region_update_delay = 500  # 500ms待機

        # ViewBoxトラッキング用のラムダ関数を保存（切断/再接続に使用）
        self._region_viewbox_handler = lambda: self.region_update_timer.start(self.region_update_delay)
        region_viewbox.sigRangeChanged.connect(self._region_viewbox_handler)

        # 下段: フルスパン波形表示（全幅）
        self.overview_plot = pg.PlotWidget()
        self.overview_plot.setLabel('left', '振幅')
        self.overview_plot.setLabel('bottom', '時間', units='s')
        self.overview_plot.showGrid(x=True, y=True, alpha=0.3)
        self.overview_plot.setTitle("全データ波形（Min-Maxダウンサンプリング / Region選択）")
        self.overview_curve = self.overview_plot.plot(pen=pg.mkPen('y', width=1))

        # ViewBox範囲変更時の再計算設定
        self.overview_viewbox = self.overview_plot.getViewBox()

        # Y軸の操作を無効化（X軸のみズーム/パン可能）
        self.overview_viewbox.setMouseEnabled(x=True, y=False)

        # Y軸の自動スケーリングを有効化（常に全データが見える）
        self.overview_viewbox.enableAutoRange(axis='y', enable=True)
        self.overview_viewbox.setAutoVisible(y=True)

        # プログラム制御フラグ（プログラムからのViewBox変更を識別）
        self._programmatic_overview_update = False

        # 処理ロックフラグ（連続したViewBox変更の並行実行を防止）
        self._overview_processing = False

        # ViewBox変更イベントを直接接続（フラグで制御）
        self.overview_viewbox.sigRangeChanged.connect(self.on_overview_range_changed)

        # Region選択
        self.region = pg.LinearRegionItem(
            values=(0, 100000),
            brush=(100, 100, 255, 30),
            pen=pg.mkPen('b', width=2)
        )
        # Region変更中はRegion波形のみ更新（軽量）
        self.region.sigRegionChanged.connect(self.on_region_changed)
        # Region変更完了後にスペクトログラムを更新（重量級処理）
        self.region.sigRegionChangeFinished.connect(self.on_region_change_finished)
        self.overview_plot.addItem(self.region)

        # コンテキストメニューの設定（元のメニューは維持）
        self.overview_plot.scene().sigMouseClicked.connect(self.on_overview_mouse_clicked)

        # スプリッターで3段に配置
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.spectrogram_widget)
        splitter.addWidget(self.region_plot)
        splitter.addWidget(self.overview_plot)

        # スプリッターのサイズ比率設定
        # スペクトログラム:Region波形:全体波形 = 3:2:1
        splitter.setSizes([600, 400, 200])

        plot_layout.addWidget(splitter)
        main_splitter.addWidget(plot_container)

        # メインスプリッターのサイズ比率設定
        # ファイルブラウザ:プロット表示 = 1:8 (幅を半分に)
        main_splitter.setSizes([150, 1200])

        # メインスプリッターをストレッチファクター1で追加（可変高さ）
        main_layout.addWidget(main_splitter, 1)

        # === 標準出力 + 調整パネル（横並び） ===
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)

        # 左側: 標準出力表示エリア
        stdout_group = QGroupBox("標準出力")
        stdout_layout = QVBoxLayout()

        self.stdout_text = QTextEdit()
        self.stdout_text.setReadOnly(True)
        self.stdout_text.setMinimumHeight(self.stdout_min_height)
        self.stdout_text.setMaximumHeight(self.stdout_max_height)
        # 垂直方向のサイズポリシー: 固定
        self.stdout_text.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.stdout_text.setStyleSheet(f"""
            QTextEdit {{
                background-color: #1e1e1e;
                color: #d4d4d4;
                font-family: 'SF Mono', 'Menlo', 'Consolas', 'Courier New', monospace;
                font-size: {self.font_size_large}pt;
                border: 1px solid #3e3e3e;
                line-height: 1.3;
            }}
        """)
        self.stdout_text.setPlaceholderText("標準出力がここに表示されます...")

        stdout_layout.addWidget(self.stdout_text)
        stdout_group.setLayout(stdout_layout)
        bottom_layout.addWidget(stdout_group, 3)  # 幅の比率3

        # 右側: 調整パネル
        self.adjust_panel = AdjustmentPanel()
        self.adjust_panel.colormap_changed.connect(self.on_colormap_changed)
        self.adjust_panel.cutoff_changed.connect(self.on_cutoff_changed)
        self.adjust_panel.auto_update_changed.connect(self._on_auto_update_toggled)
        # 旧 attribute alias（calculate_spectrogram などから直接参照されている）
        self.nfft_spin = self.adjust_panel.nfft_spin
        self.overlap_spin = self.adjust_panel.overlap_spin
        self.colormap_combo = self.adjust_panel.colormap_combo
        self.lower_cutoff_spin = self.adjust_panel.lower_cutoff_spin
        self.auto_update_checkbox = self.adjust_panel.auto_update_checkbox
        bottom_layout.addWidget(self.adjust_panel, 1)  # 幅の比率1

        # 標準出力+調整パネルをストレッチファクター0で追加（固定高さ）
        main_layout.addWidget(bottom_container, 0)

        # 標準出力リダイレクト設定
        self.stdout_redirector = StdoutRedirector(sys.stdout)
        self.stdout_redirector.text_written.connect(self.append_stdout)
        sys.stdout = self.stdout_redirector

        # ステータスバー（水平レイアウト）
        status_container = QWidget()
        status_layout = QHBoxLayout()
        status_layout.setContentsMargins(5, 5, 5, 5)
        status_container.setLayout(status_layout)

        # 左側：ステータスメッセージ
        self.status_label = QLabel("左側のファイルブラウザからファイルを選択してください")
        self.status_label.setStyleSheet(f"font-size: {self.font_size_large}pt; padding: 5px;")
        status_layout.addWidget(self.status_label, 1)  # ストレッチファクター1で伸縮

        # 右側：メモリ使用量表示
        self.memory_label = QLabel("メモリ: -- / -- GB")
        self.memory_label.setStyleSheet(f"font-size: {self.font_size_large}pt; padding: 5px; color: #888888;")
        self.memory_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        status_layout.addWidget(self.memory_label, 0)  # ストレッチファクター0で固定幅

        main_layout.addWidget(status_container)

        # メモリ更新タイマー（2秒ごと）
        self.memory_timer = QTimer()
        self.memory_timer.timeout.connect(self.update_memory_info)
        self.memory_timer.start(2000)  # 2秒間隔
        self.update_memory_info()  # 初回更新

        # キーボードショートカットの設定
        self.setup_shortcuts()


    def setup_shortcuts(self):
        """
        キーボードショートカットの設定

        - Ctrl+S / Cmd+S: Region範囲を保存
        - Ctrl+Q / Cmd+Q: 終了（確認なし）
        """
        # 保存 (Ctrl+S / Cmd+S)
        shortcut_save = QShortcut(QKeySequence.StandardKey.Save, self)
        shortcut_save.activated.connect(self.save_region)

        # 終了 (Ctrl+Q / Cmd+Q)
        shortcut_quit = QShortcut(QKeySequence.StandardKey.Quit, self)
        shortcut_quit.activated.connect(self.safe_exit)


    def cleanup_previous_data(self):
        """
        既存データのクリーンアップ

        別のファイルを開く前に、現在開いているファイルの
        メモリマップやプロットデータを完全に解放する。
        これにより、大容量ファイルを連続して開いてもメモリエラーを防ぐ。
        """
        try:
            print("既存データのクリーンアップ開始...")

            # ========================================
            # 1. プロットデータをクリア
            # ========================================
            if hasattr(self, 'overview_curve') and self.overview_curve is not None:
                try:
                    self.overview_curve.setData([], [])
                except:
                    pass

            # 下段Overview波形プロットをリセット（Regionは保持）
            if hasattr(self, 'overview_plot') and self.overview_plot is not None:
                try:
                    # カーブのみクリア（overview_curveを削除してから再作成）
                    if hasattr(self, 'overview_curve') and self.overview_curve is not None:
                        self.overview_plot.removeItem(self.overview_curve)
                    # 軸ラベルをリセット
                    self.overview_plot.setLabel('bottom', '時間', units='s')
                    self.overview_plot.setLabel('left', '振幅')
                    # プロットカーブを再作成
                    self.overview_curve = self.overview_plot.plot(pen=pg.mkPen('y', width=1))

                    # Regionを再作成（clear()で削除されるため）
                    if hasattr(self, 'region') and self.region is not None:
                        try:
                            self.overview_plot.removeItem(self.region)
                        except:
                            pass
                    # 新しいRegionを作成（初期位置は後で設定）
                    self.region = pg.LinearRegionItem(values=(0, 1), movable=True)
                    self.region.sigRegionChanged.connect(self.on_region_changed)
                    self.region.sigRegionChangeFinished.connect(self.on_region_change_finished)
                    self.overview_plot.addItem(self.region)
                except:
                    pass

            if hasattr(self, 'region_curve') and self.region_curve is not None:
                try:
                    self.region_curve.setData([], [])
                except:
                    pass

            # 中段Region波形プロットをリセット
            if hasattr(self, 'region_plot') and self.region_plot is not None:
                try:
                    # カーブのみクリア
                    if hasattr(self, 'region_curve') and self.region_curve is not None:
                        self.region_plot.removeItem(self.region_curve)
                    # 軸ラベルをリセット
                    self.region_plot.setLabel('bottom', '時間', units='s')
                    self.region_plot.setLabel('left', '振幅')
                    # プロットカーブを再作成
                    self.region_curve = self.region_plot.plot(pen=pg.mkPen('c', width=1))
                except:
                    pass

            if hasattr(self, 'spectrogram_widget') and self.spectrogram_widget is not None:
                # スペクトログラムデータを完全削除
                self.spectrogram_widget.spectrogram_data = None
                self.spectrogram_widget.frequencies = None
                self.spectrogram_widget.times = None
                self.spectrogram_widget.time_unit = None
                # ImageItemのデータのみクリア（ウィジェット自体は削除しない）
                if hasattr(self.spectrogram_widget, 'img_item') and self.spectrogram_widget.img_item is not None:
                    try:
                        # 空のデータでImageを更新（ImageItem自体は保持）
                        import numpy as np
                        empty_data = np.zeros((1, 1))
                        self.spectrogram_widget.img_item.setImage(empty_data)
                    except:
                        pass
                # 軸ラベルをリセット
                try:
                    self.spectrogram_widget.plot_item.setLabel('bottom', '時間')
                    self.spectrogram_widget.plot_item.setLabel('left', '周波数', units='MHz')
                except:
                    pass

            # ========================================
            # 2. Qtイベント処理
            # ========================================
            QApplication.processEvents()

            # ========================================
            # 3. メモリマップをクローズ
            # ========================================
            if hasattr(self, 'wv_loader') and self.wv_loader is not None:
                try:
                    self.wv_loader.close()
                except Exception as e:
                    pass  # エラーは無視して続行

            # ========================================
            # 4. 内部状態をリセット
            # ========================================
            self.total_samples = 0
            self.sample_rate = 1.0
            self.center_frequency = 0
            self.region_start = 0
            self.region_end = 100000

            # ========================================
            # 5. 強制ガベージコレクション（重要！）
            # ========================================
            import gc
            gc.collect()


        except Exception as e:
            pass  # エラーは無視して続行

    def load_file(self, file_path):
        """
        指定されたWVHファイルまたはiq.tarファイルを読み込む

        Args:
            file_path: ファイルパス（文字列またはPath）
        """
        # ファイル読み込み中はOverview ViewBox変更イベントを無視
        self._programmatic_overview_update = True
        print("[ファイル読み込み] プログラム制御モード ON")

        try:
            print("=" * 60)
            print(f"[ファイル読み込み開始] {Path(file_path).name}")
            print("=" * 60)

            # ========================================
            # 既存データのクリーンアップ（重要！）
            # ========================================
            print("既存データをクリーンアップ中...")
            self.cleanup_previous_data()
            print("クリーンアップ完了")

            self.status_label.setText("ファイル読み込み中...")
            QApplication.processEvents()

            # ファイル形式判定
            file_path_obj = Path(file_path)
            if file_path_obj.suffix == '.tar' or file_path.endswith('.iq.tar'):
                # iq.tarファイル
                print(f"形式: iq.tar")
                self.file_type = 'iqtar'
                self.wv_loader = IQTarLoader()

                # iq.tarファイル解析
                print("iq.tarファイルを解析中...")
                header = self.wv_loader.parse_iqtar(file_path)

                # バイナリデータをメモリマップとして開く
                print("データファイルをメモリマップで開いています...")
                self.wv_loader.open_data()

                # ファイル情報表示
                fname = file_path_obj.name
                size_gb = self.wv_loader.data_file_path.stat().st_size / 1e9
                print(f"データサイズ: {size_gb:.2f} GB")

            elif file_path_obj.suffix == '.wvh':
                # WVHファイル
                print(f"形式: WVH/WVD")
                self.file_type = 'wv'
                self.wv_loader = WVFileLoader()

                # WVHヘッダー解析
                print("WVHヘッダーを解析中...")
                header = self.wv_loader.parse_wvh(file_path)

                # WVDファイルをメモリマップとして開く
                print("WVDファイルをメモリマップで開いています...")
                self.wv_loader.open_wvd()

                # ファイル情報表示
                fname = file_path_obj.name
                size_gb = self.wv_loader.wvd_path.stat().st_size / 1e9
                print(f"WVDサイズ: {size_gb:.2f} GB")
            else:
                raise ValueError(f"未対応のファイル形式: {file_path_obj.suffix}")

            # パラメータ取得
            self.total_samples = header['SAMPLES']
            self.sample_rate = header['CLOCK']
            self.center_frequency = header.get('FREQUENCY', 0)

            print(f"サンプル数: {self.total_samples:,}")
            print(f"サンプリング周波数: {self.sample_rate/1e6:.2f} MHz")
            print(f"中心周波数: {self.center_frequency/1e6:.2f} MHz")

            # パンくずリストを読み込んだファイルのディレクトリに更新
            file_dir = Path(fname).parent
            self.update_breadcrumb(file_dir)

            # ステータスバーに読み込み完了を表示（詳細情報含む）
            self.status_label.setText(
                f"✅ 読み込み完了: {fname} | {self.total_samples:,} samples | "
                f"Fs={self.sample_rate/1e6:.1f} MHz | Fc={self.center_frequency/1e6:.1f} MHz | {size_gb:.2f} GB"
            )

            # Region初期化（中央50%）- 時間ベースで設定
            initial_start = int(self.total_samples * 0.25)
            initial_end = int(self.total_samples * 0.75)
            initial_start_time = initial_start / self.sample_rate
            initial_end_time = initial_end / self.sample_rate

            # ファイル読み込み時はRegionシグナルをブロック
            self.region.blockSignals(True)
            self.region.setRegion((initial_start_time, initial_end_time))
            self.region.blockSignals(False)

            self.region_start = initial_start
            self.region_end = initial_end
            print(f"Region初期化: {initial_start:,} ~ {initial_end:,} (中央50%, {initial_start_time:.6f}s ~ {initial_end_time:.6f}s)")

            # フルスパン波形の表示（Min-Maxダウンサンプリング）
            print("フルスパン波形を表示中...")

            # Overview波形の表示範囲をフルスパンにリセット（重要: Regionは50%のまま、表示のみフルスパン）
            # フラグベース制御により、誤発火を防止
            print("Overview表示範囲をフルスパンにリセット中...")
            self.reset_overview_to_fullspan()
            print("Overview表示範囲リセット完了")

            # 中段波形の初期表示（Region範囲）
            print("中段波形（Region範囲）を表示中...")
            # ファイル読み込み時のイベントループを防止
            self._event_state['programmatic_update'] = True
            try:
                self.update_region_waveform()
            finally:
                self._event_state['programmatic_update'] = False
            print("中段波形表示完了")

            # ボタン有効化
            self.calc_spec_btn.setEnabled(True)
            self.save_btn.setEnabled(True)

            # WVH/WVD不整合チェックと自動修正（WV形式のみ）
            if self.file_type == 'wv' and hasattr(self.wv_loader, 'header_mismatch') and self.wv_loader.header_mismatch:
                self.prompt_fix_wvh_header()

            print("=" * 60)
            print("[ファイル読み込み完了]")
            print("=" * 60)
            self.status_label.setText("ファイル読み込み完了")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイル読み込みエラー:\n{e}")
            self.status_label.setText(f"エラー: {e}")

        finally:
            # プログラム制御フラグをOFF（ユーザー操作を再び受け付ける）
            self._programmatic_overview_update = False
            print("[ファイル読み込み] プログラム制御モード OFF")

    def _should_skip_event_processing(self, event_name):
        """
        統合的なイベント処理スキップチェック

        Args:
            event_name: イベント名（ログ出力用）

        Returns:
            True: イベント処理をスキップすべき
            False: イベント処理を実行可能
        """
        # 終了処理中はスキップ（最優先）
        if self._event_state.get('closing', False) or self.is_closing:
            return True

        # wv_loaderが存在しない場合はスキップ
        if self.wv_loader is None:
            return True

        # スペクトログラム計算中はスキップ
        if self._event_state.get('spectrogram_calculating', False) or self._spectrogram_calculating:
            print(f"[{event_name}] スペクトログラム計算中のためスキップ")
            return True

        # プログラム制御中はスキップ
        if self._event_state.get('programmatic_update', False):
            print(f"[{event_name}] プログラム制御中のためスキップ")
            return True

        # _programmatic_overview_update の確認（後方互換性）
        if hasattr(self, '_programmatic_overview_update') and self._programmatic_overview_update:
            print(f"[{event_name}] プログラム制御中のためスキップ")
            return True

        # その他のイベント処理中チェック（必要に応じて）
        if event_name == "Region変更" and self._event_state.get('viewbox_updating', False):
            print(f"[{event_name}] ViewBox更新中のためスキップ")
            return True

        if event_name == "ViewBox変更" and self._event_state.get('region_updating', False):
            print(f"[{event_name}] Region更新中のためスキップ")
            return True

        return False

    def on_region_changed(self):
        """
        Region変更中の処理（ドラッグ中に連続呼び出し）

        軽量な処理（Region波形の更新）のみ実行。
        スペクトログラム計算は on_region_change_finished() で実行。
        """
        # 統合的なイベント処理チェック
        if self._should_skip_event_processing("Region変更"):
            return

        # イベント処理中フラグを設定
        self._event_state['region_updating'] = True

        try:
            region = self.region.getRegion()
            # 時間（秒）からサンプル番号に変換
            region_start_time, region_end_time = region
            self.region_start = int(max(0, region_start_time * self.sample_rate))
            self.region_end = int(min(self.total_samples, region_end_time * self.sample_rate))

            # Region範囲の波形を更新（軽量処理）
            # ViewBox更新によるイベント連鎖を防ぐため一時的にプログラム制御フラグを設定
            self._event_state['programmatic_update'] = True
            try:
                self.update_region_waveform()
            finally:
                self._event_state['programmatic_update'] = False

            # ステータス更新
            samples = self.region_end - self.region_start
            duration = samples / self.sample_rate

            if duration < 1e-3:
                duration_str = f"{duration*1e6:.2f} μs"
            elif duration < 1:
                duration_str = f"{duration*1e3:.2f} ms"
            else:
                duration_str = f"{duration:.3f} s"

            # 自動更新状態をステータスに表示
            auto_status = " [自動更新: ON]" if self.auto_update_spectrogram else ""
            self.status_label.setText(
                f"Region選択: {samples:,} samples ({duration_str}){auto_status}"
            )

            # 標準出力に情報を表示
            print("-" * 60)
            print(f"[Region変更]")
            print(f"  範囲: {self.region_start:,} ~ {self.region_end:,}")
            print(f"  サンプル数: {samples:,}")
            print(f"  継続時間: {duration_str}")
            print("-" * 60)
        finally:
            # イベント処理フラグをリセット
            self._event_state['region_updating'] = False

    def on_region_change_finished(self):
        """
        Region変更完了時の処理（ドラッグ完了後に1回だけ呼び出し）

        重量級処理（スペクトログラム計算）を実行。
        デバウンスタイマーを使用して、連続変更時は最後の変更から0.5秒後に計算開始。
        """
        # スペクトログラム自動更新（フラグがONの場合のみ）
        if self.auto_update_spectrogram:
            # デバウンスタイマーを再スタート（既に実行中なら中断して再開）
            if not hasattr(self, 'spectrogram_update_timer'):
                self.spectrogram_update_timer = QTimer()
                self.spectrogram_update_timer.setSingleShot(True)
                self.spectrogram_update_timer.timeout.connect(self.execute_spectrogram_calculation)

            # タイマーを500ms後に設定
            self.spectrogram_update_timer.start(500)
            print("[Region変更完了] スペクトログラム計算を0.5秒後に開始します")
        else:
            print("[Region変更完了] 自動更新OFF")

    def execute_spectrogram_calculation(self):
        """
        デバウンスタイマーから呼び出されるスペクトログラム計算実行メソッド

        最後のRegion変更から0.5秒経過後に実行される。
        """
        print("[スペクトログラム自動計算] 開始")
        self.calculate_spectrogram()

    def update_region_waveform(self):
        """
        Region範囲の時間-振幅波形を更新（現在のself.region_start/end使用）

        Region範囲のデータを読み込み、Min-Maxダウンサンプリングで表示。
        スペクトログラムと横軸（時間軸）を連動させる。
        """
        self.update_region_waveform_with_range(self.region_start, self.region_end)

    def update_region_waveform_with_range(self, start_sample, end_sample):
        """
        指定範囲の時間-振幅波形を更新

        Args:
            start_sample: 開始サンプル番号
            end_sample: 終了サンプル番号

        スペクトログラムと同じ範囲を表示するため、明示的に範囲を指定可能。
        時間単位もスペクトログラムと統一。
        """
        # アプリケーション終了時などでwv_loaderがNoneの場合は処理をスキップ
        if self.wv_loader is None or self.region_curve is None:
            return

        if self.total_samples == 0 or start_sample >= end_sample:
            self.region_curve.setData([], [])
            return

        try:
            # 目標ピクセル数（画面幅に応じて調整）
            target_pixels = 4000

            # Region範囲のMin-Maxダウンサンプリング（秒単位）
            x_data, y_data = self._minmax_downsample(start_sample, end_sample, target_pixels)

            # スペクトログラムの時間単位・スケールを取得
            if hasattr(self.spectrogram_widget, 'time_unit') and self.spectrogram_widget.time_unit:
                time_unit = self.spectrogram_widget.time_unit
                # 時間スケール変換
                if time_unit == 'ns':
                    x_data_scaled = x_data * 1e9
                elif time_unit == 'μs':
                    x_data_scaled = x_data * 1e6
                elif time_unit == 'ms':
                    x_data_scaled = x_data * 1e3
                else:  # 's'
                    x_data_scaled = x_data

                # 軸ラベル更新
                self.region_plot.setLabel('bottom', '時間', units=time_unit)
            else:
                # スペクトログラムが未計算の場合は秒単位
                x_data_scaled = x_data
                time_unit = 's'
                self.region_plot.setLabel('bottom', '時間', units='s')

            # プロット更新
            self.region_curve.setData(x_data_scaled, y_data)

            # Y軸の自動スケーリング
            self.region_plot.enableAutoRange(axis='y')

            # X軸をRegion全範囲に設定（View All）
            region_start_time = start_sample / self.sample_rate
            region_end_time = end_sample / self.sample_rate

            # スケール変換後の範囲
            if time_unit == 'ns':
                region_start_scaled = region_start_time * 1e9
                region_end_scaled = region_end_time * 1e9
            elif time_unit == 'μs':
                region_start_scaled = region_start_time * 1e6
                region_end_scaled = region_end_time * 1e6
            elif time_unit == 'ms':
                region_start_scaled = region_start_time * 1e3
                region_end_scaled = region_end_time * 1e3
            else:  # 's'
                region_start_scaled = region_start_time
                region_end_scaled = region_end_time

            # ViewBox範囲設定時にイベントループを防止
            region_viewbox = self.region_plot.getViewBox()

            # プログラム制御中の場合、ViewBoxイベントを一時的にブロック
            if self._event_state.get('programmatic_update', False):
                # sigRangeChangedをブロック
                region_viewbox.blockSignals(True)
                region_viewbox.setXRange(region_start_scaled, region_end_scaled, padding=0)
                region_viewbox.blockSignals(False)
            else:
                region_viewbox.setXRange(region_start_scaled, region_end_scaled, padding=0)

            print(f"[Region波形更新] サンプル: {start_sample:,} ~ {end_sample:,} | 点数: {len(x_data):,} | View All: {region_start_scaled:.6f}{time_unit} ~ {region_end_scaled:.6f}{time_unit}")

        except Exception as e:
            print(f"[ERROR] Region波形の更新に失敗: {e}")
            self.region_curve.setData([], [])

    def _minmax_downsample(self, start_sample, end_sample, target_pixels):
        """Min-Max ダウンサンプリング（iq_analyzer.core.min_max_downsample へ委譲）。"""
        return min_max_downsample(
            self.wv_loader.get_iq_data,
            start_sample,
            end_sample,
            target_pixels,
            self.sample_rate,
        )

    def show_fullspan_waveform(self):
        """
        全データ範囲の波形を表示（Min-Maxダウンサンプリング）

        ファイル読み込み後に一度だけ呼び出され、全データ範囲の波形を
        Min-Maxダウンサンプリングで表示する。Region変更時には再描画しない。
        """
        if self.total_samples == 0:
            print("[WARNING] total_samples=0のため、フルスパン波形を表示できません")
            return

        print(f"フルスパン波形を計算中... (0 ~ {self.total_samples:,} samples)")

        try:
            # 画面幅に応じた目標ピクセル数（4000ピクセル分）
            target_pixels = 4000

            # Min-Maxダウンサンプリング
            x_data, y_data = self._minmax_downsample(0, self.total_samples, target_pixels)

            # プロット
            self.overview_curve.setData(x_data, y_data)

            # Y軸の自動スケーリングを有効化
            self.overview_plot.enableAutoRange(axis='y')

            print(f"フルスパン波形表示完了 ({len(x_data):,} points)")

        except (MemoryError, np.core._exceptions._ArrayMemoryError) as e:
            # メモリ不足エラーの場合、ユーザーに通知して続行
            print(f"[ERROR] メモリ不足のため、フルスパン波形の表示をスキップします")
            print(f"  ファイルサイズが大きすぎます: {self.total_samples:,} samples")
            print(f"  解決策: より小さなRegion範囲を選択してください")
            # プレースホルダーとして空のプロットを表示
            self.overview_curve.setData([], [])
        except Exception as e:
            print(f"[ERROR] フルスパン波形の表示に失敗: {e}")
            import traceback
            traceback.print_exc()
            # プレースホルダーとして空のプロットを表示
            self.overview_curve.setData([], [])

    def on_overview_range_changed(self):
        """
        Overview波形のViewBox範囲変更時の処理

        フラグベースの制御により、プログラムからの変更を無視する。

        ズーム範囲に応じて波形を再計算：
        - 表示範囲が3.2M以下: 実波形表示
        - 表示範囲が3.2Mを超える: Min-Maxダウンサンプリング
        """
        # スペクトログラム計算中はスキップ（無限ループ防止 - 最重要）
        if self._spectrogram_calculating:
            print("[Overview範囲変更] スペクトログラム計算中のためスキップ")
            return

        # プログラム制御中はスキップ（プログラムからのViewBox変更を無視）
        if self._programmatic_overview_update:
            print("[Overview範囲変更] プログラム制御中のためスキップ")
            return

        # 処理中の場合はスキップ（連続したViewBox変更の並行実行を防止）
        if self._overview_processing:
            print("[Overview範囲変更] 既に処理中のためスキップ")
            return

        if self.total_samples == 0 or self.wv_loader is None:
            return

        # 処理ロックを取得し、ViewBoxのシグナルをブロック
        self._overview_processing = True
        self.overview_viewbox.blockSignals(True)

        try:
            # ViewBoxの表示範囲を取得
            overview_viewbox = self.overview_plot.getViewBox()
            view_range = overview_viewbox.viewRange()
            x_min, x_max = view_range[0]

            # Overview グラフは常に秒単位で動作
            # （スペクトログラムの時間単位変換は適用しない）
            x_min_sec = x_min
            x_max_sec = x_max

            # サンプル番号に変換
            start_sample = int(max(0, x_min_sec * self.sample_rate))
            end_sample = int(min(self.total_samples, x_max_sec * self.sample_rate))

            if end_sample <= start_sample:
                return

            total_samples = end_sample - start_sample

            print(f"[Overview ViewBox変更] 表示範囲: {start_sample:,} ~ {end_sample:,} ({total_samples:,} samples)")

            # 3.2M以下なら実波形、それ以上ならMin-Max間引き
            if total_samples <= 3200000:
                print(f"[Overview実波形表示] サンプル数: {total_samples:,} <= 3,200,000 → 間引きなし")
                # 全データをそのまま表示
                iq_data = self.wv_loader.get_iq_data(start_sample, end_sample)
                amplitudes = np.abs(iq_data)
                x_data = np.arange(start_sample, end_sample, dtype=np.float64) / self.sample_rate
                y_data = amplitudes.astype(np.float32)
            else:
                # Min-Maxダウンサンプリング
                target_pixels = 4000
                x_data, y_data = self._minmax_downsample(start_sample, end_sample, target_pixels)
                print(f"[Overview Min-Max間引き] サンプル数: {total_samples:,} > 3,200,000 → target_pixels: {target_pixels}")

            # プロット更新
            self.overview_curve.setData(x_data, y_data)
            print(f"[Overview波形更新] サンプル: {start_sample:,} ~ {end_sample:,} | 点数: {len(x_data):,}")

        except Exception as e:
            print(f"[ERROR] Overview ViewBox更新に失敗: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # ViewBoxのシグナルブロックを解除し、処理ロックを解放
            self.overview_viewbox.blockSignals(False)
            self._overview_processing = False

    def on_region_viewbox_changed(self):
        """
        Region plotのViewBox範囲変更時の処理

        ズーム範囲に応じて波形を再計算。
        - 表示範囲が3.2M以下: 実波形表示
        - 表示範囲が3.2Mを超える: Min-Maxダウンサンプリング
        """
        # 統合的なイベント処理チェック
        if self._should_skip_event_processing("ViewBox変更"):
            return

        # 処理中の場合はスキップ（並行実行を防止）
        if hasattr(self, '_overview_processing') and self._overview_processing:
            print("[ViewBox変更] 既に処理中のためスキップ")
            return

        # イベント処理中フラグを設定
        self._event_state['viewbox_updating'] = True
        self._overview_processing = True

        try:
            # ViewBoxの表示範囲を取得
            region_viewbox = self.region_plot.getViewBox()
            view_range = region_viewbox.viewRange()
            x_min, x_max = view_range[0]

            # スペクトログラムの時間単位を考慮
            if hasattr(self.spectrogram_widget, 'time_unit') and self.spectrogram_widget.time_unit:
                time_unit = self.spectrogram_widget.time_unit
                # 秒単位に変換
                if time_unit == 'ns':
                    x_min_sec = x_min / 1e9
                    x_max_sec = x_max / 1e9
                elif time_unit == 'μs':
                    x_min_sec = x_min / 1e6
                    x_max_sec = x_max / 1e6
                elif time_unit == 'ms':
                    x_min_sec = x_min / 1e3
                    x_max_sec = x_max / 1e3
                else:  # 's'
                    x_min_sec = x_min
                    x_max_sec = x_max
            else:
                x_min_sec = x_min
                x_max_sec = x_max

            # サンプル番号に変換
            start_sample = int(max(0, x_min_sec * self.sample_rate))
            end_sample = int(min(self.total_samples, x_max_sec * self.sample_rate))

            # Region範囲内に制限
            start_sample = max(self.region_start, start_sample)
            end_sample = min(self.region_end, end_sample)

            if end_sample <= start_sample:
                return

            print(f"[ViewBox変更] 表示範囲: {start_sample:,} ~ {end_sample:,} ({end_sample - start_sample:,} samples)")

            # 表示範囲の波形を再計算
            self.update_region_waveform_with_range(start_sample, end_sample)

        except Exception as e:
            print(f"[ERROR] Region ViewBox更新に失敗: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # イベント処理フラグをリセット
            self._event_state['viewbox_updating'] = False
            # 処理ロックを解放
            self._overview_processing = False

    def update_overview_waveform(self):
        """
        ViewBox範囲に応じたRegion波形の再計算

        表示範囲とピクセル幅に応じて適切なMin-Maxダウンサンプリングを実行。
        """
        if self.total_samples == 0:
            return

        try:
            # ViewBoxの表示範囲を取得（時間、秒）
            view_range = self.overview_viewbox.viewRange()
            x_min, x_max = view_range[0]

            # 時間（秒）からサンプル番号に変換
            start_sample = int(max(0, x_min * self.sample_rate))
            end_sample = int(min(self.total_samples, x_max * self.sample_rate))

            if end_sample <= start_sample:
                return

            # ViewBoxのピクセル幅を取得
            view_rect = self.overview_viewbox.viewRect()
            widget_rect = self.overview_plot.rect()

            # ピクセル幅を推定（ウィジェット幅の80%程度）
            target_pixels = int(widget_rect.width() * 0.8)
            target_pixels = max(100, min(target_pixels, 4000))  # 100-4000の範囲

            # Min-Maxダウンサンプリング
            x_data, y_data = self._minmax_downsample(start_sample, end_sample, target_pixels)

            # プロット更新
            self.overview_curve.setData(x_data, y_data)

            # Y軸は常に自動調整（X軸のみ変更）
            self.overview_plot.enableAutoRange(axis='y')

            print(f"[ViewBox更新] 範囲: {start_sample:,} ~ {end_sample:,} | ピクセル: {target_pixels} | 点数: {len(x_data):,}")

        except Exception as e:
            print(f"[ERROR] Region波形の更新に失敗: {e}")

    def on_overview_mouse_clicked(self, event):
        """
        Region波形のマウスクリックイベント処理

        右クリック時にカスタムコンテキストメニューを表示。
        元のpyqtgraphメニューは左クリック時に表示される。
        """
        if event.button() == Qt.RightButton:
            # 右クリック時：カスタムメニューを表示
            self.show_overview_context_menu(event.screenPos())
            event.accept()
        # 左クリックは通常処理（pyqtgraphのデフォルト動作）

    def show_overview_context_menu(self, screen_pos):
        """
        Region波形のカスタムコンテキストメニューを表示

        Args:
            screen_pos: メニュー表示位置（QPointF、スクリーン座標）
        """
        menu = QMenu()
        menu.setStyleSheet(f"QMenu {{ font-size: {self.font_size_large}pt; }}")

        # フルスパンに戻るアクション
        reset_action = menu.addAction("🔄 フルスパン表示に戻る")
        reset_action.triggered.connect(self.reset_overview_to_fullspan)

        # 表示範囲をRegionとして設定
        set_region_action = menu.addAction("📍 表示範囲をRegionに設定")
        set_region_action.triggered.connect(self.set_viewrange_as_region)

        # メニュー表示
        menu.exec(screen_pos.toPoint())

    def reset_overview_to_fullspan(self):
        """
        Region波形をフルスパン表示に戻す

        X軸の表示範囲を全データ範囲（0 ~ total_time）にリセット。
        フラグベース制御により、ViewBox変更イベントの誤発火を防止。
        """
        if self.total_samples == 0:
            return

        # プログラム制御フラグをON（ViewBox変更イベントを無視させる）
        self._programmatic_overview_update = True
        print("[フルスパン表示] プログラム制御モード ON")

        try:
            # 時間範囲を計算（秒）
            total_time = self.total_samples / self.sample_rate

            # ViewBoxの範囲を全データ範囲に設定（時間ベース）
            self.overview_viewbox.setXRange(0, total_time, padding=0)

            print(f"[フルスパン表示] ViewBox範囲を設定: 0 ~ {total_time:.6f} s = {self.total_samples:,} samples")

            # フルスパン波形を明示的に表示
            self.show_fullspan_waveform()

            print("[フルスパン表示] 完了")

        finally:
            # プログラム制御フラグをOFF（ユーザー操作を再び受け付ける）
            self._programmatic_overview_update = False
            print("[フルスパン表示] プログラム制御モード OFF")

    def set_viewrange_as_region(self):
        """
        現在の表示範囲をRegionとして設定

        ズームイン状態で、表示されている範囲をスペクトログラム解析の
        Region範囲として設定する。
        """
        if self.total_samples == 0:
            return

        # ViewBoxの現在の表示範囲を取得（時間、秒）
        view_range = self.overview_viewbox.viewRange()
        x_min, x_max = view_range[0]

        # 時間（秒）からサンプル数に変換（境界チェック）
        new_start = int(max(0, x_min * self.sample_rate))
        new_end = int(min(self.total_samples, x_max * self.sample_rate))

        if new_end <= new_start:
            print("[WARNING] 無効な表示範囲です")
            return

        # Regionを更新（時間ベースで設定）
        # プログラムによる更新時はシグナルをブロック
        self._event_state['programmatic_update'] = True
        self.region.blockSignals(True)
        try:
            self.region.setRegion((x_min, x_max))
        finally:
            self.region.blockSignals(False)
            self._event_state['programmatic_update'] = False

        self.region_start = new_start
        self.region_end = new_end

        # ステータス更新
        samples = new_end - new_start
        duration = samples / self.sample_rate

        if duration < 1e-3:
            duration_str = f"{duration*1e6:.2f} μs"
        elif duration < 1:
            duration_str = f"{duration*1e3:.2f} ms"
        else:
            duration_str = f"{duration:.3f} s"

        print(f"[Region更新] 表示範囲をRegionに設定: {new_start:,} ~ {new_end:,} ({samples:,} samples, {duration_str})")

        # 自動更新がONの場合はスペクトログラムを計算
        if self.auto_update_spectrogram:
            self.calculate_spectrogram()

    def auto_optimize_spectrogram_params(self, region_samples):
        """スペクトログラムパラメータ自動最適化（iq_analyzer.core.auto_optimize_params へ委譲）。"""
        params = auto_optimize_params(region_samples, self.sample_rate)
        return params.nfft, params.overlap_percent, params.window, params.message

    def calculate_spectrogram(self):
        """
        Region範囲のスペクトログラム計算（信号抽出最適化版）

        メモリ効率を考慮し、適切なNFFT、オーバーラップを使用。
        短いパルスも漏れなく抽出するため、自動パラメータ最適化を実装。
        大容量データでもメモリエラーが発生しないよう注意深く実装。
        """
        if self.region_start >= self.region_end:
            QMessageBox.warning(self, "警告", "有効なRegion範囲を選択してください")
            return

        # スペクトログラム計算中フラグを設定（無限ループ防止 - 最優先）
        self._spectrogram_calculating = True
        print("[スペクトログラム計算] 計算開始 - Region変更イベントをブロック中")

        # ViewBoxトラッキングを完全に切断（無限ループ防止）
        # タイマー停止だけでは不十分：シグナルも切断する必要がある
        if hasattr(self, 'region_viewbox') and hasattr(self, '_region_viewbox_handler'):
            try:
                self.region_viewbox.sigRangeChanged.disconnect(self._region_viewbox_handler)
                print("[Region ViewBoxトラッキング] シグナル切断完了")
            except Exception as e:
                print(f"[Region ViewBoxトラッキング] シグナル切断エラー: {e}")

        try:
            # スペクトログラム計算に使用する範囲を保存（中段波形との同期用）
            spectrogram_start = self.region_start
            spectrogram_end = self.region_end
            region_samples = spectrogram_end - spectrogram_start

            # パラメータ自動最適化（信号漏れ防止）
            optimal_nfft, optimal_overlap, optimal_window, opt_msg = \
                self.auto_optimize_spectrogram_params(region_samples)

            # 現在のUI設定値を取得
            current_nfft = self.nfft_spin.value()
            current_overlap = self.overlap_spin.value()

            # 最適化パラメータを自動適用（ダイアログなし）
            nfft = optimal_nfft
            overlap_percent = optimal_overlap
            window = optimal_window

            # UI設定を更新
            self.nfft_spin.setValue(nfft)
            self.overlap_spin.setValue(overlap_percent)

            # ログ出力
            if abs(current_nfft - optimal_nfft) > optimal_nfft * 0.5 or \
               abs(current_overlap - optimal_overlap) > 20:
                print(f"[パラメータ自動最適化] {opt_msg}")
                print(f"  NFFT: {current_nfft} → {nfft}")
                print(f"  オーバーラップ: {current_overlap}% → {overlap_percent}%")
                print(f"  ウィンドウ: {window}")
            else:
                print(f"[パラメータ最適化] {opt_msg}")
                print(f"  NFFT: {nfft}, オーバーラップ: {overlap_percent}%, ウィンドウ: {window}")

            # メモリ使用量推定（8GB RAM環境での安全性チェック）
            iq_data_mb = (region_samples * 8) / 1024 / 1024  # complex64
            time_frames = int(region_samples / (nfft * (1 - overlap_percent/100)))
            spectrogram_mb = (nfft * time_frames * 4) / 1024 / 1024  # float32
            total_estimated_mb = iq_data_mb + spectrogram_mb

            # メモリ使用量をログに出力（警告ダイアログは表示しない）
            if total_estimated_mb > 2000:
                print(f"[メモリ使用量] 推定 {total_estimated_mb/1024:.2f} GB（大容量処理）")
                print(f"  8GB RAM環境では処理に時間がかかる可能性があります")
                # ダイアログなしで処理を続行

            # 計算中の表示と操作制限
            self.status_label.setText("⏳ スペクトログラム計算中...")
            self.calc_spec_btn.setEnabled(False)
            self.calc_spec_btn.setText("計算中...")
            QApplication.processEvents()

            print("=" * 60)
            print("[スペクトログラム計算開始]")
            print(f"  サンプル数: {region_samples:,}")
            print(f"  時間長: {region_samples/self.sample_rate*1e6:.3f} μs")
            print(f"  NFFT: {nfft}")
            print(f"  オーバーラップ: {overlap_percent}%")
            print(f"  ウィンドウ関数: {window}")
            print(f"  推定メモリ使用量: {total_estimated_mb:.1f} MB")
            print("=" * 60)

            # IQデータ取得（保存した範囲を使用）
            print("IQデータを読み込み中...")
            iq_data = self.wv_loader.get_iq_data(
                start_sample=spectrogram_start,
                end_sample=spectrogram_end
            )
            print(f"データ読み込み完了: {len(iq_data):,} samples")

            # STFT を core ヘルパへ委譲
            def _report(done, total):
                print(f"  進捗: {done / total * 100:.1f}% ({done}/{total})")

            frequencies, times, sxx_db = compute_spectrogram(
                iq_data,
                nfft=nfft,
                overlap_percent=overlap_percent,
                window=window,
                sample_rate=self.sample_rate,
                progress=_report,
            )

            print(f"計算完了")
            print(f"  スペクトログラム形状: {sxx_db.shape}")
            print(f"  周波数ビン数: {len(frequencies)}")
            print(f"  時間フレーム数: {len(times)}")
            print(f"  周波数範囲: {frequencies[0]/1e6:.2f} ~ {frequencies[-1]/1e6:.2f} MHz")
            print("=" * 60)

            # 時間軸を全データ内の絶対時間に変換
            # scipy.signal.spectrogramは0から始まる相対時間を返すため、
            # Region開始位置の時刻を加算して絶対時刻に変換
            region_start_time = spectrogram_start / self.sample_rate
            times_absolute = times + region_start_time

            # 表示更新
            print("スペクトログラムを表示中...")

            # スペクトログラム更新中はRegionウィジェット全体のシグナルをブロック
            # （無限ループ防止 - Qt公式メソッド）
            self.region.blockSignals(True)

            try:
                if self.spectrogram_widget is not None:
                    self.spectrogram_widget.update_spectrogram(
                        frequencies,
                        times_absolute,  # 絶対時刻を使用
                        sxx_db,
                        center_freq=self.center_frequency
                    )
                else:
                    print("[ERROR] spectrogram_widgetがNoneです")
            finally:
                # シグナルブロックを解除
                self.region.blockSignals(False)
                print("[Region シグナルブロック解除]")

            print("[スペクトログラム計算完了]")

            # スペクトログラム表示最適化後の最終的なRegion範囲を取得して、
            # 中段波形を同期更新（Region範囲が変更されている可能性があるため）
            region = self.region.getRegion()
            region_start_time, region_end_time = region
            self.region_start = int(max(0, region_start_time * self.sample_rate))
            self.region_end = int(min(self.total_samples, region_end_time * self.sample_rate))

            # 中段波形を最終的なRegion範囲で更新
            # プログラム制御フラグを設定してイベントループを防止
            print(f"[スペクトログラム完了後の同期] Region範囲: {self.region_start} ~ {self.region_end}")
            self._event_state['programmatic_update'] = True
            try:
                self.update_region_waveform()
            finally:
                self._event_state['programmatic_update'] = False

            self.status_label.setText("✓ スペクトログラム計算完了")

        except MemoryError:
            QMessageBox.critical(
                self,
                "メモリエラー",
                "メモリ不足です。NFFT値を小さくするか、Region範囲を狭めてください。"
            )
            self.status_label.setText("❌ メモリエラー")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"スペクトログラム計算エラー:\n{e}")
            self.status_label.setText(f"❌ エラー: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # ViewBoxトラッキングを再接続（正常終了/エラー終了に関わらず）
            if hasattr(self, 'region_viewbox') and hasattr(self, '_region_viewbox_handler'):
                try:
                    self.region_viewbox.sigRangeChanged.connect(self._region_viewbox_handler)
                    print("[Region ViewBoxトラッキング] シグナル再接続完了")
                except Exception as e:
                    print(f"[Region ViewBoxトラッキング] シグナル再接続エラー: {e}")

            # スペクトログラム計算中フラグをOFF（正常終了/エラー終了に関わらず）
            self._spectrogram_calculating = False
            print("[スペクトログラム計算] 計算完了 - Region変更イベントのブロック解除")

            # ボタンの状態を復元
            self.calc_spec_btn.setEnabled(True)
            self.calc_spec_btn.setText("スペクトログラム計算 (手動)")

    def save_region(self):
        """Region範囲のデータをWVH/WVD形式で保存"""
        if self.region_start >= self.region_end:
            QMessageBox.warning(self, "警告", "有効なRegion範囲を選択してください")
            return

        # デフォルトファイル名生成
        if self.file_type == 'wv' and hasattr(self.wv_loader, 'wvh_path') and self.wv_loader.wvh_path:
            default_name = self.wv_loader.wvh_path.stem + f"_region_{self.region_start}_{self.region_end}"
        elif self.file_type == 'iqtar' and hasattr(self.wv_loader, 'tar_path') and self.wv_loader.tar_path:
            default_name = self.wv_loader.tar_path.stem + f"_region_{self.region_start}_{self.region_end}"
        else:
            default_name = f"region_{self.region_start}_{self.region_end}"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存先を選択（WVH/WVD形式）",
            default_name,
            "WV Files (*.wvh);;All Files (*)"
        )

        if not file_path:
            return

        try:
            self.status_label.setText("データ保存中...")
            QApplication.processEvents()

            print("=" * 60)
            print("[Region保存開始]")
            print(f"  保存先: {file_path}")
            print(f"  Range: {self.region_start:,} ~ {self.region_end:,}")
            print(f"  サンプル数: {self.region_end - self.region_start:,}")
            print("=" * 60)

            # IQデータ取得
            print("IQデータを読み込み中...")
            iq_data = self.wv_loader.get_iq_data(
                start_sample=self.region_start,
                end_sample=self.region_end
            )
            print(f"データ読み込み完了: {len(iq_data):,} samples")

            # IQデータをint16形式に変換（WVH/WVD形式はRAW16LE）
            # iq.tarの場合はfloat32なので、適切にスケーリングが必要
            if self.file_type == 'iqtar':
                # float32 -> int16 変換
                # 正規化してint16の範囲にマッピング
                max_val = np.max(np.abs(iq_data))
                if max_val > 0:
                    # int16の最大値（32767）でスケーリング
                    scale_factor = 32767.0 / max_val
                    iq_data_scaled = iq_data * scale_factor
                else:
                    iq_data_scaled = iq_data

                # int16範囲にクリップ
                iq_data = np.clip(iq_data_scaled, -32768, 32767)

            # WVH/WVD形式で保存
            # ヘッダー情報は元ファイルから継承（TYPE, COMPONENTS等は上書き）
            header_template = self.wv_loader.header.copy()

            # WVH/WVD形式に必要なフィールドを追加/上書き
            if self.file_type == 'iqtar':
                header_template['TYPE'] = 'RAW16LE'
                header_template['COMPONENTS'] = 'IQ'
                header_template['RESOLUTION'] = 16

            print("WVH/WVD形式で保存中...")
            wvh_path, wvd_path = WVFileLoader.save_region_as_wv(
                save_path=file_path,
                iq_data=iq_data,
                header_template=header_template
            )

            wvd_size_mb = wvd_path.stat().st_size / 1e6
            print(f"保存完了:")
            print(f"  WVHファイル: {wvh_path.name}")
            print(f"  WVDファイル: {wvd_path.name} ({wvd_size_mb:.2f} MB)")
            print("=" * 60)
            print("[Region保存完了]")
            print("=" * 60)

            # 標準出力に情報を表示
            self.status_label.setText(f"✅ 保存完了: {wvh_path.name}, {wvd_path.name}")
            QMessageBox.information(
                self,
                "完了",
                f"Region範囲を保存しました:\n\n"
                f"WVH: {wvh_path.name}\n"
                f"WVD: {wvd_path.name}\n"
                f"サンプル数: {len(iq_data):,}"
            )

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"保存エラー:\n{e}")
            self.status_label.setText(f"保存エラー: {e}")
            import traceback
            traceback.print_exc()

    def on_colormap_changed(self, colormap_name):
        """カラーマップ変更"""
        self.spectrogram_widget.set_colormap(colormap_name)

    def on_cutoff_changed(self, value):
        """
        下位カットオフ変更時の処理

        スペクトログラムの表示レベルを再調整して、
        ノイズフロアをカットし小信号を相対的に強調する。

        Args:
            value: 下位カットオフパーセンタイル (%)
        """
        if not hasattr(self.spectrogram_widget, 'current_sxx_db'):
            # スペクトログラムがまだ計算されていない
            return

        sxx_db = self.spectrogram_widget.current_sxx_db

        # パーセンタイルベースでレベルを再計算
        min_level = np.percentile(sxx_db, value)
        max_level = np.percentile(sxx_db, 100)  # 最大値は固定

        # レベルを更新
        self.spectrogram_widget.hist.setLevels(min_level, max_level)
        self.spectrogram_widget.img_item.setLevels([min_level, max_level])

        # 更新された値を保存
        self.spectrogram_widget.current_min_level = min_level

    def update_memory_info(self):
        """メモリ使用量を表示（実体は iq_analyzer.core.memory.memory_status）。"""
        try:
            status = memory_status()
            self.memory_label.setText(status.label())
            self.memory_label.setStyleSheet(
                f"font-size: {self.font_size_large}pt; padding: 5px; color: {status.color};"
            )
        except Exception:
            self.memory_label.setText("メモリ: -- / -- GB")
            self.memory_label.setStyleSheet(
                f"font-size: {self.font_size_large}pt; padding: 5px; color: #888888;"
            )

    def _on_breadcrumb_clicked(self, path):
        """パンくずリストのボタンが押されたら FileBrowserPanel に伝える。"""
        self.file_browser.set_root_dir(path)

    def _on_auto_update_toggled(self, enabled):
        """AdjustmentPanel.auto_update_changed (bool) ハンドラ。"""
        self.auto_update_spectrogram = bool(enabled)
        if self.auto_update_spectrogram:
            if self.total_samples > 0 and self.region_start < self.region_end:
                self.calculate_spectrogram()
        # ステータス再描画
        self.on_region_changed()


    def append_stdout(self, text):
        """
        標準出力テキストをテキストエリアに追加

        print()の出力ごとに即座に表示されるようにUIを強制更新する。
        ただし、終了処理中はprocessEvents()を呼ばない（segfault回避）。

        Args:
            text: 追加するテキスト
        """
        # 終了処理中は何もしない（Segmentation fault回避）
        if self.is_closing:
            return

        try:
            # テキストを追加
            self.stdout_text.append(text.rstrip())

            # 自動スクロール（最新の出力が常に表示される）
            cursor = self.stdout_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.stdout_text.setTextCursor(cursor)

            # 最大行数制限（メモリ節約のため、古い行を削除）
            max_lines = 1000
            text_lines = self.stdout_text.toPlainText().split('\n')
            if len(text_lines) > max_lines:
                # 古い行を削除
                self.stdout_text.setPlainText('\n'.join(text_lines[-max_lines:]))
                # カーソルを最後に移動
                cursor = self.stdout_text.textCursor()
                cursor.movePosition(QTextCursor.End)
                self.stdout_text.setTextCursor(cursor)

            # UIを即座に更新（print()ごとにリアルタイム表示）
            # ただし終了処理中は呼ばない
            if not self.is_closing:
                QCoreApplication.processEvents()
        except:
            # 終了処理中にオブジェクトが削除されている場合は無視
            pass

    def safe_exit(self):
        """
        安全にアプリケーションを終了

        確認ダイアログなしで、適切なクリーンアップ処理を実行してから終了。
        closeEventでクリーンアップが行われるため、ここでは最小限の処理のみ。
        """
        # ウィンドウを閉じる（closeEventが呼ばれてクリーンアップされる）
        self.close()

    def prompt_fix_wvh_header(self):
        """
        WVH/WVD不整合検出時に修正を促すダイアログ

        ユーザーに確認し、承認された場合は自動的にWVHファイルを修正する。
        """
        # 不整合の詳細情報
        actual_samples = self.wv_loader.header['SAMPLES']
        wvh_path = self.wv_loader.wvh_path
        wvd_path = self.wv_loader.wvd_path

        message = (
            f"WVHヘッダーとWVDファイルサイズに不整合が検出されました。\n\n"
            f"ファイル: {wvh_path.name}\n"
            f"実際のサンプル数: {actual_samples:,}\n\n"
            f"WVDファイルに問題がないことを確認しました。\n"
            f"元のWVHファイルを .bak として保存し、\n"
            f"正しいヘッダー情報を含む新しいWVHファイルを作成しますか？"
        )

        reply = QMessageBox.question(
            self,
            "WVHファイル修正の確認",
            message,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            try:
                # WVHファイル修正実行
                self.wv_loader.fix_wvh_header()

                QMessageBox.information(
                    self,
                    "修正完了",
                    f"WVHファイルを修正しました。\n\n"
                    f"元のファイル: {wvh_path.with_suffix('.wvh.bak').name}\n"
                    f"修正後: {wvh_path.name}\n"
                    f"正しいサンプル数: {actual_samples:,}"
                )

            except Exception as e:
                QMessageBox.critical(
                    self,
                    "修正エラー",
                    f"WVHファイルの修正中にエラーが発生しました:\n{e}"
                )
        else:
            QMessageBox.information(
                self,
                "キャンセル",
                "WVHファイルは修正されませんでした。\n"
                "実際のWVDファイルサイズに基づいてデータを読み込みます。"
            )

    def closeEvent(self, event):
        """
        ウィンドウクローズ時のクリーンアップ

        Segmentation fault回避のため、以下の順序で解放:
        0. 終了フラグをセット（これ以降はprocessEvents()を呼ばない）
        1. 標準出力のシグナル接続を切断（最優先）
        2. メモリ更新タイマーを停止
        3. メモリマップを先にクローズ
        4. プロットデータをクリア
        5. 標準出力を元に戻す
        6. ガベージコレクション

        Cmd+Q（Ctrl+Q）でも安全に終了できるよう設計。
        """
        try:
            # 0. 終了フラグをセット（これ以降、append_stdoutはprocessEvents()を呼ばない）
            self.is_closing = True

            # 1. 標準出力のシグナル接続を最初に切断（最重要！）
            if hasattr(self, 'stdout_redirector') and self.stdout_redirector:
                try:
                    # シグナル接続を切断
                    self.stdout_redirector.text_written.disconnect()
                except:
                    pass
                try:
                    # 標準出力を元に戻す
                    sys.stdout = self.stdout_redirector.original_stdout
                    self.stdout_redirector = None
                except:
                    pass

            # 2. メモリ更新タイマーを停止
            if hasattr(self, 'memory_timer') and self.memory_timer:
                try:
                    self.memory_timer.stop()
                    self.memory_timer.deleteLater()
                    self.memory_timer = None
                except:
                    pass

            # 3. すべてのシグナル接続を切断
            try:
                if hasattr(self, 'region') and self.region is not None:
                    try:
                        self.region.sigRegionChanged.disconnect()
                    except:
                        pass
                    try:
                        self.region.sigRegionChangeFinished.disconnect()
                    except:
                        pass
            except:
                pass

            try:
                if hasattr(self, 'region_viewbox') and self.region_viewbox is not None:
                    try:
                        self.region_viewbox.sigRangeChanged.disconnect()
                    except:
                        pass
            except:
                pass

            # 4. メモリマップを先にクローズ（プロットデータより前に）
            if hasattr(self, 'wv_loader') and self.wv_loader is not None:
                try:
                    self.wv_loader.close()
                    self.wv_loader = None
                except:
                    pass

            # 5. プロットデータをクリア（メモリマップへの参照を解放）
            try:
                if hasattr(self, 'overview_curve') and self.overview_curve is not None:
                    self.overview_curve.setData([], [])
                    self.overview_curve.clear()
                    self.overview_curve = None
            except:
                pass

            try:
                if hasattr(self, 'region_curve') and self.region_curve is not None:
                    self.region_curve.setData([], [])
                    self.region_curve.clear()
                    self.region_curve = None
            except:
                pass

            try:
                if hasattr(self, 'spectrogram_widget') and self.spectrogram_widget is not None:
                    if hasattr(self.spectrogram_widget, 'img_item') and self.spectrogram_widget.img_item is not None:
                        self.spectrogram_widget.img_item.clear()
                        self.spectrogram_widget.img_item = None
                    self.spectrogram_widget = None
            except:
                pass

            # 6. PlotItems自体を削除
            try:
                if hasattr(self, 'overview') and self.overview is not None:
                    self.overview.clear()
                    self.overview = None
            except:
                pass

            try:
                if hasattr(self, 'region') and self.region is not None:
                    if hasattr(self.region, 'lines'):
                        for line in self.region.lines:
                            try:
                                line.setParentItem(None)
                            except:
                                pass
                    self.region = None
            except:
                pass

            # 5. ガベージコレクション
            import gc
            gc.collect()

        except:
            pass

        # イベントを受け入れてウィンドウを閉じる
        event.accept()


def main():
    """
    メイン関数

    Segmentation fault回避のため、適切なクリーンアップを実施する。
    Cmd+Q（Ctrl+Q）でも安全に終了できるよう設計。
    """
    # Qtアプリケーション作成
    app = QApplication(sys.argv)

    # アプリケーション全体のフォントサイズをプラットフォーム別に設定
    from PySide6.QtGui import QFont
    app_font = QFont()
    if sys.platform == 'win32':
        app_font.setPointSize(9)  # Windows: 9ptに統一（コンパクト表示）
    else:
        app_font.setPointSize(20)  # macOS/Linux: 大きめ
    app.setFont(app_font)

    # ダークテーマ適用（利用可能な場合）
    if HAS_DARKTHEME:
        try:
            app.setStyleSheet(qdarktheme.load_stylesheet())
        except:
            pass

    # メインウィンドウ作成・表示
    viewer = RSIQViewer()
    viewer.show()

    # 起動メッセージ
    print("=" * 60)
    print("Rohde & Schwarz IQ Data Viewer")
    print("Target: Windows 11, Core i3, 8GB RAM")
    print("Supported formats: WVH/WVD, iq.tar")
    print("=" * 60)
    print("アプリケーションが起動しました。")
    print("左側のファイルブラウザからファイルをダブルクリックしてください。")

    # アプリケーション実行
    exit_code = app.exec()

    # ========================================
    # クリーンアップ（Cmd+Q対応）
    # closeEventで既にクリーンアップされているため、
    # ここでは最小限の処理のみ行う
    # ========================================

    # ガベージコレクション
    import gc
    gc.collect()

    # 正常終了
    sys.exit(exit_code)


if __name__ == '__main__':
    main()

# IQ Analyzer

[![CI](https://github.com/mashi727/iq-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/mashi727/iq-analyzer/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

GUI viewer for very large IQ recordings from Rohde & Schwarz and Keysight test
equipment. Designed to render and explore captures up to ~20 GB on commodity
hardware (Windows 11, Core i3, 8 GB RAM) without ever loading the file into RAM.

## 主な機能

- **大容量対応** — `numpy.memmap` ベースの遅延読み込み。20 GB クラスの IQ
  ファイルでも RSS 2.5 GB 程度に収まる
- **3 フォーマット対応** — 同じ UI から透過的に開ける
  - Rohde & Schwarz **WVH / WVD** (RAW16LE)
  - Rohde & Schwarz **iq.tar** (float32 / float64)
  - Keysight **N5110A `.bin` + `.bin.txt`** (16-bit LE + YScale 自動適用)
- **3 ペイン同期 UI**
  - 上段: スペクトログラム (ROI 選択, plasma/viridis/inferno/magma カラーマップ)
  - 中段: Region 内の詳細時間–振幅波形
  - 下段: 全体波形のオーバービュー + 線形 Region セレクタ
- **適応的 STFT パラメータ** — Region の長さから NFFT / overlap / 窓関数を
  自動選択。8 GB マシン向けにメモリ予算 (~4 GB) で頭打ち
- **Min-Max エンベロープ デシメーション** — 全体波形プロットでパルス信号の
  ピークを保持
- **WVH/WVD 形式での書き出し** — Region 範囲をいつでも切り出せる (iq.tar /
  Keysight からの変換も対応)

## レイアウト

```
┌─────────────────────────────────────────────────────────────────┐
│ Spectrogram (Region 範囲, MHz × time)                           │
└─────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────┬──────────────────────────┐
│ Region 内 詳細波形 (横軸は spec と連動) │ コントロール (cmap,      │
│                                      │  NFFT, overlap, cutoff)  │
└──────────────────────────────────────┴──────────────────────────┘
┌─────────────────────────────────────────────────────────────────┐
│ Overview (全データ Min-Max エンベロープ) + Region セレクタ      │
└─────────────────────────────────────────────────────────────────┘
```

## インストール (uv)

[uv](https://docs.astral.sh/uv/) を入れていれば一発で起動できます:

```bash
git clone https://github.com/mashi727/iq-analyzer.git
cd iq-analyzer
uv sync          # Python 3.12 + 全依存関係を解決
uv run iq-analyzer
```

(pip / Poetry の場合: `pyproject.toml` を直接 `pip install -e .[build]`)

### 依存パッケージ

- **PySide6** ≥ 6.6 — Qt バインディング
- **pyqtgraph** ≥ 0.13 — 高速プロット
- **numpy** ≥ 1.26, **scipy** ≥ 1.11 — 信号処理
- **psutil** ≥ 5.9 — メモリ使用量表示
- **pyqtdarktheme** ≥ 2.1 — ダークテーマ (任意)

## 使い方

1. 左ペインのファイルブラウザから IQ ファイルをダブルクリックで開く
   (`.wvh`, `.iq.tar`, または `.bin.txt` を伴う `.bin`)
2. 下段の Overview で青い線形 Region をドラッグして関心範囲を選択
3. **📊 スペクトログラム計算** ボタン (or 「Region 変更時に自動更新」 ON) で
   2D スペクトログラムを描画
4. 右側パネルで cmap / NFFT / overlap / 下位カットオフを微調整
5. **💾 保存** で Region 範囲を WVH/WVD に書き出し

### 起動方法のバリエーション

```bash
uv run iq-analyzer           # インストール済みエントリポイント
python -m iq_analyzer        # モジュール実行
python rs_iq_viewer.py       # 旧スクリプト (後方互換 shim)
```

## パッケージ構造

```
src/iq_analyzer/
├── cli.py                 # QApplication 起動 + main()
├── core/
│   ├── decimation.py      # Min-Max エンベロープ
│   ├── memory.py          # メモリ監視 / 色閾値
│   ├── spectrogram.py     # STFT + auto_optimize_params
│   └── stdout_redirector.py
├── loaders/
│   ├── base.py            # IQLoader Protocol
│   ├── wv.py              # R&S WVH/WVD
│   ├── iqtar.py           # R&S iq.tar
│   └── keysight.py        # Keysight N5110A .bin
├── widgets/
│   ├── spectrogram.py     # SpectrogramWidget + max_pool_2d
│   ├── file_browser.py    # FileBrowserPanel (QTreeView + プレビュー)
│   ├── breadcrumb.py      # BreadcrumbBar
│   ├── control_panel.py   # トップバー (計算/保存/終了)
│   └── adjustment_panel.py # 表示設定
└── ui/main_window.py      # RSIQViewer (3 プロット同期)
```

## 開発

```bash
uv sync                       # dev 依存関係込みでセットアップ
uv run pytest -q              # 75 ケース、3 秒程度
uv run ruff check .           # lint
uv run ruff check --fix .     # auto-fix
```

詳細は [CONTRIBUTING.md](CONTRIBUTING.md) を参照。

## パフォーマンス目標 (検証環境: macOS, Apple Silicon)

| シナリオ                                  | 起動時間 | ピーク RAM |
|-------------------------------------------|---------|-----------|
| WVD 250 MB (65 M samples)                 | < 1 s   | ~450 MB   |
| iq.tar 2 GB (250 M samples, float32)      | < 2 s   | ~680 MB   |
| Keysight 850 MB (223 M samples @ 2.4 GSa/s, Fc=10 GHz) | < 2 s   | ~600 MB   |
| Region 50% でのスペクトログラム計算       | < 10 s  | ~2.5 GB   |

## License

[MIT](LICENSE) © 2025 MASAMI Mashino

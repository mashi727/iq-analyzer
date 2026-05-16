# IQ Analyzer

Rohde & Schwarz IQ測定データ用の高性能ビューア・処理ツール。

## 概要

最大20GBの大規模IQデータファイルを、リソース制約のあるハードウェア（Core i3 / 8GB RAM / Windows 11）上で効率的に扱うためのGUIアプリケーションです。

## 対応フォーマット

- **WVH/WVD** (Legacy IQW): RAW16LE形式のバイナリIQデータ + XML風メタデータヘッダ
- **iq.tar** (Modern IQ-TAR): float32形式のバイナリIQデータ + XMLメタデータを含むtarアーカイブ

## 主な機能

- メモリマップによる大規模ファイルの効率的な読み込み（`np.memmap`）
- スペクトログラム表示（ROI選択対応）
- 詳細時間-振幅波形表示
- 全体オーバービュー表示（インテリジェントなデシメーション）
- フォーマット間変換（iq.tar → WVH/WVD）
- 複数カラーマップ対応（plasma / viridis / inferno / magma）

## 技術スタック

- Python 3
- PySide6（GUIフレームワーク）
- PyQtGraph（プロットライブラリ）
- NumPy / SciPy

## 必要な依存パッケージ

```bash
pip install PySide6 pyqtgraph numpy scipy
pip install qdarktheme  # オプション
```

## 実行方法

```bash
python rs_iq_viewer.py
```

## パフォーマンス目標

- **起動時間**: 20GBファイルで5秒以内（メタデータのみ読み込み）
- **ROI更新**: スペクトログラム再計算2秒以内
- **メモリ使用量**: ピーク2.5GB以下（8GB RAMシステム上で安全動作）

## ライセンス

MIT

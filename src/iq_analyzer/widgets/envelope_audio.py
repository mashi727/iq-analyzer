"""Playback of the envelope of the spectrogram selection.

The panel reads the selection (time × band) from the spectrogram, renders
the envelope audio in a worker thread (:func:`iq_analyzer.core.audio.envelope_audio`),
plays it through Qt Multimedia and moves a playhead across the spectrogram.
The last rendering is cached, so replaying or saving it costs nothing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QWidget,
)

from iq_analyzer.core.audio import (
    EnvelopeAudio,
    EnvelopeAudioParams,
    envelope_audio,
    write_wav,
)
from iq_analyzer.ui.style import action_button

logger = logging.getLogger(__name__)

# (slowdown factor, label). < 1 plays faster: scan/schedule rhythms of
# seconds are easier to hear sped up, intra-burst PRF only slowed down.
SLOWDOWNS = [
    (0.25, "4倍速"),
    (0.5, "2倍速"),
    (1.0, "実時間"),
    (2.0, "1/2"),
    (4.0, "1/4"),
    (10.0, "1/10"),
    (100.0, "1/100"),
    (1000.0, "1/1000"),
]
# Above this much raw IQ the user is asked first (≈ 8 s of reading on an external drive).
_CONFIRM_BYTES = 4e9


@dataclass(frozen=True)
class AudioRequest:
    start: int  # IQ samples
    end: int
    t0_s: float  # absolute time of ``start`` (for the playhead)
    params: EnvelopeAudioParams

    def key(self) -> tuple[Any, ...]:
        p = self.params
        return (self.start, self.end, p.f_lo_hz, p.f_hi_hz, p.slowdown, p.detector, p.log_scale)


class AudioWorker(QThread):
    progress = Signal("qlonglong", "qlonglong")  # qlonglong: sample counts exceed 2^31
    done = Signal(object)  # EnvelopeAudio
    failed = Signal(str)

    def __init__(self, loader: Any, fs: float, req: AudioRequest, parent: Any = None) -> None:
        super().__init__(parent)
        self._loader, self._fs, self._req = loader, fs, req
        self._cancel = threading.Event()
        self._last = 0.0

    def cancel(self) -> None:
        self._cancel.set()

    def _on_progress(self, done: int, total: int) -> None:
        now = time.monotonic()
        if done >= total or now - self._last > 0.2:
            self._last = now
            self.progress.emit(done, total)

    def run(self) -> None:
        try:
            res = envelope_audio(
                self._loader,
                self._req.start,
                self._req.end,
                self._fs,
                self._req.params,
                progress=self._on_progress,
                cancel=self._cancel,
            )
        except Exception as exc:  # reported in the panel, never fatal for the viewer
            logger.exception("envelope audio failed")
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        if res.cancelled:
            self.failed.emit("中止しました")
            return
        self.done.emit(res)


class AudioPlayer(QObject):
    """Mono int16 playback of a float array via QAudioSink, with optional loop."""

    finished = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._sink: Any = None
        self._buf: Any = None
        self.loop = False
        self._loops_done = 0
        self.duration_s = 0.0

    def play(self, audio: np.ndarray, rate: int) -> None:
        # Imported lazily: a build without the multimedia plugin still runs
        # the viewer, and only playback reports the problem.
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

        self.stop()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            raise RuntimeError("音声出力デバイスが見つかりません")
        fmt = QAudioFormat()
        fmt.setSampleRate(int(rate))
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not device.isFormatSupported(fmt):
            raise RuntimeError(f"出力デバイスが {rate} Hz / 16 bit モノラルに対応していません")
        pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        self._buf = QBuffer(self)
        self._buf.setData(QByteArray(pcm))
        self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink = QAudioSink(device, fmt, self)
        self._idle = QAudio.State.IdleState
        self._sink.stateChanged.connect(self._on_state)
        self.duration_s = len(audio) / rate
        self._loops_done = 0
        self._sink.start(self._buf)

    def _on_state(self, state: Any) -> None:
        if state != self._idle or self._sink is None:
            return
        if self.loop:
            # Restart from the top; processedUSecs restarts too, so count loops.
            self._loops_done += 1
            self._sink.stop()
            self._buf.seek(0)
            self._sink.start(self._buf)
        else:
            self.stop()

    def position_s(self) -> float | None:
        """Seconds of audio played in the current pass, or None if stopped."""
        if self._sink is None:
            return None
        return min(self._sink.processedUSecs() / 1e6, self.duration_s)

    def stop(self) -> None:
        sink, self._sink = self._sink, None
        if sink is not None:
            sink.stateChanged.disconnect(self._on_state)
            sink.stop()
            sink.deleteLater()
            self.finished.emit()
        if self._buf is not None:
            self._buf.close()
            self._buf = None


class EnvelopeAudioPanel(QWidget):
    """Controls under the spectrogram: play / stop / speed / detector / loop / WAV."""

    def __init__(
        self,
        *,
        selection: Callable[[], tuple[float, float, float, float] | None],
        context: Callable[[], tuple[Any, float, float, int] | None],
        playhead: Callable[[float | None], None],
        bulk_read: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """``selection`` → (t0 s, t1 s, f0 Hz, f1 Hz) absolute;
        ``context`` → (loader, sample_rate, centre Hz, total samples)."""
        super().__init__(parent)
        self._selection = selection
        self._context = context
        self._playhead = playhead
        self._bulk_read = bulk_read or (lambda _on: None)
        self._worker: AudioWorker | None = None
        self._rendered: tuple[AudioRequest, EnvelopeAudio] | None = None
        self._playing: AudioRequest | None = None
        self.player = AudioPlayer(self)
        self.player.finished.connect(self._on_play_finished)
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._update_playhead)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        self.play_btn = action_button(
            "▶ 包絡線を再生",
            "スペクトログラム上の水色の枠（時間×周波数）の IQ を FFT → 帯域外を除去 → IFFT し、\n"
            "その複素信号の絶対値（ヒルベルト包絡）を音として再生",
            "run",
        )
        self.play_btn.clicked.connect(self.play)
        lay.addWidget(self.play_btn)
        self.stop_btn = action_button("■ 停止", "再生・計算を止める", "stop")
        self.stop_btn.clicked.connect(self.stop)
        lay.addWidget(self.stop_btn)
        lay.addWidget(QLabel("速度"))
        self.speed_combo = QComboBox()
        for v, label in SLOWDOWNS:
            self.speed_combo.addItem(label, v)
        self.speed_combo.setCurrentIndex(self.speed_combo.findData(1.0))
        self.speed_combo.setToolTip("遅くすると音の高さも下がる（PRF 2.5 kHz → 1/10 で 250 Hz）")
        lay.addWidget(self.speed_combo)
        lay.addWidget(QLabel("検波"))
        self.detector_combo = QComboBox()
        self.detector_combo.addItem("ピーク", "peak")
        self.detector_combo.addItem("平均", "mean")
        self.detector_combo.setToolTip("ピーク: 短いパルスも欠けずにクリックとして聞こえる\n平均: 連続波の振幅変化向き")
        lay.addWidget(self.detector_combo)
        lay.addWidget(QLabel("振幅"))
        self.scale_combo = QComboBox()
        self.scale_combo.addItem("線形", False)
        self.scale_combo.addItem("対数 (dB)", True)
        self.scale_combo.setToolTip("対数: 強い信号と弱い信号を同時に聞き取りやすくする")
        lay.addWidget(self.scale_combo)
        self.loop_check = QCheckBox("ループ")
        self.loop_check.toggled.connect(lambda on: setattr(self.player, "loop", on))
        lay.addWidget(self.loop_check)
        self.wav_btn = action_button("💾 WAV 保存", "最後に計算した包絡線を WAV で保存", "save")
        self.wav_btn.setEnabled(False)
        self.wav_btn.clicked.connect(self.save_wav)
        lay.addWidget(self.wav_btn)
        self.status = QLabel("スペクトログラムを計算すると、水色の枠で範囲を指定できます")
        # Long status texts must not widen the window (it pushed the minimum
        # window width to ~2100 px at 20 pt); the full text is in the tooltip.
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setMinimumWidth(0)
        lay.addWidget(self.status, 1)

    # ------------------------------------------------------------ request

    def describe_selection(self) -> str:
        sel = self._selection()
        if sel is None:
            return "選択範囲なし"
        t0, t1, f0, f1 = sel
        return f"選択 {_fmt_t(t0)}–{_fmt_t(t1)}（{_fmt_t(t1 - t0)}）、{f0 / 1e6:.3f}–{f1 / 1e6:.3f} MHz（帯域 {(f1 - f0) / 1e6:.3f} MHz）"

    def build_request(self) -> AudioRequest | None:
        sel, ctx = self._selection(), self._context()
        if sel is None or ctx is None:
            return None
        _loader, fs, center, total = ctx
        t0, t1, f0, f1 = sel
        start = int(max(0, np.floor(t0 * fs)))
        end = int(min(total, np.ceil(t1 * fs)))
        params = EnvelopeAudioParams(
            f_lo_hz=f0 - center,
            f_hi_hz=f1 - center,
            slowdown=float(self.speed_combo.currentData()),
            detector=str(self.detector_combo.currentData()),
            log_scale=bool(self.scale_combo.currentData()),
        )
        return AudioRequest(start, end, start / fs, params)

    # --------------------------------------------------------------- play

    def play(self) -> None:
        req = self.build_request()
        if req is None:
            self.status.setText("スペクトログラムを計算してから、水色の枠で範囲を指定してください")
            return
        if self._rendered is not None and self._rendered[0].key() == req.key():
            self._start_playback(*self._rendered)
            return
        self.render_audio(req, then_play=True)

    def render_audio(self, req: AudioRequest, *, then_play: bool) -> None:
        if self._worker is not None:
            return
        ctx = self._context()
        if ctx is None:
            return
        loader, fs, _center, _total = ctx
        nbytes = (req.end - req.start) * 4
        if nbytes > _CONFIRM_BYTES:
            ret = QMessageBox.question(
                self,
                "包絡線の再生",
                f"選択範囲の IQ は約 {nbytes / 1e9:.1f} GB あります。計算に時間がかかります。続行しますか？",
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
        self.player.stop()
        worker = AudioWorker(loader, fs, req, self)
        worker.progress.connect(lambda d, t: self.status.setText(f"包絡線を計算中… {d / max(t, 1) * 100:.0f}%（■ で中止）"))
        worker.done.connect(lambda res: self._on_done(req, res, then_play))
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self.play_btn.setEnabled(False)
        self._bulk_read(True)
        worker.start()

    def _finish_worker(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.wait()
            self._bulk_read(False)
        self.play_btn.setEnabled(True)

    def _on_done(self, req: AudioRequest, res: EnvelopeAudio, then_play: bool) -> None:
        self._finish_worker()
        self._rendered = (req, res)
        self.wav_btn.setEnabled(True)
        if then_play:
            self._start_playback(req, res)
        else:
            self.status.setText("計算済み")

    def _on_failed(self, message: str) -> None:
        self._finish_worker()
        if message == "中止しました":
            self.status.setText(message)
            return
        if "audio would last" in message:
            message = "音声が長すぎます（上限 10 分）。範囲を短くするか、速度を上げてください。\n" + message
        self.status.setText("包絡線の計算に失敗しました")
        QMessageBox.warning(self, "包絡線の再生", message)

    def _start_playback(self, req: AudioRequest, res: EnvelopeAudio) -> None:
        try:
            self.player.play(res.audio, res.rate)
        except Exception as exc:
            self.status.setText(f"再生できません: {exc}")
            return
        self._playing = req
        band = "全帯域（フィルタなし）" if not res.filtered else f"帯域 {(res.band_hz[1] - res.band_hz[0]) / 1e6:.3f} MHz"
        self.status.setText(
            f"再生中 {res.audio.size / res.rate:.2f} s（{req.params.slowdown:g} 倍に伸長）、{band}、"
            f"{self.detector_combo.currentText()}検波"
        )
        self._timer.start()

    def _update_playhead(self) -> None:
        pos = self.player.position_s()
        req = self._playing
        if pos is None or req is None:
            self._playhead(None)
            return
        self._playhead(req.t0_s + pos / req.params.slowdown)

    def _on_play_finished(self) -> None:
        self._timer.stop()
        self._playhead(None)
        if self._worker is None and self.status.text().startswith("再生中"):
            self.status.setText("停止")

    def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self.player.stop()

    def shutdown(self) -> None:
        """Stop everything before the loader is closed (blocking)."""
        self.player.stop()
        if self._worker is not None:
            self._worker.cancel()
            self._finish_worker()
        self._rendered = None
        self.wav_btn.setEnabled(False)

    # ----------------------------------------------------------------- WAV

    def save_wav(self) -> None:
        if self._rendered is None:
            return
        req, res = self._rendered
        default = str(Path.home() / f"envelope_{req.t0_s:.6f}s.wav")
        path, _ = QFileDialog.getSaveFileName(self, "包絡線を WAV で保存", default, "WAV (*.wav)")
        if not path:
            return
        try:
            write_wav(Path(path), res.audio, res.rate)
        except OSError as exc:
            QMessageBox.warning(self, "WAV 保存", f"保存に失敗しました。\n{exc}")
            return
        self.status.setText(f"保存しました: {path}")


def _fmt_t(t: float) -> str:
    a = abs(t)
    if a >= 1:
        return f"{t:.4f} s"
    if a >= 1e-3:
        return f"{t * 1e3:.3f} ms"
    return f"{t * 1e6:.2f} µs"

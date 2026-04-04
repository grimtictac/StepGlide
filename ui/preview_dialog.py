"""
Preview dialog — modeless dialog that plays a track on a separate VLC
media player routed to the preview audio output device.
"""

import os

import vlc
import qtawesome as qta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
)

from ui.theme import COLORS
from ui.waveform_bar import WaveformScrubBar


def _fmt_ms(ms):
    """Format milliseconds as m:ss."""
    if ms <= 0:
        return '0:00'
    s = int(ms / 1000)
    return f'{s // 60}:{s % 60:02d}'


class PreviewDialog(QDialog):
    """Modeless dialog that previews a single track on a secondary output."""

    # Emitted when the dialog is closed (for cleanup in MainWindow)
    closed = Signal()

    def __init__(self, track_entry, device_id='', waveform_data=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Preview')
        self.setMinimumSize(420, 200)
        self.resize(480, 210)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowStaysOnTopHint
            | Qt.Dialog
        )
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._track = track_entry
        self._device_id = device_id
        self._waveform_data = waveform_data

        # ── Own VLC instance + player ────────────────────
        self._vlc_instance = vlc.Instance()
        self._vlc_player = self._vlc_instance.media_player_new()

        # Route to preview device
        if device_id:
            self._vlc_player.audio_output_device_set(None, device_id)

        self._is_playing = False
        self._user_scrubbing = False

        self._build_ui()
        self._load_and_play()

        # ── Poll timer ───────────────────────────────────
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Title
        title = self._track.get('title', self._track.get('basename', ''))
        self._lbl_title = QLabel(f'\U0001f3a7  {title}')
        self._lbl_title.setStyleSheet(
            f'color: {COLORS["cyan"]}; font-size: 13px; font-weight: bold;')
        self._lbl_title.setWordWrap(True)
        layout.addWidget(self._lbl_title)

        # Scrub row
        scrub_row = QHBoxLayout()
        scrub_row.setSpacing(6)

        self._lbl_cur = QLabel('0:00')
        self._lbl_cur.setFixedWidth(44)
        self._lbl_cur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._lbl_cur.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 11px;')
        scrub_row.addWidget(self._lbl_cur)

        self._scrub = WaveformScrubBar()
        self._scrub.BAR_HEIGHT = 40
        self._scrub.setFixedHeight(40)
        self._scrub.setMinimumHeight(40)
        if self._waveform_data:
            self._scrub.set_waveform(self._waveform_data)
        self._scrub.scrub_pressed.connect(self._on_scrub_pressed)
        self._scrub.scrub_released.connect(self._on_scrub_released)
        scrub_row.addWidget(self._scrub, stretch=1)

        self._lbl_total = QLabel('0:00')
        self._lbl_total.setFixedWidth(44)
        self._lbl_total.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._lbl_total.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 11px;')
        scrub_row.addWidget(self._lbl_total)

        layout.addLayout(scrub_row)

        # Controls row
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self._btn_play = QPushButton()
        self._icon_play = qta.icon('mdi6.play', color='white')
        self._icon_pause = qta.icon('mdi6.pause', color='white')
        self._btn_play.setIcon(self._icon_pause)  # starts playing
        self._btn_play.setFixedSize(42, 32)
        self._btn_play.setIconSize(self._btn_play.size() * 0.6)
        self._btn_play.setStyleSheet(
            f'QPushButton {{ background-color: {COLORS["accent"]}; border-radius: 4px; }}'
            f'QPushButton:hover {{ background-color: {COLORS["accent_hover"]}; }}')
        self._btn_play.setToolTip('Play / Pause')
        self._btn_play.clicked.connect(self._toggle_play)
        ctrl_row.addWidget(self._btn_play)

        self._btn_stop = QPushButton()
        self._btn_stop.setIcon(qta.icon('mdi6.stop', color=COLORS['fg']))
        self._btn_stop.setFixedSize(36, 32)
        self._btn_stop.setIconSize(self._btn_stop.size() * 0.6)
        self._btn_stop.setToolTip('Stop preview')
        self._btn_stop.clicked.connect(self._stop)
        ctrl_row.addWidget(self._btn_stop)

        ctrl_row.addSpacing(12)

        # Volume
        vol_icon = QLabel()
        vol_icon.setPixmap(
            qta.icon('mdi6.volume-high', color=COLORS['fg_dim']).pixmap(16, 16))
        ctrl_row.addWidget(vol_icon)

        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(80)
        self._vol_slider.setFixedWidth(100)
        self._vol_slider.setToolTip('Preview volume')
        self._vol_slider.valueChanged.connect(self._on_volume)
        ctrl_row.addWidget(self._vol_slider)

        ctrl_row.addStretch()

        # Close
        self._btn_close = QPushButton('Close')
        self._btn_close.setFixedSize(60, 32)
        self._btn_close.clicked.connect(self.close)
        ctrl_row.addWidget(self._btn_close)

        layout.addLayout(ctrl_row)

    # ── Playback ─────────────────────────────────────────

    def _load_and_play(self):
        path = self._track.get('_abs_path', '')
        if not path or not os.path.isfile(path):
            self._lbl_title.setText('File not found')
            return
        media = self._vlc_instance.media_new(path)
        self._vlc_player.set_media(media)
        self._vlc_player.audio_set_volume(self._vol_slider.value())
        self._vlc_player.play()
        self._is_playing = True

    def _toggle_play(self):
        if self._is_playing:
            self._vlc_player.pause()
            self._is_playing = False
            self._btn_play.setIcon(self._icon_play)
        else:
            self._vlc_player.play()
            self._is_playing = True
            self._btn_play.setIcon(self._icon_pause)

    def _stop(self):
        self._vlc_player.stop()
        self._is_playing = False
        self._btn_play.setIcon(self._icon_play)
        self._scrub.set_position(0.0)
        self._lbl_cur.setText('0:00')

    def _on_volume(self, val):
        self._vlc_player.audio_set_volume(val)

    # ── Scrub ────────────────────────────────────────────

    def _on_scrub_pressed(self):
        self._user_scrubbing = True

    def _on_scrub_released(self, pos):
        self._user_scrubbing = False
        length = self._vlc_player.get_length()
        if length > 0:
            self._vlc_player.set_position(pos)

    # ── Poll ─────────────────────────────────────────────

    def _poll(self):
        if not self._user_scrubbing:
            length = self._vlc_player.get_length()
            pos = self._vlc_player.get_position()
            if length > 0 and pos >= 0:
                self._scrub.set_position(pos)
                self._lbl_cur.setText(_fmt_ms(int(pos * length)))
                self._lbl_total.setText(_fmt_ms(length))

        # Auto-close when track finishes
        if (self._is_playing
                and not self._vlc_player.is_playing()
                and self._vlc_player.get_position() >= 0.99):
            self._is_playing = False
            self._btn_play.setIcon(self._icon_play)

    # ── Cleanup ──────────────────────────────────────────

    def stop_and_release(self):
        """Stop playback and release VLC resources. Safe to call multiple times."""
        self._poll_timer.stop()
        try:
            self._vlc_player.stop()
            self._vlc_player.release()
            self._vlc_instance.release()
        except Exception:
            pass

    def closeEvent(self, event):
        self.stop_and_release()
        self.closed.emit()
        super().closeEvent(event)

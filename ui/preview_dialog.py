"""
PreviewDock - modeless, dockable preview/cue panel.

Always routes to the **headphones** (preview) audio output via
OutputManager - never the speakers.  Designed for the "party flow":
the user is monitoring their next pick on headphones while the main
transport keeps playing on speakers, and decides whether to slot the
preview track in via Play Now / Play Next / Add to Queue.

Caller contract (MainWindow):
- Pre-flight: refuse to open the dock when the active output is
  already 'headphones' (no point cueing the same channel that's
  playing) or when no headphones device resolves.  This dock assumes
  the caller has already checked.
- Provides the action triggers as Qt Signals; the dock itself does
  not touch the main transport or the queue.
- Calls ``stop_and_release()`` to tear down VLC resources cleanly
  (also done automatically on closeEvent).

Preview playback deliberately does NOT write to ``track_plays`` -
this dock is for evaluation, not for inflating the play count.
"""

import os

import vlc
import qtawesome as qta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from ui.theme import COLORS
from ui.waveform_bar import WaveformScrubBar
from core.waveform import WaveformWorker


def _fmt_ms(ms):
    """Format milliseconds as m:ss."""
    if ms <= 0:
        return '0:00'
    s = int(ms / 1000)
    return f'{s // 60}:{s % 60:02d}'


class PreviewDock(QDockWidget):
    """Modeless, dockable, headphones-only preview/cue surface."""

    play_now_requested = Signal()
    play_next_requested = Signal()
    add_to_queue_requested = Signal()
    closed = Signal()

    def __init__(self, track_entry, output_mgr, headphones_device,
                 router, waveform_data=None, parent=None):
        super().__init__('Preview (Headphones)', parent)
        self.setObjectName('PreviewDock')
        self.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable,
        )
        self.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
            | Qt.BottomDockWidgetArea | Qt.TopDockWidgetArea,
        )
        self.setAttribute(Qt.WA_DeleteOnClose)

        self._track = track_entry
        self._output_mgr = output_mgr
        self._headphones_device = headphones_device
        self._router = router
        self._waveform_data = waveform_data

        # Own VLC instance + player.  Routing is handled out-of-band
        # by the AudioRouter (which on Linux uses ``pactl
        # move-sink-input`` because libvlc 3's audio_output_device_set
        # is silently broken on the pulse/pipewire-pulse backend).  See
        # ``core/audio_router.py`` for the full story.
        self._vlc_instance = vlc.Instance()
        self._vlc_player = self._vlc_instance.media_player_new()
        self._router.attach(self._vlc_player, label='preview')

        self._is_playing = False
        self._user_scrubbing = False
        self._waveform_worker = None

        self._build_ui()
        self._load_and_play()

        if not self._waveform_data:
            abs_path = self._track.get('_abs_path', '')
            if abs_path and os.path.isfile(abs_path):
                self._scrub.set_loading(True)
                self._waveform_worker = WaveformWorker(abs_path, parent=self)
                self._waveform_worker.finished.connect(self._on_waveform_ready)
                self._waveform_worker.start()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(250)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

    def _build_ui(self):
        body = QWidget(self)
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        title = self._track.get('title', self._track.get('basename', '')) or '?'
        artist = self._track.get('artist', '')
        header_text = f'\U0001f3a7  {title}'
        if artist:
            header_text += f'  -  {artist}'
        self._lbl_title = QLabel(header_text)
        self._lbl_title.setStyleSheet(
            f'color: {COLORS["cyan"]}; font-size: 13px; font-weight: bold;')
        self._lbl_title.setWordWrap(True)
        layout.addWidget(self._lbl_title)

        dest = self._headphones_device.description or self._headphones_device.device_id
        sub = QLabel(f'Routing to: {dest}')
        sub.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 10px;')
        layout.addWidget(sub)

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

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)

        self._btn_play = QPushButton()
        self._icon_play = qta.icon('mdi6.play', color='white')
        self._icon_pause = qta.icon('mdi6.pause', color='white')
        self._btn_play.setIcon(self._icon_pause)
        self._btn_play.setFixedSize(42, 32)
        self._btn_play.setIconSize(self._btn_play.size() * 0.6)
        self._btn_play.setStyleSheet(
            f'QPushButton {{ background-color: {COLORS["accent"]}; border-radius: 4px; }}'
            f'QPushButton:hover {{ background-color: {COLORS["accent_hover"]}; }}')
        self._btn_play.setToolTip('Play / Pause preview')
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

        layout.addLayout(ctrl_row)

        # Action row - these route through the MAIN transport / queue.
        action_row = QHBoxLayout()
        action_row.setSpacing(6)

        self._btn_play_now = QPushButton('\u25b6  Play Now')
        self._btn_play_now.setToolTip(
            'Stop the preview and play this track on the main output now.')
        self._btn_play_now.clicked.connect(self._on_play_now)
        action_row.addWidget(self._btn_play_now)

        self._btn_play_next = QPushButton('\u23ed  Play Next')
        self._btn_play_next.setToolTip(
            'Insert this track at the front of the queue. '
            'Preview keeps playing.')
        self._btn_play_next.clicked.connect(self._on_play_next)
        action_row.addWidget(self._btn_play_next)

        self._btn_add_queue = QPushButton('\U0001f4cb  Add to Queue')
        self._btn_add_queue.setToolTip(
            'Append this track to the end of the queue. '
            'Preview keeps playing.')
        self._btn_add_queue.clicked.connect(self._on_add_to_queue)
        action_row.addWidget(self._btn_add_queue)

        action_row.addStretch()

        self._btn_close = QPushButton('Close')
        self._btn_close.setFixedHeight(32)
        self._btn_close.clicked.connect(self.close)
        action_row.addWidget(self._btn_close)

        layout.addLayout(action_row)

        self.setWidget(body)

    def _on_play_now(self):
        self._stop()
        self.play_now_requested.emit()
        self.close()

    def _on_play_next(self):
        self.play_next_requested.emit()

    def _on_add_to_queue(self):
        self.add_to_queue_requested.emit()

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
        # Pin to the headphones sink — router will discover the new
        # sink-input within a few hundred ms and move it.
        self._router.pin_player(
            self._vlc_player, self._headphones_device.device_id or '')

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

    def _on_scrub_pressed(self):
        self._user_scrubbing = True

    def _on_scrub_released(self, pos):
        self._user_scrubbing = False
        length = self._vlc_player.get_length()
        if length > 0:
            self._vlc_player.set_position(pos)

    def _poll(self):
        if not self._user_scrubbing:
            length = self._vlc_player.get_length()
            pos = self._vlc_player.get_position()
            if length > 0 and pos >= 0:
                self._scrub.set_position(pos)
                self._lbl_cur.setText(_fmt_ms(int(pos * length)))
                self._lbl_total.setText(_fmt_ms(length))

        if (self._is_playing
                and not self._vlc_player.is_playing()
                and self._vlc_player.get_position() >= 0.99):
            self._is_playing = False
            self._btn_play.setIcon(self._icon_play)

    def _on_waveform_ready(self, _file_path, data):
        self._waveform_worker = None
        if data:
            self._scrub.set_waveform(data)
        else:
            self._scrub.set_loading(False)

    def stop_and_release(self):
        """Stop playback and release VLC resources. Idempotent."""
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        if self._waveform_worker is not None:
            try:
                self._waveform_worker.cancel()
            except Exception:
                pass
            self._waveform_worker = None
        try:
            self._router.detach(self._vlc_player)
        except Exception:
            pass
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

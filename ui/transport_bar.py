"""
Transport bar — play/pause, stop, scrub slider, time labels, volume,
speed controls, and mute button.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QVBoxLayout, QWidget,
)

from ui.theme import COLORS


class TransportBar(QWidget):
    """
    Horizontal bar with transport controls:
      [⏮] [▶/⏸] [⏭] [Stop]  [scrub slider]  [time]
      [🔊 volume slider %]  [speed ▼ 1.0× ▲] [auto-reset]
    """

    # Signals emitted to MainWindow
    play_pause_clicked = Signal()
    stop_clicked = Signal()
    next_clicked = Signal()
    prev_clicked = Signal()
    scrub_moved = Signal(float)          # 0.0–1.0 position while dragging
    scrub_released = Signal(float)       # 0.0–1.0 final position on release
    volume_changed = Signal(int)         # 0–100
    mute_toggled = Signal()
    speed_up_clicked = Signal()
    speed_down_clicked = Signal()
    speed_reset_clicked = Signal()
    auto_reset_speed_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._user_scrubbing = False
        self._build_ui()

    # ── Build ────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(2)

        # ── Row 1: transport buttons + scrub + time ──────
        row1 = QHBoxLayout()
        row1.setSpacing(4)

        # Prev
        self.btn_prev = QPushButton('⏮')
        self.btn_prev.setFixedSize(36, 32)
        self.btn_prev.setToolTip('Previous track')
        self.btn_prev.clicked.connect(self.prev_clicked)
        row1.addWidget(self.btn_prev)

        # Play / Pause
        self.btn_play = QPushButton('▶')
        self.btn_play.setFixedSize(44, 32)
        self.btn_play.setToolTip('Play / Pause')
        self.btn_play.setStyleSheet(
            f'QPushButton {{ background-color: {COLORS["accent"]}; font-size: 16px; }}'
            f'QPushButton:hover {{ background-color: {COLORS["accent_hover"]}; }}')
        self.btn_play.clicked.connect(self.play_pause_clicked)
        row1.addWidget(self.btn_play)

        # Next
        self.btn_next = QPushButton('⏭')
        self.btn_next.setFixedSize(36, 32)
        self.btn_next.setToolTip('Next track')
        self.btn_next.clicked.connect(self.next_clicked)
        row1.addWidget(self.btn_next)

        # Stop
        self.btn_stop = QPushButton('⏹')
        self.btn_stop.setFixedSize(36, 32)
        self.btn_stop.setToolTip('Stop')
        self.btn_stop.clicked.connect(self.stop_clicked)
        row1.addWidget(self.btn_stop)

        row1.addSpacing(8)

        # Current time
        self.lbl_time_cur = QLabel('0:00')
        self.lbl_time_cur.setFixedWidth(48)
        self.lbl_time_cur.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lbl_time_cur.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 11px;')
        row1.addWidget(self.lbl_time_cur)

        # Scrub slider
        self.scrub_slider = QSlider(Qt.Horizontal)
        self.scrub_slider.setRange(0, 10000)
        self.scrub_slider.setValue(0)
        self.scrub_slider.setToolTip('Seek')
        self.scrub_slider.sliderPressed.connect(self._on_scrub_pressed)
        self.scrub_slider.sliderMoved.connect(self._on_scrub_moved)
        self.scrub_slider.sliderReleased.connect(self._on_scrub_released)
        row1.addWidget(self.scrub_slider, stretch=1)

        # Total time
        self.lbl_time_total = QLabel('0:00')
        self.lbl_time_total.setFixedWidth(48)
        self.lbl_time_total.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lbl_time_total.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 11px;')
        row1.addWidget(self.lbl_time_total)

        outer.addLayout(row1)

        # ── Row 2: volume + speed ────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        # Mute button
        self.btn_mute = QPushButton('🔊')
        self.btn_mute.setFixedSize(32, 26)
        self.btn_mute.setToolTip('Mute / Unmute')
        self.btn_mute.clicked.connect(self.mute_toggled)
        row2.addWidget(self.btn_mute)

        # Volume slider
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setToolTip('Volume')
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        row2.addWidget(self.volume_slider)

        # Volume percentage label
        self.lbl_vol_pct = QLabel('80%')
        self.lbl_vol_pct.setFixedWidth(36)
        self.lbl_vol_pct.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 10px;')
        row2.addWidget(self.lbl_vol_pct)

        row2.addSpacing(16)

        # ── Speed controls ───────────────────────────────
        # Speed frame (highlighted when ≠ 1.0)
        self._speed_frame = QFrame()
        self._speed_frame.setFrameShape(QFrame.NoFrame)
        speed_layout = QHBoxLayout(self._speed_frame)
        speed_layout.setContentsMargins(4, 0, 4, 0)
        speed_layout.setSpacing(2)

        self.btn_speed_down = QPushButton('◀')
        self.btn_speed_down.setFixedSize(26, 24)
        self.btn_speed_down.setToolTip('Decrease speed')
        self.btn_speed_down.clicked.connect(self.speed_down_clicked)
        speed_layout.addWidget(self.btn_speed_down)

        self.lbl_speed = QLabel('1.0×')
        self.lbl_speed.setFixedWidth(40)
        self.lbl_speed.setAlignment(Qt.AlignCenter)
        self.lbl_speed.setStyleSheet('font-weight: bold; font-size: 11px;')
        speed_layout.addWidget(self.lbl_speed)

        self.btn_speed_up = QPushButton('▶')
        self.btn_speed_up.setFixedSize(26, 24)
        self.btn_speed_up.setToolTip('Increase speed')
        self.btn_speed_up.clicked.connect(self.speed_up_clicked)
        speed_layout.addWidget(self.btn_speed_up)

        self.btn_speed_reset = QPushButton('1×')
        self.btn_speed_reset.setFixedSize(30, 24)
        self.btn_speed_reset.setToolTip('Reset speed to 1×')
        self.btn_speed_reset.clicked.connect(self.speed_reset_clicked)
        speed_layout.addWidget(self.btn_speed_reset)

        row2.addWidget(self._speed_frame)

        # Auto-reset speed checkbox
        self.chk_auto_reset = QCheckBox('Auto-reset')
        self.chk_auto_reset.setToolTip('Auto-reset speed to 1× when track changes')
        self.chk_auto_reset.setChecked(True)
        self.chk_auto_reset.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 10px;')
        self.chk_auto_reset.toggled.connect(self.auto_reset_speed_changed)
        row2.addWidget(self.chk_auto_reset)

        row2.addStretch()
        outer.addLayout(row2)

    # ── Scrub helpers ────────────────────────────────────

    def _on_scrub_pressed(self):
        self._user_scrubbing = True

    def _on_scrub_moved(self, value):
        pos = value / 10000.0
        self.scrub_moved.emit(pos)

    def _on_scrub_released(self):
        self._user_scrubbing = False
        pos = self.scrub_slider.value() / 10000.0
        self.scrub_released.emit(pos)

    def _on_volume_changed(self, value):
        self.lbl_vol_pct.setText(f'{value}%')
        self.volume_changed.emit(value)

    # ── Public API for MainWindow ────────────────────────

    @property
    def is_user_scrubbing(self):
        return self._user_scrubbing

    def set_scrub_position(self, pos):
        """Set the scrub slider position (0.0–1.0) without emitting signals."""
        if not self._user_scrubbing:
            self.scrub_slider.blockSignals(True)
            self.scrub_slider.setValue(int(pos * 10000))
            self.scrub_slider.blockSignals(False)

    def set_time_labels(self, current_ms, total_ms):
        self.lbl_time_cur.setText(self._fmt(current_ms))
        self.lbl_time_total.setText(self._fmt(total_ms))

    def set_playing_state(self, playing):
        """Update the play button appearance."""
        if playing:
            self.btn_play.setText('⏸')
            self.btn_play.setStyleSheet(
                f'QPushButton {{ background-color: {COLORS["green"]}; font-size: 16px; }}'
                f'QPushButton:hover {{ background-color: {COLORS["green_hover"]}; }}')
        else:
            self.btn_play.setText('▶')
            self.btn_play.setStyleSheet(
                f'QPushButton {{ background-color: {COLORS["accent"]}; font-size: 16px; }}'
                f'QPushButton:hover {{ background-color: {COLORS["accent_hover"]}; }}')

    def set_speed_label(self, speed):
        """Update the speed display. Highlight when not 1.0×."""
        self.lbl_speed.setText(f'{speed:.1f}×')
        if abs(speed - 1.0) > 0.05:
            self._speed_frame.setStyleSheet(
                f'QFrame {{ background-color: #5c2d00; border: 2px solid {COLORS["orange"]}; '
                f'border-radius: 4px; }}')
            self.lbl_speed.setStyleSheet(
                f'color: {COLORS["orange"]}; font-weight: bold; font-size: 11px;')
        else:
            self._speed_frame.setStyleSheet('')
            self.lbl_speed.setStyleSheet('font-weight: bold; font-size: 11px;')

    def set_mute_icon(self, muted):
        self.btn_mute.setText('🔇' if muted else '🔊')

    def set_volume(self, vol):
        """Set volume slider (0–100) without emitting signal."""
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(vol)
        self.volume_slider.blockSignals(False)
        self.lbl_vol_pct.setText(f'{vol}%')

    def reset_display(self):
        """Reset scrub + time to zero (e.g. on stop)."""
        self.set_scrub_position(0)
        self.lbl_time_cur.setText('0:00')
        self.lbl_time_total.setText('0:00')
        self.set_playing_state(False)

    # ── Helpers ──────────────────────────────────────────

    @staticmethod
    def _fmt(ms):
        """Format milliseconds as m:ss."""
        if ms <= 0:
            return '0:00'
        secs = int(ms / 1000)
        m, s = divmod(secs, 60)
        return f'{m}:{s:02d}'

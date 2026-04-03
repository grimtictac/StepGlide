"""
Transport bar — play/pause, stop, scrub slider, time labels, volume,
speed controls, and mute button.
"""

import time

from PySide6.QtCore import Qt, QEvent, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QVBoxLayout, QWidget,
)

import qtawesome as qta
from ui.theme import COLORS

_ICON_SIZE = 18  # default icon pixel size for transport buttons


# ═════════════════════════════════════════════════════════
# Vertical volume strip — sits on the far-right of the window
# ═════════════════════════════════════════════════════════

class VolumeStrip(QWidget):
    """
    A tall, vertical volume slider with mute button and percentage label.
    Designed to be easy to grab — wide groove, large handle.

    Momentum fade: when the user scrolls the mouse wheel the volume begins
    fading to 0 (scroll down) or 100 (scroll up) at a speed proportional
    to scroll velocity.  Scrolling again *in the same direction* during an
    active fade **adds** speed — it never slows down or stops.  The fade
    always runs to the limit (0 or 100).

    The fade halts ONLY when: scrolling in the **opposite** direction,
    clicking the slider handle, or toggling mute.
    """

    volume_changed = Signal(int)   # 0–100
    mute_toggled = Signal()
    debug_log = Signal(str, str)   # (level, message) → route to debug panel

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(52)

        # ── Tunable parameters (driven by FadeTuningPanel) ──
        self._fade_step = 1           # volume units per tick
        self._min_interval_ms = 20    # fastest allowed fade tick (cap)
        self._max_interval_ms = 200   # slowest fade tick (single gentle scroll)
        self._velocity_window_s = 0.4 # sliding window for measuring scroll speed
        self._vel_low = 3.0           # scroll evt/s that maps to slowest fade
        self._vel_high = 30.0         # scroll evt/s that maps to fastest fade

        # ── Fade state ──
        self._fade_direction = 0   # -1 = fading down, +1 = fading up, 0 = idle
        self._current_interval_ms = self._max_interval_ms
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_tick)

        # Velocity measurement: timestamps of recent wheel events
        self._wheel_times: list[float] = []

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter)

        # Mute button at top
        self._icon_vol_high = qta.icon('mdi6.volume-high', color=COLORS['fg'])
        self._icon_vol_off = qta.icon('mdi6.volume-off', color=COLORS['red_text'])
        self.btn_mute = QPushButton()
        self.btn_mute.setIcon(self._icon_vol_high)
        self.btn_mute.setFixedSize(40, 36)
        self.btn_mute.setIconSize(self.btn_mute.size() * 0.6)
        self.btn_mute.setToolTip('Mute / Unmute')
        self.btn_mute.clicked.connect(self._on_mute_clicked)
        layout.addWidget(self.btn_mute, alignment=Qt.AlignHCenter)

        # Percentage label
        self.lbl_vol_pct = QLabel('80%')
        self.lbl_vol_pct.setAlignment(Qt.AlignCenter)
        self.lbl_vol_pct.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; font-size: 10px; font-weight: bold;')
        layout.addWidget(self.lbl_vol_pct, alignment=Qt.AlignHCenter)

        # Vertical slider — stretches to fill height
        self.volume_slider = QSlider(Qt.Vertical)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setToolTip('Volume')
        self.volume_slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.volume_slider.setFixedWidth(36)
        self.volume_slider.setStyleSheet(f'''
            QSlider::groove:vertical {{
                background: {COLORS["bg_light"]};
                width: 14px;
                border-radius: 7px;
            }}
            QSlider::handle:vertical {{
                background: {COLORS["accent"]};
                border: 2px solid {COLORS["fg_dim"]};
                height: 24px;
                width: 28px;
                margin: 0 -7px;
                border-radius: 6px;
            }}
            QSlider::handle:vertical:hover {{
                background: {COLORS["accent_hover"]};
                border: 2px solid {COLORS["fg"]};
            }}
            QSlider::sub-page:vertical {{
                background: {COLORS["bg_light"]};
                border-radius: 7px;
            }}
            QSlider::add-page:vertical {{
                background: {COLORS["accent"]};
                border-radius: 7px;
            }}
        ''')
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        # Stop fade if user grabs the handle
        self.volume_slider.sliderPressed.connect(self._stop_fade)
        # Intercept wheel events on the slider so they go through our
        # momentum handler instead of the slider's built-in wheel logic.
        self.volume_slider.installEventFilter(self)
        layout.addWidget(self.volume_slider, stretch=1, alignment=Qt.AlignHCenter)

    # ── Wheel event with momentum fade ───────────────────

    def eventFilter(self, obj, event):
        """Catch wheel events on the slider and route them to our handler."""
        if obj is self.volume_slider and event.type() == QEvent.Wheel:
            self._handle_wheel(event)
            return True  # consumed — don't let QSlider handle it
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        """Intercept scroll wheel on the strip background (outside the slider)."""
        self._handle_wheel(event)

    def _velocity_to_interval(self, velocity):
        """Map scroll velocity (evt/s) → timer interval (ms).

        Higher velocity → shorter interval (faster fade).
        Returns a value clamped to [_min_interval_ms, _max_interval_ms].
        """
        rng = self._vel_high - self._vel_low
        if rng <= 0:
            rng = 1.0
        t = max(0.0, min(1.0, (velocity - self._vel_low) / rng))
        interval = int(self._max_interval_ms
                       - t * (self._max_interval_ms - self._min_interval_ms))
        return max(self._min_interval_ms, min(self._max_interval_ms, interval))

    def _handle_wheel(self, event):
        """Unified wheel handler with velocity-based momentum fade.

        Rules:
        - Scrolling starts a fade toward 0 or 100.
        - Scrolling in the same direction during an active fade can only
          speed it up (interval decreases), never slow it down.
        - Scrolling in the opposite direction stops the active fade.
        - A fade always runs to 0 or 100 once started.
        """
        delta = event.angleDelta().y()
        if delta == 0:
            return

        direction = 1 if delta > 0 else -1

        # Opposite direction → stop current fade
        if self._fade_direction != 0 and direction != self._fade_direction:
            self._log('DEBUG', 'Volume fade: reversed direction → stopping fade')
            self._stop_fade()
            event.accept()
            return

        # Apply the immediate scroll step (2 units per wheel notch)
        step = 2
        new_val = max(0, min(100, self.volume_slider.value() + direction * step))
        self.volume_slider.setValue(new_val)

        # Record this wheel event for velocity measurement
        now = time.monotonic()
        self._wheel_times.append(now)
        cutoff = now - self._velocity_window_s
        self._wheel_times = [t for t in self._wheel_times if t >= cutoff]

        # Calculate velocity
        n = len(self._wheel_times)
        if n >= 2:
            span = self._wheel_times[-1] - self._wheel_times[0]
            velocity = (n - 1) / span if span > 0 else float(n)
        else:
            velocity = self._vel_low  # single event → slowest

        new_interval = self._velocity_to_interval(velocity)

        # If a fade is already active, only allow speeding up (lower interval)
        if self._fade_direction != 0:
            old_interval = self._current_interval_ms
            if new_interval >= old_interval:
                # New scroll is same speed or slower — keep existing speed,
                # but still restart the timer to avoid a stale tick gap.
                self._log(
                    'DEBUG',
                    f'Volume scroll (boost ignored): vel={velocity:.1f} evt/s  '
                    f'new_interval={new_interval}ms >= current={old_interval}ms'
                )
                event.accept()
                return
            self._log(
                'DEBUG',
                f'Volume scroll (speed up): vel={velocity:.1f} evt/s  '
                f'interval {old_interval}ms → {new_interval}ms'
            )
        else:
            self._log(
                'DEBUG',
                f'Volume scroll (new fade): '
                f'{"UP" if direction > 0 else "DOWN"}  events={n}  '
                f'vel={velocity:.1f} evt/s  interval={new_interval}ms  '
                f'step={self._fade_step}'
            )

        # Start / speed-up the fade
        self._fade_direction = direction
        self._current_interval_ms = new_interval
        self._fade_timer.setInterval(new_interval)
        self._fade_timer.start()

        event.accept()

    def _fade_tick(self):
        """Called by the timer — move volume one step toward the limit."""
        current = self.volume_slider.value()
        new_val = current + self._fade_direction * self._fade_step
        new_val = max(0, min(100, new_val))

        if new_val == current:
            self._log('DEBUG', f'Volume fade: hit limit at {current}% → stopped')
            self._stop_fade()
            return

        self.volume_slider.setValue(new_val)

    def _stop_fade(self):
        """Stop the momentum fade."""
        self._fade_timer.stop()
        self._fade_direction = 0
        self._current_interval_ms = self._max_interval_ms
        self._wheel_times.clear()

    def _log(self, level, msg):
        """Emit a debug_log signal for the main window to pick up."""
        self.debug_log.emit(level, msg)

    def _on_mute_clicked(self):
        """Stop any active fade and emit the mute signal."""
        self._stop_fade()
        self.mute_toggled.emit()

    def _on_volume_changed(self, value):
        self.lbl_vol_pct.setText(f'{value}%')
        self.volume_changed.emit(value)

    # ── Public API ───────────────────────────────────────

    def set_mute_icon(self, muted):
        self.btn_mute.setIcon(self._icon_vol_off if muted else self._icon_vol_high)

    def set_volume(self, vol):
        """Set volume slider (0–100) without emitting signal."""
        self._stop_fade()
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(vol)
        self.volume_slider.blockSignals(False)
        self.lbl_vol_pct.setText(f'{vol}%')

    # ── Tuning setters (called by FadeTuningPanel) ───────

    def set_fade_step(self, v):
        self._fade_step = v

    def set_min_interval(self, v):
        self._min_interval_ms = v

    def set_max_interval(self, v):
        self._max_interval_ms = v

    def set_velocity_window(self, v):
        self._velocity_window_s = v / 1000.0  # panel sends ms, store as seconds

    def set_vel_low(self, v):
        self._vel_low = v

    def set_vel_high(self, v):
        self._vel_high = v


# ═════════════════════════════════════════════════════════
# Fade tuning panel — dev knobs for the momentum fade
# ═════════════════════════════════════════════════════════

class FadeTuningPanel(QWidget):
    """
    Raw dev controls for tweaking the momentum-fade parameters.
    Each row: label — slider — live value readout.
    Sits below the VolumeStrip; will move to Settings later.
    """

    _LABEL_CSS = f'color:{COLORS["fg_dim"]};font-size:9px;'
    _VALUE_CSS = f'color:{COLORS["accent"]};font-size:9px;font-weight:bold;'

    def __init__(self, volume_strip: VolumeStrip, parent=None):
        super().__init__(parent)
        self._vs = volume_strip
        self._build_ui()

    # helper: one tuning row  →  (slider, value_label)
    def _row(self, layout, label_text, min_val, max_val, default, suffix,
             callback, *, float_scale=0):
        """Add a labelled slider row.  *float_scale*: if >0, the slider
        works in integer units of 1/*float_scale* (e.g. 10 → 0.1 steps)."""
        lbl = QLabel(label_text)
        lbl.setStyleSheet(self._LABEL_CSS)
        layout.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(4)

        sl = QSlider(Qt.Horizontal)
        sl.setRange(min_val, max_val)
        sl.setValue(default)
        sl.setFixedHeight(16)
        row.addWidget(sl, stretch=1)

        val_lbl = QLabel()
        val_lbl.setStyleSheet(self._VALUE_CSS)
        val_lbl.setFixedWidth(48)
        val_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(val_lbl)

        def _on_change(v):
            if float_scale:
                real = v / float_scale
                val_lbl.setText(f'{real:.{len(str(float_scale))-1}f}{suffix}')
                callback(real)
            else:
                val_lbl.setText(f'{v}{suffix}')
                callback(v)

        sl.valueChanged.connect(_on_change)
        _on_change(default)  # set initial text
        layout.addLayout(row)
        return sl, val_lbl

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)

        title = QLabel('Fade Tuning')
        title.setStyleSheet(
            f'color:{COLORS["fg_muted"]};font-size:9px;font-weight:bold;')
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(f'color:{COLORS["border"]};')
        layout.addWidget(sep)

        self._row(layout, 'step (vol/tick)', 1, 10, 1, '',
                  self._vs.set_fade_step)

        self._row(layout, 'min interval (cap)', 5, 100, 20, 'ms',
                  self._vs.set_min_interval)

        self._row(layout, 'max interval', 50, 500, 200, 'ms',
                  self._vs.set_max_interval)

        self._row(layout, 'vel window', 100, 2000, 400, 'ms',
                  self._vs.set_velocity_window)

        self._row(layout, 'vel low', 1, 30, 3, ' e/s',
                  self._vs.set_vel_low)

        self._row(layout, 'vel high', 5, 80, 30, ' e/s',
                  self._vs.set_vel_high)

        layout.addStretch()


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
        self.btn_prev = QPushButton()
        self.btn_prev.setIcon(qta.icon('mdi6.skip-previous', color=COLORS['fg']))
        self.btn_prev.setFixedSize(40, 34)
        self.btn_prev.setIconSize(self.btn_prev.size() * 0.6)
        self.btn_prev.setToolTip('Previous track')
        self.btn_prev.clicked.connect(self.prev_clicked)
        row1.addWidget(self.btn_prev)

        # Play / Pause
        self.btn_play = QPushButton()
        self._icon_play = qta.icon('mdi6.play', color='white')
        self._icon_pause = qta.icon('mdi6.pause', color='white')
        self.btn_play.setIcon(self._icon_play)
        self.btn_play.setFixedSize(48, 34)
        self.btn_play.setIconSize(self.btn_play.size() * 0.65)
        self.btn_play.setToolTip('Play / Pause')
        self.btn_play.setStyleSheet(
            f'QPushButton {{ background-color: {COLORS["accent"]}; border-radius: 4px; }}'
            f'QPushButton:hover {{ background-color: {COLORS["accent_hover"]}; }}')
        self.btn_play.clicked.connect(self.play_pause_clicked)
        row1.addWidget(self.btn_play)

        # Next
        self.btn_next = QPushButton()
        self.btn_next.setIcon(qta.icon('mdi6.skip-next', color=COLORS['fg']))
        self.btn_next.setFixedSize(40, 34)
        self.btn_next.setIconSize(self.btn_next.size() * 0.6)
        self.btn_next.setToolTip('Next track')
        self.btn_next.clicked.connect(self.next_clicked)
        row1.addWidget(self.btn_next)

        # Stop
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(qta.icon('mdi6.stop', color=COLORS['fg']))
        self.btn_stop.setFixedSize(40, 34)
        self.btn_stop.setIconSize(self.btn_stop.size() * 0.6)
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

        # ── Row 2: speed controls ────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        # ── Speed controls ───────────────────────────────
        # Speed frame (highlighted when ≠ 1.0)
        self._speed_frame = QFrame()
        self._speed_frame.setFrameShape(QFrame.NoFrame)
        speed_layout = QHBoxLayout(self._speed_frame)
        speed_layout.setContentsMargins(4, 0, 4, 0)
        speed_layout.setSpacing(2)

        self.btn_speed_down = QPushButton()
        self.btn_speed_down.setIcon(qta.icon('mdi6.minus', color=COLORS['fg']))
        self.btn_speed_down.setFixedSize(28, 26)
        self.btn_speed_down.setIconSize(self.btn_speed_down.size() * 0.6)
        self.btn_speed_down.setToolTip('Decrease speed')
        self.btn_speed_down.clicked.connect(self.speed_down_clicked)
        speed_layout.addWidget(self.btn_speed_down)

        self.lbl_speed = QLabel('1.0×')
        self.lbl_speed.setFixedWidth(40)
        self.lbl_speed.setAlignment(Qt.AlignCenter)
        self.lbl_speed.setStyleSheet('font-weight: bold; font-size: 11px;')
        speed_layout.addWidget(self.lbl_speed)

        self.btn_speed_up = QPushButton()
        self.btn_speed_up.setIcon(qta.icon('mdi6.plus', color=COLORS['fg']))
        self.btn_speed_up.setFixedSize(28, 26)
        self.btn_speed_up.setIconSize(self.btn_speed_up.size() * 0.6)
        self.btn_speed_up.setToolTip('Increase speed')
        self.btn_speed_up.clicked.connect(self.speed_up_clicked)
        speed_layout.addWidget(self.btn_speed_up)

        self.btn_speed_reset = QPushButton('1×')
        self.btn_speed_reset.setFixedSize(32, 26)
        self.btn_speed_reset.setStyleSheet('font-size: 11px; font-weight: bold;')
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
            self.btn_play.setIcon(self._icon_pause)
            self.btn_play.setStyleSheet(
                f'QPushButton {{ background-color: {COLORS["green"]}; border-radius: 4px; }}'
                f'QPushButton:hover {{ background-color: {COLORS["green_hover"]}; }}')
        else:
            self.btn_play.setIcon(self._icon_play)
            self.btn_play.setStyleSheet(
                f'QPushButton {{ background-color: {COLORS["accent"]}; border-radius: 4px; }}'
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

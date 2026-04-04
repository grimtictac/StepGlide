"""
Transport bar — play/pause, stop, scrub slider, time labels, volume,
speed controls, and mute button.
"""

import time

from PySide6.QtCore import Qt, QEvent, QTimer, Signal
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QSlider, QSizePolicy, QStyleOptionSlider,
    QStyle, QVBoxLayout, QWidget,
)

import qtawesome as qta
from ui.theme import COLORS

_ICON_SIZE = 18  # default icon pixel size for transport buttons


# ═════════════════════════════════════════════════════════
# TickSlider — QSlider that paints tick marks even with a stylesheet
# ═════════════════════════════════════════════════════════

class TickSlider(QSlider):
    """QSlider subclass that manually draws tick marks and optional labels.

    Qt's style engine stops rendering tick marks whenever a custom
    stylesheet is applied.  This subclass paints them in ``paintEvent``
    so they remain visible regardless of stylesheet.

    Parameters
    ----------
    tick_color : str | QColor
        Colour used for the tick lines (default: theme ``fg_dim``).
    tick_labels : dict[int, str] | None
        Mapping of slider value → label text drawn beside the tick.
        Only values that land on a tick interval are drawn.
    label_side : str
        ``'left'`` or ``'right'`` for vertical sliders (default ``'right'``).
    """

    def __init__(self, orientation=Qt.Horizontal, parent=None, *,
                 tick_color=None, tick_labels=None, label_side='right'):
        super().__init__(orientation, parent)
        self._tick_color = QColor(tick_color or COLORS['fg_dim'])
        self._tick_labels: dict[int, str] = tick_labels or {}
        self._label_side = label_side
        self._label_font_size = 7

    def set_tick_labels(self, labels: dict):
        """Replace tick labels and repaint."""
        self._tick_labels = labels
        self.repaint()  # force immediate repaint (not deferred)

    def paintEvent(self, event):
        # Let the stylesheet-driven painting happen first
        super().paintEvent(event)

        interval = self.tickInterval()
        if interval <= 0 or self.tickPosition() == QSlider.NoTicks:
            return

        painter = QPainter(self)
        painter.setPen(QPen(self._tick_color, 1))

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)

        # Pixel span available for the slider travel
        groove = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)

        num_ticks = (self.maximum() - self.minimum()) // interval
        if num_ticks <= 0:
            painter.end()
            return

        if self.orientation() == Qt.Horizontal:
            half_handle = handle.width() / 2
            span_start = groove.x() + half_handle
            span_end = groove.right() - half_handle
            span = span_end - span_start

            for i in range(num_ticks + 1):
                val = self.minimum() + i * interval
                if val > self.maximum():
                    break
                frac = (val - self.minimum()) / (self.maximum() - self.minimum())
                x = int(span_start + frac * span)

                tick_len = 4
                tp = self.tickPosition()
                if tp in (QSlider.TicksAbove, QSlider.TicksBothSides):
                    painter.drawLine(x, groove.top() - 2, x, groove.top() - 2 - tick_len)
                if tp in (QSlider.TicksBelow, QSlider.TicksBothSides):
                    painter.drawLine(x, groove.bottom() + 2, x, groove.bottom() + 2 + tick_len)
        else:
            # Vertical — ticks on left / right / both
            half_handle = handle.height() / 2
            span_start = groove.y() + half_handle
            span_end = groove.bottom() - half_handle
            span = span_end - span_start

            from PySide6.QtGui import QFont
            label_font = QFont()
            label_font.setPixelSize(self._label_font_size)

            for i in range(num_ticks + 1):
                val = self.minimum() + i * interval
                if val > self.maximum():
                    break
                # For vertical sliders higher value = lower pixel-y
                frac = 1.0 - (val - self.minimum()) / (self.maximum() - self.minimum())
                y = int(span_start + frac * span)

                tick_len = 4
                tp = self.tickPosition()
                if tp in (QSlider.TicksLeft, QSlider.TicksBothSides):
                    painter.drawLine(groove.left() - 2, y, groove.left() - 2 - tick_len, y)
                if tp in (QSlider.TicksRight, QSlider.TicksBothSides):
                    painter.drawLine(groove.right() + 2, y, groove.right() + 2 + tick_len, y)

                # Draw label if provided for this value
                label_text = self._tick_labels.get(val)
                if label_text:
                    painter.setFont(label_font)
                    fm = painter.fontMetrics()
                    text_h = fm.height()
                    text_w = fm.horizontalAdvance(label_text)
                    if self._label_side == 'left':
                        tx = 0  # flush to left edge of widget
                    else:
                        tx = self.width() - text_w  # flush to right edge
                    ty = y + text_h // 3  # vertically centre on tick
                    painter.drawText(tx, ty, label_text)

        painter.end()


# ── Gauge stylesheets (vertical orientation) ─────────────
_GAUGE_WIDTH = 10

# Speed gauge — fills upward (green→yellow→red, bottom-to-top)
_SPEED_BAR_UP_CSS = '''
    QProgressBar {{
        background: {bg};
        border: 1px solid {border};
        border-radius: 3px;
    }}
    QProgressBar::chunk {{
        border-radius: 2px;
        background: qlineargradient(
            x1:0, y1:1, x2:0, y2:0,
            stop:0 {green}, stop:0.5 {yellow}, stop:1 {red});
    }}
'''.format(bg=COLORS['bg'], border=COLORS['border'],
           green=COLORS['green'], yellow=COLORS['yellow'], red=COLORS['red'])

# Speed gauge — fills downward (green→yellow→red, top-to-bottom)
_SPEED_BAR_DN_CSS = '''
    QProgressBar {{
        background: {bg};
        border: 1px solid {border};
        border-radius: 3px;
    }}
    QProgressBar::chunk {{
        border-radius: 2px;
        background: qlineargradient(
            x1:0, y1:0, x2:0, y2:1,
            stop:0 {green}, stop:0.5 {yellow}, stop:1 {red});
    }}
'''.format(bg=COLORS['bg'], border=COLORS['border'],
           green=COLORS['green'], yellow=COLORS['yellow'], red=COLORS['red'])

# Boost gauge — fills upward (cyan→accent, bottom-to-top)
_BOOST_BAR_UP_CSS = '''
    QProgressBar {{
        background: {bg};
        border: 1px solid {border};
        border-radius: 3px;
    }}
    QProgressBar::chunk {{
        border-radius: 2px;
        background: qlineargradient(
            x1:0, y1:1, x2:0, y2:0,
            stop:0 {cyan}, stop:1 {accent});
    }}
'''.format(bg=COLORS['bg'], border=COLORS['border'],
           cyan=COLORS['cyan'], accent=COLORS['accent'])

# Boost gauge — fills downward (cyan→accent, top-to-bottom)
_BOOST_BAR_DN_CSS = '''
    QProgressBar {{
        background: {bg};
        border: 1px solid {border};
        border-radius: 3px;
    }}
    QProgressBar::chunk {{
        border-radius: 2px;
        background: qlineargradient(
            x1:0, y1:0, x2:0, y2:1,
            stop:0 {cyan}, stop:1 {accent});
    }}
'''.format(bg=COLORS['bg'], border=COLORS['border'],
           cyan=COLORS['cyan'], accent=COLORS['accent'])


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
    fade_state_changed = Signal(bool)  # is_fading
    settings_requested = Signal()      # gear button clicked → open settings

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(96)

        # ── Tunable parameters (driven by FadeTuningPanel) ──
        self._fade_step = 1           # volume units per tick
        self._min_interval_ms = 20    # fastest allowed fade tick (cap)
        self._max_interval_ms = 200   # slowest fade tick
        self._velocity_window_s = 0.4 # sliding window for measuring scroll speed
        self._vel_low = 3.0           # scroll evt/s considered "slow"
        self._vel_high = 30.0         # scroll evt/s considered "fast"
        self._tick_threshold = 120    # angleDelta° per logical tick (120 = 1 mouse notch)

        # ── Fade state ──
        # The model: accumulated_speed is in "vol units per second".
        # Each scroll burst *adds* to it based on instantaneous velocity.
        # The timer interval is derived: interval = 1000 / (accumulated_speed / step).
        self._fade_direction = 0      # -1 / 0 / +1
        self._accumulated_speed = 0.0 # vol-units/sec, grows with each scroll burst
        self._instant_velocity = 0.0  # latest scroll velocity (evt/s), for boost bar
        self._delta_accumulator = 0   # angleDelta accumulator for tick detection
        self._pending_interval = 0    # deferred interval for set_fade_speed()
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._fade_tick)

        # Timer to detect "user stopped scrolling" → reset boost bar
        self._boost_decay_timer = QTimer(self)
        self._boost_decay_timer.setSingleShot(True)
        self._boost_decay_timer.timeout.connect(self._on_boost_decay)

        # Velocity measurement: timestamps of recent wheel events
        self._wheel_times: list[float] = []
        self._scroll_input_enabled = True  # can be toggled off by VolumePanel

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

        # ── Slider row: [speed col] [volume slider] [boost col] ──
        slider_row = QHBoxLayout()
        slider_row.setSpacing(3)
        slider_row.setContentsMargins(0, 0, 0, 0)

        # Helper to create one half-height gauge bar
        def _make_bar(css, inverted=False, tooltip=''):
            bar = QProgressBar()
            bar.setOrientation(Qt.Vertical)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setFixedWidth(_GAUGE_WIDTH)
            bar.setStyleSheet(css)
            bar.setFormat('')
            bar.setTextVisible(False)
            bar.setToolTip(tooltip)
            if inverted:
                bar.setInvertedAppearance(True)
            return bar

        # ── Speed column (left of slider) ──
        speed_col = QVBoxLayout()
        speed_col.setSpacing(1)
        speed_col.setContentsMargins(0, 0, 0, 0)

        self._speed_bar_up = _make_bar(
            _SPEED_BAR_UP_CSS, inverted=False, tooltip='Fade speed (up)')
        speed_col.addWidget(self._speed_bar_up, stretch=1)

        self._speed_bar_dn = _make_bar(
            _SPEED_BAR_DN_CSS, inverted=True, tooltip='Fade speed (down)')
        speed_col.addWidget(self._speed_bar_dn, stretch=1)

        slider_row.addLayout(speed_col)

        # Volume slider (center) — with percent labels
        _vol_labels = {0: '0', 20: '20', 40: '40', 60: '60', 80: '80', 100: '100'}
        self.volume_slider = TickSlider(
            Qt.Vertical, tick_labels=_vol_labels, label_side='right')
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setToolTip('Volume')
        self.volume_slider.setTickPosition(QSlider.TicksLeft)
        self.volume_slider.setTickInterval(10)
        self.volume_slider._label_font_size = 6
        self.volume_slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.volume_slider.setFixedWidth(50)
        self.volume_slider.setStyleSheet(f'''
            QSlider::groove:vertical {{
                width: 14px;
                border-radius: 7px;
                background: qlineargradient(
                    x1:0, y1:0, x2:0, y2:1,
                    stop:0.00 {COLORS["red"]},
                    stop:0.50 {COLORS["yellow"]},
                    stop:1.00 {COLORS["green"]});
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
                background: transparent;
                border-radius: 7px;
            }}
            QSlider::add-page:vertical {{
                background: transparent;
                border-radius: 7px;
            }}
        ''')
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.sliderPressed.connect(self._stop_fade)
        self.volume_slider.installEventFilter(self)
        slider_row.addWidget(self.volume_slider)

        # ── Boost column (right of slider) ──
        boost_col = QVBoxLayout()
        boost_col.setSpacing(1)
        boost_col.setContentsMargins(0, 0, 0, 0)

        self._boost_bar_up = _make_bar(
            _BOOST_BAR_UP_CSS, inverted=False, tooltip='Boost (up)')
        boost_col.addWidget(self._boost_bar_up, stretch=1)

        self._boost_bar_dn = _make_bar(
            _BOOST_BAR_DN_CSS, inverted=True, tooltip='Boost (down)')
        boost_col.addWidget(self._boost_bar_dn, stretch=1)

        slider_row.addLayout(boost_col)

        layout.addLayout(slider_row, stretch=1)

        # Labels under the bars
        bar_labels = QHBoxLayout()
        bar_labels.setSpacing(3)
        bar_labels.setContentsMargins(0, 0, 0, 0)

        self._speed_lbl = QLabel('—')
        self._speed_lbl.setStyleSheet(
            f'color:{COLORS["fg_dim"]};font-size:7px;')
        self._speed_lbl.setAlignment(Qt.AlignCenter)
        bar_labels.addWidget(self._speed_lbl)

        self._vel_lbl = QLabel('0.0')
        self._vel_lbl.setStyleSheet(
            f'color:{COLORS["accent"]};font-size:8px;font-weight:bold;')
        self._vel_lbl.setAlignment(Qt.AlignCenter)
        bar_labels.addWidget(self._vel_lbl)

        self._boost_lbl = QLabel('—')
        self._boost_lbl.setStyleSheet(
            f'color:{COLORS["fg_dim"]};font-size:7px;')
        self._boost_lbl.setAlignment(Qt.AlignCenter)
        bar_labels.addWidget(self._boost_lbl)

        layout.addLayout(bar_labels)

        # Gear button at the bottom
        self._btn_gear = QPushButton()
        self._btn_gear.setIcon(qta.icon('mdi6.cog', color=COLORS['fg_dim']))
        self._btn_gear.setFixedSize(32, 28)
        self._btn_gear.setIconSize(self._btn_gear.size() * 0.6)
        self._btn_gear.setToolTip('Volume & fade settings')
        self._btn_gear.setStyleSheet('border: none;')
        self._btn_gear.clicked.connect(self.settings_requested)
        layout.addWidget(self._btn_gear, alignment=Qt.AlignHCenter)

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

    def _velocity_to_speed_contribution(self, velocity):
        """Map scroll velocity (evt/s) → speed contribution (vol-units/sec).

        Slow scroll → small contribution, fast scroll → large contribution.
        The range maps vel_low..vel_high to a contribution of
        min_speed..max_speed where those are derived from the interval bounds.
        """
        # max_speed corresponds to the fastest fade (min_interval)
        # min_speed corresponds to the slowest fade (max_interval)
        min_speed = self._fade_step * 1000.0 / self._max_interval_ms  # e.g. 1*1000/200 = 5 vol/s
        max_speed = self._fade_step * 1000.0 / self._min_interval_ms  # e.g. 1*1000/20 = 50 vol/s

        rng = self._vel_high - self._vel_low
        if rng <= 0:
            rng = 1.0
        t = max(0.0, min(1.0, (velocity - self._vel_low) / rng))
        contribution = min_speed + t * (max_speed - min_speed)
        return contribution

    def _speed_to_interval(self, speed):
        """Convert accumulated speed (vol-units/sec) → timer interval (ms)."""
        if speed <= 0:
            return self._max_interval_ms
        interval = int(self._fade_step * 1000.0 / speed)
        return max(self._min_interval_ms, min(self._max_interval_ms, interval))

    def _handle_wheel(self, event):
        """Unified wheel handler with delta accumulation and additive speed.

        Raw angleDelta values are accumulated until they cross _tick_threshold
        (default 120° = one mouse-wheel notch).  This normalises trackpad
        micro-events so they produce the same tick rate as a mouse wheel for
        equivalent scroll distance.

        Each logical tick:
        - Applies an immediate volume step.
        - Records a timestamp for velocity measurement.
        - Maps velocity → speed contribution → adds to accumulated speed.
        - Updates the fade timer interval.

        Scrolling opposite direction stops the fade entirely.
        """
        if not self._scroll_input_enabled:
            event.accept()
            return

        delta = event.angleDelta().y()
        if delta == 0:
            return

        direction = 1 if delta > 0 else -1

        # Opposite direction → stop current fade and reset accumulator
        if self._fade_direction != 0 and direction != self._fade_direction:
            self._log('DEBUG', 'Volume fade: reversed direction → stopping fade')
            self._stop_fade()
            event.accept()
            return

        # Accumulate raw delta
        self._delta_accumulator += delta

        # How many logical ticks did we cross?
        ticks = int(self._delta_accumulator / self._tick_threshold)
        if ticks == 0:
            # Not enough delta yet — just consume the event
            event.accept()
            return

        # Keep the remainder for next event
        self._delta_accumulator -= ticks * self._tick_threshold
        abs_ticks = abs(ticks)

        # Apply the immediate scroll step (2 vol-units per logical tick)
        step_per_tick = 2
        new_val = max(0, min(100,
                    self.volume_slider.value() + direction * step_per_tick * abs_ticks))
        self.volume_slider.setValue(new_val)

        # Record logical ticks for velocity measurement
        now = time.monotonic()
        for _ in range(abs_ticks):
            self._wheel_times.append(now)
        cutoff = now - self._velocity_window_s
        self._wheel_times = [t for t in self._wheel_times if t >= cutoff]

        # Calculate instantaneous velocity (logical ticks/s)
        n = len(self._wheel_times)
        if n >= 2:
            span = self._wheel_times[-1] - self._wheel_times[0]
            velocity = (n - 1) / span if span > 0 else float(n)
        else:
            velocity = self._vel_low  # single tick → minimum

        self._instant_velocity = velocity

        # Map velocity to a speed contribution and ADD to accumulated speed
        contribution = self._velocity_to_speed_contribution(velocity)

        was_idle = self._fade_direction == 0
        old_speed = self._accumulated_speed
        self._accumulated_speed += contribution

        # Cap speed so interval doesn't go below min_interval_ms
        max_speed = self._fade_step * 1000.0 / self._min_interval_ms
        self._accumulated_speed = min(self._accumulated_speed, max_speed)

        new_interval = self._speed_to_interval(self._accumulated_speed)

        if was_idle:
            self._log(
                'DEBUG',
                f'Volume fade: NEW {"UP" if direction > 0 else "DOWN"}  '
                f'ticks={abs_ticks}  vel={velocity:.1f} t/s  '
                f'contribution={contribution:.1f} v/s  '
                f'total_speed={self._accumulated_speed:.1f} v/s  '
                f'interval={new_interval}ms'
            )
        else:
            self._log(
                'DEBUG',
                f'Volume fade: BOOST  ticks={abs_ticks}  '
                f'vel={velocity:.1f} t/s  +{contribution:.1f} v/s  '
                f'speed {old_speed:.1f}→{self._accumulated_speed:.1f} v/s  '
                f'interval={new_interval}ms'
            )

        # Start / update the fade
        self._fade_direction = direction
        self._fade_timer.setInterval(new_interval)
        self._fade_timer.start()

        # Reset the boost decay timer (fires when user stops scrolling)
        self._boost_decay_timer.start(int(self._velocity_window_s * 1000) + 100)

        self._emit_fade_state()
        event.accept()

    def _on_boost_decay(self):
        """Called when user stops scrolling — reset the boost bar to zero."""
        self._instant_velocity = 0.0
        self._wheel_times.clear()
        self._update_gauges(is_fading=self._fade_direction != 0)

    def _fade_tick(self):
        """Called by the timer — move volume one step toward the limit."""
        current = self.volume_slider.value()
        new_val = current + self._fade_direction * self._fade_step
        new_val = max(0, min(100, new_val))

        if new_val == current:
            self._log('DEBUG', f'Volume fade: hit limit at {current}% → stopped')
            self._stop_fade()
            # Auto-mute when fade reaches zero
            if current == 0:
                self._log('DEBUG', 'Volume fade: reached zero → auto-mute')
                self.mute_toggled.emit()
            return

        self.volume_slider.setValue(new_val)
        self._emit_fade_state()

        # Apply deferred interval change from set_fade_speed()
        if self._pending_interval > 0:
            self._fade_timer.setInterval(self._pending_interval)
            self._pending_interval = 0

    def _stop_fade(self):
        """Stop the momentum fade and reset all state."""
        self._fade_timer.stop()
        self._boost_decay_timer.stop()
        self._fade_direction = 0
        self._accumulated_speed = 0.0
        self._instant_velocity = 0.0
        self._delta_accumulator = 0
        self._pending_interval = 0
        self._wheel_times.clear()
        self._emit_fade_state()

    # ── Public: external speed injection (used by PullFader) ──

    def inject_fade_speed(self, speed_vps, direction):
        """Add *speed_vps* vol-units/sec to the shared fade in *direction*.

        This is the entry point for the pull-fader (and any future fade
        source) to feed into the single unified fade model.  The injected
        speed is added to whatever accumulated speed already exists.  If
        direction conflicts with a running fade, the fade is stopped first
        (same rule as opposite-scroll).
        """
        if direction == 0 or speed_vps <= 0:
            return

        # Opposite direction → stop, then start fresh in new direction
        if self._fade_direction != 0 and direction != self._fade_direction:
            self._log('DEBUG',
                      f'inject_fade_speed: direction conflict → stopping fade')
            self._stop_fade()

        self._fade_direction = direction
        self._accumulated_speed += speed_vps

        # Cap at max speed
        max_speed = self._fade_step * 1000.0 / self._min_interval_ms
        self._accumulated_speed = min(self._accumulated_speed, max_speed)

        new_interval = self._speed_to_interval(self._accumulated_speed)

        self._log('DEBUG',
                  f'inject_fade_speed: +{speed_vps:.1f} v/s  '
                  f'total={self._accumulated_speed:.1f} v/s  '
                  f'interval={new_interval}ms  '
                  f'dir={"UP" if direction > 0 else "DOWN"}')

        self._fade_timer.setInterval(new_interval)
        self._fade_timer.start()
        self._emit_fade_state()

    def set_fade_speed(self, speed_vps, direction):
        """Set the fade speed to exactly *speed_vps* vol-units/sec.

        Unlike inject_fade_speed (which adds), this *replaces* the current
        speed.  Designed for continuous live control (e.g. pull-fader).

        The desired interval is stored in *_pending_interval*.  If the timer
        is already running it is picked up on the next ``_fade_tick`` rather
        than calling ``setInterval()`` mid-countdown, which would restart the
        timer and starve ticks during rapid drag events.
        """
        if direction == 0 or speed_vps <= 0:
            return

        if self._fade_direction != 0 and direction != self._fade_direction:
            self._stop_fade()

        self._fade_direction = direction
        max_speed = self._fade_step * 1000.0 / self._min_interval_ms
        self._accumulated_speed = min(speed_vps, max_speed)

        new_interval = self._speed_to_interval(self._accumulated_speed)
        if self._fade_timer.isActive():
            # Don't touch the running timer — let _fade_tick pick it up
            self._pending_interval = new_interval
        else:
            self._pending_interval = 0
            self._fade_timer.setInterval(new_interval)
            self._fade_timer.start()

    def _emit_fade_state(self):
        """Broadcast current fade state and update local gauges."""
        is_fading = self._fade_direction != 0
        self.fade_state_changed.emit(is_fading)
        self._update_gauges(is_fading)

    def _update_gauges(self, is_fading):
        """Update the split up/down speed & boost bars and labels.

        When fading UP   → top halves show values, bottom halves zeroed.
        When fading DOWN → bottom halves show values, top halves zeroed.
        When idle        → all zeroed.
        """
        max_speed = self._fade_step * 1000.0 / self._min_interval_ms
        direction = self._fade_direction  # -1, 0, +1

        # ── Speed bars ──
        if is_fading or self._accumulated_speed > 0:
            speed_pct = int(100 * self._accumulated_speed / max_speed) if max_speed > 0 else 0
            speed_pct = max(0, min(100, speed_pct))
            self._speed_lbl.setText(f'{self._accumulated_speed:.0f}v/s')

            if direction >= 0:  # fading up (or idle-with-residual → show up)
                self._speed_bar_up.setValue(speed_pct)
                self._speed_bar_dn.setValue(0)
            else:               # fading down
                self._speed_bar_up.setValue(0)
                self._speed_bar_dn.setValue(speed_pct)
        else:
            self._speed_bar_up.setValue(0)
            self._speed_bar_dn.setValue(0)
            self._speed_lbl.setText('—')

        # ── Boost bars ──
        if self._instant_velocity > 0:
            boost_pct = int(100 * self._instant_velocity / self._vel_high)
            boost_pct = max(0, min(100, boost_pct))
            self._boost_lbl.setText(f'{self._instant_velocity:.0f}e/s')

            if direction >= 0:
                self._boost_bar_up.setValue(boost_pct)
                self._boost_bar_dn.setValue(0)
            else:
                self._boost_bar_up.setValue(0)
                self._boost_bar_dn.setValue(boost_pct)
        else:
            self._boost_bar_up.setValue(0)
            self._boost_bar_dn.setValue(0)
            self._boost_lbl.setText('—')

        self._vel_lbl.setText(f'{self._instant_velocity:.1f}')

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

    # ── Tuning setters (called by settings dialog / FadeTuningPanel) ──

    def set_fade_step(self, v):
        self._fade_step = v

    def set_min_interval(self, v):
        self._min_interval_ms = v

    def set_max_interval(self, v):
        self._max_interval_ms = v

    def set_velocity_window(self, v):
        self._velocity_window_s = v / 1000.0  # caller sends ms, store as seconds

    def set_vel_low(self, v):
        self._vel_low = v

    def set_vel_high(self, v):
        self._vel_high = v

    def set_tick_threshold(self, v):
        self._tick_threshold = v

    def apply_config(self, config):
        """Apply fade settings from an AppConfig instance."""
        self.set_fade_step(config.fade_step)
        self.set_min_interval(config.fade_min_interval)
        self.set_max_interval(config.fade_max_interval)
        self.set_velocity_window(config.fade_vel_window)
        self.set_vel_low(config.fade_vel_low)
        self.set_vel_high(config.fade_vel_high)
        self.set_tick_threshold(config.fade_tick_threshold)


# ═════════════════════════════════════════════════════════
# Pull-fader — direct live speed controller
# ═════════════════════════════════════════════════════════

_PULL_FADER_CSS = '''
    QSlider::groove:vertical {{
        width: 12px;
        border-radius: 6px;
        background: qlineargradient(
            x1:0, y1:0, x2:0, y2:1,
            stop:0.00 {bg_dark},
            stop:0.08 {bg_dark},
            stop:0.12 {cyan_dim},
            stop:0.50 {cyan},
            stop:1.00 {cyan_bright});
    }}
    QSlider::handle:vertical {{
        background: {fg};
        border: 2px solid {accent};
        height: 20px;
        width: 24px;
        margin: 0 -6px;
        border-radius: 5px;
    }}
    QSlider::handle:vertical:hover {{
        background: {accent};
        border: 2px solid {fg};
    }}
    QSlider::handle:vertical:pressed {{
        background: {red};
        border: 2px solid {fg};
    }}
    QSlider::sub-page:vertical {{
        background: transparent;
        border-radius: 6px;
    }}
    QSlider::add-page:vertical {{
        background: transparent;
        border-radius: 6px;
    }}
'''.format(
    bg_dark=COLORS['bg'], cyan_dim='#1a4a5a',
    cyan=COLORS['cyan'], cyan_bright=COLORS['cyan_bright'],
    red=COLORS['red'], fg=COLORS['fg'], accent=COLORS['accent'],
)


class PullFader(QWidget):
    """
    Direct live speed controller for volume fade.

    A vertical slider whose handle rests at the top (100).  While the user
    holds the handle and drags it down, volume fades continuously — how far
    the handle is pulled directly determines fade speed.  Releasing the
    handle snaps it back to 100 and stops the fade.

    Dead zone: small region at the top where pulling has no effect.
    Below the dead zone: speed scales from minimum to maximum proportional
    to pull distance.

    This is a *live* controller: fading happens the entire time the handle
    is held outside the dead zone, not on release.
    """

    debug_log = Signal(str, str)

    def __init__(self, volume_strip: 'VolumeStrip', parent=None):
        super().__init__(parent)
        self._vs = volume_strip
        self.setFixedWidth(82)

        # Pull-fader own tunable parameters (independent of scroll-fade)
        self._min_interval_ms = 20    # fastest fade (full pull)
        self._max_interval_ms = 200   # slowest fade (tiny pull)
        self._fade_step = 1           # volume units per tick
        self._dead_zone_pct = 5       # pull < this% is ignored

        self._user_holding = False     # True while mouse button is down

        self._build_ui()
        self._update_tick_labels()     # compute seconds labels from params

    def _pull_pct_to_speed(self, pull_pct):
        """Convert pull_pct (0–100) → speed in vol-units/sec, or 0 if in dead zone."""
        dz = self._dead_zone_pct
        if pull_pct < dz:
            return 0.0
        usable = 100 - dz
        t = (pull_pct - dz) / usable if usable > 0 else 1.0
        t = max(0.0, min(1.0, t))
        min_iv = self._min_interval_ms
        max_iv = self._max_interval_ms
        interval = max_iv - t * (max_iv - min_iv)
        interval = max(min_iv, min(max_iv, interval))
        return self._fade_step * 1000.0 / interval

    def _update_tick_labels(self):
        """Recalculate seconds-to-fade labels for each tick mark."""
        labels: dict[int, str] = {}
        for val in range(0, 101, 10):
            pull_pct = 100 - val  # slider value → pull distance
            speed = self._pull_pct_to_speed(pull_pct)
            if speed > 0:
                secs = 100.0 / speed  # time to fade 100→0 at this speed
                if secs >= 10:
                    labels[val] = f'{secs:.0f}s'
                else:
                    labels[val] = f'{secs:.1f}s'
            else:
                labels[val] = ''  # dead zone — no label
        self._slider.set_tick_labels(labels)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignHCenter)

        # Label
        title = QLabel('PULL')
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f'color:{COLORS["fg_dim"]};font-size:8px;font-weight:bold;')
        layout.addWidget(title)

        # Pull slider — 100 at top (resting), 0 at bottom (max pull)
        self._slider = TickSlider(
            Qt.Vertical, label_side='right')
        self._slider.setRange(0, 100)
        self._slider.setValue(100)
        self._slider.setTickPosition(QSlider.TicksLeft)
        self._slider.setTickInterval(10)
        self._slider._label_font_size = 6
        self._slider.setFixedWidth(48)
        self._slider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._slider.setStyleSheet(_PULL_FADER_CSS)
        self._slider.setToolTip(
            'Hold and pull down to fade volume.\n'
            'Further pull = faster fade.\n'
            'Release to stop.')
        self._slider.sliderPressed.connect(self._on_pressed)
        self._slider.sliderReleased.connect(self._on_released)
        self._slider.valueChanged.connect(self._on_value_changed)
        layout.addWidget(self._slider, stretch=1, alignment=Qt.AlignHCenter)

        # Speed readout
        self._lbl_speed = QLabel('—')
        self._lbl_speed.setAlignment(Qt.AlignCenter)
        self._lbl_speed.setStyleSheet(
            f'color:{COLORS["fg_dim"]};font-size:8px;')
        layout.addWidget(self._lbl_speed)

        # Fade-time indicator
        self._lbl_fade_time = QLabel('—')
        self._lbl_fade_time.setAlignment(Qt.AlignCenter)
        self._lbl_fade_time.setStyleSheet(
            f'color:{COLORS["cyan"]};font-size:9px;font-weight:bold;')
        self._lbl_fade_time.setToolTip('Estimated seconds to fade to zero')
        layout.addWidget(self._lbl_fade_time)

        # Pull-distance indicator bar
        self._pull_bar = QProgressBar()
        self._pull_bar.setOrientation(Qt.Horizontal)
        self._pull_bar.setRange(0, 100)
        self._pull_bar.setValue(0)
        self._pull_bar.setFixedHeight(6)
        self._pull_bar.setTextVisible(False)
        self._pull_bar.setStyleSheet(f'''
            QProgressBar {{
                background: {COLORS["bg"]};
                border: 1px solid {COLORS["border"]};
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                border-radius: 2px;
                background: qlineargradient(
                    x1:0, y1:0, x2:1, y2:0,
                    stop:0 {COLORS["cyan"]}, stop:1 {COLORS["cyan_bright"]});
            }}
        ''')
        layout.addWidget(self._pull_bar)

    # ── Slider interaction ──

    def _on_pressed(self):
        """User grabbed the handle — stop any existing scroll-fade."""
        self._user_holding = True
        self._vs._stop_fade()

    def _on_value_changed(self, value):
        """Called on every pixel of handle drag.

        While the user is holding and outside the dead zone, continuously
        feed the fade model with the current speed.
        """
        if not self._user_holding:
            return

        pull_pct = 100 - value   # 0 = no pull, 100 = max pull
        speed_vps = self._pull_pct_to_speed(pull_pct)

        if speed_vps <= 0:
            # Inside dead zone — stop fade, clear readout
            self._vs._stop_fade()
            self._pull_bar.setValue(0)
            self._lbl_speed.setText('—')
            self._lbl_fade_time.setText('—')
            return

        # Update visuals
        self._pull_bar.setValue(pull_pct)
        self._lbl_speed.setText(f'{speed_vps:.0f}v/s')

        # Compute time remaining to fade current volume to zero
        cur_vol = self._vs.volume_slider.value()
        if cur_vol > 0 and speed_vps > 0:
            secs = cur_vol / speed_vps
            if secs >= 10:
                self._lbl_fade_time.setText(f'{secs:.0f}s')
            else:
                self._lbl_fade_time.setText(f'{secs:.1f}s')
        else:
            self._lbl_fade_time.setText('0s')

        # Feed the fade model — always fade DOWN
        self._vs.set_fade_speed(speed_vps, -1)

    def _on_released(self):
        """User released — stop the fade, snap handle back to top."""
        self._user_holding = False
        self._vs._stop_fade()

        # Snap handle back to resting position
        self._slider.blockSignals(True)
        self._slider.setValue(100)
        self._slider.blockSignals(False)

        # Clear readout
        self._pull_bar.setValue(0)
        self._lbl_speed.setText('—')
        self._lbl_fade_time.setText('—')

        self.debug_log.emit('DEBUG', 'Pull-fader: released → fade stopped')

    def stop(self):
        """Public: reset visual state (called externally if needed)."""
        self._user_holding = False
        self._pull_bar.setValue(0)
        self._lbl_speed.setText('—')
        self._lbl_fade_time.setText('—')
        self._slider.blockSignals(True)
        self._slider.setValue(100)
        self._slider.blockSignals(False)

    # ── Tuning setters (called by settings dialog) ──

    def set_min_interval(self, v):
        self._min_interval_ms = v
        self._update_tick_labels()

    def set_max_interval(self, v):
        self._max_interval_ms = v
        self._update_tick_labels()

    def set_fade_step(self, v):
        self._fade_step = v
        self._update_tick_labels()

    def set_dead_zone(self, v):
        self._dead_zone_pct = v
        self._update_tick_labels()

    def apply_config(self, config):
        """Apply pull-fader settings from an AppConfig instance."""
        self.set_fade_step(config.pull_fade_step)
        self.set_min_interval(config.pull_min_interval)
        self.set_max_interval(config.pull_max_interval)
        self.set_dead_zone(config.pull_dead_zone)


# ═════════════════════════════════════════════════════════
# Volume panel — tabbed container: VolumeStrip + mode switch
# ═════════════════════════════════════════════════════════

class VolumePanel(QWidget):
    """
    Wraps VolumeStrip and PullFader side-by-side.  Both are always visible.

    Two toggle buttons control which *inputs* are accepted:
      - Scroll toggle: enables/disables scroll-wheel fade input
      - Pull toggle:   enables/disables pull-fader interaction
    Both default ON.  Toggling never interrupts a running fade — it only
    gates new input from that source.

    Layout:  [toggle col]  [VolumeStrip]  [PullFader]
    """

    def __init__(self, volume_strip: VolumeStrip, parent=None):
        super().__init__(parent)
        self._vs = volume_strip
        self._pull_fader = PullFader(volume_strip, self)
        self._pull_fader.debug_log.connect(volume_strip.debug_log)

        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── Toggle buttons (vertical column) ──
        toggle_col = QVBoxLayout()
        toggle_col.setSpacing(2)
        toggle_col.setContentsMargins(0, 0, 0, 0)

        self._btn_scroll = QPushButton()
        self._btn_scroll.setIcon(qta.icon('mdi6.mouse', color=COLORS['fg']))
        self._btn_scroll.setFixedSize(24, 32)
        self._btn_scroll.setIconSize(self._btn_scroll.size() * 0.65)
        self._btn_scroll.setCheckable(True)
        self._btn_scroll.setChecked(True)
        self._btn_scroll.setToolTip('Enable scroll-wheel fade')
        self._btn_scroll.setStyleSheet(self._toggle_css())
        self._btn_scroll.toggled.connect(self._on_scroll_toggled)
        toggle_col.addWidget(self._btn_scroll)

        self._btn_pull = QPushButton()
        self._btn_pull.setIcon(
            qta.icon('mdi6.arrow-down-bold', color=COLORS['fg']))
        self._btn_pull.setFixedSize(24, 32)
        self._btn_pull.setIconSize(self._btn_pull.size() * 0.65)
        self._btn_pull.setCheckable(True)
        self._btn_pull.setChecked(True)
        self._btn_pull.setToolTip('Enable pull-fader')
        self._btn_pull.setStyleSheet(self._toggle_css())
        self._btn_pull.toggled.connect(self._on_pull_toggled)
        toggle_col.addWidget(self._btn_pull)

        toggle_col.addStretch()
        layout.addLayout(toggle_col)

        # ── Volume strip (always visible) ──
        layout.addWidget(self._vs)

        # ── Pull fader (always visible) ──
        layout.addWidget(self._pull_fader)

    def _on_scroll_toggled(self, checked):
        """Enable/disable scroll-wheel input. Does NOT stop a running fade."""
        self._vs._scroll_input_enabled = checked

    def _on_pull_toggled(self, checked):
        """Enable/disable pull-fader interaction. Does NOT stop a running fade."""
        self._pull_fader.setEnabled(checked)

    @staticmethod
    def _toggle_css():
        return (
            f'QPushButton {{ border: none; border-radius: 3px; '
            f'background: transparent; }}'
            f'QPushButton:checked {{ background: {COLORS["bg_mid"]}; '
            f'border: 1px solid {COLORS["border"]}; }}'
            f'QPushButton:hover {{ background: {COLORS["bg_light"]}; }}'
        )

    @property
    def volume_strip(self):
        return self._vs

    @property
    def pull_fader(self):
        return self._pull_fader


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

        sl = TickSlider(Qt.Horizontal)
        sl.setRange(min_val, max_val)
        sl.setValue(default)
        sl.setFixedHeight(20)
        sl.setTickPosition(QSlider.TicksBelow)
        sl.setTickInterval(max(1, (max_val - min_val) // 10))
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

        # ── Tuning sliders ───────────────────────────────

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
        self.scrub_slider = TickSlider(Qt.Horizontal)
        self.scrub_slider.setRange(0, 10000)
        self.scrub_slider.setValue(0)
        self.scrub_slider.setToolTip('Seek')
        self.scrub_slider.setTickPosition(QSlider.TicksBelow)
        self.scrub_slider.setTickInterval(1000)
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

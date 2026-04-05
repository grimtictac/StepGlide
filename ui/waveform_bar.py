"""
WaveformScrubBar -- frequency-coloured mirrored waveform + scrub control.

Supports two draw modes (configurable via waveform_settings.draw_mode):
  - **bars**:     discrete vertical bars with configurable width & gap
  - **envelope**: smooth filled polygon (SoundCloud-style) with per-column colour

Reads all visual tuning from ``core.waveform.waveform_settings`` at paint time
so changes from the settings panel take effect immediately.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from ui.theme import COLORS


class WaveformScrubBar(QWidget):
    """Frequency-coloured mirrored waveform that doubles as a scrub bar.

    Signals
    -------
    scrub_pressed()
        User pressed the mouse -- scrubbing started.
    scrub_moved(float)
        Position 0.0-1.0 while dragging.
    scrub_released(float)
        Final position 0.0-1.0 on mouse release.
    """

    scrub_pressed = Signal()
    scrub_moved = Signal(float)
    scrub_released = Signal(float)

    # Fallback defaults (overridden by waveform_settings at paint time)
    PLAYHEAD_COLOR = QColor('#ffffff')
    BG_COLOR = QColor(COLORS['bg_dark'])
    LOADING_COLOR = QColor(COLORS['fg_very_dim'])

    def __init__(self, parent=None, *, height=None):
        super().__init__(parent)
        from core.waveform import waveform_settings
        self._settings = waveform_settings
        h = height or self._settings.bar_height
        self.setMinimumHeight(h)
        self.setFixedHeight(h)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self._waveform = None       # list of (r, g, b, amp) normalised 0-1
        self._binned = None         # re-sampled to bar count
        self._position = 0.0        # 0.0-1.0
        self._is_scrubbing = False
        self._loading = False
        self._hover_x = -1          # -1 = no hover

    # -- Public API -----------------------------------------------

    def set_waveform(self, data):
        """Load waveform data: list of (r, g, b, amp) or None to clear."""
        self._waveform = data
        self._loading = False
        self._rebin()
        self.update()

    def set_position(self, pos):
        """Set playback position 0.0-1.0 (called from poll timer)."""
        self._position = max(0.0, min(1.0, pos))
        self.update()

    def set_loading(self, loading):
        """Show a loading/analysing indicator."""
        self._loading = loading
        self.update()

    def clear(self):
        """Reset to empty state."""
        self._waveform = None
        self._binned = None
        self._position = 0.0
        self._loading = False
        self.update()

    @property
    def is_scrubbing(self):
        return self._is_scrubbing

    def apply_height(self, h):
        """Update the bar height (called from settings panel)."""
        self.setMinimumHeight(h)
        self.setFixedHeight(h)
        self._rebin()
        self.update()

    # -- Internal -------------------------------------------------

    def _rebin(self):
        """Resample waveform data to match current widget width and stride."""
        w = self.width()
        src = self._waveform
        if not src or w <= 0:
            self._binned = None
            return

        s = self._settings
        if s.draw_mode == 'envelope':
            # One bin per pixel column for smooth envelope
            num_bars = max(1, w)
        else:
            stride = s.bar_width + s.bar_gap
            num_bars = max(1, w // stride)

        n = len(src)
        binned = []
        for i in range(num_bars):
            lo = int(i * n / num_bars)
            hi = int((i + 1) * n / num_bars)
            if hi <= lo:
                hi = lo + 1
            hi = min(hi, n)

            r_sum = g_sum = b_sum = amp_max = 0.0
            count = hi - lo
            for j in range(lo, hi):
                r, g, b, a = src[j]
                r_sum += r
                g_sum += g
                b_sum += b
                if a > amp_max:
                    amp_max = a
            inv = 1.0 / count if count else 1.0
            binned.append((r_sum * inv, g_sum * inv, b_sum * inv, amp_max))

        self._binned = binned

    # -- Paint ----------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w = self.width()
        h = self.height()
        mid_y = h // 2
        s = self._settings

        # Background
        painter.fillRect(0, 0, w, h, self.BG_COLOR)

        if self._loading and not self._binned:
            painter.setPen(QPen(self.LOADING_COLOR))
            painter.drawText(self.rect(), Qt.AlignCenter, 'Analysing waveform\u2026')
            painter.end()
            return

        if not self._binned:
            painter.setPen(QPen(QColor(COLORS['border']), 1))
            painter.drawLine(0, mid_y, w, mid_y)
            self._draw_playhead(painter, w, h)
            painter.end()
            return

        if s.draw_mode == 'envelope':
            self._paint_envelope(painter, w, h, mid_y, s)
        else:
            self._paint_bars(painter, w, h, mid_y, s)

        self._draw_playhead(painter, w, h)

        # Hover position indicator
        if self._hover_x >= 0 and not self._is_scrubbing:
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawLine(self._hover_x, 0, self._hover_x, h)

        painter.end()

    def _paint_bars(self, painter, w, h, mid_y, s):
        """Draw discrete vertical bars (Serato/rekordbox style)."""
        playhead_x = int(self._position * w)
        stride = s.bar_width + s.bar_gap
        played_a = s.played_alpha
        unplayed_a = s.unplayed_alpha

        for i, (r, g, b, amp) in enumerate(self._binned):
            x = i * stride
            if x >= w:
                break

            bar_h = max(1, int(amp * (mid_y - 2)))
            alpha = played_a if x < playhead_x else unplayed_a
            color = QColor(
                min(int(r * 255), 255),
                min(int(g * 255), 255),
                min(int(b * 255), 255),
                alpha,
            )

            bw = s.bar_width
            if bw == 1:
                painter.setPen(QPen(color, 1))
                painter.drawLine(x, mid_y - bar_h, x, mid_y + bar_h)
            else:
                painter.fillRect(x, mid_y - bar_h, bw, bar_h * 2, color)

    def _paint_envelope(self, painter, w, h, mid_y, s):
        """Draw a smooth filled envelope (SoundCloud style).

        Strategy: build a QPainterPath for the top contour, mirror it
        for the bottom, then fill column-by-column strips so each
        column gets its own RGB colour from the frequency data.
        """
        binned = self._binned
        n = len(binned)
        if n == 0:
            return

        playhead_x = int(self._position * w)
        played_a = s.played_alpha
        unplayed_a = s.unplayed_alpha

        # Pre-compute heights for each column
        heights = []
        for i in range(n):
            amp = binned[i][3]
            bar_h = max(1, int(amp * (mid_y - 2)))
            heights.append(bar_h)

        # Smooth the heights with a 3-tap moving average for a nicer contour
        if n > 4:
            smoothed = [heights[0]]
            for i in range(1, n - 1):
                smoothed.append((heights[i - 1] + heights[i] + heights[i + 1]) // 3)
            smoothed.append(heights[-1])
            heights = smoothed

        # Draw column-by-column filled strips (each column = 1px wide)
        for x in range(min(n, w)):
            r, g, b, _amp = binned[x]
            bar_h = heights[x]
            alpha = played_a if x < playhead_x else unplayed_a
            color = QColor(
                min(int(r * 255), 255),
                min(int(g * 255), 255),
                min(int(b * 255), 255),
                alpha,
            )
            painter.fillRect(x, mid_y - bar_h, 1, bar_h * 2, color)

    def _draw_playhead(self, painter, w, h):
        """Draw the vertical playhead line."""
        px = int(self._position * w)
        painter.setPen(QPen(self.PLAYHEAD_COLOR, 2))
        painter.drawLine(px, 0, px, h)

    # -- Resize ---------------------------------------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebin()

    # -- Mouse interaction (scrubbing) ----------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._is_scrubbing = True
            self.scrub_pressed.emit()
            pos = self._clamp_pos(event.position().x())
            self._position = pos
            self.scrub_moved.emit(pos)
            self.update()

    def mouseMoveEvent(self, event):
        if self._is_scrubbing:
            pos = self._clamp_pos(event.position().x())
            self._position = pos
            self.scrub_moved.emit(pos)
            self.update()
        else:
            self._hover_x = int(event.position().x())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._is_scrubbing:
            self._is_scrubbing = False
            pos = self._clamp_pos(event.position().x())
            self._position = pos
            self.scrub_released.emit(pos)
            self.update()

    def leaveEvent(self, event):
        self._hover_x = -1
        self.update()
        super().leaveEvent(event)

    def _clamp_pos(self, x):
        w = self.width()
        if w <= 0:
            return 0.0
        return max(0.0, min(1.0, x / w))

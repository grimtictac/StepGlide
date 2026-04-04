"""
WaveformScrubBar — a custom QWidget that displays a frequency-coloured
moodbar waveform (Serato-style: mirrored, RGB = bass/mid/treble) and
acts as a seek / scrub control.

Drop-in replacement for the TickSlider scrub bar in TransportBar.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSizePolicy

from ui.theme import COLORS


class WaveformScrubBar(QWidget):
    """Frequency-coloured mirrored waveform that doubles as a scrub bar.

    Signals
    -------
    scrub_pressed()
        User pressed the mouse — scrubbing started.
    scrub_moved(float)
        Position 0.0–1.0 while dragging.
    scrub_released(float)
        Final position 0.0–1.0 on mouse release.
    """

    scrub_pressed = Signal()
    scrub_moved = Signal(float)
    scrub_released = Signal(float)

    # Visual tuning
    BAR_HEIGHT = 60
    PLAYED_ALPHA = 255
    UNPLAYED_ALPHA = 80
    PLAYHEAD_COLOR = QColor('#ffffff')
    BG_COLOR = QColor(COLORS['bg_dark'])
    LOADING_COLOR = QColor(COLORS['fg_very_dim'])

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.BAR_HEIGHT)
        self.setFixedHeight(self.BAR_HEIGHT)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

        self._waveform = None       # list of (r, g, b, amp) normalised 0–1
        self._binned = None         # re-sampled to widget width
        self._position = 0.0        # 0.0–1.0
        self._is_scrubbing = False
        self._loading = False
        self._hover_x = -1          # -1 = no hover

    # ── Public API ───────────────────────────────────────

    def set_waveform(self, data):
        """Load waveform data: list of (r, g, b, amp) or None to clear."""
        self._waveform = data
        self._loading = False
        self._rebin()
        self.update()

    def set_position(self, pos):
        """Set playback position 0.0–1.0 (called from poll timer)."""
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

    # ── Internal ─────────────────────────────────────────

    def _rebin(self):
        """Resample waveform data to match current widget width."""
        w = self.width()
        src = self._waveform
        if not src or w <= 0:
            self._binned = None
            return

        n = len(src)
        binned = []
        for x in range(w):
            lo = int(x * n / w)
            hi = int((x + 1) * n / w)
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

    # ── Paint ────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        w = self.width()
        h = self.height()
        mid_y = h // 2

        # Background
        painter.fillRect(0, 0, w, h, self.BG_COLOR)

        if self._loading and not self._binned:
            # Pulsing "analysing" text
            painter.setPen(QPen(self.LOADING_COLOR))
            painter.drawText(self.rect(), Qt.AlignCenter, 'Analysing waveform…')
            painter.end()
            return

        if not self._binned:
            # No waveform — draw a simple center line
            painter.setPen(QPen(QColor(COLORS['border']), 1))
            painter.drawLine(0, mid_y, w, mid_y)
            # Draw playhead anyway
            self._draw_playhead(painter, w, h)
            painter.end()
            return

        playhead_x = int(self._position * w)

        for x, (r, g, b, amp) in enumerate(self._binned):
            if x >= w:
                break

            bar_h = max(1, int(amp * (mid_y - 2)))

            # Colour: RGB from frequency bands
            alpha = self.PLAYED_ALPHA if x < playhead_x else self.UNPLAYED_ALPHA
            color = QColor(
                min(int(r * 255), 255),
                min(int(g * 255), 255),
                min(int(b * 255), 255),
                alpha,
            )
            painter.setPen(QPen(color, 1))

            # Mirrored: draw upward and downward from center
            painter.drawLine(x, mid_y - bar_h, x, mid_y + bar_h)

        self._draw_playhead(painter, w, h)

        # Hover position indicator
        if self._hover_x >= 0 and not self._is_scrubbing:
            painter.setPen(QPen(QColor(255, 255, 255, 60), 1))
            painter.drawLine(self._hover_x, 0, self._hover_x, h)

        painter.end()

    def _draw_playhead(self, painter, w, h):
        """Draw the vertical playhead line."""
        px = int(self._position * w)
        painter.setPen(QPen(self.PLAYHEAD_COLOR, 2))
        painter.drawLine(px, 0, px, h)

    # ── Resize ───────────────────────────────────────────

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rebin()

    # ── Mouse interaction (scrubbing) ────────────────────

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

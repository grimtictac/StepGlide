"""
WaveformLegend — compact colour key showing frequency-to-colour mapping.

Sits below the waveform scrub bar.  Shows a gradient strip with
labelled frequency bands: Bass (red), Mid (green), Treble (blue).
Updates live when crossover frequencies change.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QFont
from PySide6.QtWidgets import QWidget

from ui.theme import COLORS
from core.waveform import waveform_settings


class WaveformLegend(QWidget):
    """Compact frequency-colour key strip."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(22)
        self.setMinimumWidth(200)

    def paintEvent(self, event):
        """Draw the colour key: gradient bar + frequency labels."""
        w = self.width()
        h = self.height()
        if w < 10:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        s = waveform_settings
        bass_fc = s.bass_fc
        treble_fc = s.treble_fc

        # Layout: [label_left] [gradient_bar] [label_right]
        margin = 4
        bar_top = 2
        bar_h = 10
        label_y = bar_top + bar_h + 11

        # Draw gradient bar across full width
        grad = QLinearGradient(margin, 0, w - margin, 0)
        # Bass (red) -> Mid (green) -> Treble (blue)
        grad.setColorAt(0.0, QColor(220, 50, 50))      # deep red
        grad.setColorAt(0.18, QColor(255, 80, 30))      # red-orange
        grad.setColorAt(0.30, QColor(220, 180, 30))     # yellow transition
        grad.setColorAt(0.45, QColor(50, 210, 50))      # green
        grad.setColorAt(0.60, QColor(30, 180, 180))     # cyan transition
        grad.setColorAt(0.78, QColor(60, 80, 255))      # blue
        grad.setColorAt(1.0, QColor(140, 60, 255))      # violet

        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(margin, bar_top, w - 2 * margin, bar_h, 3, 3)

        # Thin border
        p.setPen(QPen(QColor(COLORS['border']), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(margin, bar_top, w - 2 * margin, bar_h, 3, 3)

        # Frequency labels
        font = QFont('sans-serif', 7)
        p.setFont(font)
        fm = p.fontMetrics()

        bar_w = w - 2 * margin

        # Place crossover markers and labels
        # Map Hz to position: we use a log scale 20 Hz .. 20 kHz
        import math
        lo_hz = 20.0
        hi_hz = 20000.0
        log_lo = math.log10(lo_hz)
        log_hi = math.log10(hi_hz)
        log_range = log_hi - log_lo

        def hz_to_x(hz):
            if hz <= lo_hz:
                return margin
            if hz >= hi_hz:
                return margin + bar_w
            return margin + bar_w * (math.log10(hz) - log_lo) / log_range

        # Bass crossover marker
        bass_x = hz_to_x(bass_fc)
        p.setPen(QPen(QColor(255, 255, 255, 180), 1, Qt.DashLine))
        p.drawLine(int(bass_x), bar_top, int(bass_x), bar_top + bar_h)

        # Treble crossover marker
        treble_x = hz_to_x(treble_fc)
        p.drawLine(int(treble_x), bar_top, int(treble_x), bar_top + bar_h)

        # Labels below the bar
        p.setPen(QColor(220, 70, 70))     # red for bass
        bass_label = f'Bass <{bass_fc} Hz'
        p.drawText(margin + 2, label_y, bass_label)

        p.setPen(QColor(70, 200, 70))     # green for mid
        mid_label = f'Mid {bass_fc}-{treble_fc} Hz'
        mid_tw = fm.horizontalAdvance(mid_label)
        mid_x = (bass_x + treble_x - mid_tw) / 2
        mid_x = max(fm.horizontalAdvance(bass_label) + margin + 10, mid_x)
        p.drawText(int(mid_x), label_y, mid_label)

        p.setPen(QColor(80, 100, 255))    # blue for treble
        treble_label = f'Treble >{treble_fc} Hz'
        treble_tw = fm.horizontalAdvance(treble_label)
        p.drawText(w - margin - treble_tw - 2, label_y, treble_label)

        p.end()

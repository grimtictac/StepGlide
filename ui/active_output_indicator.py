"""
ActiveOutputIndicator — menu-bar widget showing both audio outputs
(Speaker + Headphones) as a segmented toggle.  The active side is
highlighted; the inactive side is dimmed but still legible so the
user always knows the option exists.

This widget is purely presentational: clicking a segment EMITS
``output_changed(which)`` but does NOT mutate its own state.  The
owner (MainWindow) decides whether the change is acceptable, performs
the audio routing, and then calls ``set_output(which)`` to commit the
visual update.  This lets the owner reject (and visually revert) a
click that fails to apply — e.g. the chosen device disappeared.

Visual design:
- Segmented pill ``[🔊 Speaker | 🎧 Headphones]``
- Active half: filled blue, white text, pulsing LED (when playing)
- Inactive half: muted background, dim text, hidden LED
- Unusable half: greyed further, cursor disabled, tooltip explains why
"""

import math

from PySide6.QtCore import Qt, QTimer, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QWidget,
)


_COLOUR_SPEAKER = QColor('#3B82F6')      # blue-500
_COLOUR_HEADPHONES = QColor('#3B82F6')   # same blue — single accent colour
_COLOUR_OFFLINE = QColor('#EF4444')      # red-500


class _LedDot(QWidget):
    """A small filled circle that pulses gently while audio is playing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(QSize(18, 18))
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._colour = QColor('#FFFFFF')
        self._playing = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def set_colour(self, colour: QColor):
        self._colour = colour
        self.update()

    def set_playing(self, playing: bool):
        if playing == self._playing:
            return
        self._playing = playing
        self.update()

    def _tick(self):
        if self._playing:
            self._phase = (self._phase + 0.05 / 3.0) % 1.0
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._playing:
            t = 0.5 - 0.5 * math.cos(self._phase * 2 * math.pi)
            opacity = 0.6 + 0.4 * t
        else:
            opacity = 0.85
        c = QColor(self._colour)
        c.setAlphaF(opacity)
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, self.width(), self.height())


class _Segment(QFrame):
    """One half of the segmented toggle."""

    clicked = Signal()

    def __init__(self, icon: str, label: str, colour: QColor, parent=None):
        super().__init__(parent)
        self._colour = colour
        self._active = False
        self._usable = True
        self.setCursor(Qt.PointingHandCursor)
        self.setFrameShape(QFrame.NoFrame)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        self._led = _LedDot(self)

        self._icon_lbl = QLabel(icon)
        f = QFont(self._icon_lbl.font())
        # On Windows the inherited app font is set in pixels, so
        # pointSizeF() returns -1.  Fall back to scaling the pixel
        # size when that happens, otherwise Qt warns
        # "QFont::setPointSize: Point size <= 0 (-1)".
        if f.pointSizeF() > 0:
            f.setPointSizeF(f.pointSizeF() * 3.20)
        else:
            f.setPixelSize(max(1, int(f.pixelSize() * 3.20)))
        self._icon_lbl.setFont(f)

        self._text_lbl = QLabel(label)
        f2 = QFont(self._text_lbl.font())
        if f2.pointSizeF() > 0:
            f2.setPointSizeF(f2.pointSizeF() * 1.85)
        else:
            f2.setPixelSize(max(1, int(f2.pixelSize() * 1.85)))
        f2.setBold(True)
        self._text_lbl.setFont(f2)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(34, 12, 36, 12)
        lay.setSpacing(14)
        lay.addWidget(self._led, 0, Qt.AlignVCenter)
        lay.addWidget(self._icon_lbl, 0, Qt.AlignVCenter)
        lay.addWidget(self._text_lbl, 0, Qt.AlignVCenter)

        self._restyle()

    # ── Public API ──────────────────────────────────────

    def set_active(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self._restyle()

    def set_usable(self, usable: bool):
        if usable == self._usable:
            return
        self._usable = usable
        self.setCursor(
            Qt.PointingHandCursor if usable else Qt.ForbiddenCursor)
        self._restyle()

    def set_playing(self, playing: bool):
        # LED only meaningful while segment is active.
        self._led.set_playing(playing and self._active)

    # ── Internal ────────────────────────────────────────

    def _restyle(self):
        if self._active:
            r, g, b = self._colour.red(), self._colour.green(), self._colour.blue()
            self.setStyleSheet(f'''
                QFrame {{
                    background: rgba({r}, {g}, {b}, 230);
                    border-radius: 18px;
                }}
                QLabel {{
                    background: transparent;
                    color: white;
                }}
            ''')
            self._led.set_colour(QColor('white'))
            self._led.show()
        elif not self._usable:
            self.setStyleSheet('''
                QFrame {
                    background: rgba(255, 255, 255, 6);
                    border-radius: 18px;
                }
                QLabel {
                    background: transparent;
                    color: #4B5563;
                }
            ''')
            self._led.hide()
        else:
            self.setStyleSheet('''
                QFrame {
                    background: rgba(255, 255, 255, 12);
                    border-radius: 18px;
                }
                QFrame:hover {
                    background: rgba(255, 255, 255, 30);
                }
                QLabel {
                    background: transparent;
                    color: #9CA3AF;
                }
            ''')
            self._led.hide()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._usable:
            self.clicked.emit()
        super().mousePressEvent(event)


class ActiveOutputIndicator(QFrame):
    """Segmented Speaker | Headphones toggle for the menu-bar corner.

    Owner-driven: emits ``output_changed(which)`` on click; the owner
    must call ``set_output(which)`` to commit the visual change.
    """

    output_changed = Signal(str)   # 'speaker' | 'headphones'

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ActiveOutputIndicator')
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet('''
            QFrame#ActiveOutputIndicator {
                background: rgba(0, 0, 0, 80);
                border-radius: 20px;
                margin-right: 6px;
            }
        ''')

        self._seg_speaker = _Segment('🔊', 'Speaker', _COLOUR_SPEAKER, self)
        self._seg_headphones = _Segment(
            '🎧', 'Headphones', _COLOUR_HEADPHONES, self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        lay.addWidget(self._seg_speaker)
        lay.addWidget(self._seg_headphones)

        self._seg_speaker.clicked.connect(
            lambda: self.output_changed.emit('speaker'))
        self._seg_headphones.clicked.connect(
            lambda: self.output_changed.emit('headphones'))

        self._output = 'speaker'
        self._refresh()

    # ── Public API ──────────────────────────────────────

    def current_output(self) -> str:
        return self._output

    def set_output(self, which: str):
        """Commit the visual state to ``which``.  No-op if already there."""
        if which not in ('speaker', 'headphones'):
            return
        if which == self._output:
            return
        self._output = which
        self._refresh()

    def set_segment_usable(self, which: str, usable: bool, tooltip: str = ''):
        """Grey out a segment and disable clicks; show ``tooltip`` on hover."""
        seg = self._segment(which)
        if seg is None:
            return
        seg.set_usable(usable)
        seg.setToolTip(tooltip if not usable else '')

    def set_playing(self, playing: bool):
        """Toggle the LED pulse on the active segment."""
        self._seg_speaker.set_playing(playing)
        self._seg_headphones.set_playing(playing)

    # ── Internal ────────────────────────────────────────

    def _segment(self, which: str):
        if which == 'speaker':
            return self._seg_speaker
        if which == 'headphones':
            return self._seg_headphones
        return None

    def _refresh(self):
        self._seg_speaker.set_active(self._output == 'speaker')
        self._seg_headphones.set_active(self._output == 'headphones')

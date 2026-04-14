"""
Splash / loading screen shown during application startup.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSplashScreen, QWidget


class SplashScreen(QSplashScreen):
    """Dark themed splash screen with app name, animated status dots,
    and a progress bar."""

    WIDTH = 420
    HEIGHT = 280

    def __init__(self, *, total_steps: int = 4):
        super().__init__()
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        self._status = 'Loading…'
        self._dot_count = 0
        self._total_steps = max(total_steps, 1)
        self._current_step = 0

        # Animate the dots
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(400)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start()

    def set_status(self, text: str):
        """Update the status message and advance the progress bar."""
        self._status = text
        self._dot_count = 0
        self._current_step = min(self._current_step + 1, self._total_steps)
        self.repaint()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def set_progress(self, step: int):
        """Manually set the progress step (1-based)."""
        self._current_step = max(0, min(step, self._total_steps))
        self.repaint()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _tick_dots(self):
        self._dot_count = (self._dot_count + 1) % 4
        self.repaint()

    def drawContents(self, painter: QPainter):
        """Custom paint: dark background, app name, progress bar, status."""
        # Background
        painter.fillRect(self.rect(), QColor('#1e1e2e'))

        # Border
        painter.setPen(QColor('#444444'))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        # Music note icon
        painter.setPen(Qt.NoPen)
        icon_font = QFont('Segoe UI', 48)
        painter.setFont(icon_font)
        painter.setPen(QColor('#1f6aa5'))
        painter.drawText(self.rect().adjusted(0, 20, 0, -100),
                         Qt.AlignHCenter | Qt.AlignTop, '♫')

        # App title
        title_font = QFont('Segoe UI', 22, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor('#dce4ee'))
        painter.drawText(self.rect().adjusted(0, 110, 0, -80),
                         Qt.AlignHCenter | Qt.AlignTop, 'StepGlide')

        # Subtitle
        sub_font = QFont('Segoe UI', 10)
        painter.setFont(sub_font)
        painter.setPen(QColor('#888888'))
        painter.drawText(self.rect().adjusted(0, 150, 0, -60),
                         Qt.AlignHCenter | Qt.AlignTop, 'PySide6 Edition')

        # ── Progress bar ─────────────────────────────────
        bar_margin = 50
        bar_h = 6
        bar_y = self.height() - 55
        bar_x = bar_margin
        bar_w = self.width() - 2 * bar_margin

        # Track (background)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor('#333344'))
        painter.drawRoundedRect(bar_x, bar_y, bar_w, bar_h, 3, 3)

        # Fill
        fraction = self._current_step / self._total_steps
        fill_w = int(bar_w * fraction)
        if fill_w > 0:
            painter.setBrush(QColor('#1f6aa5'))
            painter.drawRoundedRect(bar_x, bar_y, fill_w, bar_h, 3, 3)

        # ── Status line with animated dots ───────────────
        status_font = QFont('Segoe UI', 10)
        painter.setFont(status_font)
        painter.setPen(QColor('#aaaaaa'))
        dots = '.' * self._dot_count
        painter.drawText(self.rect().adjusted(0, 0, 0, -12),
                         Qt.AlignHCenter | Qt.AlignBottom,
                         f'{self._status}{dots}')

    def finish_splash(self, main_window: QWidget):
        """Stop the animation and close."""
        self._dot_timer.stop()
        self.finish(main_window)

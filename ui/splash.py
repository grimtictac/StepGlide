"""
Splash / loading screen shown during application startup.
"""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QSplashScreen, QWidget


class SplashScreen(QSplashScreen):
    """Dark themed splash screen with app name and animated status dots."""

    WIDTH = 420
    HEIGHT = 260

    def __init__(self):
        super().__init__()
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)

        self._status = 'Loading…'
        self._dot_count = 0

        # Animate the dots
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(400)
        self._dot_timer.timeout.connect(self._tick_dots)
        self._dot_timer.start()

    def set_status(self, text: str):
        """Update the status message beneath the title."""
        self._status = text
        self._dot_count = 0
        self.repaint()
        # Process events so the repaint is visible immediately
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def _tick_dots(self):
        self._dot_count = (self._dot_count + 1) % 4
        self.repaint()

    def drawContents(self, painter: QPainter):
        """Custom paint: dark background, app name, status line."""
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
        painter.drawText(self.rect().adjusted(0, 20, 0, -80),
                         Qt.AlignHCenter | Qt.AlignTop, '♫')

        # App title
        title_font = QFont('Segoe UI', 22, QFont.Bold)
        painter.setFont(title_font)
        painter.setPen(QColor('#dce4ee'))
        painter.drawText(self.rect().adjusted(0, 110, 0, -60),
                         Qt.AlignHCenter | Qt.AlignTop, 'StepGlide')

        # Subtitle
        sub_font = QFont('Segoe UI', 10)
        painter.setFont(sub_font)
        painter.setPen(QColor('#888888'))
        painter.drawText(self.rect().adjusted(0, 150, 0, -40),
                         Qt.AlignHCenter | Qt.AlignTop, 'PySide6 Edition')

        # Status line with animated dots
        status_font = QFont('Segoe UI', 10)
        painter.setFont(status_font)
        painter.setPen(QColor('#aaaaaa'))
        dots = '.' * self._dot_count
        painter.drawText(self.rect().adjusted(0, 0, 0, -20),
                         Qt.AlignHCenter | Qt.AlignBottom,
                         f'{self._status}{dots}')

    def finish_splash(self, main_window: QWidget):
        """Stop the animation and close."""
        self._dot_timer.stop()
        self.finish(main_window)

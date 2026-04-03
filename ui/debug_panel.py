"""
Debug log panel — collapsible panel that shows timestamped log messages.
"""

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

import qtawesome as qta


_LOG_COLORS = {
    'INFO':  '#4caf50',
    'WARN':  '#ff9800',
    'ERROR': '#f44336',
    'DEBUG': '#888888',
    'PERF':  '#64b5f6',
}

_MAX_ENTRIES = 2000


class DebugPanel(QWidget):
    """In-window scrollable debug log with color-coded severity levels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []  # list of (level, formatted_line)
        self._build_ui()
        self.hide()  # hidden by default

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 4)
        layout.setSpacing(0)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(6, 4, 6, 2)

        lbl = QLabel()
        lbl.setPixmap(qta.icon('mdi6.bug', color='#888').pixmap(14, 14))
        header.addWidget(lbl)
        lbl2 = QLabel('Debug Log')
        lbl2.setStyleSheet('font-size: 11px; font-weight: bold; color: #888;')
        header.addWidget(lbl2)
        header.addStretch()

        btn_clear = QPushButton()
        btn_clear.setIcon(qta.icon('mdi6.eraser', color='#aaa'))
        btn_clear.setFixedSize(24, 22)
        btn_clear.setIconSize(btn_clear.size() * 0.65)
        btn_clear.setToolTip('Clear log')
        btn_clear.clicked.connect(self.clear)
        header.addWidget(btn_clear)

        btn_hide = QPushButton()
        btn_hide.setIcon(qta.icon('mdi6.close', color='#aaa'))
        btn_hide.setFixedSize(24, 22)
        btn_hide.setIconSize(btn_hide.size() * 0.65)
        btn_hide.setToolTip('Hide debug panel')
        btn_hide.clicked.connect(self.hide)
        header.addWidget(btn_hide)

        layout.addLayout(header)

        # Text area
        self._text = QTextEdit()
        self._text.setReadOnly(True)
        self._text.setStyleSheet(
            'background-color: #111111; color: #dce4ee; '
            'font-family: Consolas, monospace; font-size: 10px; border: none;')
        self._text.setMinimumHeight(160)
        self._text.setMaximumHeight(280)
        layout.addWidget(self._text)

    # ── Public API ───────────────────────────────────────

    def log(self, level, msg):
        """Add a timestamped log line. *level*: INFO, WARN, ERROR, DEBUG, PERF."""
        ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        line = f'[{ts}] {level:5s}  {msg}'
        self._entries.append((level, line))
        if len(self._entries) > _MAX_ENTRIES:
            self._entries = self._entries[-_MAX_ENTRIES:]

        # Append to widget only if visible
        if self.isVisible():
            self._append_line(level, line)

    def clear(self):
        """Clear all buffered entries and the text widget."""
        self._entries.clear()
        self._text.clear()

    def showEvent(self, event):
        """When made visible, replay buffered entries."""
        super().showEvent(event)
        self._text.clear()
        for level, line in self._entries:
            self._append_line(level, line)

    # ── Internals ────────────────────────────────────────

    def _append_line(self, level, line):
        fmt = QTextCharFormat()
        color = _LOG_COLORS.get(level, '#dce4ee')
        fmt.setForeground(QColor(color))
        cursor = self._text.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(line + '\n', fmt)
        self._text.setTextCursor(cursor)
        self._text.ensureCursorVisible()

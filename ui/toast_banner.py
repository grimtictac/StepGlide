"""
Toast banner — a full-width notification bar with message and action buttons.
Slides into the layout, auto-dismisses after a timeout (if set), or waits
for the user to click a button.
"""

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

from ui.theme import COLORS


class ToastBanner(QWidget):
    """Full-width banner that sits in a layout and emits signals on button clicks.

    Parameters
    ----------
    message : str
        Text shown on the left of the banner.
    buttons : list[tuple[str, str]]
        Each entry is ``(label, key)`` — a button is created with the given
        label and when clicked, ``button_clicked`` is emitted with the *key*.
    parent : QWidget | None
    auto_dismiss_ms : int
        If > 0, the banner hides itself after this many milliseconds.
        Set to 0 to require manual interaction.
    """

    button_clicked = Signal(str)   # emits the key of the button clicked

    def __init__(self, message, buttons, parent=None, *, auto_dismiss_ms=0):
        super().__init__(parent)
        self.setObjectName('ToastBanner')

        # ── Styling ──
        self.setStyleSheet(f'''
            #ToastBanner {{
                background-color: #5c2d00;
                border-top: 2px solid {COLORS['orange']};
                border-bottom: 2px solid {COLORS['orange']};
            }}
        ''')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(44)

        # ── Layout ──
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(12)

        lbl = QLabel(message)
        lbl.setStyleSheet(
            f'color: {COLORS["orange"]}; font-size: 13px; font-weight: bold;'
            f' background: transparent; border: none;')
        lay.addWidget(lbl)
        lay.addStretch()

        for label, key in buttons:
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(28)
            btn.setStyleSheet(
                f'QPushButton {{'
                f'  background-color: #5c2d00;'
                f'  color: {COLORS["fg"]};'
                f'  border: 1px solid {COLORS["orange"]};'
                f'  border-radius: 4px;'
                f'  padding: 2px 14px;'
                f'  font-size: 12px;'
                f'  font-weight: bold;'
                f'  min-height: 0px;'
                f'}}'
                f'QPushButton:hover {{'
                f'  background-color: {COLORS["yellow_hover"]};'
                f'  border-color: {COLORS["yellow"]};'
                f'  color: #000;'
                f'}}')
            btn.clicked.connect(lambda _=False, k=key: self._on_click(k))
            lay.addWidget(btn)

        # ── Auto-dismiss ──
        if auto_dismiss_ms > 0:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(auto_dismiss_ms, self.dismiss)

    # ── API ──────────────────────────────────────────────

    def _on_click(self, key):
        self.button_clicked.emit(key)
        self.dismiss()

    def dismiss(self):
        """Remove the banner from its parent layout and delete it."""
        self.hide()
        self.setParent(None)
        self.deleteLater()

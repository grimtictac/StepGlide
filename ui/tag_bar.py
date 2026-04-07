"""
Tag filter bar — row of toggle buttons for tag-based filtering.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


class TagBar(QWidget):
    """Horizontal bar of tag toggle buttons, grouped by row assignment."""

    # Emitted when active tags change — sends the current set (empty = All)
    tags_changed = Signal(set)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_tags = set()
        self._tag_rows = {}        # tag → row number
        self._active_tags = set()
        self._tag_buttons = {}     # tag → QPushButton

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(4, 2, 4, 2)
        self._layout.setSpacing(2)

        # Initially hidden until tags are populated
        self.setVisible(False)

    # ── Public API ───────────────────────────────────────

    def set_tags(self, all_tags, tag_rows=None):
        """Rebuild the tag buttons from a set of tag names.
        tag_rows is an optional dict mapping tag→row_number for layout grouping.
        """
        self._all_tags = all_tags
        self._tag_rows = tag_rows or {}
        self._active_tags = set()
        self._rebuild()

    def get_active_tags(self):
        return set(self._active_tags)

    # ── Internal ─────────────────────────────────────────

    def _rebuild(self):
        """Destroy and recreate all tag buttons."""
        # Clear existing
        while self._layout.count():
            child = self._layout.takeAt(0)
            w = child.widget()
            if w:
                w.deleteLater()
        self._tag_buttons = {}

        if not self._all_tags:
            self.setVisible(False)
            return

        self.setVisible(True)

        # Group tags by row
        rows_dict = {}  # row_num → [tag, ...]
        max_row = 0
        for tag in sorted(self._all_tags):
            r = self._tag_rows.get(tag, 99)
            rows_dict.setdefault(r, []).append(tag)
            if r != 99 and r > max_row:
                max_row = r

        # Remap row 99 to max_row + 1
        if 99 in rows_dict:
            rows_dict[max_row + 1] = rows_dict.pop(99)

        first_row = True
        for row_num in sorted(rows_dict.keys()):
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(2)

            for tag in rows_dict[row_num]:
                btn = QPushButton(tag.upper())
                btn.setCheckable(True)
                btn.setFixedHeight(20)
                btn.setMinimumWidth(40)
                btn.setStyleSheet(self._btn_style(False))
                btn.clicked.connect(lambda checked, t=tag: self._on_tag_clicked(t))
                row_layout.addWidget(btn)
                self._tag_buttons[tag] = btn

            # "ALL" button on the first row
            if first_row:
                btn_all = QPushButton('ALL')
                btn_all.setFixedHeight(20)
                btn_all.setMinimumWidth(36)
                btn_all.setStyleSheet(self._all_btn_style(True))
                btn_all.clicked.connect(self._on_all_clicked)
                row_layout.addWidget(btn_all)
                self._btn_all = btn_all
                first_row = False

            row_layout.addStretch()
            self._layout.addWidget(row_widget)

    def _on_tag_clicked(self, tag):
        if tag in self._active_tags:
            self._active_tags.discard(tag)
        else:
            self._active_tags.add(tag)
        self._update_highlights()
        self.tags_changed.emit(set(self._active_tags))

    def _on_all_clicked(self):
        self._active_tags.clear()
        self._update_highlights()
        self.tags_changed.emit(set())

    def _update_highlights(self):
        all_active = not self._active_tags
        for tag, btn in self._tag_buttons.items():
            btn.setStyleSheet(self._btn_style(tag in self._active_tags))
        if hasattr(self, '_btn_all'):
            self._btn_all.setStyleSheet(self._all_btn_style(all_active))

    @staticmethod
    def _btn_style(active):
        if active:
            return (
                'QPushButton { '
                f'  background-color: {COLORS["accent"]}; '
                f'  color: #ffffff; '
                '  border: 1px solid #555555; border-radius: 3px; '
                '  font-size: 9px; font-weight: bold; padding: 1px 4px; '
                '}'
            )
        return (
            'QPushButton { '
            '  background-color: transparent; '
            f'  color: {COLORS["fg"]}; '
            '  border: 1px solid #555555; border-radius: 3px; '
            '  font-size: 9px; font-weight: bold; padding: 1px 4px; '
            '}'
            'QPushButton:hover { background-color: #3b3b3b; }'
        )

    @staticmethod
    def _all_btn_style(all_active):
        if all_active:
            return (
                'QPushButton { '
                '  background-color: transparent; '
                f'  color: {COLORS["fg_dim"]}; '
                '  border: 1px solid #555555; border-radius: 3px; '
                '  font-size: 9px; font-weight: bold; padding: 1px 4px; '
                '}'
            )
        return (
            'QPushButton { '
            '  background-color: transparent; '
            f'  color: {COLORS["accent"]}; '
            f'  border: 1px solid {COLORS["accent"]}; border-radius: 3px; '
            '  font-size: 9px; font-weight: bold; padding: 1px 4px; '
            '}'
            'QPushButton:hover { background-color: #3b3b3b; }'
        )

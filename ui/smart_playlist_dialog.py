"""
Smart Playlist dialog — create playlists that auto-populate based on
rules (genre, rating, play count, tags, recency, etc.).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

import qtawesome as qta
from ui.theme import COLORS

# ── Rule field definitions ───────────────────────────────

RULE_FIELDS = [
    'Genre',
    'Rating',
    'Play Count',
    'Tag',
    'Last Played (days)',
    'Artist',
    'Title',
]

# Operator sets per field
FIELD_OPS = {
    'Genre':              ['is', 'is not', 'contains'],
    'Rating':             ['>=', '<=', '=', '!='],
    'Play Count':         ['>=', '<=', '=', '!='],
    'Tag':                ['has', 'has not'],
    'Last Played (days)': ['within', 'older than'],
    'Artist':             ['is', 'is not', 'contains'],
    'Title':              ['contains'],
}


class RuleRow(QWidget):
    """A single rule row: [Field] [Operator] [Value] [Remove]."""

    remove_clicked = Signal(object)  # self

    def __init__(self, genres=None, tags=None, parent=None):
        super().__init__(parent)
        self._genres = sorted(genres or [])
        self._tags = sorted(tags or [])

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        # Field combo
        self.field_combo = QComboBox()
        self.field_combo.addItems(RULE_FIELDS)
        self.field_combo.setFixedWidth(130)
        self.field_combo.currentTextChanged.connect(self._on_field_changed)
        lay.addWidget(self.field_combo)

        # Operator combo
        self.op_combo = QComboBox()
        self.op_combo.setFixedWidth(100)
        lay.addWidget(self.op_combo)

        # Value widget — swapped depending on field
        self._value_stack = QHBoxLayout()
        self._value_stack.setContentsMargins(0, 0, 0, 0)

        self.value_text = QLineEdit()
        self.value_text.setPlaceholderText('value')
        self._value_stack.addWidget(self.value_text)

        self.value_spin = QSpinBox()
        self.value_spin.setRange(-999, 999999)
        self.value_spin.setFixedWidth(80)
        self._value_stack.addWidget(self.value_spin)
        self.value_spin.hide()

        self.value_genre_combo = QComboBox()
        self.value_genre_combo.setEditable(True)
        self.value_genre_combo.addItems(self._genres)
        self._value_stack.addWidget(self.value_genre_combo)
        self.value_genre_combo.hide()

        self.value_tag_combo = QComboBox()
        self.value_tag_combo.setEditable(True)
        self.value_tag_combo.addItems(self._tags)
        self._value_stack.addWidget(self.value_tag_combo)
        self.value_tag_combo.hide()

        lay.addLayout(self._value_stack)

        # Remove button
        btn_remove = QPushButton()
        btn_remove.setIcon(qta.icon('mdi6.close', color=COLORS['red_text']))
        btn_remove.setFixedSize(28, 28)
        btn_remove.setIconSize(btn_remove.size() * 0.6)
        btn_remove.setToolTip('Remove rule')
        btn_remove.clicked.connect(lambda: self.remove_clicked.emit(self))
        lay.addWidget(btn_remove)

        # Initialise for the default field
        self._on_field_changed(self.field_combo.currentText())

    def _on_field_changed(self, field):
        """Update operators and value widget for the selected field."""
        ops = FIELD_OPS.get(field, ['='])
        self.op_combo.clear()
        self.op_combo.addItems(ops)

        # Show the right value widget
        self.value_text.hide()
        self.value_spin.hide()
        self.value_genre_combo.hide()
        self.value_tag_combo.hide()

        if field == 'Genre':
            self.value_genre_combo.show()
        elif field == 'Tag':
            self.value_tag_combo.show()
        elif field in ('Rating', 'Play Count', 'Last Played (days)'):
            self.value_spin.show()
            if field == 'Rating':
                self.value_spin.setRange(-100, 100)
                self.value_spin.setValue(0)
            elif field == 'Play Count':
                self.value_spin.setRange(0, 999999)
                self.value_spin.setValue(1)
            else:  # Last Played (days)
                self.value_spin.setRange(1, 99999)
                self.value_spin.setValue(30)
        else:
            self.value_text.show()

    def get_rule(self):
        """Return a dict describing this rule, or None if invalid."""
        field = self.field_combo.currentText()
        op = self.op_combo.currentText()

        if field == 'Genre':
            value = self.value_genre_combo.currentText().strip()
        elif field == 'Tag':
            value = self.value_tag_combo.currentText().strip()
        elif field in ('Rating', 'Play Count', 'Last Played (days)'):
            value = self.value_spin.value()
        else:
            value = self.value_text.text().strip()

        if value == '' or value is None:
            return None

        return {'field': field, 'op': op, 'value': value}

    def set_rule(self, rule):
        """Populate from a rule dict."""
        self.field_combo.setCurrentText(rule.get('field', 'Genre'))
        self._on_field_changed(self.field_combo.currentText())
        self.op_combo.setCurrentText(rule.get('op', '='))

        field = rule.get('field', '')
        value = rule.get('value', '')

        if field == 'Genre':
            self.value_genre_combo.setCurrentText(str(value))
        elif field == 'Tag':
            self.value_tag_combo.setCurrentText(str(value))
        elif field in ('Rating', 'Play Count', 'Last Played (days)'):
            self.value_spin.setValue(int(value))
        else:
            self.value_text.setText(str(value))


class SmartPlaylistDialog(QDialog):
    """Dialog for creating / editing a smart playlist."""

    def __init__(self, parent=None, *, genres=None, tags=None,
                 name='', rules=None, match_mode='all'):
        super().__init__(parent)
        self.setWindowTitle('Smart Playlist')
        self.setMinimumWidth(560)
        self._genres = genres or []
        self._tags = tags or []
        self._rule_rows = []

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Name
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel('Name:'))
        self._name_edit = QLineEdit(name)
        self._name_edit.setPlaceholderText('My Smart Playlist')
        name_row.addWidget(self._name_edit)
        layout.addLayout(name_row)

        # Match mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel('Match'))
        self._match_combo = QComboBox()
        self._match_combo.addItems(['all', 'any'])
        self._match_combo.setCurrentText(match_mode)
        self._match_combo.setFixedWidth(60)
        mode_row.addWidget(self._match_combo)
        mode_row.addWidget(QLabel('of the following rules:'))
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # Rules area
        self._rules_layout = QVBoxLayout()
        self._rules_layout.setSpacing(4)
        layout.addLayout(self._rules_layout)

        # Add rule button
        btn_add = QPushButton('+ Add Rule')
        btn_add.setFixedWidth(120)
        btn_add.clicked.connect(self._add_rule_row)
        layout.addWidget(btn_add)

        layout.addStretch()

        # Dialog buttons
        btn_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # Populate initial rules
        if rules:
            for rule in rules:
                row = self._add_rule_row()
                row.set_rule(rule)
        else:
            self._add_rule_row()  # start with one empty rule

    def _add_rule_row(self):
        row = RuleRow(genres=self._genres, tags=self._tags, parent=self)
        row.remove_clicked.connect(self._remove_rule_row)
        self._rules_layout.addWidget(row)
        self._rule_rows.append(row)
        return row

    def _remove_rule_row(self, row):
        if len(self._rule_rows) <= 1:
            return  # keep at least one rule
        self._rules_layout.removeWidget(row)
        self._rule_rows.remove(row)
        row.deleteLater()

    def _accept(self):
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, 'Missing Name',
                                'Please enter a name for the smart playlist.')
            return
        rules = []
        for row in self._rule_rows:
            r = row.get_rule()
            if r is not None:
                rules.append(r)
        if not rules:
            QMessageBox.warning(self, 'No Rules',
                                'Please add at least one valid rule.')
            return
        self.result_name = name
        self.result_rules = rules
        self.result_match = self._match_combo.currentText()
        self.accept()

    def get_result(self):
        """Return (name, rules_list, match_mode) after accept."""
        return self.result_name, self.result_rules, self.result_match

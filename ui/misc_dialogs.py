"""
Miscellaneous dialogs — Random Queue Generator and Audit Log Viewer.
"""

import random as _random
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


# ═════════════════════════════════════════════════════════
#  Random Queue Generator
# ═════════════════════════════════════════════════════════

_WEIGHT_LABELS = ['—', 'Low', 'Med', 'High', 'Max']

_RECENCY_OPTS = [
    'No filter', '1 day', '3 days', '1 week', '2 weeks', '1 month', 'Never played',
]
_RECENCY_DAYS = {
    '1 day': 1, '3 days': 3, '1 week': 7, '2 weeks': 14, '1 month': 30,
}


class RandomQueueDialog(QDialog):
    """Configure and generate a weighted random play queue."""

    def __init__(self, parent, *, playlist, genres, all_tags):
        super().__init__(parent)
        self.setWindowTitle('Random Queue Generator')
        self.resize(560, 620)
        self.setModal(True)

        self._playlist = playlist
        self._genres = sorted(genres)
        self._all_tags = sorted(all_tags)
        self.result_indices = []  # populated on Generate

        # Pre-compute genre counts
        self._genre_counts = {}
        for e in playlist:
            g = e.get('genre', 'Unknown')
            self._genre_counts[g] = self._genre_counts.get(g, 0) + 1

        self._build_ui()

    # ── UI ───────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(6)

        root.addWidget(_heading('Random Queue Generator'))
        root.addWidget(_subtext('Configure genre proportions, rating, and recency filters.'))

        # Queue size
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel('Queue size:'))
        self._spin_size = QSpinBox()
        self._spin_size.setRange(5, 500)
        self._spin_size.setValue(50)
        self._spin_size.setFixedWidth(70)
        size_row.addWidget(self._spin_size)
        size_row.addStretch()
        root.addLayout(size_row)

        # Rating filter
        rat_row = QHBoxLayout()
        rat_row.addWidget(QLabel('Min rating:'))
        self._combo_rating = QComboBox()
        self._combo_rating.addItems(['Any', '+1', '+2', '+3', '+4', '+5'])
        self._combo_rating.setCurrentText('+3')
        self._combo_rating.setFixedWidth(80)
        rat_row.addWidget(self._combo_rating)
        rat_row.addStretch()
        root.addLayout(rat_row)

        # Recency filter
        rec_row = QHBoxLayout()
        rec_row.addWidget(QLabel('Not played in last:'))
        self._combo_recency = QComboBox()
        self._combo_recency.addItems(_RECENCY_OPTS)
        self._combo_recency.setFixedWidth(140)
        rec_row.addWidget(self._combo_recency)
        rec_row.addStretch()
        root.addLayout(rec_row)

        # Tag filter
        if self._all_tags:
            root.addWidget(QLabel('Tags (must have ALL selected):'))
            tag_row = QHBoxLayout()
            tag_row.setSpacing(2)
            self._tag_checks = {}
            for tag in self._all_tags:
                cb = QCheckBox(tag.upper())
                cb.setStyleSheet('font-size: 10px;')
                tag_row.addWidget(cb)
                self._tag_checks[tag] = cb
            tag_row.addStretch()
            root.addLayout(tag_row)
        else:
            self._tag_checks = {}

        # Genre proportions
        root.addWidget(QLabel('Genre Proportions:'))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        genre_widget = QWidget()
        genre_layout = QVBoxLayout(genre_widget)
        genre_layout.setAlignment(Qt.AlignTop)
        genre_layout.setSpacing(2)

        self._genre_sliders = {}
        for genre in self._genres:
            row = QHBoxLayout()
            row.setSpacing(4)
            lbl = QLabel(genre)
            lbl.setFixedWidth(140)
            row.addWidget(lbl)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 4)
            slider.setValue(0)
            slider.setFixedWidth(120)
            row.addWidget(slider)

            val_lbl = QLabel('—')
            val_lbl.setFixedWidth(36)
            val_lbl.setStyleSheet('font-size: 10px; color: #aaa;')
            slider.valueChanged.connect(
                lambda v, vl=val_lbl: vl.setText(_WEIGHT_LABELS[v]))
            row.addWidget(val_lbl)

            count = self._genre_counts.get(genre, 0)
            cnt_lbl = QLabel(f'({count})')
            cnt_lbl.setStyleSheet('font-size: 10px; color: #666;')
            row.addWidget(cnt_lbl)

            row.addStretch()
            genre_layout.addLayout(row)
            self._genre_sliders[genre] = slider

        scroll.setWidget(genre_widget)
        root.addWidget(scroll, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setStyleSheet('padding: 6px 14px;')
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)

        btn_gen = QPushButton('Generate Queue')
        btn_gen.setStyleSheet(
            f'background-color: {COLORS["accent"]}; color: white; padding: 6px 14px;')
        btn_gen.clicked.connect(self._generate)
        btn_row.addWidget(btn_gen)
        root.addLayout(btn_row)

    # ── Generate ─────────────────────────────────────────

    def _generate(self):
        size = self._spin_size.value()

        # Rating filter
        rv = self._combo_rating.currentText()
        min_rat = 0 if rv == 'Any' else int(rv.replace('+', ''))

        # Recency filter
        recency = self._combo_recency.currentText()
        cutoff = None
        if recency == 'Never played':
            cutoff = 'never'
        elif recency != 'No filter':
            days = _RECENCY_DAYS.get(recency, 0)
            if days:
                cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

        # Tag filter
        selected_tags = {t for t, cb in self._tag_checks.items() if cb.isChecked()}

        # Genre weights
        weights = {}
        for genre, slider in self._genre_sliders.items():
            w = slider.value()
            if w > 0:
                weights[genre] = w

        if not weights:
            QMessageBox.information(self, 'Random Queue',
                                    'Set at least one genre weight above "—".')
            return

        # Collect eligible tracks per genre
        eligible_by_genre = {}
        for idx, entry in enumerate(self._playlist):
            g = entry.get('genre', 'Unknown')
            if g not in weights:
                continue
            if entry.get('rating', 0) < min_rat:
                continue
            if cutoff == 'never':
                if entry.get('last_played'):
                    continue
            elif cutoff:
                lp = entry.get('last_played')
                if lp and lp > cutoff:
                    continue
            if selected_tags:
                track_tags = set(entry.get('tags', []))
                if not selected_tags.issubset(track_tags):
                    continue
            eligible_by_genre.setdefault(g, []).append(idx)

        if not eligible_by_genre:
            QMessageBox.information(self, 'Random Queue',
                                    'No tracks match the criteria.')
            return

        # Weighted random selection
        genre_list = list(eligible_by_genre.keys())
        weight_list = [weights.get(g, 1) for g in genre_list]

        queue = []
        queue_set = set()
        for _ in range(size):
            if not genre_list:
                break
            chosen = _random.choices(genre_list, weights=weight_list, k=1)[0]
            pool = eligible_by_genre.get(chosen, [])
            available = [t for t in pool if t not in queue_set]
            if not available:
                gi = genre_list.index(chosen)
                genre_list.pop(gi)
                weight_list.pop(gi)
                continue
            pick = _random.choice(available)
            queue.append(pick)
            queue_set.add(pick)

        self.result_indices = queue
        self.accept()


# ═════════════════════════════════════════════════════════
#  Audit Log Viewer
# ═════════════════════════════════════════════════════════

class AuditLogDialog(QDialog):
    """Display recent entries from the audit_log table."""

    def __init__(self, parent, *, db):
        super().__init__(parent)
        self.setWindowTitle('Audit Log')
        self.resize(720, 500)
        self.setModal(True)

        rows = db.get_audit_log(500)

        layout = QVBoxLayout(self)
        layout.addWidget(_heading('Audit Log — Recent Actions'))

        table = QTableWidget(len(rows), 3)
        table.setHorizontalHeaderLabels(['Time', 'Action', 'Detail'])
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        for r, (ts, action, detail) in enumerate(rows):
            try:
                dt = datetime.fromisoformat(ts).astimezone(tz=None)
                display_ts = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                display_ts = str(ts)[:19] if ts else ''
            table.setItem(r, 0, QTableWidgetItem(display_ts))
            table.setItem(r, 1, QTableWidgetItem(action or ''))
            table.setItem(r, 2, QTableWidgetItem(detail or ''))

        layout.addWidget(table, 1)

        btn_close = QPushButton('Close')
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, 0, Qt.AlignRight)


# ── Helpers ──────────────────────────────────────────────

def _heading(text):
    lbl = QLabel(text)
    lbl.setStyleSheet('font-size: 14px; font-weight: bold; padding: 4px 0;')
    return lbl

def _subtext(text):
    lbl = QLabel(text)
    lbl.setStyleSheet('font-size: 11px; color: #888888;')
    return lbl

"""
Search / filter bar — search box + filter dropdowns for the track table.
"""

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from ui.theme import COLORS


# Field-prefix mapping for field-specific search (e.g. "artist:beatles")
_SEARCH_FIELD_PREFIXES = {
    'title:':   lambda e: (e.get('title') or e.get('basename', '')).lower(),
    'artist:':  lambda e: (e.get('artist') or '').lower(),
    'album:':   lambda e: (e.get('album') or '').lower(),
    'genre:':   lambda e: (e.get('genre') or '').lower(),
    'comment:': lambda e: (e.get('comment') or '').lower(),
    'tags:':    lambda e: ' '.join(e.get('tags', [])).lower(),
    'liked:':   lambda e: ' '.join(e.get('liked_by', set())).lower(),
}


def parse_search_tokens(raw):
    """Parse search string into [(field_fn_or_None, term), ...].

    Supports plain words (AND logic), field prefixes (artist:beatles),
    and quoted phrases ("abbey road").
    """
    tokens = []
    i = 0
    while i < len(raw):
        if raw[i] == ' ':
            i += 1
            continue
        field_fn = None
        for prefix, fn in _SEARCH_FIELD_PREFIXES.items():
            if raw[i:].startswith(prefix):
                field_fn = fn
                i += len(prefix)
                break
        if i < len(raw) and raw[i] == '"':
            end = raw.find('"', i + 1)
            if end == -1:
                end = len(raw)
            term = raw[i + 1:end].strip().lower()
            i = end + 1
        else:
            end = raw.find(' ', i)
            if end == -1:
                end = len(raw)
            term = raw[i:end].lower()
            i = end
        if term:
            tokens.append((field_fn, term))
    return tokens


class SearchFilterBar(QWidget):
    """Horizontal bar: [🔍 search] [Rating ▾] [Liked By ▾] [First Played ▾]
    [Last Played ▾] [File Created ▾] [Length ▾] [Reset]"""

    search_changed = Signal(list)         # list of (field_fn, term) tokens
    rating_changed = Signal(object)       # (op, val) or None
    liked_by_changed = Signal(object)     # voter name or None
    first_played_changed = Signal(str)    # 'All', 'Today', 'This Week', 'This Month'
    last_played_changed = Signal(str)
    file_created_changed = Signal(str)
    length_changed = Signal(str, object, object)  # label, lo, hi
    filters_reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(200)
        self._debounce_timer.timeout.connect(self._emit_search)
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(6)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText('Search… (artist:name, "quoted phrase")')
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumWidth(200)
        self._search.textChanged.connect(self._on_search_text)
        layout.addWidget(self._search, stretch=1)

        # Rating filter
        self._rating_cb = self._add_combo(layout, 'Rating', [
            'All', '≥ +1', '≥ +2', '≥ +3', '= 0', '≤ -1', '≤ -2',
        ])
        self._rating_cb.currentTextChanged.connect(self._on_rating)

        # Liked By filter
        self._liked_by_cb = self._add_combo(layout, 'Liked By', ['All'])
        self._liked_by_cb.currentTextChanged.connect(self._on_liked_by)

        # Date filters
        date_opts = ['All', 'Today', 'This Week', 'This Month']
        self._first_played_cb = self._add_combo(layout, 'First Played', date_opts)
        self._first_played_cb.currentTextChanged.connect(
            lambda v: self.first_played_changed.emit(v))

        self._last_played_cb = self._add_combo(layout, 'Last Played', date_opts)
        self._last_played_cb.currentTextChanged.connect(
            lambda v: self.last_played_changed.emit(v))

        self._file_created_cb = self._add_combo(layout, 'File Created', date_opts)
        self._file_created_cb.currentTextChanged.connect(
            lambda v: self.file_created_changed.emit(v))

        # Length filter
        self._length_cb = self._add_combo(layout, 'Length', ['All'])
        self._length_cb.currentTextChanged.connect(self._on_length)

        # Reset button
        btn_reset = QPushButton('Reset')
        btn_reset.setFixedHeight(26)
        btn_reset.setToolTip('Reset all filters')
        btn_reset.clicked.connect(self._reset_all)
        layout.addWidget(btn_reset)

    @staticmethod
    def _add_combo(layout, label, items):
        """Create a label-above-combo pair and add to the parent layout."""
        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-size: 9px;')
        col.addWidget(lbl)
        cb = QComboBox()
        cb.addItems(items)
        cb.setFixedHeight(22)
        cb.setMinimumWidth(55)
        col.addWidget(cb)
        layout.addLayout(col)
        return cb

    # ── Slots ────────────────────────────────────────────

    def _on_search_text(self, text):
        self._debounce_timer.start()

    def _emit_search(self):
        tokens = parse_search_tokens(self._search.text())
        self.search_changed.emit(tokens)

    def _on_rating(self, text):
        if text == 'All':
            self.rating_changed.emit(None)
        elif text.startswith('≥'):
            val = int(text.split('+')[1])
            self.rating_changed.emit(('>=', val))
        elif text.startswith('≤'):
            val = -int(text.split('-')[1])
            self.rating_changed.emit(('<=', val))
        elif text.startswith('='):
            self.rating_changed.emit(('=', 0))

    def _on_liked_by(self, text):
        self.liked_by_changed.emit(None if text == 'All' else text)

    def _on_length(self, text):
        # Length options are set dynamically; parse "X–Y min" style labels
        if text == 'All':
            self.length_changed.emit('All', None, None)
            return
        self.length_changed.emit(text, None, None)  # main window resolves

    def _reset_all(self):
        self._search.clear()
        self._rating_cb.setCurrentIndex(0)
        self._liked_by_cb.setCurrentIndex(0)
        self._first_played_cb.setCurrentIndex(0)
        self._last_played_cb.setCurrentIndex(0)
        self._file_created_cb.setCurrentIndex(0)
        self._length_cb.setCurrentIndex(0)
        self.filters_reset.emit()

    # ── Public API ───────────────────────────────────────

    def focus_search(self):
        """Focus and select all text in the search box."""
        self._search.setFocus()
        self._search.selectAll()

    def set_voters(self, voters):
        """Populate the Liked By dropdown with voter names."""
        self._liked_by_cb.blockSignals(True)
        self._liked_by_cb.clear()
        self._liked_by_cb.addItem('All')
        for v in sorted(voters):
            self._liked_by_cb.addItem(v)
        self._liked_by_cb.blockSignals(False)

    def set_length_options(self, options):
        """Set length filter dropdown options. options is a list of label strings."""
        self._length_cb.blockSignals(True)
        self._length_cb.clear()
        self._length_cb.addItem('All')
        for opt in options:
            self._length_cb.addItem(opt)
        self._length_cb.blockSignals(False)

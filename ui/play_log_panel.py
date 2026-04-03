"""
Play log panel — shows recent play history grouped by date.
"""

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


class PlayLogPanel(QWidget):
    """Play history panel with date-grouped tree and context actions."""

    # Emitted when user wants to play a track — sends playlist index
    play_requested = Signal(int)
    # Emitted when user wants to add a track to queue — sends playlist index
    add_to_queue_requested = Signal(int)
    # Emitted when user wants to jump to a track in the table — sends playlist index
    jump_to_track = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path_to_idx = {}   # set via set_path_map
        self._log_entries = []   # raw rows from DB

        self._init_ui()

    # ── UI ───────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Header
        header = QHBoxLayout()
        self._title_lbl = QLabel('Play Log')
        self._title_lbl.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; font-weight: bold; font-size: 12px;')
        header.addWidget(self._title_lbl)
        header.addStretch()

        btn_refresh = QPushButton('⟳')
        btn_refresh.setFixedSize(28, 24)
        btn_refresh.setToolTip('Refresh play log')
        btn_refresh.clicked.connect(self._request_refresh)
        header.addWidget(btn_refresh)
        layout.addLayout(header)

        # Tree widget (date-grouped)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['Time', 'Title', 'Genre'])
        self._tree.setRootIsDecorated(True)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_right_click)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree, stretch=1)

    # ── Public API ───────────────────────────────────────

    def set_path_map(self, path_to_idx):
        """Provide the path→playlist_index lookup dict."""
        self._path_to_idx = path_to_idx

    def load(self, db):
        """Load play log from the database and rebuild the tree."""
        self._log_entries = db.get_play_log(limit=500)
        self._rebuild()

    # Keep a ref so refresh can call db again
    _db_ref = None

    def set_db(self, db):
        self._db_ref = db

    def refresh(self):
        """Reload from DB if reference is available."""
        if self._db_ref:
            self.load(self._db_ref)

    def _request_refresh(self):
        self.refresh()

    # ── Rebuild tree ─────────────────────────────────────

    def _rebuild(self):
        self._tree.clear()

        # Group by date
        date_nodes = {}   # date_str → QTreeWidgetItem
        for track_id, file_path, title, genre, played_at in self._log_entries:
            try:
                dt = datetime.fromisoformat(played_at)
                date_str = dt.strftime('%Y-%m-%d')
                time_str = dt.strftime('%H:%M')
            except Exception:
                date_str = str(played_at)[:10]
                time_str = ''

            if date_str not in date_nodes:
                parent = QTreeWidgetItem([f'▸ {date_str}', '', ''])
                parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
                parent.setExpanded(len(date_nodes) == 0)  # expand first date
                self._tree.addTopLevelItem(parent)
                date_nodes[date_str] = parent

            child = QTreeWidgetItem([time_str, title or '?', genre or ''])
            child.setData(0, Qt.UserRole, file_path)
            child.setData(1, Qt.UserRole, title or '?')
            date_nodes[date_str].addChild(child)

        self._title_lbl.setText(f'Play Log ({len(self._log_entries)})')

    # ── Context menu ─────────────────────────────────────

    def _on_right_click(self, pos):
        item = self._tree.itemAt(pos)
        if item is None or item.parent() is None:
            return  # clicked on a date header or empty
        file_path = item.data(0, Qt.UserRole)
        title = item.data(1, Qt.UserRole)
        pl_idx = self._path_to_idx.get(file_path)
        if pl_idx is None:
            return

        self._tree.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(f'🎵  {title[:40]}').setEnabled(False)
        menu.addSeparator()
        menu.addAction('▶  Play Now', lambda: self.play_requested.emit(pl_idx))
        menu.addAction('📋  Add to Queue',
                       lambda: self.add_to_queue_requested.emit(pl_idx))
        menu.addAction('⎆  Jump to Track',
                       lambda: self.jump_to_track.emit(pl_idx))
        menu.exec(self._tree.mapToGlobal(pos))

    def _on_double_click(self, item, _col):
        if item.parent() is None:
            return  # date header
        file_path = item.data(0, Qt.UserRole)
        pl_idx = self._path_to_idx.get(file_path)
        if pl_idx is not None:
            self.jump_to_track.emit(pl_idx)

"""
Play log panel — shows recent play history grouped by date.
"""

from datetime import datetime, date as date_cls

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QMenu,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
import qtawesome as qta

from ui.theme import COLORS


class PlayLogPanel(QWidget):
    """Play history panel with date-grouped tree and context actions."""

    # Emitted when user wants to play a track — sends playlist index
    play_requested = Signal(int)
    # Emitted when user wants to add a track to queue — sends playlist index
    add_to_queue_requested = Signal(int)
    # Emitted when user wants to jump to a track in the table — sends playlist index
    jump_to_track = Signal(int)
    # Emitted when user votes on a selected track — (file_path, vote, voter)
    vote_requested = Signal(str, int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path_to_idx = {}   # set via set_path_map
        self._log_entries = []   # raw rows from DB
        self._playlist = []      # set via set_playlist

        self._init_ui()

    # ── UI ───────────────────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 4)
        layout.setSpacing(4)

        # Voting strip: [voter combo] [thumbs-down] [thumbs-up]
        vote_row = QHBoxLayout()
        vote_row.setContentsMargins(0, 0, 0, 6)
        vote_row.setSpacing(4)

        _vote_btn_css = (
            'QPushButton { background: transparent;'
            '  border: 1px solid #555; border-radius: 3px; }'
            'QPushButton:hover { background-color: #444; }')

        self._voter_combo = QComboBox()
        self._voter_combo.setEditable(True)
        self._voter_combo.setInsertPolicy(QComboBox.NoInsert)
        self._voter_combo.setToolTip('Voter name (type or pick)')
        self._voter_combo.lineEdit().setPlaceholderText('anonymous')
        self._voter_combo.setStyleSheet(
            'QComboBox { padding: 2px 4px; }')
        vote_row.addWidget(self._voter_combo, stretch=1)

        btn_dislike = QPushButton()
        btn_dislike.setIcon(qta.icon('mdi6.thumb-down', color=COLORS['yellow']))
        btn_dislike.setFixedSize(28, 24)
        btn_dislike.setIconSize(btn_dislike.size() * 0.6)
        btn_dislike.setToolTip('Dislike selected track')
        btn_dislike.setStyleSheet(_vote_btn_css)
        btn_dislike.clicked.connect(lambda: self._do_vote(-1))
        vote_row.addWidget(btn_dislike)

        btn_like = QPushButton()
        btn_like.setIcon(qta.icon('mdi6.thumb-up', color=COLORS['yellow']))
        btn_like.setFixedSize(28, 24)
        btn_like.setIconSize(btn_like.size() * 0.6)
        btn_like.setToolTip('Like selected track')
        btn_like.setStyleSheet(_vote_btn_css)
        btn_like.clicked.connect(lambda: self._do_vote(+1))
        vote_row.addWidget(btn_like)

        layout.addLayout(vote_row)

        # Tree widget (date-grouped)
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(['Genre', 'Title'])
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(12)
        self._tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_right_click)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.currentItemChanged.connect(self._on_selection_changed)
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

    def select_track(self, file_path):
        """Select the first (most recent) entry matching file_path."""
        for i in range(self._tree.topLevelItemCount()):
            parent = self._tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, Qt.UserRole) == file_path:
                    self._tree.setCurrentItem(child)
                    self._tree.scrollToItem(child)
                    return

    # ── Rebuild tree ─────────────────────────────────────

    def _rebuild(self):
        self._tree.clear()

        today_str = date_cls.today().strftime('%Y-%m-%d')

        # Group by date
        date_nodes = {}   # date_str → QTreeWidgetItem
        for track_id, file_path, title, genre, played_at in self._log_entries:
            try:
                dt = datetime.fromisoformat(played_at)
                date_str = dt.strftime('%Y-%m-%d')
            except Exception:
                date_str = str(played_at)[:10]

            if date_str not in date_nodes:
                parent = QTreeWidgetItem([date_str, ''])
                parent.setFlags(parent.flags() & ~Qt.ItemIsSelectable)
                parent.setExpanded(date_str == today_str)
                self._tree.addTopLevelItem(parent)
                date_nodes[date_str] = parent

            child = QTreeWidgetItem([genre or '', title or '?'])
            child.setData(0, Qt.UserRole, file_path)
            child.setData(1, Qt.UserRole, title or '?')
            date_nodes[date_str].addChild(child)

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

    # ── Voting ───────────────────────────────────────────

    def set_playlist(self, playlist):
        """Provide the playlist list so we can look up ratings."""
        self._playlist = playlist

    def set_voters(self, voters):
        """Populate the voter dropdown with known voter names."""
        current = self._voter_combo.currentText()
        self._voter_combo.blockSignals(True)
        self._voter_combo.clear()
        self._voter_combo.addItem('')  # anonymous
        for name in sorted(voters):
            self._voter_combo.addItem(name)
        idx = self._voter_combo.findText(current)
        if idx >= 0:
            self._voter_combo.setCurrentIndex(idx)
        self._voter_combo.blockSignals(False)

    def voter_name(self):
        """Return the current voter name text."""
        return self._voter_combo.currentText().strip()

    def _selected_file_path(self):
        """Return the file_path of the currently selected play-log entry, or None."""
        item = self._tree.currentItem()
        if item is None or item.parent() is None:
            return None
        return item.data(0, Qt.UserRole)

    def _do_vote(self, vote):
        """Emit vote_requested for the selected play-log entry."""
        file_path = self._selected_file_path()
        if file_path is None:
            return
        voter = self._voter_combo.currentText().strip()
        self.vote_requested.emit(file_path, vote, voter)

    def _on_selection_changed(self, current, _previous):
        """Placeholder for future selection-change handling."""
        pass

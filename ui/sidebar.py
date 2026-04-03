"""
Left sidebar — Genre list + Playlist list with CRUD.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme import COLORS


class SidebarWidget(QWidget):
    """Left sidebar containing a genre list and a playlist list."""

    # ── Signals ──────────────────────────────────────────
    genre_selected = Signal(object)      # set of genres or None (All)
    playlist_selected = Signal(object)   # set of paths or None (All Tracks)

    playlist_changed = Signal()          # emitted after any playlist CRUD

    def __init__(self, parent=None):
        super().__init__(parent)
        self._genre_groups = {}  # group_name → [genre, ...]
        self._all_genres = set()
        self._genre_label_map = {}  # display label → ('all'|'group'|'genre', name)

        self._playlists = {}  # name → [path, ...]
        self._active_playlist = None

        self._init_ui()

    # ── UI construction ──────────────────────────────────

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── Genre section ────────────────────────────────
        genre_header = QLabel('Genres')
        genre_header.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; font-weight: bold; font-size: 12px;')
        layout.addWidget(genre_header)

        self._genre_list = QListWidget()
        self._genre_list.setSelectionMode(QListWidget.SingleSelection)
        self._genre_list.currentItemChanged.connect(self._on_genre_item_changed)
        layout.addWidget(self._genre_list, stretch=1)

        # ── Playlist section ─────────────────────────────
        pl_header_row = QHBoxLayout()
        pl_label = QLabel('Playlists')
        pl_label.setStyleSheet(
            f'color: {COLORS["fg_dim"]}; font-weight: bold; font-size: 12px;')
        pl_header_row.addWidget(pl_label)
        pl_header_row.addStretch()

        btn_new_pl = QPushButton('+')
        btn_new_pl.setFixedSize(24, 24)
        btn_new_pl.setToolTip('New playlist')
        btn_new_pl.clicked.connect(self._create_playlist)
        pl_header_row.addWidget(btn_new_pl)

        layout.addLayout(pl_header_row)

        self._playlist_list = QListWidget()
        self._playlist_list.setSelectionMode(QListWidget.SingleSelection)
        self._playlist_list.currentItemChanged.connect(self._on_playlist_item_changed)
        self._playlist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._playlist_list.customContextMenuRequested.connect(
            self._on_playlist_right_click)
        layout.addWidget(self._playlist_list, stretch=1)

    # ── Public API ───────────────────────────────────────

    def set_genre_data(self, all_genres, genre_groups):
        """Rebuild the genre list from a set of all genres and a groups dict."""
        self._all_genres = all_genres
        self._genre_groups = genre_groups
        self._build_genre_list()

    def set_playlist_data(self, playlists):
        """Set the playlists dict (name → [path, ...]) and rebuild."""
        self._playlists = playlists
        self._refresh_playlist_list()

    def get_active_playlist_name(self):
        return self._active_playlist

    # ── Genre list ───────────────────────────────────────

    def _build_genre_list(self):
        """Populate the genre QListWidget with groups + ungrouped."""
        self._genre_list.blockSignals(True)
        self._genre_list.clear()
        self._genre_label_map = {}

        # "All" entry
        item = QListWidgetItem('All')
        item.setData(Qt.UserRole, ('all', 'All'))
        self._genre_list.addItem(item)

        grouped_genres = set()
        for gname, members in sorted(self._genre_groups.items()):
            # Group header
            group_item = QListWidgetItem(f'▸ {gname}')
            group_item.setData(Qt.UserRole, ('group', gname))
            group_item.setForeground(
                Qt.GlobalColor.cyan if COLORS else Qt.GlobalColor.white)
            self._genre_list.addItem(group_item)

            for genre in sorted(members):
                sub = QListWidgetItem(f'    {genre}')
                sub.setData(Qt.UserRole, ('genre', genre))
                self._genre_list.addItem(sub)
                grouped_genres.add(genre)

        # Ungrouped genres
        ungrouped = sorted(g for g in self._all_genres if g and g not in grouped_genres)
        for genre in ungrouped:
            gi = QListWidgetItem(genre)
            gi.setData(Qt.UserRole, ('genre', genre))
            self._genre_list.addItem(gi)

        # Select "All" by default
        self._genre_list.setCurrentRow(0)
        self._genre_list.blockSignals(False)

    def _on_genre_item_changed(self, current, _previous):
        if current is None:
            return
        kind, name = current.data(Qt.UserRole)
        if kind == 'all':
            self.genre_selected.emit(None)
        elif kind == 'group':
            members = self._genre_groups.get(name, [])
            self.genre_selected.emit(set(members))
        else:
            self.genre_selected.emit({name})

    # ── Playlist list ────────────────────────────────────

    def _refresh_playlist_list(self):
        self._playlist_list.blockSignals(True)
        self._playlist_list.clear()

        # "All Tracks" entry
        all_item = QListWidgetItem('\u266b  All Tracks')
        all_item.setData(Qt.UserRole, None)
        self._playlist_list.addItem(all_item)

        for name in sorted(self._playlists.keys()):
            count = len(self._playlists[name])
            pl_item = QListWidgetItem(f'{name}  ({count})')
            pl_item.setData(Qt.UserRole, name)
            self._playlist_list.addItem(pl_item)

        # Restore selection
        if self._active_playlist is None:
            self._playlist_list.setCurrentRow(0)
        else:
            for i in range(self._playlist_list.count()):
                item = self._playlist_list.item(i)
                if item.data(Qt.UserRole) == self._active_playlist:
                    self._playlist_list.setCurrentRow(i)
                    break
            else:
                self._playlist_list.setCurrentRow(0)
                self._active_playlist = None

        self._playlist_list.blockSignals(False)

    def _on_playlist_item_changed(self, current, _previous):
        if current is None:
            return
        name = current.data(Qt.UserRole)
        self._active_playlist = name
        if name is None:
            self.playlist_selected.emit(None)
        else:
            paths = self._playlists.get(name, [])
            self.playlist_selected.emit(set(paths))

    def _on_playlist_right_click(self, pos):
        item = self._playlist_list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.UserRole)
        menu = QMenu(self)
        if name is None:
            # "All Tracks" — limited menu
            menu.addAction('New Playlist\u2026', self._create_playlist)
        else:
            menu.addAction('Rename\u2026', lambda: self._rename_playlist(name))
            menu.addAction('Duplicate\u2026', lambda: self._duplicate_playlist(name))
            menu.addAction('Delete', lambda: self._delete_playlist(name))
            menu.addSeparator()
            menu.addAction('New Playlist\u2026', self._create_playlist)
        menu.exec(self._playlist_list.mapToGlobal(pos))

    # ── Playlist CRUD ────────────────────────────────────

    def _create_playlist(self):
        name, ok = QInputDialog.getText(self, 'New Playlist', 'Playlist name:')
        if ok and name.strip():
            name = name.strip()
            if name not in self._playlists:
                self._playlists[name] = []
                self._refresh_playlist_list()
                self.playlist_changed.emit()

    def _rename_playlist(self, old_name):
        new_name, ok = QInputDialog.getText(
            self, 'Rename Playlist', 'New name:', text=old_name)
        if ok and new_name.strip() and new_name.strip() != old_name:
            self._playlists[new_name.strip()] = self._playlists.pop(old_name)
            if self._active_playlist == old_name:
                self._active_playlist = new_name.strip()
            self._refresh_playlist_list()
            self.playlist_changed.emit()

    def _delete_playlist(self, name):
        reply = QMessageBox.question(
            self, 'Delete Playlist', f'Delete playlist "{name}"?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._playlists.pop(name, None)
            if self._active_playlist == name:
                self._active_playlist = None
                self.playlist_selected.emit(None)
            self._refresh_playlist_list()
            self.playlist_changed.emit()

    def _duplicate_playlist(self, name):
        new_name, ok = QInputDialog.getText(
            self, 'Duplicate Playlist', 'Name for copy:',
            text=f'{name} (copy)')
        if ok and new_name.strip():
            new_name = new_name.strip()
            if new_name not in self._playlists:
                self._playlists[new_name] = list(self._playlists.get(name, []))
                self._refresh_playlist_list()
                self.playlist_changed.emit()

    # ── External helpers (called by MainWindow) ──────────

    def add_tracks_to_playlist(self, playlist_name, paths):
        """Add paths to a named playlist (dedup). Called by context menu."""
        if playlist_name not in self._playlists:
            return
        existing = set(self._playlists[playlist_name])
        for p in paths:
            if p not in existing:
                self._playlists[playlist_name].append(p)
                existing.add(p)
        self._refresh_playlist_list()
        self.playlist_changed.emit()

    def remove_tracks_from_active_playlist(self, paths_to_remove):
        """Remove paths from the currently active playlist."""
        if not self._active_playlist or self._active_playlist not in self._playlists:
            return
        self._playlists[self._active_playlist] = [
            p for p in self._playlists[self._active_playlist]
            if p not in paths_to_remove
        ]
        self._refresh_playlist_list()
        self.playlist_changed.emit()
        # Re-emit so filter updates
        remaining = set(self._playlists.get(self._active_playlist, []))
        self.playlist_selected.emit(remaining)

    def get_playlist_names(self):
        """Return sorted list of playlist names (for context-menu sub-menus)."""
        return sorted(self._playlists.keys())

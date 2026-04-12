"""
Left sidebar — Genre list + Playlist list with CRUD.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QInputDialog, QLabel, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ui.theme import COLORS

import qtawesome as qta


TRACK_PATHS_MIME = 'application/x-musicplayer-track-paths'


class _PlaylistDropListWidget(QListWidget):
    """QListWidget that accepts drops of track paths onto static playlists."""

    tracks_dropped = Signal(str, list)  # (playlist_name, [path, ...])

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    # ── Drag-enter / drag-move: accept only our MIME on static items ─

    def _target_playlist_name(self, pos):
        """Return the static playlist name under *pos*, or None."""
        item = self.itemAt(pos)
        if item is None:
            return None
        if item.data(Qt.UserRole + 1) != 'static':
            return None
        return item.data(Qt.UserRole)   # playlist name

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TRACK_PATHS_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if (event.mimeData().hasFormat(TRACK_PATHS_MIME)
                and self._target_playlist_name(event.position().toPoint())):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        name = self._target_playlist_name(event.position().toPoint())
        if not name or not event.mimeData().hasFormat(TRACK_PATHS_MIME):
            event.ignore()
            return
        raw = bytes(event.mimeData().data(TRACK_PATHS_MIME)).decode('utf-8')
        paths = [p for p in raw.split('\n') if p]
        if paths:
            self.tracks_dropped.emit(name, paths)
        event.acceptProposedAction()


class SidebarWidget(QWidget):
    """Left sidebar containing a genre list and a playlist list."""

    # ── Signals ──────────────────────────────────────────
    genre_selected = Signal(object)      # set of genres or None (All)
    playlist_selected = Signal(object)   # set of paths or None (All Tracks)

    playlist_changed = Signal()          # emitted after any playlist CRUD
    smart_playlist_changed = Signal()    # emitted after smart playlist CRUD
    smart_playlist_evaluate = Signal(str)  # name → request evaluation

    def __init__(self, parent=None):
        super().__init__(parent)
        self._genre_groups = {}  # group_name → [genre, ...]
        self._all_genres = set()
        self._genre_label_map = {}  # display label → ('all'|'group'|'genre', name)

        self._playlists = {}  # name → [path, ...]
        self._smart_playlists = {}  # name → {'rules': [...], 'match': str}
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

        btn_new_pl = QPushButton()
        btn_new_pl.setIcon(qta.icon('mdi6.plus', color=COLORS['fg']))
        btn_new_pl.setFixedSize(28, 28)
        btn_new_pl.setIconSize(btn_new_pl.size() * 0.6)
        btn_new_pl.setToolTip('New playlist')
        btn_new_pl.clicked.connect(self._create_playlist)
        pl_header_row.addWidget(btn_new_pl)

        btn_new_smart = QPushButton()
        btn_new_smart.setIcon(
            qta.icon('mdi6.lightning-bolt', color=COLORS['yellow']))
        btn_new_smart.setFixedSize(28, 28)
        btn_new_smart.setIconSize(btn_new_smart.size() * 0.6)
        btn_new_smart.setToolTip('New smart playlist')
        btn_new_smart.clicked.connect(self._create_smart_playlist)
        pl_header_row.addWidget(btn_new_smart)

        layout.addLayout(pl_header_row)

        self._playlist_list = _PlaylistDropListWidget()
        self._playlist_list.setSelectionMode(QListWidget.SingleSelection)
        self._playlist_list.currentItemChanged.connect(self._on_playlist_item_changed)
        self._playlist_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._playlist_list.customContextMenuRequested.connect(
            self._on_playlist_right_click)
        self._playlist_list.tracks_dropped.connect(self._on_tracks_dropped)
        layout.addWidget(self._playlist_list, stretch=1)

    # ── Public API ───────────────────────────────────────

    def set_genre_data(self, all_genres, genre_groups, genre_counts=None):
        """Rebuild the genre list from a set of all genres and a groups dict."""
        self._all_genres = all_genres
        self._genre_groups = genre_groups
        self._genre_counts = genre_counts or {}
        self._build_genre_list()

    def set_playlist_data(self, playlists, smart_playlists=None):
        """Set the playlists dict (name → [path, ...]) and rebuild."""
        self._playlists = playlists
        if smart_playlists is not None:
            self._smart_playlists = smart_playlists
        self._refresh_playlist_list()

    def get_active_playlist_name(self):
        return self._active_playlist

    def get_smart_playlists(self):
        """Return the smart playlists dict."""
        return self._smart_playlists

    # ── Genre list ───────────────────────────────────────

    def _build_genre_list(self):
        """Populate the genre QListWidget with groups + ungrouped."""
        self._genre_list.blockSignals(True)
        self._genre_list.clear()
        self._genre_label_map = {}
        counts = self._genre_counts

        # "All" entry — total track count
        total = sum(counts.values()) if counts else 0
        all_label = f'All  ({total})' if total else 'All'
        item = QListWidgetItem(all_label)
        item.setData(Qt.UserRole, ('all', 'All'))
        self._genre_list.addItem(item)

        grouped_genres = set()
        for gname, members in sorted(self._genre_groups.items()):
            # Group header with summed count
            group_count = sum(counts.get(g, 0) for g in members)
            group_label = f'▸ {gname}  ({group_count})' if group_count else f'▸ {gname}'
            group_item = QListWidgetItem(group_label)
            group_item.setData(Qt.UserRole, ('group', gname))
            group_item.setForeground(
                Qt.GlobalColor.cyan if COLORS else Qt.GlobalColor.white)
            self._genre_list.addItem(group_item)

            for genre in sorted(members):
                c = counts.get(genre, 0)
                sub_label = f'    {genre}  ({c})' if c else f'    {genre}'
                sub = QListWidgetItem(sub_label)
                sub.setData(Qt.UserRole, ('genre', genre))
                self._genre_list.addItem(sub)
                grouped_genres.add(genre)

        # Ungrouped genres
        ungrouped = sorted(g for g in self._all_genres if g and g not in grouped_genres)
        for genre in ungrouped:
            c = counts.get(genre, 0)
            label = f'{genre}  ({c})' if c else genre
            gi = QListWidgetItem(label)
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
        all_item.setData(Qt.UserRole + 1, 'all')  # type tag
        self._playlist_list.addItem(all_item)

        # Static playlists
        for name in sorted(self._playlists.keys()):
            count = len(self._playlists[name])
            pl_item = QListWidgetItem(f'{name}  ({count})')
            pl_item.setData(Qt.UserRole, name)
            pl_item.setData(Qt.UserRole + 1, 'static')
            self._playlist_list.addItem(pl_item)

        # Smart playlists
        for name in sorted(self._smart_playlists.keys()):
            sp = self._smart_playlists[name]
            n_rules = len(sp.get('rules', []))
            sp_item = QListWidgetItem(f'\u26a1 {name}  ({n_rules} rules)')
            sp_item.setData(Qt.UserRole, name)
            sp_item.setData(Qt.UserRole + 1, 'smart')
            sp_item.setForeground(QColor(COLORS['yellow']))
            self._playlist_list.addItem(sp_item)

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
        kind = current.data(Qt.UserRole + 1) or 'all'
        self._active_playlist = name
        if name is None:
            self.playlist_selected.emit(None)
        elif kind == 'smart':
            # Ask main window to evaluate rules and filter
            self.smart_playlist_evaluate.emit(name)
        else:
            paths = self._playlists.get(name, [])
            self.playlist_selected.emit(set(paths))

    def _on_playlist_right_click(self, pos):
        item = self._playlist_list.itemAt(pos)
        if item is None:
            return
        name = item.data(Qt.UserRole)
        kind = item.data(Qt.UserRole + 1) or 'all'
        menu = QMenu(self)
        if name is None:
            # "All Tracks" — limited menu
            menu.addAction('New Playlist\u2026', self._create_playlist)
            menu.addAction('New Smart Playlist\u2026', self._create_smart_playlist)
        elif kind == 'smart':
            menu.addAction('Edit\u2026', lambda: self._edit_smart_playlist(name))
            menu.addAction('Delete', lambda: self._delete_smart_playlist(name))
            menu.addSeparator()
            menu.addAction('New Smart Playlist\u2026', self._create_smart_playlist)
        else:
            menu.addAction('Rename\u2026', lambda: self._rename_playlist(name))
            menu.addAction('Duplicate\u2026', lambda: self._duplicate_playlist(name))
            menu.addAction('Delete', lambda: self._delete_playlist(name))
            menu.addSeparator()
            menu.addAction('New Playlist\u2026', self._create_playlist)
            menu.addAction('New Smart Playlist\u2026', self._create_smart_playlist)
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

    def _on_tracks_dropped(self, playlist_name, paths):
        """Handle tracks dragged from the track table onto a playlist."""
        self.add_tracks_to_playlist(playlist_name, paths)

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

    # ── Smart playlist CRUD ──────────────────────────────

    def _create_smart_playlist(self):
        """Open the smart playlist dialog to create a new smart playlist."""
        from ui.smart_playlist_dialog import SmartPlaylistDialog
        dlg = SmartPlaylistDialog(
            self,
            genres=sorted(self._all_genres),
            tags=sorted(getattr(self, '_all_tags', set())),
        )
        if dlg.exec() == SmartPlaylistDialog.Accepted:
            name, rules, match = dlg.get_result()
            if name in self._smart_playlists or name in self._playlists:
                QMessageBox.warning(
                    self, 'Name Conflict',
                    f'A playlist named "{name}" already exists.')
                return
            self._smart_playlists[name] = {'rules': rules, 'match': match}
            self._refresh_playlist_list()
            self.smart_playlist_changed.emit()

    def _edit_smart_playlist(self, name):
        """Open the smart playlist dialog to edit an existing smart playlist."""
        from ui.smart_playlist_dialog import SmartPlaylistDialog
        sp = self._smart_playlists.get(name, {})
        dlg = SmartPlaylistDialog(
            self,
            genres=sorted(self._all_genres),
            tags=sorted(getattr(self, '_all_tags', set())),
            name=name,
            rules=sp.get('rules', []),
            match_mode=sp.get('match', 'all'),
        )
        if dlg.exec() == SmartPlaylistDialog.Accepted:
            new_name, rules, match = dlg.get_result()
            # If renamed, remove old
            if new_name != name:
                self._smart_playlists.pop(name, None)
            self._smart_playlists[new_name] = {'rules': rules, 'match': match}
            self._refresh_playlist_list()
            self.smart_playlist_changed.emit()
            # Re-evaluate if this is the active playlist
            if self._active_playlist == name or self._active_playlist == new_name:
                self._active_playlist = new_name
                self.smart_playlist_evaluate.emit(new_name)

    def _delete_smart_playlist(self, name):
        reply = QMessageBox.question(
            self, 'Delete Smart Playlist',
            f'Delete smart playlist "{name}"?',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._smart_playlists.pop(name, None)
            if self._active_playlist == name:
                self._active_playlist = None
                self.playlist_selected.emit(None)
            self._refresh_playlist_list()
            self.smart_playlist_changed.emit()

    def set_all_tags(self, tags):
        """Store available tags for smart playlist rule dropdowns."""
        self._all_tags = tags

"""
Main window — QMainWindow shell with layout, splitters, and panel wiring.
"""

import os
import time

import vlc

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QSplitter, QStatusBar, QVBoxLayout, QWidget,
)

from ui.theme import COLORS, DARK_THEME
from ui.track_table import ALL_COLUMNS, TrackFilterProxy, TrackTableModel, TrackTableView
from ui.transport_bar import TransportBar


class MainWindow(QMainWindow):
    """Top-level window for the music player."""

    def __init__(self, db, config, parent=None):
        super().__init__(parent)
        self.db = db
        self.config = config

        self.setWindowTitle('Python Music Player')
        self.resize(1920, 1080)
        self.setMinimumSize(900, 500)
        self.setStyleSheet(DARK_THEME)

        # ── Data ─────────────────────────────────────────
        self.playlist = []
        self.genres = set()
        self.all_voters = set()
        self._path_set = set()
        self._path_to_idx = {}
        self.current_index = None
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self._play_started_at = 0.0
        self._consecutive_skips = 0

        # Play queue: list of playlist indices
        self._play_queue = []

        # Playback speed
        self._speed = 1.0
        self._auto_reset_speed = True

        # Volume / mute
        self._muted = False
        self._pre_mute_vol = 80

        # ── VLC ──────────────────────────────────────────
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        # ── Build the UI ─────────────────────────────────
        self._build_ui()
        self._build_menu_bar()
        self._build_status_bar()

        # ── Poll timer (replaces Tk's self.after(500, ...)) ──
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(500)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start()

        # ── Load data ────────────────────────────────────
        self._load_tracks()

        # ── Keyboard shortcuts ───────────────────────────
        self._bind_shortcuts()

    def _build_ui(self):
        """Construct the main layout with splitters."""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Main horizontal splitter: [sidebar | center | right panel] ──
        self._main_splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(self._main_splitter)

        # Left sidebar placeholder
        self._sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(4, 4, 4, 4)
        sidebar_label = QLabel('Genres / Playlists')
        sidebar_label.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-weight: bold;')
        sidebar_layout.addWidget(sidebar_label)
        sidebar_layout.addStretch()

        # Center: now-playing + transport + track table
        self._center = QWidget()
        center_layout = QVBoxLayout(self._center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        # Now-playing bar
        now_playing_bar = QWidget()
        now_playing_bar.setStyleSheet(f'background-color: {COLORS["bg_dark"]};')
        np_layout = QHBoxLayout(now_playing_bar)
        np_layout.setContentsMargins(12, 6, 12, 6)
        self._lbl_now_playing = QLabel('Not Playing')
        self._lbl_now_playing.setStyleSheet('font-size: 18px; font-weight: bold;')
        np_layout.addWidget(self._lbl_now_playing)
        self._lbl_genre = QLabel('')
        self._lbl_genre.setStyleSheet(
            f'color: {COLORS["cyan"]}; font-size: 11px; padding: 2px 6px;')
        np_layout.addWidget(self._lbl_genre)
        np_layout.addStretch()

        # Jump-to-playing button
        self.btn_jump = QPushButton('⎆')
        self.btn_jump.setFixedSize(32, 28)
        self.btn_jump.setToolTip('Jump to now playing track')
        self.btn_jump.clicked.connect(self._jump_to_playing)
        np_layout.addWidget(self.btn_jump)

        center_layout.addWidget(now_playing_bar)

        # Transport bar
        self._transport = TransportBar(self)
        self._transport.setStyleSheet(f'background-color: {COLORS["bg_dark"]};')
        self._connect_transport()
        center_layout.addWidget(self._transport)

        # Track table
        self._track_model = TrackTableModel(self)
        self._filter_proxy = TrackFilterProxy(self)
        self._filter_proxy.setSourceModel(self._track_model)
        self._track_table = TrackTableView(self)
        self._track_table.setModel(self._filter_proxy)

        # Connect signals
        self._track_table.play_requested.connect(self._on_play_requested)
        self._track_table.selection_changed.connect(self._on_selection_changed)
        self._track_table.context_menu_requested.connect(self._on_context_menu)

        center_layout.addWidget(self._track_table, stretch=1)

        # Right panel: queue + play log (vertical splitter)
        self._right_panel = QWidget()
        right_layout = QVBoxLayout(self._right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_label = QLabel('Queue / Play Log')
        right_label.setStyleSheet(f'color: {COLORS["fg_dim"]}; font-weight: bold;')
        right_layout.addWidget(right_label)
        right_layout.addStretch()

        # Add to main splitter
        self._main_splitter.addWidget(self._sidebar)
        self._main_splitter.addWidget(self._center)
        self._main_splitter.addWidget(self._right_panel)
        self._main_splitter.setSizes([170, 900, 240])
        self._main_splitter.setStretchFactor(0, 0)
        self._main_splitter.setStretchFactor(1, 1)
        self._main_splitter.setStretchFactor(2, 0)

    def _build_menu_bar(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu('&File')

        add_files_action = QAction('Add &Files...', self)
        add_files_action.triggered.connect(self._add_files)
        file_menu.addAction(add_files_action)

        add_folder_action = QAction('Add F&older...', self)
        add_folder_action.triggered.connect(self._add_folder)
        file_menu.addAction(add_folder_action)

        file_menu.addSeparator()
        quit_action = QAction('&Quit', self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menu_bar.addMenu('&View')
        sidebar_action = QAction('Toggle &Sidebar', self)
        sidebar_action.setShortcut('F1')
        sidebar_action.triggered.connect(self._toggle_sidebar)
        view_menu.addAction(sidebar_action)

        right_action = QAction('Toggle &Right Panel', self)
        right_action.setShortcut('F2')
        right_action.triggered.connect(self._toggle_right_panel)
        view_menu.addAction(right_action)

        fullscreen_action = QAction('Toggle &Fullscreen', self)
        fullscreen_action.setShortcut('F11')
        fullscreen_action.triggered.connect(self._toggle_fullscreen)
        view_menu.addAction(fullscreen_action)

    def _build_status_bar(self):
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._track_count_lbl = QLabel('0 tracks')
        self._status_bar.addWidget(self._track_count_lbl)
        self._perf_lbl = QLabel('')
        self._perf_lbl.setStyleSheet(f'color: {COLORS["fg_very_dim"]};')
        self._status_bar.addPermanentWidget(self._perf_lbl)

    # ── Data loading ─────────────────────────────────────

    def _load_tracks(self):
        """Load all tracks from DB and populate the table model."""
        tracks, voters, genres = self.db.load_all_tracks()
        self.all_voters = voters
        self.genres = genres

        # Annotate each entry with its playlist index and absolute path
        abs_fn = lambda p: os.path.join(self.config.library_root, p) if self.config.library_root and not os.path.isabs(p) else p
        self.playlist = []
        self._path_set = set()
        self._path_to_idx = {}
        for i, entry in enumerate(tracks):
            entry['_playlist_idx'] = i
            entry['_abs_path'] = abs_fn(entry['path'])
            self.playlist.append(entry)
            self._path_set.add(entry['path'])
            self._path_to_idx[entry['path']] = i

        self._track_model.set_tracks(self.playlist)

        # Apply visible columns from config
        if self.config.visible_columns:
            self._track_table.set_visible_columns(self.config.visible_columns)

        self._update_track_count()
        self._lbl_now_playing.setText(f'{len(self.playlist)} tracks loaded')

    def _update_track_count(self):
        total = len(self.playlist)
        shown = self._filter_proxy.rowCount()
        if shown == total:
            self._track_count_lbl.setText(f'{total} tracks')
        else:
            self._track_count_lbl.setText(f'{shown} of {total} tracks')

    # ── Add files / folders ──────────────────────────────

    def _rel_path(self, abs_path):
        """Convert an absolute path to relative (using library_root)."""
        if not self.config.library_root:
            return abs_path
        return os.path.relpath(abs_path, self.config.library_root)

    def _abs_path(self, rel_path):
        """Convert a relative path back to absolute."""
        if not self.config.library_root:
            return rel_path
        if os.path.isabs(rel_path):
            return rel_path
        return os.path.join(self.config.library_root, rel_path)

    def _add_path(self, abs_path):
        """Add a single track by absolute path. Returns True if new."""
        rel = self._rel_path(abs_path)
        if rel in self._path_set:
            return False

        title = os.path.basename(abs_path)
        genre, artist, album, comment, length = 'Unknown', '', '', '', None

        try:
            from mutagen import File as MutagenFile
            tags = MutagenFile(abs_path, easy=True)
            if tags is not None:
                title = tags.get('title', [title])[0]
                genre = tags.get('genre', [genre])[0]
                artist = tags.get('artist', [''])[0] or ''
                album = tags.get('album', [''])[0] or ''
                comment = str(tags.get('comment', [''])[0] or '')
            audio = MutagenFile(abs_path)
            if audio is not None and audio.info is not None:
                length = audio.info.length
        except Exception:
            pass

        entry = {
            'path': rel, 'title': title, 'basename': os.path.basename(abs_path),
            'artist': artist, 'album': album, 'genre': genre, 'comment': comment,
            'length': length, 'tags': [], 'rating': 0,
            'liked_by': set(), 'disliked_by': set(),
        }

        idx = len(self.playlist)
        entry['_playlist_idx'] = idx
        entry['_abs_path'] = abs_path
        self.playlist.append(entry)
        self._path_set.add(rel)
        self._path_to_idx[rel] = idx
        self.genres.add(genre)

        stats = self.db.ensure_track(rel, title, genre, comment, length, artist, album)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        if stats[4] is not None:
            entry['length'] = stats[4]
        return True

    def _add_files(self):
        """File > Add Files... dialog."""
        files, _ = QFileDialog.getOpenFileNames(
            self, 'Select audio files', '',
            'Audio Files (*.mp3 *.wav *.ogg *.flac);;All Files (*)')
        if not files:
            return
        for f in files:
            self._add_path(f)
        self._track_model.set_tracks(self.playlist)
        self._update_track_count()
        self._lbl_now_playing.setText(f'{len(files)} file(s) added')

    def _add_folder(self):
        """File > Add Folder... dialog."""
        folder = QFileDialog.getExistingDirectory(self, 'Select folder')
        if not folder:
            return
        exts = ('.mp3', '.wav', '.ogg', '.flac')
        self._lbl_now_playing.setText('Scanning folder\u2026')

        audio_files = []
        for root, _, filenames in os.walk(folder):
            for name in filenames:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            QMessageBox.information(self, 'No files',
                                    'No supported audio files found in folder.')
            self._lbl_now_playing.setText('Not Playing')
            return

        # Show progress in the status bar
        progress = QProgressBar()
        progress.setRange(0, total)
        progress.setFixedWidth(200)
        self._status_bar.addWidget(progress)

        added = 0
        from PySide6.QtWidgets import QApplication
        for i, path in enumerate(audio_files, 1):
            if self._add_path(path):
                added += 1
            progress.setValue(i)
            if i % 25 == 0 or i == total:
                QApplication.processEvents()

        self._status_bar.removeWidget(progress)
        progress.deleteLater()

        self._track_model.set_tracks(self.playlist)
        self._update_track_count()
        self._lbl_now_playing.setText(f'Added {added} tracks')

    # ── Connect transport bar signals ───────────────────

    def _connect_transport(self):
        t = self._transport
        t.play_pause_clicked.connect(self._play_pause)
        t.stop_clicked.connect(self._stop)
        t.next_clicked.connect(self._next_track)
        t.prev_clicked.connect(self._prev_track)
        t.scrub_released.connect(self._on_scrub_released)
        t.volume_changed.connect(self._on_volume_changed)
        t.mute_toggled.connect(self._toggle_mute)
        t.speed_up_clicked.connect(self._speed_up)
        t.speed_down_clicked.connect(self._speed_down)
        t.speed_reset_clicked.connect(self._speed_reset)
        t.auto_reset_speed_changed.connect(self._on_auto_reset_speed)

    # ── VLC helpers ──────────────────────────────────────

    def _vlc_mp(self):
        """Shortcut to the underlying media player."""
        return self.vlc_player.get_media_player()

    def _load(self, index):
        """Prepare VLC to play playlist[index]. Returns True on success."""
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]['_abs_path']
        if not os.path.isfile(path):
            title = self.playlist[index].get('title', os.path.basename(path))
            QMessageBox.warning(self, 'File not found',
                                f'Cannot play \u201c{title}\u201d\n\n'
                                f'The file no longer exists:\n{path}')
            return False
        try:
            media = self.vlc_instance.media_new(path)
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            self.current_index = index
            self._track_model.set_now_playing(index)
            self._track_table.jump_to_playlist_index(index)
            return True
        except Exception as e:
            QMessageBox.critical(self, 'Error', f'Could not load {path}: {e}')
            return False

    def _update_now_playing(self, text=None):
        """Update the now-playing label and genre badge."""
        if text:
            self._lbl_now_playing.setText(text)
            self._lbl_genre.setText('')
        elif self.current_index is not None:
            entry = self.playlist[self.current_index]
            title = entry.get('title', entry.get('basename', ''))
            genre = entry.get('genre', '')
            self._lbl_now_playing.setText(title)
            if genre and genre != 'Unknown':
                self._lbl_genre.setText(f'  {genre}  ')
            else:
                self._lbl_genre.setText('')
        else:
            self._lbl_now_playing.setText('Not Playing')
            self._lbl_genre.setText('')

    def _record_play_immediate(self):
        """Record the play for the current track and update the table row."""
        if self.current_index is None:
            return
        path = self.playlist[self.current_index]['path']
        stats = self.db.record_play(path)
        if stats:
            entry = self.playlist[self.current_index]
            entry['play_count'] = stats[0]
            entry['first_played'] = stats[1]
            entry['last_played'] = stats[2]
        self._track_model.update_row(self.current_index)

    # ── Playback controls ────────────────────────────────

    def _play_pause(self):
        """Toggle play / pause / start."""
        # Currently playing → pause
        if self.is_playing and not self.is_paused:
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self._transport.set_playing_state(False)
            self._update_now_playing('Paused')
            return

        # Currently paused → resume
        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._transport.set_playing_state(True)
            self._update_now_playing()
            return

        # Nothing playing → start
        if not self.playlist:
            QMessageBox.information(self, 'No tracks', 'Add some audio files first.')
            return
        if self.current_index is None:
            self.current_index = 0

        self._play_index(self.current_index)

    def _play_index(self, index):
        """Load and start playing a specific playlist index."""
        if not self._load(index):
            return
        try:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._consecutive_skips = 0
            self._record_play_immediate()
            self._transport.set_playing_state(True)
            self._update_now_playing()
        except Exception as e:
            QMessageBox.critical(self, 'Playback error', str(e))

    def _stop(self):
        """Stop playback."""
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self._transport.reset_display()
        self._update_now_playing('Stopped')

    def _next_track(self):
        """Advance to the next track (queue-aware)."""
        if not self.playlist:
            return
        # Auto-reset speed if enabled
        if self._auto_reset_speed and abs(self._speed - 1.0) > 0.05:
            self._speed_reset()

        # Check play queue first
        if self._play_queue:
            nxt = self._play_queue.pop(0)
        elif self.current_index is not None:
            nxt = (self.current_index + 1) % len(self.playlist)
        else:
            nxt = 0

        if not self._load(nxt):
            self._last_action = 'stopped'
            self.is_playing = False
            self.is_paused = False
            return
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._record_play_immediate()
        self._transport.set_playing_state(True)
        self._update_now_playing()

    def _prev_track(self):
        """Go back to the previous track."""
        if not self.playlist:
            return
        if self.current_index is not None:
            prev = (self.current_index - 1) % len(self.playlist)
        else:
            prev = 0

        if not self._load(prev):
            self._last_action = 'stopped'
            self.is_playing = False
            self.is_paused = False
            return
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._record_play_immediate()
        self._transport.set_playing_state(True)
        self._update_now_playing()

    # ── Scrub / Volume / Speed ───────────────────────────

    def _on_scrub_released(self, pos):
        """Seek to position (0.0–1.0) in the current track."""
        mp = self._vlc_mp()
        length = mp.get_length()
        if length > 0 and (self.is_playing or self.is_paused):
            mp.set_position(pos)

    def _on_volume_changed(self, volume):
        """Apply volume (0–100) to VLC."""
        self._vlc_mp().audio_set_volume(volume)
        if volume > 0:
            self._muted = False
            self._transport.set_mute_icon(False)

    def _toggle_mute(self):
        if self._muted:
            self._transport.set_volume(self._pre_mute_vol)
            self._vlc_mp().audio_set_volume(self._pre_mute_vol)
            self._muted = False
            self._transport.set_mute_icon(False)
        else:
            self._pre_mute_vol = self._transport.volume_slider.value()
            self._transport.set_volume(0)
            self._vlc_mp().audio_set_volume(0)
            self._muted = True
            self._transport.set_mute_icon(True)

    def _speed_up(self):
        self._speed = min(round(self._speed + 0.1, 1), 3.0)
        self._apply_speed()

    def _speed_down(self):
        self._speed = max(round(self._speed - 0.1, 1), 0.3)
        self._apply_speed()

    def _speed_reset(self):
        self._speed = 1.0
        self._apply_speed()

    def _apply_speed(self):
        self._vlc_mp().set_rate(self._speed)
        self._transport.set_speed_label(self._speed)

    def _on_auto_reset_speed(self, checked):
        self._auto_reset_speed = checked

    # ── Jump to playing ──────────────────────────────────

    def _jump_to_playing(self):
        """Scroll the track table to the currently playing track."""
        if self.current_index is not None:
            self._track_table.jump_to_playlist_index(self.current_index)

    # ── Slot handlers ────────────────────────────────────

    def _on_play_requested(self, playlist_idx):
        """Handle double-click on a track — load and play it."""
        # Auto-reset speed if enabled
        if self._auto_reset_speed and abs(self._speed - 1.0) > 0.05:
            self._speed_reset()
        self._play_index(playlist_idx)

    def _on_selection_changed(self, indices):
        """Handle track selection change."""
        pass  # TODO: update preview/details

    def _on_context_menu(self, playlist_idx, pos):
        """Handle right-click on a track."""
        # TODO: full context menu
        pass

    # ── Poll timer ───────────────────────────────────────

    def _poll(self):
        """Periodic update: scrub position, time labels, auto-advance."""
        try:
            self._poll_inner()
        except Exception:
            pass  # swallow errors in the poll loop

    def _poll_inner(self):
        mp = self._vlc_mp()
        is_playing = mp.is_playing()

        # Reset consecutive skip counter when a track is genuinely playing
        if is_playing and self._consecutive_skips > 0:
            self._consecutive_skips = 0

        # Update scrub slider and time labels
        if not self._transport.is_user_scrubbing:
            length = mp.get_length()
            pos = mp.get_position()
            if length > 0 and pos >= 0:
                self._transport.set_scrub_position(pos)
                self._transport.set_time_labels(int(pos * length), length)
            elif not is_playing and not self.is_paused:
                self._transport.set_scrub_position(0)
                self._transport.set_time_labels(0, 0)

        # Auto-advance: VLC finished playing and we were in 'playing' state
        if not is_playing and self._last_action == 'playing' and not self.is_paused:
            # Guard: don't auto-advance within 1.5s of play (VLC async startup)
            if time.time() - self._play_started_at < 1.5:
                return
            if self._consecutive_skips >= 3:
                self._consecutive_skips = 0
                self._stop()
            elif len(self.playlist) > 1:
                self._consecutive_skips += 1
                self._next_track()
            elif self.playlist:
                self._stop()

    # ── Keyboard shortcuts ─────────────────────────────

    def _bind_shortcuts(self):
        """Set up global keyboard shortcuts."""
        def _sc(key, slot):
            s = QShortcut(QKeySequence(key), self)
            s.activated.connect(slot)
            return s

        _sc('Space',      self._play_pause)
        _sc('Right',      self._next_track)
        _sc('Left',       self._prev_track)
        _sc('Escape',     self._stop)
        _sc('Ctrl+F',     self._focus_search)
        _sc('F1',         self._toggle_sidebar)
        _sc('F2',         self._toggle_right_panel)
        _sc('F11',        self._toggle_fullscreen)

    def _focus_search(self):
        """Focus the search box (once it exists)."""
        # TODO: wire to search bar widget
        pass

    def _toggle_sidebar(self):
        """Show/hide the left sidebar panel."""
        if self._sidebar.isVisible():
            self._sidebar.hide()
        else:
            self._sidebar.show()

    def _toggle_right_panel(self):
        """Show/hide the right queue/play-log panel."""
        if self._right_panel.isVisible():
            self._right_panel.hide()
        else:
            self._right_panel.show()

    def _toggle_fullscreen(self):
        """Toggle between fullscreen and normal window."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # ── Cleanup ──────────────────────────────────────────

    def closeEvent(self, event):
        self._poll_timer.stop()
        self.vlc_player.stop()
        self.config.visible_columns = self._track_table.get_visible_columns()
        self.config.save()
        super().closeEvent(event)

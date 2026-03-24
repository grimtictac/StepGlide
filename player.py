#!/usr/bin/env python3
"""A small music player using tkinter + VLC

Features:
- Add files / folders to a playlist
- Play / Pause / Stop / Next / Previous
- Volume control
- Filter by genre (reads tags via mutagen if available)

Run: python3 player.py
"""
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Configuration ────────────────────────────────────────
PLAY_MIN_SECONDS = 5        # Only count a play after this many seconds
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player.db')

try:
    import vlc
except Exception:
    print("Missing dependency: python-vlc. Install with: pip install python-vlc")
    raise

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

try:
    import aubio
except ImportError:
    aubio = None


class MusicPlayer(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Python Music Player')
        self.geometry('1100x500')

        # playlist: list of dicts {path, title, basename, genre, comment}
        self.playlist = []
        # display_indices maps the current listbox positions -> playlist indices
        self.display_indices = []
        self.genres = set()
        
        # queue: list of playlist indices to play next
        self.queue = []

        self.current_index = None
        self.is_playing = False
        self.is_paused = False
        self._last_action = None  # 'playing' | 'stopped' | 'paused'

        # VLC instance and player
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        # Play tracking
        self._playback_start_time = None   # epoch when current track started
        self._play_recorded = False        # has this play already been counted?
        self._init_database()

        self._build_ui()
        # Reload previously-added tracks from the database
        self._load_tracks_from_db()
        # poll to detect end of track
        self.after(500, self._poll)

    # ── Database helpers ─────────────────────────────────

    def _init_database(self):
        """Create the SQLite database and tables if they don't exist."""
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id          INTEGER PRIMARY KEY,
                file_path   TEXT UNIQUE NOT NULL,
                title       TEXT,
                play_count  INTEGER DEFAULT 0,
                first_played TIMESTAMP,
                last_played  TIMESTAMP,
                file_created TIMESTAMP,
                db_created   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS play_history (
                id        INTEGER PRIMARY KEY,
                track_id  INTEGER NOT NULL,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(track_id) REFERENCES tracks(id)
            )
        """)
        con.commit()
        # Migration: add bpm column if missing
        cur = con.execute("PRAGMA table_info(tracks)")
        columns = [row[1] for row in cur.fetchall()]
        if 'bpm' not in columns:
            con.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
            con.commit()
        con.close()

    def _load_tracks_from_db(self):
        """Reload all previously-added tracks from the database on startup."""
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT file_path, title, play_count, first_played, last_played, file_created, bpm FROM tracks ORDER BY title")
        rows = cur.fetchall()
        con.close()

        if not rows:
            return

        # Show progress bar
        total = len(rows)
        self.load_progress['maximum'] = total
        self.load_progress['value'] = 0
        self.load_progress.pack(fill='x', pady=2)
        self.lbl_load.pack(fill='x')
        self.lbl_status.config(text='Loading library…')

        for i, (path, db_title, play_count, first_played, last_played, file_created, bpm) in enumerate(rows, 1):
            if not os.path.isfile(path):
                continue
            if any(t['path'] == path for t in self.playlist):
                continue

            title = db_title or os.path.basename(path)
            genre = 'Unknown'
            comment = ''
            if MutagenFile is not None:
                try:
                    tags = MutagenFile(path, easy=True)
                    if tags is not None:
                        title = tags.get('title', [title])[0]
                        genre = tags.get('genre', [genre])[0]
                        comment_val = tags.get('comment', [''])[0]
                        comment = str(comment_val) if comment_val else ''
                except Exception:
                    pass

            entry = {
                'path': path,
                'title': title,
                'basename': os.path.basename(path),
                'genre': genre,
                'comment': comment,
                'bpm': bpm,
                'play_count': play_count or 0,
                'first_played': first_played,
                'last_played': last_played,
                'file_created': file_created,
            }
            self.playlist.append(entry)
            self.genres.add(genre)

            self.load_progress['value'] = i
            self.lbl_load.config(text=f'Loading {i}/{total}…')
            if i % 50 == 0 or i == total:
                self.update_idletasks()

        # Hide progress bar
        self.load_progress.pack_forget()
        self.lbl_load.pack_forget()

        self._update_genre_options()
        self._apply_filter()
        self.lbl_status.config(text=f'Loaded {len(self.playlist)} tracks')

    def _ensure_track_in_db(self, path, title=''):
        """Make sure a track row exists; return (play_count, first_played, last_played, file_created)."""
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            try:
                file_created = datetime.fromtimestamp(os.path.getctime(path), tz=timezone.utc).isoformat()
            except OSError:
                file_created = None
            cur.execute(
                "INSERT INTO tracks (file_path, title, file_created) VALUES (?, ?, ?)",
                (path, title, file_created)
            )
            con.commit()
            con.close()
            return (0, None, None, file_created)
        con.close()
        return row   # (play_count, first_played, last_played, file_created)

    def _record_play(self, path):
        """Increment play_count, set first/last played, and log history."""
        now = datetime.now(tz=timezone.utc).isoformat()
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        # Update first_played only if it is NULL
        cur.execute("""
            UPDATE tracks
               SET play_count  = play_count + 1,
                   first_played = COALESCE(first_played, ?),
                   last_played  = ?
             WHERE file_path = ?
        """, (now, now, path))
        # Insert into play_history
        cur.execute("SELECT id FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row:
            cur.execute("INSERT INTO play_history (track_id, played_at) VALUES (?, ?)", (row[0], now))
        con.commit()
        con.close()

    def _get_track_stats(self, path):
        """Return (play_count, first_played, last_played, file_created) or defaults."""
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row:
            return row
        return (0, None, None, None)

    @staticmethod
    def _format_ts(iso_str, relative=False):
        """Format an ISO timestamp for display."""
        if not iso_str:
            return 'Never'
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz=None)  # convert to local
        except Exception:
            return str(iso_str)[:16]

        if not relative:
            return dt.strftime('%b %d, %Y')

        now = datetime.now()
        diff = now - dt.replace(tzinfo=None)
        secs = int(diff.total_seconds())
        if secs < 60:
            return 'Just now'
        if secs < 3600:
            m = secs // 60
            return f'{m} min ago'
        if secs < 86400:
            h = secs // 3600
            return f'{h}h ago'
        days = secs // 86400
        if days == 1:
            return 'Yesterday'
        if days < 7:
            return f'{days}d ago'
        return dt.strftime('%b %d, %Y')

    def _analyze_bpm(self, path):
        """Detect BPM using aubio. Returns float BPM or None."""
        if aubio is None:
            return None
        try:
            win_s = 1024
            hop_s = 512
            src = aubio.source(path, 0, hop_s)
            samplerate = src.samplerate
            tempo = aubio.tempo("default", win_s, hop_s, samplerate)
            beats = []
            total_frames = 0
            while True:
                samples, read = src()
                is_beat = tempo(samples)
                if is_beat:
                    beats.append(tempo.get_last_s())
                total_frames += read
                if read < hop_s:
                    break
            if len(beats) > 1:
                intervals = [beats[i+1] - beats[i] for i in range(len(beats)-1)]
                avg_interval = sum(intervals) / len(intervals)
                if avg_interval > 0:
                    return round(60.0 / avg_interval, 1)
            bpm = tempo.get_bpm()
            return round(bpm, 1) if bpm > 0 else None
        except Exception:
            return None

    def _get_or_analyze_bpm(self, playlist_idx):
        """Get BPM from DB cache, or analyze and store it."""
        entry = self.playlist[playlist_idx]
        path = entry['path']

        # Already cached in memory?
        if entry.get('bpm') is not None:
            return entry['bpm']

        # Check database
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT bpm FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row and row[0] is not None:
            entry['bpm'] = row[0]
            return row[0]

        # Analyze
        self.lbl_status.config(text=f'Analyzing BPM…')
        self.update_idletasks()
        bpm = self._analyze_bpm(path)
        if bpm is not None:
            entry['bpm'] = bpm
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET bpm = ? WHERE file_path = ?", (bpm, path))
            con.commit()
            con.close()
        return bpm

    def _build_ui(self):
        main = ttk.Frame(self)
        main.pack(fill='both', expand=True, padx=8, pady=8)

        left = ttk.Frame(main)
        left.pack(side='left', fill='both', expand=True)

        # Create Treeview with columns: Title, Genre, Comment, BPM, Plays, First Played, Last Played, File Created
        self.tree = ttk.Treeview(left, columns=('Title', 'Genre', 'Comment', 'BPM', 'Plays', 'First Played', 'Last Played', 'File Created'), show='headings', height=15)
        self.tree.column('Title', width=180, anchor='w')
        self.tree.column('Genre', width=60, anchor='w')
        self.tree.column('Comment', width=120, anchor='w')
        self.tree.column('BPM', width=50, anchor='center')
        self.tree.column('Plays', width=45, anchor='center')
        self.tree.column('First Played', width=90, anchor='w')
        self.tree.column('Last Played', width=90, anchor='w')
        self.tree.column('File Created', width=90, anchor='w')
        self.tree.heading('Title', text='Title')
        self.tree.heading('Genre', text='Genre')
        self.tree.heading('Comment', text='Comment')
        self.tree.heading('BPM', text='BPM')
        self.tree.heading('Plays', text='Plays')
        self.tree.heading('First Played', text='First Played')
        self.tree.heading('Last Played', text='Last Played')
        self.tree.heading('File Created', text='File Created')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<Button-3>', self._on_right_click)  # Right-click menu
        self.tree.bind('<<TreeviewSelect>>', self._on_select)  # Single-click: BPM analysis

        sb = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.config(yscrollcommand=sb.set)

        # Middle section: Queue panel
        queue_frame = ttk.LabelFrame(main, text='Queue', padding=4)
        queue_frame.pack(side='left', fill='both', expand=False, padx=(8, 0))

        self.queue_tree = ttk.Treeview(queue_frame, columns=('Track',), show='headings', height=15)
        self.queue_tree.column('Track', width=150, anchor='w')
        self.queue_tree.heading('Track', text='Upcoming')
        self.queue_tree.pack(side='left', fill='both', expand=True)
        self.queue_tree.bind('<Button-3>', self._on_queue_right_click)

        queue_sb = ttk.Scrollbar(queue_frame, orient='vertical', command=self.queue_tree.yview)
        queue_sb.pack(side='left', fill='y')
        self.queue_tree.config(yscrollcommand=queue_sb.set)

        ctrl = ttk.Frame(main)
        ctrl.pack(side='right', fill='y')

        ttk.Button(ctrl, text='Add Files', command=self.add_files).pack(fill='x', pady=2)
        ttk.Button(ctrl, text='Add Folder', command=self.add_folder).pack(fill='x', pady=2)
        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)

        # Genre filter
        ttk.Label(ctrl, text='Genre').pack(fill='x')
        self.genre_var = tk.StringVar(value='All')
        self.genre_box = ttk.Combobox(ctrl, textvariable=self.genre_var, state='readonly')
        self.genre_box.pack(fill='x', pady=2)
        self.genre_box.bind('<<ComboboxSelected>>', lambda e: self._apply_filter())
        self._update_genre_options()

        btn_frame = ttk.Frame(ctrl)
        btn_frame.pack(fill='x')
        ttk.Button(btn_frame, text='Prev', command=self.prev_track).grid(row=0, column=0, padx=2)
        self.btn_play = ttk.Button(btn_frame, text='Play', command=self.play_pause)
        self.btn_play.grid(row=0, column=1, padx=2)
        ttk.Button(btn_frame, text='Stop', command=self.stop).grid(row=0, column=2, padx=2)
        ttk.Button(btn_frame, text='Next', command=self.next_track).grid(row=0, column=3, padx=2)

        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)

        vol_frame = ttk.Frame(ctrl)
        vol_frame.pack(fill='x')
        ttk.Label(vol_frame, text='Volume').pack(side='left')
        self.vol = tk.DoubleVar(value=0.8)
        vol = ttk.Scale(vol_frame, from_=0.0, to=1.0, orient='horizontal', variable=self.vol, command=self._on_volume)
        vol.pack(side='left', fill='x', expand=True, padx=6)
        # Initial volume set is deferred until VLC player is ready
        self._on_volume()

        ttk.Separator(ctrl, orient='horizontal').pack(fill='x', pady=6)
        self.lbl_status = ttk.Label(ctrl, text='Stopped', wraplength=150)
        self.lbl_status.pack(fill='x', pady=2)

        # Progress bar for folder loading (hidden by default)
        self.load_progress = ttk.Progressbar(ctrl, orient='horizontal', mode='determinate')
        self.load_progress.pack(fill='x', pady=2)
        self.load_progress.pack_forget()   # hide until needed
        self.lbl_load = ttk.Label(ctrl, text='', wraplength=150)
        self.lbl_load.pack(fill='x')
        self.lbl_load.pack_forget()

    def add_files(self):
        files = filedialog.askopenfilenames(title='Select audio files', filetypes=[('Audio', '*.mp3 *.wav *.ogg *.flac'), ('All files', '*.*')])
        for f in files:
            self._add_path(f)
        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._update_genre_options()
        self._apply_filter()

    def add_folder(self):
        folder = filedialog.askdirectory(title='Select folder')
        if not folder:
            return
        exts = ('.mp3', '.wav', '.ogg', '.flac')

        # Phase 1: scan for all matching files
        self.lbl_status.config(text='Scanning folder…')
        self.update_idletasks()
        audio_files = []
        for root, _, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            messagebox.showinfo('No files', 'No supported audio files found in folder')
            self.lbl_status.config(text='Stopped')
            return

        # Phase 2: load files with progress bar
        self.load_progress['maximum'] = total
        self.load_progress['value'] = 0
        self.load_progress.pack(fill='x', pady=2)
        self.lbl_load.pack(fill='x')

        added = 0
        for i, path in enumerate(audio_files, 1):
            if self._add_path(path):
                added += 1
            self.load_progress['value'] = i
            self.lbl_load.config(text=f'Loading {i}/{total}…')
            if i % 25 == 0 or i == total:
                self.update_idletasks()

        # Hide progress bar
        self.load_progress.pack_forget()
        self.lbl_load.pack_forget()

        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._update_genre_options()
        self._apply_filter()
        self.lbl_status.config(text=f'Added {added} tracks')

    def _add_path(self, path):
        # returns True if added
        if any(t['path'] == path for t in self.playlist):
            return False
        title = os.path.basename(path)
        genre = 'Unknown'
        comment = ''
        if MutagenFile is not None:
            try:
                tags = MutagenFile(path, easy=True)
                if tags is not None:
                    title = tags.get('title', [title])[0]
                    genre = tags.get('genre', [genre])[0]
                    # Extract comment (may be a list or single value)
                    comment_val = tags.get('comment', [''])[0]
                    comment = str(comment_val) if comment_val else ''
            except Exception:
                # ignore metadata read errors
                pass
        entry = {'path': path, 'title': title, 'basename': os.path.basename(path), 'genre': genre, 'comment': comment}
        self.playlist.append(entry)
        self.genres.add(genre)
        # Register in database and fetch stats
        stats = self._ensure_track_in_db(path, title)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        return True

    def _update_genre_options(self):
        vals = ['All'] + sorted(x for x in self.genres if x)
        self.genre_box['values'] = vals
        if self.genre_var.get() not in vals:
            self.genre_var.set('All')

    def _apply_filter(self):
        sel = self.genre_var.get()
        # Clear all items from tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.display_indices = []
        for idx, entry in enumerate(self.playlist):
            if sel == 'All' or not sel or entry.get('genre') == sel:
                title = entry.get('title', entry['basename'])
                genre = entry.get('genre', '')
                comment = entry.get('comment', '')
                bpm = entry.get('bpm')
                bpm_str = str(int(bpm)) if bpm else '—'
                plays = entry.get('play_count', 0)
                first_p = self._format_ts(entry.get('first_played'), relative=False)
                last_p = self._format_ts(entry.get('last_played'), relative=True)
                file_c = self._format_ts(entry.get('file_created'), relative=False)
                self.tree.insert('', 'end', values=(title, genre, comment, bpm_str, plays, first_p, last_p, file_c))
                self.display_indices.append(idx)

    def _load(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]['path']
        try:
            # Reset play tracking for new track
            self._playback_start_time = None
            self._play_recorded = False

            # Create VLC media and add to player
            media = self.vlc_instance.media_new(path)
            # Clear existing media by recreating the media list
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            
            self.current_index = index
            # highlight in current view if present
            # Clear all selections first
            for item in self.tree.selection():
                self.tree.selection_remove(item)
            try:
                pos = self.display_indices.index(index)
                all_items = self.tree.get_children()
                if pos < len(all_items):
                    item = all_items[pos]
                    self.tree.selection_set(item)
                    self.tree.see(item)
            except ValueError:
                # not in filtered view
                pass
            self.lbl_status.config(text=f'Loaded: {os.path.basename(path)}')
            return True
        except Exception as e:
            messagebox.showerror('Error', f'Could not load {path}: {e}')
            return False

    def play_pause(self):
        if self.is_playing and not self.is_paused:
            # pause
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self.btn_play.config(text='Play')
            self.lbl_status.config(text='Paused')
            return

        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self.btn_play.config(text='Pause')
            if self.current_index is not None:
                self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")
            return

        # not playing -> start
        if not self.playlist:
            messagebox.showinfo('No tracks', 'Add some audio files first')
            return
        if self.current_index is None:
            # start with first visible track in filtered view
            if self.display_indices:
                self.current_index = self.display_indices[0]
            else:
                self.current_index = 0
        loaded = self._load(self.current_index)
        if not loaded:
            return
        try:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._playback_start_time = time.time()
            self._play_recorded = False
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")
        except Exception as e:
            messagebox.showerror('Playback error', str(e))

    def stop(self):
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self._playback_start_time = None
        self.btn_play.config(text='Play')
        self.lbl_status.config(text='Stopped')

    def next_track(self):
        if not self.playlist:
            return
        # If a genre filter is active, advance within the filtered list
        if self.genre_var.get() != 'All' and self.display_indices:
            try:
                pos = self.display_indices.index(self.current_index)
            except ValueError:
                pos = 0
            next_pos = (pos + 1) % len(self.display_indices)
            nxt = self.display_indices[next_pos]
        else:
            nxt = 0 if self.current_index is None else (self.current_index + 1) % len(self.playlist)
        self._load(nxt)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._playback_start_time = time.time()
        self._play_recorded = False
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def prev_track(self):
        if not self.playlist:
            return
        if self.genre_var.get() != 'All' and self.display_indices:
            try:
                pos = self.display_indices.index(self.current_index)
            except ValueError:
                pos = 0
            prev_pos = (pos - 1) % len(self.display_indices)
            prev = self.display_indices[prev_pos]
        else:
            prev = 0 if self.current_index is None else (self.current_index - 1) % len(self.playlist)
        self._load(prev)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._playback_start_time = time.time()
        self._play_recorded = False
        self.btn_play.config(text='Pause')
        self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def _on_volume(self, _=None):
        v = float(self.vol.get())
        # VLC volume is 0-100
        self.vlc_player.get_media_player().audio_set_volume(int(v * 100))

    def _on_right_click(self, ev):
        """Right-click on playlist to queue a song"""
        item = self.tree.identify('item', ev.x, ev.y)
        if not item:
            return
        
        # Get the index of this item in the tree
        all_items = self.tree.get_children()
        try:
            idx = all_items.index(item)
            playlist_idx = self.display_indices[idx]
        except (ValueError, IndexError):
            return
        
        # Show context menu
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Add to Queue', command=lambda: self._queue_track(playlist_idx))
        menu.post(ev.x_root, ev.y_root)

    def _on_queue_right_click(self, ev):
        """Right-click on queue to remove a song"""
        item = self.queue_tree.identify('item', ev.x, ev.y)
        if not item:
            return
        
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Remove', command=lambda: self._remove_from_queue(item))
        menu.post(ev.x_root, ev.y_root)

    def _queue_track(self, playlist_idx):
        """Add a track to the queue"""
        if playlist_idx in self.queue:
            messagebox.showinfo('Already Queued', 'Track is already in the queue')
            return
        
        self.queue.append(playlist_idx)
        self._update_queue_display()
        
        track_title = self.playlist[playlist_idx].get('title', self.playlist[playlist_idx]['basename'])
        messagebox.showinfo('Queued', f'"{track_title}" added to queue')

    def _remove_from_queue(self, item):
        """Remove a track from the queue"""
        all_items = self.queue_tree.get_children()
        try:
            idx = all_items.index(item)
            self.queue.pop(idx)
            self._update_queue_display()
        except (ValueError, IndexError):
            pass

    def _update_queue_display(self):
        """Update the queue panel display"""
        # Clear queue display
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        
        # Add queued tracks
        for playlist_idx in self.queue:
            if 0 <= playlist_idx < len(self.playlist):
                title = self.playlist[playlist_idx].get('title', self.playlist[playlist_idx]['basename'])
                self.queue_tree.insert('', 'end', values=(title,))

    def _on_select(self, ev):
        """Single-click on a track: analyze BPM if not yet known."""
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        all_items = self.tree.get_children()
        try:
            idx = all_items.index(item)
            playlist_idx = self.display_indices[idx]
        except (ValueError, IndexError):
            return
        entry = self.playlist[playlist_idx]
        if entry.get('bpm') is not None:
            self.lbl_status.config(text=f"{entry['title']}  •  {int(entry['bpm'])} BPM")
            return
        bpm = self._get_or_analyze_bpm(playlist_idx)
        if bpm is not None:
            # Update the BPM cell in the treeview
            current_vals = list(self.tree.item(item, 'values'))
            current_vals[3] = str(int(bpm))
            self.tree.item(item, values=current_vals)
            self.lbl_status.config(text=f"{entry['title']}  •  {int(bpm)} BPM")
        else:
            self.lbl_status.config(text=f"{entry['title']}  •  BPM: N/A")

    def _on_double(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        # sel is a tuple of item IDs; get the first selected item
        item = sel[0]
        # Find the index of this item in the tree's children
        all_items = self.tree.get_children()
        try:
            idx = all_items.index(item)
            playlist_idx = self.display_indices[idx]
        except Exception:
            return
        self.current_index = playlist_idx
        loaded = self._load(playlist_idx)
        if loaded:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._playback_start_time = time.time()
            self._play_recorded = False
            self.btn_play.config(text='Pause')
            self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

    def _poll(self):
        # Called periodically to detect end of track and auto-advance
        # Use the underlying media_player for reliable state checks
        mp = self.vlc_player.get_media_player()
        is_playing = mp.is_playing()

        # ── Play-tracking: record after PLAY_MIN_SECONDS ──
        if (self._playback_start_time is not None
                and not self._play_recorded
                and self.current_index is not None):
            elapsed = time.time() - self._playback_start_time
            if elapsed >= PLAY_MIN_SECONDS:
                path = self.playlist[self.current_index]['path']
                self._record_play(path)
                self._play_recorded = True
                # Refresh in-memory stats and tree row
                stats = self._get_track_stats(path)
                entry = self.playlist[self.current_index]
                entry['play_count'] = stats[0]
                entry['first_played'] = stats[1]
                entry['last_played'] = stats[2]
                self._apply_filter()

        # If nothing is playing, and we expected playing, advance to next
        if not is_playing and self._last_action == 'playing' and not self.is_paused:
            # Check if there's a queued track
            if self.queue:
                next_idx = self.queue.pop(0)
                self._update_queue_display()
                self._load(next_idx)
                self.vlc_player.play()
                self.is_playing = True
                self.is_paused = False
                self._last_action = 'playing'
                self._playback_start_time = time.time()
                self._play_recorded = False
                self.btn_play.config(text='Pause')
                self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")
            # Otherwise, auto-advance to next in playlist
            elif self.playlist:
                # advance respecting filter
                if self.genre_var.get() != 'All' and self.display_indices:
                    try:
                        pos = self.display_indices.index(self.current_index)
                    except ValueError:
                        pos = 0
                    next_pos = (pos + 1) % len(self.display_indices)
                    next_idx = self.display_indices[next_pos]
                else:
                    next_idx = (self.current_index + 1) % len(self.playlist) if self.current_index is not None else 0
                # if only one track, stop
                if len(self.playlist) == 1:
                    self.stop()
                else:
                    self._load(next_idx)
                    self.vlc_player.play()
                    self.is_playing = True
                    self.is_paused = False
                    self._last_action = 'playing'
                    self._playback_start_time = time.time()
                    self._play_recorded = False
                    self.btn_play.config(text='Pause')
                    self.lbl_status.config(text=f"Playing: {os.path.basename(self.playlist[self.current_index]['path'])}")

        self.after(500, self._poll)


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()

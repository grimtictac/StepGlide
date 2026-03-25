#!/usr/bin/env python3
"""A music player using CustomTkinter + VLC

Layout:
- Top bar: hamburger menu + now-playing title (big, bold)
- Left sidebar: genre groups treeview with settings gear
- Center: tag filter bar + track list
- Right: tag editor panel + volume slider
- Bottom: big play/stop buttons + scrub bar
"""
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import customtkinter as ctk

# ── Configuration ────────────────────────────────────────
PLAY_MIN_SECONDS = 5
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


class MusicPlayer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Python Music Player')
        self.geometry('1400x750')

        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')

        self.playlist = []
        self.display_indices = []
        self.genres = set()

        self.current_index = None
        self.is_playing = False
        self.is_paused = False
        self._last_action = None

        # Active filters
        self._active_genre = 'All'
        self._active_tag = 'All'

        # Genre groups: {group_name: [genre1, genre2, ...]}
        self._genre_groups = {}
        self._all_tags = set()

        # VLC
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        # Play tracking
        self._playback_start_time = None
        self._play_recorded = False
        self._init_database()

        self._build_ui()
        self._load_tracks_from_db()
        self.after(500, self._poll)

    # ── Database helpers ─────────────────────────────────

    def _init_database(self):
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
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT UNIQUE NOT NULL,
                value TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS genre_groups (
                id         INTEGER PRIMARY KEY,
                group_name TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS genre_group_members (
                group_id   INTEGER NOT NULL,
                genre      TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY(group_id) REFERENCES genre_groups(id),
                UNIQUE(group_id, genre)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS track_tags (
                track_id INTEGER NOT NULL,
                tag      TEXT NOT NULL,
                FOREIGN KEY(track_id) REFERENCES tracks(id),
                UNIQUE(track_id, tag)
            )
        """)
        con.commit()

        cur = con.execute("PRAGMA table_info(tracks)")
        columns = [row[1] for row in cur.fetchall()]
        if 'bpm' not in columns:
            con.execute("ALTER TABLE tracks ADD COLUMN bpm REAL")
            con.commit()
        if 'genre' not in columns:
            con.execute("ALTER TABLE tracks ADD COLUMN genre TEXT DEFAULT 'Unknown'")
            con.commit()
        if 'comment' not in columns:
            con.execute("ALTER TABLE tracks ADD COLUMN comment TEXT DEFAULT ''")
            con.commit()

        # One-time backfill
        cur = con.execute("SELECT COUNT(*) FROM tracks WHERE genre != 'Unknown'")
        has_real_genres = cur.fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM tracks")
        total_tracks = cur.fetchone()[0]
        if has_real_genres == 0 and total_tracks > 0 and MutagenFile is not None:
            cur = con.execute("SELECT id, file_path, title FROM tracks")
            for track_id, fpath, db_title in cur.fetchall():
                genre = 'Unknown'
                comment = ''
                title = db_title
                try:
                    tags = MutagenFile(fpath, easy=True)
                    if tags is not None:
                        title = tags.get('title', [db_title or os.path.basename(fpath)])[0]
                        genre = tags.get('genre', ['Unknown'])[0]
                        c = tags.get('comment', [''])[0]
                        comment = str(c) if c else ''
                except Exception:
                    pass
                con.execute(
                    "UPDATE tracks SET genre = ?, comment = ?, title = ? WHERE id = ?",
                    (genre, comment, title, track_id)
                )
            con.commit()
        con.close()
        self._load_genre_groups()

    def _load_genre_groups(self):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT id, group_name FROM genre_groups ORDER BY sort_order, group_name")
        groups = cur.fetchall()
        self._genre_groups = {}
        for gid, gname in groups:
            cur.execute("SELECT genre FROM genre_group_members WHERE group_id = ? ORDER BY sort_order, genre", (gid,))
            self._genre_groups[gname] = [r[0] for r in cur.fetchall()]
        con.close()

    def _save_genre_groups(self):
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM genre_group_members")
        con.execute("DELETE FROM genre_groups")
        for sort_order, (gname, members) in enumerate(self._genre_groups.items()):
            cur = con.execute("INSERT INTO genre_groups (group_name, sort_order) VALUES (?, ?)", (gname, sort_order))
            gid = cur.lastrowid
            for m_order, genre in enumerate(members):
                con.execute("INSERT INTO genre_group_members (group_id, genre, sort_order) VALUES (?, ?, ?)",
                            (gid, genre, m_order))
        con.commit()
        con.close()

    def _load_tracks_from_db(self):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute(
            "SELECT file_path, title, play_count, first_played, last_played, "
            "file_created, bpm, genre, comment FROM tracks ORDER BY title"
        )
        rows = cur.fetchall()

        cur.execute("SELECT t.file_path, tt.tag FROM track_tags tt JOIN tracks t ON t.id = tt.track_id")
        tag_rows = cur.fetchall()
        con.close()

        tags_by_path = {}
        for fpath, tag in tag_rows:
            tags_by_path.setdefault(fpath, []).append(tag)
            self._all_tags.add(tag)

        if not rows:
            return

        seen = set()
        for (path, db_title, play_count, first_played, last_played,
             file_created, bpm, genre, comment) in rows:
            if path in seen:
                continue
            seen.add(path)
            entry = {
                'path': path,
                'title': db_title or os.path.basename(path),
                'basename': os.path.basename(path),
                'genre': genre or 'Unknown',
                'comment': comment or '',
                'bpm': bpm,
                'play_count': play_count or 0,
                'first_played': first_played,
                'last_played': last_played,
                'file_created': file_created,
                'tags': tags_by_path.get(path, []),
            }
            self.playlist.append(entry)
            self.genres.add(entry['genre'])

        self._build_genre_list()
        self._apply_filter()
        self._build_tag_bar()
        self.lbl_now_playing.configure(text=f'\u266b  {len(self.playlist)} tracks loaded')

    def _ensure_track_in_db(self, path, title='', genre='Unknown', comment=''):
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
                "INSERT INTO tracks (file_path, title, file_created, genre, comment) VALUES (?, ?, ?, ?, ?)",
                (path, title, file_created, genre, comment)
            )
            con.commit()
            con.close()
            return (0, None, None, file_created)
        con.close()
        return row

    def _record_play(self, path):
        now = datetime.now(tz=timezone.utc).isoformat()
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("""
            UPDATE tracks
               SET play_count  = play_count + 1,
                   first_played = COALESCE(first_played, ?),
                   last_played  = ?
             WHERE file_path = ?
        """, (now, now, path))
        cur.execute("SELECT id FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row:
            cur.execute("INSERT INTO play_history (track_id, played_at) VALUES (?, ?)", (row[0], now))
        con.commit()
        con.close()

    def _get_track_stats(self, path):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        return row if row else (0, None, None, None)

    @staticmethod
    def _format_ts(iso_str, relative=False):
        if not iso_str:
            return 'Never'
        try:
            dt = datetime.fromisoformat(iso_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz=None)
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
            return f'{secs // 60} min ago'
        if secs < 86400:
            return f'{secs // 3600}h ago'
        days = secs // 86400
        if days == 1:
            return 'Yesterday'
        if days < 7:
            return f'{days}d ago'
        return dt.strftime('%b %d, %Y')

    def _analyze_bpm(self, path):
        if aubio is None:
            return None
        tmp_wav = None
        try:
            analyse_path = path
            if not path.lower().endswith('.wav'):
                tmp_wav = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                tmp_wav.close()
                ret = subprocess.run(
                    ['ffmpeg', '-y', '-i', path, '-ac', '1', '-ar', '44100', '-sample_fmt', 's16', tmp_wav.name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
                )
                if ret.returncode != 0:
                    return None
                analyse_path = tmp_wav.name
            win_s = 1024
            hop_s = 512
            src = aubio.source(analyse_path, 0, hop_s)
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
        finally:
            if tmp_wav is not None:
                try:
                    os.unlink(tmp_wav.name)
                except OSError:
                    pass

    def _get_or_analyze_bpm(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        path = entry['path']
        if entry.get('bpm') is not None:
            return entry['bpm']
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT bpm FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row and row[0] is not None:
            entry['bpm'] = row[0]
            return row[0]
        self.lbl_now_playing.configure(text='\u266b  Analyzing BPM\u2026')
        self.update_idletasks()
        bpm = self._analyze_bpm(path)
        if bpm is not None:
            entry['bpm'] = bpm
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET bpm = ? WHERE file_path = ?", (bpm, path))
            con.commit()
            con.close()
        return bpm

    # ── Tag helpers ──────────────────────────────────────

    def _get_track_id(self, path):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT id FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        return row[0] if row else None

    def _add_tag_to_track(self, playlist_idx, tag):
        entry = self.playlist[playlist_idx]
        tag = tag.strip().lower()
        if not tag:
            return
        if tag in entry.get('tags', []):
            return
        entry.setdefault('tags', []).append(tag)
        self._all_tags.add(tag)
        track_id = self._get_track_id(entry['path'])
        if track_id:
            con = sqlite3.connect(DB_PATH)
            con.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)", (track_id, tag))
            con.commit()
            con.close()

    def _remove_tag_from_track(self, playlist_idx, tag):
        entry = self.playlist[playlist_idx]
        if tag in entry.get('tags', []):
            entry['tags'].remove(tag)
        track_id = self._get_track_id(entry['path'])
        if track_id:
            con = sqlite3.connect(DB_PATH)
            con.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?", (track_id, tag))
            con.commit()
            con.close()

    # ── Build UI ─────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('Treeview',
                        background='#2b2b2b',
                        foreground='#dce4ee',
                        fieldbackground='#2b2b2b',
                        borderwidth=0,
                        rowheight=34,
                        font=('Segoe UI', 10))
        style.configure('Treeview.Heading',
                        background='#3b3b3b',
                        foreground='#dce4ee',
                        font=('Segoe UI', 10, 'bold'),
                        borderwidth=0)
        style.map('Treeview',
                  background=[('selected', '#1f6aa5')],
                  foreground=[('selected', '#ffffff')])
        style.map('Treeview.Heading',
                  background=[('active', '#4a4a4a')])
        style.configure('Genre.Treeview',
                        background='#2b2b2b',
                        foreground='#dce4ee',
                        fieldbackground='#2b2b2b',
                        borderwidth=0,
                        rowheight=34,
                        font=('Segoe UI', 11))
        style.map('Genre.Treeview',
                  background=[('selected', '#1f6aa5')],
                  foreground=[('selected', '#ffffff')])

        # ═══ TOP BAR ═══
        top_bar = ctk.CTkFrame(self, height=50, fg_color='#1a1a2e')
        top_bar.pack(fill='x')
        top_bar.pack_propagate(False)

        self.btn_menu = ctk.CTkButton(top_bar, text='\u2630', width=45, height=36,
                                      font=ctk.CTkFont(size=20), command=self._show_menu)
        self.btn_menu.pack(side='left', padx=(10, 6), pady=7)

        self.lbl_now_playing = ctk.CTkLabel(top_bar, text='\u266b  Not Playing',
                                            font=ctk.CTkFont(size=20, weight='bold'))
        self.lbl_now_playing.pack(side='left', fill='x', expand=True, padx=10)

        self.load_progress = ctk.CTkProgressBar(top_bar, mode='determinate', width=200)
        self.load_progress.set(0)
        self.lbl_load = ctk.CTkLabel(top_bar, text='', font=ctk.CTkFont(size=10))

        # ═══ SCRUB BAR (under Now Playing) ═══
        scrub_frame = ctk.CTkFrame(self, fg_color='#1a1a2e')
        scrub_frame.pack(fill='x', padx=0)

        scrub_inner = ctk.CTkFrame(scrub_frame, fg_color='transparent')
        scrub_inner.pack(fill='x', padx=20, pady=(2, 6))

        self.lbl_time_cur = ctk.CTkLabel(scrub_inner, text='0:00', font=ctk.CTkFont(size=12), width=50)
        self.lbl_time_cur.pack(side='left')

        self._scrub_var = tk.DoubleVar(value=0)
        self._user_scrubbing = False
        self.scrub_slider = ctk.CTkSlider(scrub_inner, from_=0, to=1.0, variable=self._scrub_var,
                                          command=self._on_scrub, height=20,
                                          button_color='#00bcd4', button_hover_color='#26c6da',
                                          progress_color='#00bcd4')
        self.scrub_slider.pack(side='left', fill='x', expand=True, padx=6)
        self.scrub_slider.set(0)
        self.scrub_slider.bind('<ButtonPress-1>', lambda e: setattr(self, '_user_scrubbing', True))
        self.scrub_slider.bind('<ButtonRelease-1>', self._on_scrub_release)

        self.lbl_time_total = ctk.CTkLabel(scrub_inner, text='0:00', font=ctk.CTkFont(size=12), width=50)
        self.lbl_time_total.pack(side='left')

        # ═══ PLAY CONTROLS (under scrub bar) ═══
        controls_frame = ctk.CTkFrame(self, fg_color='#1a1a2e')
        controls_frame.pack(fill='x')

        btn_row = ctk.CTkFrame(controls_frame, fg_color='transparent')
        btn_row.pack(fill='x', padx=20, pady=(6, 10))
        btn_row.columnconfigure(0, weight=2)
        btn_row.columnconfigure(1, weight=1)

        self.btn_play = ctk.CTkButton(btn_row, text='\u25b6', height=50,
                                      font=ctk.CTkFont(size=28), command=self.play_pause,
                                      fg_color='#1f6aa5', hover_color='#1a5a8a')
        self.btn_play.grid(row=0, column=0, sticky='ew', padx=(0, 3))

        self.btn_stop = ctk.CTkButton(btn_row, text='\u23f9', height=50,
                                      font=ctk.CTkFont(size=28), command=self.stop,
                                      fg_color='#c0392b', hover_color='#e74c3c')
        self.btn_stop.grid(row=0, column=1, sticky='ew', padx=(3, 0))

        # ═══ MIDDLE AREA ═══
        middle = ctk.CTkFrame(self, fg_color='transparent')
        middle.pack(fill='both', expand=True, padx=10, pady=(14, 4))

        # Genre sidebar
        genre_panel = ctk.CTkFrame(middle, width=140)
        genre_panel.pack(side='left', fill='y', padx=(0, 6))
        genre_panel.pack_propagate(False)

        genre_header = ctk.CTkFrame(genre_panel, fg_color='transparent')
        genre_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(genre_header, text='Genre', font=ctk.CTkFont(size=13, weight='bold')).pack(side='left')
        ctk.CTkButton(genre_header, text='\u2699', width=30, height=26,
                      font=ctk.CTkFont(size=14), command=self._open_genre_settings).pack(side='right')

        self.genre_tree = ttk.Treeview(genre_panel, style='Genre.Treeview',
                                       columns=('Genre',), show='tree', selectmode='browse')
        self.genre_tree.column('#0', width=130)
        self.genre_tree.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self.genre_tree.bind('<<TreeviewSelect>>', self._on_genre_select)

        # Center: track list
        center = ctk.CTkFrame(middle, fg_color='transparent')
        center.pack(side='left', fill='both', expand=True)

        tree_frame = ctk.CTkFrame(center, fg_color='transparent')
        tree_frame.pack(fill='both', expand=True)

        self.tree = ttk.Treeview(tree_frame,
                                 columns=('Title', 'Comment', 'Tags', 'BPM', 'Plays',
                                          'First Played', 'Last Played', 'File Created'),
                                 show='headings', height=18)
        self.tree.column('Title', width=220, anchor='w')
        self.tree.column('Comment', width=120, anchor='w')
        self.tree.column('Tags', width=120, anchor='w')
        self.tree.column('BPM', width=50, anchor='center')
        self.tree.column('Plays', width=50, anchor='center')
        self.tree.column('First Played', width=100, anchor='w')
        self.tree.column('Last Played', width=100, anchor='w')
        self.tree.column('File Created', width=100, anchor='w')
        self.tree.heading('Title', text='Title')
        self.tree.heading('Comment', text='Comment')
        self.tree.heading('Tags', text='Tags')
        self.tree.heading('BPM', text='BPM')
        self.tree.heading('Plays', text='Plays')
        self.tree.heading('First Played', text='First Played')
        self.tree.heading('Last Played', text='Last Played')
        self.tree.heading('File Created', text='File Created')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        sb = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.config(yscrollcommand=sb.set)

        # Tag editor panel
        tag_panel = ctk.CTkFrame(middle, width=170)
        tag_panel.pack(side='left', fill='y', padx=(6, 0))
        tag_panel.pack_propagate(False)

        ctk.CTkLabel(tag_panel, text='Tags', font=ctk.CTkFont(size=13, weight='bold')).pack(pady=(6, 2))
        self.lbl_tag_track = ctk.CTkLabel(tag_panel, text='Select a track',
                                          font=ctk.CTkFont(size=10), wraplength=150,
                                          text_color='#888888')
        self.lbl_tag_track.pack(padx=6, pady=(0, 4))

        self.tag_pills_frame = ctk.CTkScrollableFrame(tag_panel, height=100, fg_color='transparent')
        self.tag_pills_frame.pack(fill='x', padx=6, pady=2)

        ctk.CTkLabel(tag_panel, text='Quick add:', font=ctk.CTkFont(size=10),
                     text_color='#888888').pack(padx=6, pady=(8, 2), anchor='w')

        self.tag_quick_frame = ctk.CTkScrollableFrame(tag_panel, fg_color='transparent')
        self.tag_quick_frame.pack(fill='both', expand=True, padx=6, pady=2)

        ctk.CTkButton(tag_panel, text='+ new tag\u2026', height=28,
                      font=ctk.CTkFont(size=11), command=self._add_new_tag).pack(fill='x', padx=6, pady=(4, 8))

        # Volume panel
        vol_panel = ctk.CTkFrame(middle, width=60)
        vol_panel.pack(side='left', fill='y', padx=(6, 0))
        vol_panel.pack_propagate(False)

        self.btn_mute = ctk.CTkButton(vol_panel, text='\U0001f50a', width=40, height=30,
                                      font=ctk.CTkFont(size=16), fg_color='transparent',
                                      command=self._toggle_mute)
        self.btn_mute.pack(pady=(8, 4))

        self.vol = tk.DoubleVar(value=0.8)
        self._muted = False
        self._pre_mute_vol = 0.8
        self.vol_slider = ctk.CTkSlider(vol_panel, from_=0.0, to=1.0, variable=self.vol,
                                        orientation='vertical', command=self._on_volume,
                                        height=200,
                                        button_color='#00bcd4', button_hover_color='#26c6da',
                                        progress_color='#00bcd4')
        self.vol_slider.pack(fill='y', expand=True, padx=10, pady=4)

        self.lbl_vol_pct = ctk.CTkLabel(vol_panel, text='80%', font=ctk.CTkFont(size=10))
        self.lbl_vol_pct.pack(pady=(4, 8))

        self._on_volume()

        # ═══ BOTTOM PANEL (tag bar) ═══
        self.tag_bar_frame = ctk.CTkFrame(self, height=36, fg_color='#2b2b2b', corner_radius=6)
        self.tag_bar_frame.pack(fill='x', padx=10, pady=(4, 8))
        self.tag_bar_frame.pack_propagate(False)
        self._tag_buttons = []

    # ── Menu ─────────────────────────────────────────────

    def _show_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Add Files\u2026', command=self.add_files)
        menu.add_command(label='Add Folder\u2026', command=self.add_folder)
        x = self.btn_menu.winfo_rootx()
        y = self.btn_menu.winfo_rooty() + self.btn_menu.winfo_height()
        menu.post(x, y)

    # ── Genre sidebar ────────────────────────────────────

    def _build_genre_list(self):
        self.genre_tree.delete(*self.genre_tree.get_children())
        self.genre_tree.insert('', 'end', iid='__all__', text='All')

        grouped_genres = set()
        for gname, members in self._genre_groups.items():
            gnode = self.genre_tree.insert('', 'end', iid=f'__grp__{gname}', text=gname)
            for genre in members:
                self.genre_tree.insert(gnode, 'end', iid=f'__genre__{genre}', text=genre)
                grouped_genres.add(genre)
            self.genre_tree.item(gnode, open=True)

        ungrouped = sorted(g for g in self.genres if g and g not in grouped_genres)
        for genre in ungrouped:
            self.genre_tree.insert('', 'end', iid=f'__genre__{genre}', text=genre)

        self.genre_tree.selection_set('__all__')

    def _on_genre_select(self, ev):
        sel = self.genre_tree.selection()
        if not sel:
            return
        item_id = sel[0]
        if item_id == '__all__':
            self._active_genre = 'All'
        elif item_id.startswith('__grp__'):
            self._active_genre = item_id[len('__grp__'):]
        elif item_id.startswith('__genre__'):
            self._active_genre = item_id[len('__genre__'):]
        else:
            self._active_genre = 'All'
        self._active_tag = 'All'
        self._apply_filter()
        self._build_tag_bar()

    def _get_genres_for_filter(self):
        if self._active_genre == 'All':
            return None
        if self._active_genre in self._genre_groups:
            return set(self._genre_groups[self._active_genre])
        return {self._active_genre}

    # ── Tag filter bar ───────────────────────────────────

    def _build_tag_bar(self):
        for btn in self._tag_buttons:
            btn.destroy()
        self._tag_buttons = []

        visible_tags = set()
        for idx in self.display_indices:
            for tag in self.playlist[idx].get('tags', []):
                visible_tags.add(tag)

        if not visible_tags:
            lbl = ctk.CTkLabel(self.tag_bar_frame, text='  No tags in current view',
                               font=ctk.CTkFont(size=10), text_color='#666666')
            lbl.pack(side='left', padx=6)
            self._tag_buttons.append(lbl)
            return

        btn_all = ctk.CTkButton(self.tag_bar_frame, text='All', height=26, width=50,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if self._active_tag == 'All' else 'transparent',
                                border_width=1, border_color='#555555',
                                command=lambda: self._on_tag_filter('All'))
        btn_all.pack(side='left', padx=(6, 2), pady=5)
        self._tag_buttons.append(btn_all)

        for tag in sorted(visible_tags):
            is_active = self._active_tag == tag
            btn = ctk.CTkButton(self.tag_bar_frame, text=tag, height=26,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if is_active else 'transparent',
                                border_width=1, border_color='#555555',
                                command=lambda t=tag: self._on_tag_filter(t))
            btn.pack(side='left', padx=2, pady=5)
            self._tag_buttons.append(btn)

    def _on_tag_filter(self, tag):
        self._active_tag = tag
        self._apply_filter()
        self._build_tag_bar()

    # ── Tag editor panel ─────────────────────────────────

    def _update_tag_editor(self):
        for w in self.tag_pills_frame.winfo_children():
            w.destroy()
        for w in self.tag_quick_frame.winfo_children():
            w.destroy()

        sel = self.tree.selection()
        if not sel:
            self.lbl_tag_track.configure(text='Select a track')
            return

        selected_indices = []
        all_items = self.tree.get_children()
        for item in sel:
            try:
                idx = list(all_items).index(item)
                selected_indices.append(self.display_indices[idx])
            except (ValueError, IndexError):
                pass

        if not selected_indices:
            self.lbl_tag_track.configure(text='Select a track')
            return

        if len(selected_indices) == 1:
            entry = self.playlist[selected_indices[0]]
            self.lbl_tag_track.configure(text=entry['title'][:30])
        else:
            self.lbl_tag_track.configure(text=f'{len(selected_indices)} tracks')

        if len(selected_indices) == 1:
            current_tags = set(self.playlist[selected_indices[0]].get('tags', []))
        else:
            tag_sets = [set(self.playlist[i].get('tags', [])) for i in selected_indices]
            current_tags = tag_sets[0].intersection(*tag_sets[1:]) if tag_sets else set()

        for tag in sorted(current_tags):
            pill = ctk.CTkFrame(self.tag_pills_frame, fg_color='#1f6aa5', corner_radius=12)
            pill.pack(fill='x', pady=1)
            ctk.CTkLabel(pill, text=tag, font=ctk.CTkFont(size=11)).pack(side='left', padx=(8, 2), pady=2)
            ctk.CTkButton(pill, text='\u00d7', width=20, height=20, fg_color='transparent',
                          font=ctk.CTkFont(size=12),
                          command=lambda t=tag: self._remove_tag_click(selected_indices, t)).pack(side='right', padx=2, pady=2)

        for tag in sorted(self._all_tags):
            is_applied = tag in current_tags
            btn = ctk.CTkButton(self.tag_quick_frame, text=tag, height=26,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if is_applied else 'transparent',
                                border_width=1, border_color='#555555',
                                command=lambda t=tag, applied=is_applied: self._toggle_tag_click(selected_indices, t, applied))
            btn.pack(fill='x', pady=1)

    def _toggle_tag_click(self, indices, tag, currently_applied):
        for idx in indices:
            if currently_applied:
                self._remove_tag_from_track(idx, tag)
            else:
                self._add_tag_to_track(idx, tag)
        self._apply_filter()
        self._build_tag_bar()
        self._update_tag_editor()

    def _remove_tag_click(self, indices, tag):
        for idx in indices:
            self._remove_tag_from_track(idx, tag)
        self._apply_filter()
        self._build_tag_bar()
        self._update_tag_editor()

    def _add_new_tag(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('No selection', 'Select a track first')
            return
        selected_indices = []
        all_items = self.tree.get_children()
        for item in sel:
            try:
                idx = list(all_items).index(item)
                selected_indices.append(self.display_indices[idx])
            except (ValueError, IndexError):
                pass
        if not selected_indices:
            return
        tag = simpledialog.askstring('New Tag', 'Enter tag name:', parent=self)
        if tag and tag.strip():
            tag = tag.strip().lower()
            for idx in selected_indices:
                self._add_tag_to_track(idx, tag)
            self._apply_filter()
            self._build_tag_bar()
            self._update_tag_editor()

    # ── Genre settings dialog ────────────────────────────

    def _open_genre_settings(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title('Genre Settings')
        dialog.geometry('500x600')
        dialog.transient(self)

        # Delay grab_set to avoid CTkToplevel rendering blank
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text='Genre Groups', font=ctk.CTkFont(size=16, weight='bold')).pack(pady=(10, 6))
        ctk.CTkLabel(dialog, text='Create groups and assign genres to them.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 10))

        working_groups = {k: list(v) for k, v in self._genre_groups.items()}
        all_genres = sorted(self.genres)

        content = ctk.CTkScrollableFrame(dialog)
        content.pack(fill='both', expand=True, padx=10, pady=6)

        # Track checkbox variables: cb_vars[group_name][genre] = BooleanVar
        cb_vars = {}

        def rebuild_dialog():
            """Full rebuild — only called for structural changes (add/delete/rename group)."""
            for w in content.winfo_children():
                w.destroy()
            cb_vars.clear()

            for gname in list(working_groups.keys()):
                gf = ctk.CTkFrame(content, fg_color='#2b2b2b', corner_radius=8)
                gf.pack(fill='x', pady=4)

                header = ctk.CTkFrame(gf, fg_color='transparent')
                header.pack(fill='x', padx=8, pady=(6, 2))
                ctk.CTkLabel(header, text=gname, font=ctk.CTkFont(size=13, weight='bold')).pack(side='left')
                ctk.CTkButton(header, text='\U0001f5d1', width=30, height=24, fg_color='transparent',
                              command=lambda g=gname: delete_group(g)).pack(side='right')
                ctk.CTkButton(header, text='\u270f', width=30, height=24, fg_color='transparent',
                              command=lambda g=gname: rename_group(g)).pack(side='right')

                cb_vars[gname] = {}
                for genre in all_genres:
                    is_member = genre in working_groups[gname]
                    var = tk.BooleanVar(value=is_member)
                    cb_vars[gname][genre] = var
                    cb = ctk.CTkCheckBox(gf, text=genre, variable=var,
                                         font=ctk.CTkFont(size=11),
                                         command=lambda g=gname, gr=genre, v=var: toggle_genre(g, gr, v))
                    cb.pack(anchor='w', padx=16, pady=1)

            _rebuild_ungrouped()

        def _rebuild_ungrouped():
            """Refresh just the ungrouped section at the bottom."""
            # Remove existing ungrouped frame if present
            for w in content.winfo_children():
                if hasattr(w, '_is_ungrouped'):
                    w.destroy()

            assigned = set()
            for members in working_groups.values():
                assigned.update(members)
            ungrouped = [g for g in all_genres if g not in assigned]
            if ungrouped:
                uf = ctk.CTkFrame(content, fg_color='#222222', corner_radius=8)
                uf._is_ungrouped = True
                uf.pack(fill='x', pady=4)
                ctk.CTkLabel(uf, text='Ungrouped', font=ctk.CTkFont(size=13, weight='bold'),
                             text_color='#888888').pack(anchor='w', padx=8, pady=(6, 2))
                for genre in ungrouped:
                    ctk.CTkLabel(uf, text=f'  {genre}', font=ctk.CTkFont(size=11),
                                 text_color='#666666').pack(anchor='w', padx=16, pady=1)

        def toggle_genre(group, genre, var):
            """Toggle a genre in/out of a group — update variables only, no rebuild."""
            if var.get():
                # Remove from other groups (uncheck their vars)
                for g in working_groups:
                    if genre in working_groups[g]:
                        working_groups[g].remove(genre)
                        if g in cb_vars and genre in cb_vars[g]:
                            cb_vars[g][genre].set(False)
                working_groups[group].append(genre)
            else:
                if genre in working_groups[group]:
                    working_groups[group].remove(genre)
            _rebuild_ungrouped()

        def delete_group(gname):
            del working_groups[gname]
            rebuild_dialog()

        def rename_group(gname):
            new_name = simpledialog.askstring('Rename Group', 'New name:', initialvalue=gname, parent=dialog)
            if new_name and new_name.strip() and new_name.strip() != gname:
                working_groups[new_name.strip()] = working_groups.pop(gname)
                rebuild_dialog()

        def add_group():
            name = simpledialog.askstring('New Group', 'Group name:', parent=dialog)
            if name and name.strip():
                name = name.strip()
                if name not in working_groups:
                    working_groups[name] = []
                    rebuild_dialog()

        rebuild_dialog()

        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=10, pady=10)
        ctk.CTkButton(btn_row, text='+ New Group', command=add_group).pack(side='left', padx=4)
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)

        def save_and_close():
            self._genre_groups = working_groups
            self._save_genre_groups()
            self._build_genre_list()
            self._active_genre = 'All'
            self._apply_filter()
            self._build_tag_bar()
            dialog.destroy()

        ctk.CTkButton(btn_row, text='Save', command=save_and_close).pack(side='right', padx=4)

    # ── Filter logic ─────────────────────────────────────

    def _apply_filter(self):
        # Remember which playlist indices were selected
        prev_selected = set()
        all_items = self.tree.get_children()
        for item in self.tree.selection():
            try:
                pos = list(all_items).index(item)
                prev_selected.add(self.display_indices[pos])
            except (ValueError, IndexError):
                pass

        for item in all_items:
            self.tree.delete(item)
        self.display_indices = []

        genre_filter = self._get_genres_for_filter()

        for idx, entry in enumerate(self.playlist):
            if genre_filter is not None:
                if entry.get('genre') not in genre_filter:
                    continue
            if self._active_tag != 'All':
                if self._active_tag not in entry.get('tags', []):
                    continue

            title = entry.get('title', entry['basename'])
            comment = entry.get('comment', '')
            tags_str = ', '.join(sorted(entry.get('tags', []))) if entry.get('tags') else '\u2014'
            bpm = entry.get('bpm')
            bpm_str = str(int(bpm)) if bpm else '\u2014'
            plays = entry.get('play_count', 0)
            first_p = self._format_ts(entry.get('first_played'), relative=False)
            last_p = self._format_ts(entry.get('last_played'), relative=True)
            file_c = self._format_ts(entry.get('file_created'), relative=False)
            self.tree.insert('', 'end', values=(title, comment, tags_str, bpm_str, plays, first_p, last_p, file_c))
            self.display_indices.append(idx)

        # Restore selection
        if prev_selected:
            new_items = self.tree.get_children()
            to_select = []
            for pos, pl_idx in enumerate(self.display_indices):
                if pl_idx in prev_selected and pos < len(new_items):
                    to_select.append(new_items[pos])
            if to_select:
                self.tree.selection_set(*to_select)
                self.tree.see(to_select[0])

    # ── File management ──────────────────────────────────

    def add_files(self):
        files = filedialog.askopenfilenames(title='Select audio files',
                                            filetypes=[('Audio', '*.mp3 *.wav *.ogg *.flac'), ('All files', '*.*')])
        for f in files:
            self._add_path(f)
        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._build_genre_list()
        self._apply_filter()
        self._build_tag_bar()

    def add_folder(self):
        folder = filedialog.askdirectory(title='Select folder')
        if not folder:
            return
        exts = ('.mp3', '.wav', '.ogg', '.flac')

        self.lbl_now_playing.configure(text='\u266b  Scanning folder\u2026')
        self.update_idletasks()
        audio_files = []
        for root, _, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            messagebox.showinfo('No files', 'No supported audio files found in folder')
            self.lbl_now_playing.configure(text='\u266b  Not Playing')
            return

        self.load_progress.set(0)
        self.load_progress.pack(side='right', padx=(0, 10), pady=12)
        self.lbl_load.pack(side='right', padx=4, pady=12)

        added = 0
        for i, path in enumerate(audio_files, 1):
            if self._add_path(path):
                added += 1
            self.load_progress.set(i / total)
            self.lbl_load.configure(text=f'{i}/{total}')
            if i % 25 == 0 or i == total:
                self.update_idletasks()

        self.load_progress.pack_forget()
        self.lbl_load.pack_forget()

        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._build_genre_list()
        self._apply_filter()
        self._build_tag_bar()
        self.lbl_now_playing.configure(text=f'\u266b  Added {added} tracks')

    def _add_path(self, path):
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
                    comment_val = tags.get('comment', [''])[0]
                    comment = str(comment_val) if comment_val else ''
            except Exception:
                pass
        entry = {'path': path, 'title': title, 'basename': os.path.basename(path),
                 'genre': genre, 'comment': comment, 'tags': []}
        self.playlist.append(entry)
        self.genres.add(genre)
        stats = self._ensure_track_in_db(path, title, genre, comment)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        return True

    # ── Playback ─────────────────────────────────────────

    def _load(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self.playlist[index]['path']
        try:
            self._playback_start_time = None
            self._play_recorded = False
            media = self.vlc_instance.media_new(path)
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            self.current_index = index
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
                pass
            return True
        except Exception as e:
            messagebox.showerror('Error', f'Could not load {path}: {e}')
            return False

    def _update_now_playing(self, text=None):
        if text:
            self.lbl_now_playing.configure(text=f'\u266b  {text}')
        elif self.current_index is not None:
            entry = self.playlist[self.current_index]
            title = entry.get('title', entry['basename'])
            genre = entry.get('genre', '')
            display = f'{title}  \u2014  {genre}' if genre and genre != 'Unknown' else title
            self.lbl_now_playing.configure(text=f'\u266b  {display}')
        else:
            self.lbl_now_playing.configure(text='\u266b  Not Playing')

    def play_pause(self):
        if self.is_playing and not self.is_paused:
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self.btn_play.configure(text='\u25b6', fg_color='#1f6aa5', hover_color='#1a5a8a')
            self._update_now_playing('Paused')
            return

        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()
            return

        if not self.playlist:
            messagebox.showinfo('No tracks', 'Add some audio files first')
            return
        if self.current_index is None:
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
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()
        except Exception as e:
            messagebox.showerror('Playback error', str(e))

    def stop(self):
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self._playback_start_time = None
        self.btn_play.configure(text='\u25b6', fg_color='#1f6aa5', hover_color='#1a5a8a')
        self.scrub_slider.set(0)
        self.lbl_time_cur.configure(text='0:00')
        self.lbl_time_total.configure(text='0:00')
        self._update_now_playing('Stopped')

    def _next_track(self):
        if not self.playlist:
            return
        if self.display_indices:
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
        self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
        self._update_now_playing()

    # ── Scrub / Volume ───────────────────────────────────

    @staticmethod
    def _format_time(ms):
        if ms <= 0:
            return '0:00'
        secs = int(ms / 1000)
        m, s = divmod(secs, 60)
        return f'{m}:{s:02d}'

    def _on_scrub(self, value):
        mp = self.vlc_player.get_media_player()
        length = mp.get_length()
        if length > 0:
            pos_ms = int(float(value) * length)
            self.lbl_time_cur.configure(text=self._format_time(pos_ms))

    def _on_scrub_release(self, ev):
        self._user_scrubbing = False
        mp = self.vlc_player.get_media_player()
        length = mp.get_length()
        if length > 0 and (self.is_playing or self.is_paused):
            mp.set_position(float(self._scrub_var.get()))

    def _on_volume(self, _=None):
        v = float(self.vol.get())
        self.vlc_player.get_media_player().audio_set_volume(int(v * 100))
        self.lbl_vol_pct.configure(text=f'{int(v * 100)}%')
        if v > 0:
            self._muted = False
            self.btn_mute.configure(text='\U0001f50a')

    def _toggle_mute(self):
        if self._muted:
            self.vol.set(self._pre_mute_vol)
            self._on_volume()
            self._muted = False
            self.btn_mute.configure(text='\U0001f50a')
        else:
            self._pre_mute_vol = float(self.vol.get())
            self.vol.set(0)
            self._on_volume()
            self._muted = True
            self.btn_mute.configure(text='\U0001f507')

    # ── Track selection events ───────────────────────────

    def _on_select(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        all_items = self.tree.get_children()
        try:
            idx = list(all_items).index(item)
            playlist_idx = self.display_indices[idx]
        except (ValueError, IndexError):
            return

        self._update_tag_editor()

        entry = self.playlist[playlist_idx]
        if entry.get('bpm') is not None:
            return
        bpm = self._get_or_analyze_bpm(playlist_idx)
        if bpm is not None:
            current_vals = list(self.tree.item(item, 'values'))
            current_vals[3] = str(int(bpm))
            self.tree.item(item, values=current_vals)

    def _on_double(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        all_items = self.tree.get_children()
        try:
            idx = list(all_items).index(item)
            playlist_idx = self.display_indices[idx]
        except Exception:
            return
        self._last_action = 'switching'
        self.vlc_player.stop()
        self.current_index = playlist_idx
        loaded = self._load(playlist_idx)
        if loaded:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._playback_start_time = time.time()
            self._play_recorded = False
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()

    # ── Poll ─────────────────────────────────────────────

    def _poll(self):
        mp = self.vlc_player.get_media_player()
        is_playing = mp.is_playing()

        if not self._user_scrubbing:
            length = mp.get_length()
            pos = mp.get_position()
            if length > 0 and pos >= 0:
                self.scrub_slider.set(pos)
                self.lbl_time_cur.configure(text=self._format_time(int(pos * length)))
                self.lbl_time_total.configure(text=self._format_time(length))
            elif not is_playing and not self.is_paused:
                self.scrub_slider.set(0)
                self.lbl_time_cur.configure(text='0:00')
                self.lbl_time_total.configure(text='0:00')

        if (self._playback_start_time is not None
                and not self._play_recorded
                and self.current_index is not None):
            elapsed = time.time() - self._playback_start_time
            if elapsed >= PLAY_MIN_SECONDS:
                path = self.playlist[self.current_index]['path']
                self._record_play(path)
                self._play_recorded = True
                stats = self._get_track_stats(path)
                entry = self.playlist[self.current_index]
                entry['play_count'] = stats[0]
                entry['first_played'] = stats[1]
                entry['last_played'] = stats[2]
                self._apply_filter()

        if not is_playing and self._last_action == 'playing' and not self.is_paused:
            if self.playlist and len(self.display_indices) > 1:
                self._next_track()
            elif self.playlist:
                self.stop()

        self.after(500, self._poll)


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()


"""
A music player using CustomTkinter + VLC

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
import time
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import customtkinter as ctk

try:
    import vlc
except Exception:
    print("Missing dependency: python-vlc. Install with: pip install python-vlc")
    raise

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None

PLAY_MIN_SECONDS = 5
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player.db')


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
        self._play_started_at = 0  # time.time() when play was issued

        # Active filters
        self._active_genre = 'All'
        self._active_tags = set()  # empty = All; non-empty = show tracks with ANY selected tag
        self._sort_column = None
        self._sort_reverse = False
        self._rating_threshold = None  # None = no filter, (op, val) e.g. ('>=', 3)
        self._liked_by_filter = None  # None = All, else voter name string

        # Genre groups: {group_name: [genre1, genre2, ...]}
        self._genre_groups = {}
        self._all_tags = set()
        self._all_voters = set()  # known voter names

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
        self._bind_shortcuts()
        self.after(500, self._poll)

    # ── Database helpers ─────────────────────────────────

    def _init_database(self):
        con = sqlite3.connect(DB_PATH)
        con.execute("""
            CREATE TABLE IF NOT EXISTS tracks (
                id INTEGER PRIMARY KEY,
                path TEXT UNIQUE,
                title TEXT,
                artist TEXT,
                album TEXT,
                genre TEXT,
                play_count INTEGER DEFAULT 0,
                first_played TEXT,
                last_played TEXT,
                file_created TEXT
            )
        """)
        # New table for play events
        con.execute('''CREATE TABLE IF NOT EXISTS track_plays (
            id INTEGER PRIMARY KEY,
            track_id INTEGER,
            played_at TEXT,
            FOREIGN KEY(track_id) REFERENCES tracks(id)
        )''')
        # ...existing code...
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
            "file_created, genre, comment FROM tracks ORDER BY title"
        )
        rows = cur.fetchall()

        cur.execute("SELECT t.file_path, tt.tag FROM track_tags tt JOIN tracks t ON t.id = tt.track_id")
        tag_rows = cur.fetchall()

        # Load votes: per-track vote sums, liked_by, disliked_by
        cur.execute("SELECT t.file_path, v.vote, v.voter FROM track_votes v JOIN tracks t ON t.id = v.track_id")
        vote_rows = cur.fetchall()
        con.close()

        tags_by_path = {}
        for fpath, tag in tag_rows:
            tags_by_path.setdefault(fpath, []).append(tag)
            self._all_tags.add(tag)

        votes_by_path = {}  # path -> {'rating': int, 'liked_by': set, 'disliked_by': set}
        for fpath, vote, voter in vote_rows:
            v = votes_by_path.setdefault(fpath, {'rating': 0, 'liked_by': set(), 'disliked_by': set()})
            v['rating'] += vote
            if voter:
                self._all_voters.add(voter)
                if vote > 0:
                    v['liked_by'].add(voter)
                else:
                    v['disliked_by'].add(voter)

        if not rows:
            return

        seen = set()
        for (path, db_title, play_count, first_played, last_played,
             file_created, genre, comment) in rows:
            if path in seen:
                continue
            seen.add(path)
            vdata = votes_by_path.get(path, {'rating': 0, 'liked_by': set(), 'disliked_by': set()})
            entry = {
                'path': path,
                'title': db_title or os.path.basename(path),
                'basename': os.path.basename(path),
                'genre': genre or 'Unknown',
                'comment': comment or '',
                'play_count': play_count or 0,
                'first_played': first_played,
                'last_played': last_played,
                'file_created': file_created,
                'tags': tags_by_path.get(path, []),
                'rating': vdata['rating'],
                'liked_by': vdata['liked_by'],
                'disliked_by': vdata['disliked_by'],
            }
            self.playlist.append(entry)
            self.genres.add(entry['genre'])

        self._build_genre_list()
        self._rebuild_liked_by_dropdown()
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
        # Get track_id from path
        cur.execute('SELECT id FROM tracks WHERE file_path = ?', (path,))
        row = cur.fetchone()
        if not row:
            con.close()
            return
        track_id = row[0]
        cur.execute('INSERT INTO track_plays (track_id, played_at) VALUES (?, ?)', (track_id, now))
        # Increment play_count
        cur.execute('UPDATE tracks SET play_count = play_count + 1 WHERE id = ?', (track_id,))
        # Update first_played and last_played in tracks table
        cur.execute('SELECT first_played FROM tracks WHERE id = ?', (track_id,))
        first_played = cur.fetchone()[0]
        if not first_played:
            cur.execute('UPDATE tracks SET first_played = ?, last_played = ? WHERE id = ?', (now, now, track_id))
        else:
            cur.execute('UPDATE tracks SET last_played = ? WHERE id = ?', (now, track_id))
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

    # ── Vote / Rating helpers ────────────────────────────

    def _record_vote(self, playlist_idx, vote, voter=''):
        """Record a +1 or -1 vote for a track, optionally with voter name."""
        entry = self.playlist[playlist_idx]
        track_id = self._get_track_id(entry['path'])
        if not track_id:
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO track_votes (track_id, vote, voter, voted_at) VALUES (?, ?, ?, ?)",
                    (track_id, vote, voter, now))
        con.commit()
        con.close()
        entry['rating'] = entry.get('rating', 0) + vote
        if voter:
            self._all_voters.add(voter)
            if vote > 0:
                entry.setdefault('liked_by', set()).add(voter)
            else:
                entry.setdefault('disliked_by', set()).add(voter)
        self._apply_filter()
        self._build_tag_bar()
        self._update_rating_display()
        self._rebuild_liked_by_dropdown()

    def _ask_voter_and_vote(self, vote):
        """Show voter picker, then record vote. vote is +1 or -1."""
        if self.current_index is None:
            messagebox.showinfo('No track', 'No track is currently playing.')
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title('Who is voting?')
        dialog.geometry('300x200')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text='Who is voting? (optional)',
                     font=ctk.CTkFont(size=13, weight='bold')).pack(pady=(12, 6))

        voter_var = tk.StringVar()
        known = sorted(self._all_voters)
        if known:
            voter_dropdown = ctk.CTkOptionMenu(
                dialog, variable=voter_var, values=['(anonymous)'] + known,
                width=220, height=30, font=ctk.CTkFont(size=12),
                fg_color='#3b3b3b', button_color='#4a4a4a',
                dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5')
            voter_dropdown.pack(pady=(0, 4))
            voter_dropdown.set('(anonymous)')

        ctk.CTkLabel(dialog, text='Or type a new name:',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(4, 2))
        name_entry = ctk.CTkEntry(dialog, width=220, height=30, font=ctk.CTkFont(size=12),
                                   placeholder_text='New name\u2026')
        name_entry.pack(pady=(0, 8))

        def submit():
            typed = name_entry.get().strip()
            selected = voter_var.get()
            voter = typed if typed else ('' if selected in ('', '(anonymous)') else selected)
            dialog.destroy()
            self._record_vote(self.current_index, vote, voter)

        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=20, pady=(4, 10))
        emoji = '\U0001f44d' if vote > 0 else '\U0001f44e'
        ctk.CTkButton(btn_row, text=f'{emoji}  Vote', command=submit,
                      fg_color='#27ae60' if vote > 0 else '#c0392b').pack(side='right', padx=4)
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)

        name_entry.focus_set()
        name_entry.bind('<Return>', lambda e: submit())

    def _update_rating_display(self):
        """Update the rating label in the play panel."""
        if self.current_index is not None:
            rating = self.playlist[self.current_index].get('rating', 0)
            if rating > 0:
                self._lbl_rating.configure(text=f'+{rating}', text_color='#5dff5d')
            elif rating < 0:
                self._lbl_rating.configure(text=str(rating), text_color='#ff5d5d')
            else:
                self._lbl_rating.configure(text='0', text_color='#888888')
        else:
            self._lbl_rating.configure(text='—', text_color='#888888')

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

        # Now-playing row highlight tag
        self._now_playing_tag = 'now_playing'

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
        self._controls_frame = ctk.CTkFrame(self, fg_color='#1a1a2e')
        self._controls_frame.pack(fill='x')

        btn_row = ctk.CTkFrame(self._controls_frame, fg_color='transparent')
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

        # ═══ PLAY NOW BAR (under play controls, hidden until track selected) ═══
        self.btn_play_now = ctk.CTkButton(self, text='\u25b6  Play Now', height=44,
                                          font=ctk.CTkFont(size=20, weight='bold'),
                                          fg_color='#f1c40f', hover_color='#f39c12',
                                          text_color='#000000',
                                          command=self._play_now_click)
        self._play_now_visible = False

        self._tag_buttons = []

        # ═══ MAIN AREA: Two-panel resizable splitter ═══
        paned = tk.PanedWindow(self, orient='horizontal', sashwidth=6,
                               bg='#1a1a2e', sashrelief='flat', borderwidth=0)
        paned.pack(fill='both', expand=True, padx=6, pady=(10, 4))

        # ── BROWSE PANEL (left) ──
        browse = ctk.CTkFrame(paned, fg_color='#2b2b2b', corner_radius=8)

        # ── Filter Row 1: Genre + Rating + Liked by ──
        filter_row1 = ctk.CTkFrame(browse, fg_color='transparent')
        filter_row1.pack(fill='x', padx=8, pady=(8, 2))

        ctk.CTkLabel(filter_row1, text='Genre', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._genre_var = tk.StringVar(value='All')
        self.genre_dropdown = ctk.CTkOptionMenu(
            filter_row1, variable=self._genre_var,
            values=['All'], command=self._on_genre_dropdown,
            width=160, height=26,
            font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self.genre_dropdown.pack(side='left', padx=(6, 16))

        ctk.CTkLabel(filter_row1, text='Rating', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._rating_filter_var = tk.StringVar(value='All')
        rating_vals = ['All', '≥ 1', '≥ 2', '≥ 3', '≥ 5', '≥ 10', '≤ -1', '≤ -3', '= 0']
        self._rating_filter_dropdown = ctk.CTkOptionMenu(
            filter_row1, variable=self._rating_filter_var,
            values=rating_vals, command=self._on_rating_filter,
            width=100, height=26, font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self._rating_filter_dropdown.pack(side='left', padx=(6, 16))

        ctk.CTkLabel(filter_row1, text='Liked by', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._liked_by_var = tk.StringVar(value='All')
        self._liked_by_dropdown = ctk.CTkOptionMenu(
            filter_row1, variable=self._liked_by_var,
            values=['All'], command=self._on_liked_by_filter,
            width=140, height=26, font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self._liked_by_dropdown.pack(side='left', padx=(6, 0))

        # Reset all filters button + settings gear (right-aligned on row 1)
        ctk.CTkButton(
            filter_row1, text='\u2699', width=28, height=24,
            font=ctk.CTkFont(size=14), fg_color='transparent',
            hover_color='#3b3b3b', command=self._open_settings
        ).pack(side='right', padx=(0, 2))
        self._btn_reset_filters = ctk.CTkButton(
            filter_row1, text='✕ Reset', width=70, height=24,
            font=ctk.CTkFont(size=10), fg_color='transparent',
            border_width=1, border_color='#555555',
            hover_color='#3b3b3b', text_color='#999999',
            command=self._reset_all_filters)
        self._btn_reset_filters.pack(side='right', padx=(0, 2))

        # ── Filter Row 2: First Played + Last Played + File Created ──
        filter_row2 = ctk.CTkFrame(browse, fg_color='transparent')
        filter_row2.pack(fill='x', padx=8, pady=(0, 4))

        ctk.CTkLabel(filter_row2, text='First Played', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._first_played_var = tk.StringVar(value='All')
        self._first_played_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._first_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_first_played_filter,
            width=110, height=26, font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self._first_played_dropdown.pack(side='left', padx=(6, 16))

        ctk.CTkLabel(filter_row2, text='Last Played', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._last_played_var = tk.StringVar(value='All')
        self._last_played_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._last_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_last_played_filter,
            width=110, height=26, font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self._last_played_dropdown.pack(side='left', padx=(6, 16))

        ctk.CTkLabel(filter_row2, text='File Created', font=ctk.CTkFont(size=11, weight='bold')).pack(side='left')
        self._file_created_var = tk.StringVar(value='All')
        self._file_created_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._file_created_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_file_created_filter,
            width=110, height=26, font=ctk.CTkFont(size=11),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            button_hover_color='#555555',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
            dropdown_text_color='#dce4ee')
        self._file_created_dropdown.pack(side='left', padx=(6, 0))

        # Track list section
        tree_frame = ctk.CTkFrame(browse, fg_color='transparent')
        tree_frame.pack(fill='both', expand=True, padx=6, pady=(0, 6))

        # Search box
        self._search_var = tk.StringVar()
        self._search_var.trace_add('write', lambda *_: self._apply_filter())
        self._search_entry = ctk.CTkEntry(tree_frame, textvariable=self._search_var,
                                           placeholder_text='\U0001f50d  Search tracks\u2026',
                                           height=30, font=ctk.CTkFont(size=12))
        self._search_entry.pack(fill='x', pady=(0, 4))

        # Tag filter bar (under search) — multi-row wrapping layout
        self.tag_bar_frame = ctk.CTkFrame(
            tree_frame, fg_color='#2b2b2b', corner_radius=6)
        self.tag_bar_frame.pack(fill='x', pady=(0, 4))

        self._all_columns = ('Title', 'Rating', 'Comment', 'Tags', 'Liked By', 'Disliked By',
                              'Plays', 'First Played', 'Last Played', 'File Created')
        self.tree = ttk.Treeview(tree_frame,
                                 columns=self._all_columns,
                                 show='headings', height=18)
        self.tree.column('Title', width=200, anchor='w')
        self.tree.column('Rating', width=55, anchor='center')
        self.tree.column('Comment', width=100, anchor='w')
        self.tree.column('Tags', width=100, anchor='w')
        self.tree.column('Liked By', width=100, anchor='w')
        self.tree.column('Disliked By', width=100, anchor='w')
        self.tree.column('Plays', width=45, anchor='center')
        self.tree.column('First Played', width=90, anchor='w')
        self.tree.column('Last Played', width=90, anchor='w')
        self.tree.column('File Created', width=90, anchor='w')
        for col in self._all_columns:
            self.tree.heading(col, text=col,
                              command=lambda c=col: self._sort_by_column(c))
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.tag_configure(self._now_playing_tag, background='#1a3a1a', foreground='#5dff5d')
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree.bind('<Button-3>', self._on_right_click)

        sb = ctk.CTkScrollbar(tree_frame, command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.config(yscrollcommand=sb.set)

        paned.add(browse, stretch='always')

        # ── PLAY PANEL (right) ──
        play_panel = ctk.CTkFrame(paned, fg_color='#2b2b2b', corner_radius=8)

        # Play panel content: rating + volume
        play_content = ctk.CTkFrame(play_panel, fg_color='transparent')
        play_content.pack(fill='both', expand=True, padx=4, pady=4)

        # ── Rating section (top of play panel) ──
        rating_section = ctk.CTkFrame(play_content, fg_color='#222233', corner_radius=8)
        rating_section.pack(fill='x', padx=4, pady=(4, 8))

        ctk.CTkLabel(rating_section, text='Rate this track',
                     font=ctk.CTkFont(size=11, weight='bold'),
                     text_color='#aaaaaa').pack(pady=(8, 2))

        self._lbl_rating = ctk.CTkLabel(rating_section, text='\u2014',
                                         font=ctk.CTkFont(size=28, weight='bold'),
                                         text_color='#888888')
        self._lbl_rating.pack(pady=(0, 4))

        vote_row = ctk.CTkFrame(rating_section, fg_color='transparent')
        vote_row.pack(pady=(0, 10))

        self._btn_thumbs_up = ctk.CTkButton(
            vote_row, text='\U0001f44d', width=70, height=55,
            font=ctk.CTkFont(size=32), fg_color='#27ae60', hover_color='#2ecc71',
            command=lambda: self._ask_voter_and_vote(+1))
        self._btn_thumbs_up.pack(side='left', padx=6)

        self._btn_thumbs_down = ctk.CTkButton(
            vote_row, text='\U0001f44e', width=70, height=55,
            font=ctk.CTkFont(size=32), fg_color='#c0392b', hover_color='#e74c3c',
            command=lambda: self._ask_voter_and_vote(-1))
        self._btn_thumbs_down.pack(side='left', padx=6)

        # ── Volume (below rating) ──
        vol_panel = ctk.CTkFrame(play_content, width=60, fg_color='transparent')
        vol_panel.pack(fill='y', expand=True, padx=(4, 4))
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

        paned.add(play_panel, stretch='always')

        # Set initial 60/40 split after window is drawn
        def _set_sash():
            try:
                w = paned.winfo_width()
                if w > 1:
                    paned.sash_place(0, int(w * 0.6), 0)
            except Exception:
                pass
        self.after(200, _set_sash)

    # ── Keyboard shortcuts ───────────────────────────────

    def _bind_shortcuts(self):
        self.bind('<space>', lambda e: self.play_pause() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Right>', lambda e: self._next_track() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Left>', lambda e: self._prev_track() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Escape>', lambda e: self.stop())
        self.bind('<Control-f>', lambda e: self._focus_search())

    def _focus_search(self):
        """Focus the search box."""
        if hasattr(self, '_search_entry'):
            self._search_entry.focus_set()

    def _prev_track(self):
        if not self.playlist or not self.display_indices:
            return
        try:
            pos = self.display_indices.index(self.current_index)
        except ValueError:
            pos = 0
        prev_pos = (pos - 1) % len(self.display_indices)
        prev_idx = self.display_indices[prev_pos]
        self._load(prev_idx)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._playback_start_time = time.time()
        self._play_recorded = False
        self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
        self._update_now_playing()

    # ── Menu ─────────────────────────────────────────────

    def _show_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Add Files\u2026', command=self.add_files)
        menu.add_command(label='Add Folder\u2026', command=self.add_folder)
        x = self.btn_menu.winfo_rootx()
        y = self.btn_menu.winfo_rooty() + self.btn_menu.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    # ── Genre dropdown ─────────────────────────────────

    def _build_genre_list(self):
        """Rebuild the genre dropdown values."""
        values = ['All']
        # Map display labels → filter keys
        self._genre_label_map = {'All': ('all', 'All')}

        grouped_genres = set()
        for gname, members in self._genre_groups.items():
            values.append(f'▸ {gname}')
            self._genre_label_map[f'▸ {gname}'] = ('group', gname)
            for genre in members:
                values.append(f'    {genre}')
                self._genre_label_map[f'    {genre}'] = ('genre', genre)
                grouped_genres.add(genre)

        ungrouped = sorted(g for g in self.genres if g and g not in grouped_genres)
        for genre in ungrouped:
            values.append(genre)
            self._genre_label_map[genre] = ('genre', genre)

        self.genre_dropdown.configure(values=values)
        self._genre_var.set('All')

    def _on_genre_dropdown(self, choice):
        """Handle genre dropdown selection."""
        kind, name = self._genre_label_map.get(choice, ('all', 'All'))
        if kind == 'all':
            self._active_genre = 'All'
        elif kind == 'group':
            self._active_genre = name
        else:
            self._active_genre = name
        self._active_tags = set()  # reset tag filter on genre change
        self._apply_filter()
        self._build_tag_bar()

    def _get_genres_for_filter(self):
        if self._active_genre == 'All':
            return None
        if self._active_genre in self._genre_groups:
            return set(self._genre_groups[self._active_genre])
        return {self._active_genre}

    # ── Rating / Liked-by filter handlers ────────────────

    def _on_rating_filter(self, choice):
        if choice == 'All':
            self._rating_threshold = None
        else:
            # Parse choices like '≥ 1', '≤ -1', '= 0'
            parts = choice.split()
            op_map = {'≥': '>=', '≤': '<=', '=': '='}
            op = op_map.get(parts[0], '>=')
            val = int(parts[1])
            self._rating_threshold = (op, val)
        self._apply_filter()
        self._build_tag_bar()

    def _on_liked_by_filter(self, choice):
        self._liked_by_filter = None if choice == 'All' else choice
        self._apply_filter()
        self._build_tag_bar()

    def _on_first_played_filter(self, choice):
        self._first_played_var.set(choice)
        self._apply_filter()
        self._build_tag_bar()

    def _on_last_played_filter(self, choice):
        self._last_played_var.set(choice)
        self._apply_filter()
        self._build_tag_bar()

    def _on_file_created_filter(self, choice):
        self._file_created_var.set(choice)
        self._apply_filter()
        self._build_tag_bar()

    def _reset_all_filters(self):
        """Reset all filter dropdowns back to 'All'."""
        self._rating_filter_var.set('All')
        self._rating_threshold = None
        self._liked_by_var.set('All')
        self._liked_by_filter = None
        self._first_played_var.set('All')
        self._last_played_var.set('All')
        self._file_created_var.set('All')
        self._genre_var.set('All')
        self._active_genre = 'All'
        self._active_tags = set()
        self._search_var.set('')
        self._apply_filter()
        self._build_tag_bar()

    def _rebuild_liked_by_dropdown(self):
        """Rebuild the liked-by dropdown with current voter names."""
        if hasattr(self, '_liked_by_dropdown'):
            values = ['All'] + sorted(self._all_voters)
            self._liked_by_dropdown.configure(values=values)

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
            lbl.grid(row=0, column=0, padx=6, pady=5)
            self._tag_buttons.append(lbl)
            return

        all_active = not self._active_tags  # empty set means "All"
        btn_all = ctk.CTkButton(self.tag_bar_frame, text='ALL', height=26, width=50,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if all_active else 'transparent',
                                border_width=1, border_color='#555555',
                                command=lambda: self._on_tag_filter('All'))
        btn_all.grid(row=0, column=0, padx=(6, 2), pady=3)
        self._tag_buttons.append(btn_all)

        col = 1
        row = 0
        max_cols = 8  # wrap after this many tags per row
        for tag in sorted(visible_tags):
            if col >= max_cols:
                col = 0
                row += 1
            is_active = tag in self._active_tags
            btn = ctk.CTkButton(self.tag_bar_frame, text=tag.upper(), height=26,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if is_active else 'transparent',
                                border_width=1, border_color='#555555',
                                command=lambda t=tag: self._on_tag_filter(t))
            btn.grid(row=row, column=col, padx=2, pady=3)
            self._tag_buttons.append(btn)
            col += 1

    def _on_tag_filter(self, tag):
        if tag == 'All':
            self._active_tags = set()
        else:
            if tag in self._active_tags:
                self._active_tags.discard(tag)
            else:
                self._active_tags.add(tag)
        self._apply_filter()
        self._build_tag_bar()

    def _add_new_tag(self, parent_window=None, callback=None):
        """Create a new tag (globally). Optionally apply to selected tracks."""
        tag = simpledialog.askstring('New Tag', 'Enter tag name:',
                                     parent=parent_window or self)
        if tag and tag.strip():
            tag = tag.strip().lower()
            self._all_tags.add(tag)
            # Apply to selected tracks if any
            sel = self.tree.selection()
            all_items = self.tree.get_children()
            for item in sel:
                try:
                    idx = list(all_items).index(item)
                    self._add_tag_to_track(self.display_indices[idx], tag)
                except (ValueError, IndexError):
                    pass
            self._apply_filter()
            self._build_tag_bar()
            if callback:
                callback()

    def _delete_tag_globally(self, tag):
        """Remove a tag from all tracks and from _all_tags."""
        self._all_tags.discard(tag)
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM track_tags WHERE tag = ?", (tag,))
        con.commit()
        con.close()
        for entry in self.playlist:
            if tag in entry.get('tags', []):
                entry['tags'].remove(tag)
        self._active_tags.discard(tag)
        self._apply_filter()
        self._build_tag_bar()

    def _rename_tag_globally(self, old_tag, new_tag, parent_window=None):
        """Rename a tag across all tracks."""
        new_tag = new_tag.strip().lower()
        if not new_tag or new_tag == old_tag:
            return
        con = sqlite3.connect(DB_PATH)
        # Update DB — delete new_tag duplicates first, then rename
        con.execute("DELETE FROM track_tags WHERE tag = ? AND track_id IN "
                    "(SELECT track_id FROM track_tags WHERE tag = ?)", (new_tag, old_tag))
        con.execute("UPDATE track_tags SET tag = ? WHERE tag = ?", (new_tag, old_tag))
        con.commit()
        con.close()
        for entry in self.playlist:
            tags = entry.get('tags', [])
            if old_tag in tags:
                tags.remove(old_tag)
                if new_tag not in tags:
                    tags.append(new_tag)
        self._all_tags.discard(old_tag)
        self._all_tags.add(new_tag)
        if old_tag in self._active_tags:
            self._active_tags.discard(old_tag)
            self._active_tags.add(new_tag)
        self._apply_filter()
        self._build_tag_bar()

    # ── Settings dialog (Genres + Tags) ──────────────────

    def _open_settings(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title('Settings')
        dialog.geometry('520x650')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        # ── Tab bar ──
        tab_bar = ctk.CTkFrame(dialog, fg_color='transparent')
        tab_bar.pack(fill='x', padx=10, pady=(10, 0))

        tab_container = ctk.CTkFrame(dialog, fg_color='transparent')
        tab_container.pack(fill='both', expand=True, padx=10, pady=6)

        genre_frame = ctk.CTkFrame(tab_container, fg_color='transparent')
        tags_frame = ctk.CTkFrame(tab_container, fg_color='transparent')

        active_tab = [None]

        def show_tab(name):
            if active_tab[0] == name:
                return
            active_tab[0] = name
            genre_frame.pack_forget()
            tags_frame.pack_forget()
            if name == 'genres':
                genre_frame.pack(fill='both', expand=True)
                btn_tab_genres.configure(fg_color='#1f6aa5')
                btn_tab_tags.configure(fg_color='transparent')
            else:
                tags_frame.pack(fill='both', expand=True)
                btn_tab_genres.configure(fg_color='transparent')
                btn_tab_tags.configure(fg_color='#1f6aa5')

        btn_tab_genres = ctk.CTkButton(tab_bar, text='Genres', height=30,
                                        font=ctk.CTkFont(size=12, weight='bold'),
                                        fg_color='#1f6aa5', border_width=1, border_color='#555555',
                                        command=lambda: show_tab('genres'))
        btn_tab_genres.pack(side='left', padx=(0, 4))
        btn_tab_tags = ctk.CTkButton(tab_bar, text='Tags', height=30,
                                      font=ctk.CTkFont(size=12, weight='bold'),
                                      fg_color='transparent', border_width=1, border_color='#555555',
                                      command=lambda: show_tab('tags'))
        btn_tab_tags.pack(side='left')

        # ═══════════════ GENRES TAB ═══════════════
        ctk.CTkLabel(genre_frame, text='Genre Groups',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(6, 2))
        ctk.CTkLabel(genre_frame, text='Create groups and assign genres to them.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 6))

        working_groups = {k: list(v) for k, v in self._genre_groups.items()}
        all_genres = sorted(self.genres)

        genre_content = ctk.CTkScrollableFrame(genre_frame)
        genre_content.pack(fill='both', expand=True)

        cb_vars = {}

        def rebuild_genre_tab():
            for w in genre_content.winfo_children():
                w.destroy()
            cb_vars.clear()

            for gname in list(working_groups.keys()):
                gf = ctk.CTkFrame(genre_content, fg_color='#2b2b2b', corner_radius=8)
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
            for w in genre_content.winfo_children():
                if hasattr(w, '_is_ungrouped'):
                    w.destroy()

            assigned = set()
            for members in working_groups.values():
                assigned.update(members)
            ungrouped = [g for g in all_genres if g not in assigned]
            if ungrouped:
                uf = ctk.CTkFrame(genre_content, fg_color='#222222', corner_radius=8)
                uf._is_ungrouped = True
                uf.pack(fill='x', pady=4)
                ctk.CTkLabel(uf, text='Ungrouped', font=ctk.CTkFont(size=13, weight='bold'),
                             text_color='#888888').pack(anchor='w', padx=8, pady=(6, 2))
                for genre in ungrouped:
                    ctk.CTkLabel(uf, text=f'  {genre}', font=ctk.CTkFont(size=11),
                                 text_color='#666666').pack(anchor='w', padx=16, pady=1)

        def toggle_genre(group, genre, var):
            if var.get():
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
            rebuild_genre_tab()

        def rename_group(gname):
            new_name = simpledialog.askstring('Rename Group', 'New name:', initialvalue=gname, parent=dialog)
            if new_name and new_name.strip() and new_name.strip() != gname:
                working_groups[new_name.strip()] = working_groups.pop(gname)
                rebuild_genre_tab()

        def add_group():
            name = simpledialog.askstring('New Group', 'Group name:', parent=dialog)
            if name and name.strip():
                name = name.strip()
                if name not in working_groups:
                    working_groups[name] = []
                    rebuild_genre_tab()

        rebuild_genre_tab()

        genre_btn_row = ctk.CTkFrame(genre_frame, fg_color='transparent')
        genre_btn_row.pack(fill='x', pady=(6, 0))
        ctk.CTkButton(genre_btn_row, text='+ New Group', command=add_group).pack(side='left', padx=4)

        # ═══════════════ TAGS TAB ═══════════════
        ctk.CTkLabel(tags_frame, text='Manage Tags',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(6, 2))
        ctk.CTkLabel(tags_frame, text='Create, rename, or delete tags.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 6))

        tags_content = ctk.CTkScrollableFrame(tags_frame)
        tags_content.pack(fill='both', expand=True)

        def rebuild_tags_tab():
            for w in tags_content.winfo_children():
                w.destroy()
            for tag in sorted(self._all_tags):
                row = ctk.CTkFrame(tags_content, fg_color='#2b2b2b', corner_radius=8)
                row.pack(fill='x', pady=2)
                ctk.CTkLabel(row, text=tag.upper(), font=ctk.CTkFont(size=12)).pack(side='left', padx=10, pady=6)
                ctk.CTkButton(row, text='\U0001f5d1', width=30, height=24, fg_color='transparent',
                              command=lambda t=tag: on_delete_tag(t)).pack(side='right', padx=4, pady=4)
                ctk.CTkButton(row, text='\u270f', width=30, height=24, fg_color='transparent',
                              command=lambda t=tag: on_rename_tag(t)).pack(side='right', padx=0, pady=4)

            if not self._all_tags:
                ctk.CTkLabel(tags_content, text='No tags yet.', font=ctk.CTkFont(size=11),
                             text_color='#666666').pack(pady=20)

        def on_delete_tag(tag):
            if messagebox.askyesno('Delete Tag', f'Delete tag "{tag}" from all tracks?', parent=dialog):
                self._delete_tag_globally(tag)
                rebuild_tags_tab()

        def on_rename_tag(tag):
            new_name = simpledialog.askstring('Rename Tag', 'New name:', initialvalue=tag, parent=dialog)
            if new_name and new_name.strip():
                self._rename_tag_globally(tag, new_name, parent_window=dialog)
                rebuild_tags_tab()

        def on_add_tag():
            self._add_new_tag(parent_window=dialog, callback=rebuild_tags_tab)

        rebuild_tags_tab()

        tags_btn_row = ctk.CTkFrame(tags_frame, fg_color='transparent')
        tags_btn_row.pack(fill='x', pady=(6, 0))
        ctk.CTkButton(tags_btn_row, text='+ New Tag', command=on_add_tag).pack(side='left', padx=4)

        # ═══════════════ BOTTOM BUTTONS ═══════════════
        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=10, pady=10)
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

        show_tab('genres')

    # ── Filter logic ─────────────────────────────────────

    # Column-to-entry-key mapping for sorting
    _SORT_KEYS = {
        'Title': lambda e: (e.get('title') or e['basename']).lower(),
        'Rating': lambda e: e.get('rating', 0),
        'Comment': lambda e: (e.get('comment') or '').lower(),
        'Tags': lambda e: ', '.join(sorted(e.get('tags', []))).lower(),
        'Liked By': lambda e: ', '.join(sorted(e.get('liked_by', set()))).lower(),
        'Disliked By': lambda e: ', '.join(sorted(e.get('disliked_by', set()))).lower(),
        'Plays': lambda e: e.get('play_count', 0),
        'First Played': lambda e: e.get('first_played') or '',
        'Last Played': lambda e: e.get('last_played') or '',
        'File Created': lambda e: e.get('file_created') or '',
    }

    def _sort_by_column(self, col):
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        # Update heading text to show sort indicator
        for c in self._all_columns:
            arrow = ''
            if c == self._sort_column:
                arrow = ' \u25b2' if not self._sort_reverse else ' \u25bc'
            self.tree.heading(c, text=f'{c}{arrow}')
        self._apply_filter()

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
        search_term = self._search_var.get().strip().lower() if hasattr(self, '_search_var') else ''

        # Phase 1: collect matching indices
        matched = []
        from datetime import datetime, timedelta
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        first_played_filter = getattr(self, '_first_played_var', None)
        last_played_filter = getattr(self, '_last_played_var', None)
        file_created_filter = getattr(self, '_file_created_var', None)
        for idx, entry in enumerate(self.playlist):
            if genre_filter is not None:
                if entry.get('genre') not in genre_filter:
                    continue
            if self._active_tags:
                track_tags = set(entry.get('tags', []))
                if not self._active_tags & track_tags:
                    continue
            # Rating threshold filter
            if self._rating_threshold is not None:
                op, val = self._rating_threshold
                rating = entry.get('rating', 0)
                if op == '>=' and rating < val:
                    continue
                elif op == '<=' and rating > val:
                    continue
                elif op == '=' and rating != val:
                    continue
            # Liked-by filter
            if self._liked_by_filter:
                if self._liked_by_filter not in entry.get('liked_by', set()):
                    continue
            # First Played filter
            if first_played_filter and first_played_filter.get() != 'All':
                fp = entry.get('first_played')
                if fp:
                    try:
                        fp_date = datetime.fromisoformat(fp).date()
                    except Exception:
                        fp_date = None
                else:
                    fp_date = None
                if first_played_filter.get() == 'Today' and (not fp_date or fp_date != today):
                    continue
                if first_played_filter.get() == 'This Week' and (not fp_date or fp_date < week_ago):
                    continue
                if first_played_filter.get() == 'This Month' and (not fp_date or fp_date < month_ago):
                    continue
            # Last Played filter
            if last_played_filter and last_played_filter.get() != 'All':
                lp = entry.get('last_played')
                if lp:
                    try:
                        lp_date = datetime.fromisoformat(lp).date()
                    except Exception:
                        lp_date = None
                else:
                    lp_date = None
                if last_played_filter.get() == 'Today' and (not lp_date or lp_date != today):
                    continue
                if last_played_filter.get() == 'This Week' and (not lp_date or lp_date < week_ago):
                    continue
                if last_played_filter.get() == 'This Month' and (not lp_date or lp_date < month_ago):
                    continue
            # File Created filter
            if file_created_filter and file_created_filter.get() != 'All':
                fc = entry.get('file_created')
                if fc:
                    try:
                        fc_date = datetime.fromisoformat(fc).date()
                    except Exception:
                        fc_date = None
                else:
                    fc_date = None
                if file_created_filter.get() == 'Today' and (not fc_date or fc_date != today):
                    continue
                if file_created_filter.get() == 'This Week' and (not fc_date or fc_date < week_ago):
                    continue
                if file_created_filter.get() == 'This Month' and (not fc_date or fc_date < month_ago):
                    continue
            if search_term:
                title_lower = entry.get('title', entry['basename']).lower()
                comment_lower = entry.get('comment', '').lower()
                if search_term not in title_lower and search_term not in comment_lower:
                    continue
            matched.append(idx)

        # Phase 2: sort if a column is selected
        if self._sort_column and self._sort_column in self._SORT_KEYS:
            key_fn = self._SORT_KEYS[self._sort_column]
            matched.sort(key=lambda i: key_fn(self.playlist[i]), reverse=self._sort_reverse)

        # Phase 3: insert into treeview
        for idx in matched:
            entry = self.playlist[idx]
            title = entry.get('title', entry['basename'])
            rating = entry.get('rating', 0)
            rating_str = f'+{rating}' if rating > 0 else str(rating)
            comment = entry.get('comment', '')
            tags_str = ', '.join(sorted(t.upper() for t in entry.get('tags', []))) if entry.get('tags') else '\u2014'
            liked_str = ', '.join(sorted(entry.get('liked_by', set()))) if entry.get('liked_by') else '\u2014'
            disliked_str = ', '.join(sorted(entry.get('disliked_by', set()))) if entry.get('disliked_by') else '\u2014'
            plays = entry.get('play_count', 0)
            first_p = self._format_ts(entry.get('first_played'), relative=False)
            last_p = self._format_ts(entry.get('last_played'), relative=True)
            file_c = self._format_ts(entry.get('file_created'), relative=False)
            row_tags = (self._now_playing_tag,) if idx == self.current_index and self.is_playing else ()
            self.tree.insert('', 'end',
                             values=(title, rating_str, comment, tags_str, liked_str, disliked_str,
                                     plays, first_p, last_p, file_c),
                             tags=row_tags)
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
                 'genre': genre, 'comment': comment, 'tags': [],
                 'rating': 0, 'liked_by': set(), 'disliked_by': set()}
        self.playlist.append(entry)
        self.genres.add(genre)
        stats = self._ensure_track_in_db(path, title, genre, comment)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        return True

    # ── Playback ─────────────────────────────────────────

    def _update_now_playing_highlight(self):
        """Update the now-playing row tag without rebuilding the treeview."""
        for item in self.tree.get_children():
            tags = list(self.tree.item(item, 'tags'))
            pos = list(self.tree.get_children()).index(item)
            pl_idx = self.display_indices[pos] if pos < len(self.display_indices) else None
            is_current = pl_idx == self.current_index and self.is_playing
            if is_current and self._now_playing_tag not in tags:
                tags.append(self._now_playing_tag)
                self.tree.item(item, tags=tags)
            elif not is_current and self._now_playing_tag in tags:
                tags.remove(self._now_playing_tag)
                self.tree.item(item, tags=tags)

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
        self._update_now_playing_highlight()
        self._update_rating_display()

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
            self._play_started_at = time.time()
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
            self._play_started_at = time.time()
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
        self._play_started_at = time.time()
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

    def _on_right_click(self, ev):
        """Show context menu on right-click."""
        item = self.tree.identify_row(ev.y)
        if not item:
            return
        self.tree.selection_set(item)
        all_items = self.tree.get_children()
        try:
            idx = list(all_items).index(item)
            playlist_idx = self.display_indices[idx]
        except (ValueError, IndexError):
            return

        entry = self.playlist[playlist_idx]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='\u25b6  Play', command=lambda: self._context_play(playlist_idx))
        menu.add_separator()
        menu.add_command(label='\u270f  Edit Title\u2026',
                         command=lambda: self._context_edit_title(playlist_idx))
        menu.add_command(label='\u270f  Edit Genre\u2026',
                         command=lambda: self._context_edit_genre(playlist_idx))
        menu.add_command(label='\u270f  Edit Comment\u2026',
                         command=lambda: self._context_edit_comment(playlist_idx))

        # Tags submenu
        if self._all_tags:
            tags_menu = tk.Menu(menu, tearoff=0)
            track_tags = set(entry.get('tags', []))
            for tag in sorted(self._all_tags):
                has_tag = tag in track_tags
                label = f'\u2713  {tag.upper()}' if has_tag else f'     {tag.upper()}'
                tags_menu.add_command(label=label,
                                      command=lambda t=tag, applied=has_tag: self._context_toggle_tag(playlist_idx, t, applied))
            menu.add_separator()
            menu.add_cascade(label='\U0001f3f7  Tags', menu=tags_menu)

        menu.add_separator()
        menu.add_command(label='Show Play History', command=lambda: self._show_play_history(entry))
        menu.add_separator()
        menu.add_command(label='\U0001f5d1  Remove from Library',
                         command=lambda: self._context_remove(playlist_idx))
        menu.tk_popup(ev.x_root, ev.y_root)

    def _context_play(self, playlist_idx):
        self._last_action = 'switching'
        self.vlc_player.stop()
        self.current_index = playlist_idx
        loaded = self._load(playlist_idx)
        if loaded:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._playback_start_time = time.time()
            self._play_recorded = False
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()

    def _show_play_history(self, entry):
        """Show a dialog listing all play events for a track."""
        track_id = self._get_track_id(entry['path'])
        if not track_id:
            messagebox.showinfo('Play History', 'No play history available.')
            return

        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute('SELECT played_at FROM track_plays WHERE track_id = ? ORDER BY played_at DESC', (track_id,))
        rows = cur.fetchall()
        con.close()

        dialog = ctk.CTkToplevel(self)
        title = entry.get('title', entry['basename'])
        dialog.title(f'Play History — {title}')
        dialog.geometry('400x450')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text=f'Play History',
                     font=ctk.CTkFont(size=15, weight='bold')).pack(pady=(12, 2))
        ctk.CTkLabel(dialog, text=title,
                     font=ctk.CTkFont(size=12), text_color='#aaaaaa',
                     wraplength=360).pack(pady=(0, 8))

        stats_frame = ctk.CTkFrame(dialog, fg_color='#2b2b2b', corner_radius=8)
        stats_frame.pack(fill='x', padx=16, pady=(0, 8))

        play_count = entry.get('play_count', 0)
        first_p = self._format_ts(entry.get('first_played'), relative=False)
        last_p = self._format_ts(entry.get('last_played'), relative=True)

        ctk.CTkLabel(stats_frame, text=f'Total plays: {play_count}    |    First: {first_p}    |    Last: {last_p}',
                     font=ctk.CTkFont(size=11), text_color='#cccccc').pack(padx=10, pady=8)

        if not rows:
            ctk.CTkLabel(dialog, text='No play events recorded yet.',
                         font=ctk.CTkFont(size=12), text_color='#666666').pack(pady=30)
        else:
            list_frame = ctk.CTkScrollableFrame(dialog, fg_color='#1a1a2e')
            list_frame.pack(fill='both', expand=True, padx=16, pady=(0, 8))

            for i, (played_at,) in enumerate(rows, 1):
                ts_abs = self._format_ts(played_at, relative=False)
                ts_rel = self._format_ts(played_at, relative=True)
                row = ctk.CTkFrame(list_frame, fg_color='#2b2b2b' if i % 2 == 0 else '#252535',
                                   corner_radius=4)
                row.pack(fill='x', pady=1)
                ctk.CTkLabel(row, text=f'#{i}', font=ctk.CTkFont(size=11, weight='bold'),
                             text_color='#888888', width=40).pack(side='left', padx=(8, 4), pady=4)
                ctk.CTkLabel(row, text=ts_abs, font=ctk.CTkFont(size=11),
                             text_color='#dce4ee').pack(side='left', padx=4, pady=4)
                ctk.CTkLabel(row, text=ts_rel, font=ctk.CTkFont(size=11),
                             text_color='#888888').pack(side='right', padx=8, pady=4)

        ctk.CTkButton(dialog, text='Close', fg_color='#555555', width=100,
                      command=dialog.destroy).pack(pady=(4, 12))

    def _context_edit_title(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        current = entry.get('title', entry['basename'])
        new_val = simpledialog.askstring('Edit Title', 'Title:', initialvalue=current, parent=self)
        if new_val is not None and new_val.strip():
            entry['title'] = new_val.strip()
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET title = ? WHERE file_path = ?", (new_val.strip(), entry['path']))
            con.commit()
            con.close()
            self._apply_filter()

    def _context_edit_genre(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        current = entry.get('genre', 'Unknown')
        new_val = simpledialog.askstring('Edit Genre', 'Genre:', initialvalue=current, parent=self)
        if new_val is not None and new_val.strip():
            old_genre = entry.get('genre')
            entry['genre'] = new_val.strip()
            self.genres.add(new_val.strip())
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET genre = ? WHERE file_path = ?", (new_val.strip(), entry['path']))
            con.commit()
            con.close()
            self._build_genre_list()
            self._apply_filter()

    def _context_edit_comment(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        current = entry.get('comment', '')
        new_val = simpledialog.askstring('Edit Comment', 'Comment:', initialvalue=current, parent=self)
        if new_val is not None:
            entry['comment'] = new_val.strip()
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET comment = ? WHERE file_path = ?", (new_val.strip(), entry['path']))
            con.commit()
            con.close()
            self._apply_filter()

    def _context_toggle_tag(self, playlist_idx, tag, currently_applied):
        if currently_applied:
            self._remove_tag_from_track(playlist_idx, tag)
        else:
            self._add_tag_to_track(playlist_idx, tag)
        self._apply_filter()
        self._build_tag_bar()

    def _context_remove(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        if not messagebox.askyesno('Remove Track', f'Remove "{title}" from the library?\n\n(File will not be deleted)'):
            return
        path = entry['path']
        if self.current_index == playlist_idx:
            self.stop()
            self.current_index = None
        elif self.current_index is not None and self.current_index > playlist_idx:
            self.current_index -= 1
        self.playlist.pop(playlist_idx)
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM track_tags WHERE track_id = (SELECT id FROM tracks WHERE file_path = ?)", (path,))
        con.execute("DELETE FROM play_history WHERE track_id = (SELECT id FROM tracks WHERE file_path = ?)", (path,))
        con.execute("DELETE FROM tracks WHERE file_path = ?", (path,))
        con.commit()
        con.close()
        self._apply_filter()
        self._build_tag_bar()

    def _on_select(self, ev):
        sel = self.tree.selection()
        if not sel:
            if self._play_now_visible:
                self.btn_play_now.configure(state='disabled',
                                            fg_color='#555555', text_color='#888888')
            return
        item = sel[0]
        all_items = self.tree.get_children()
        try:
            idx = list(all_items).index(item)
            playlist_idx = self.display_indices[idx]
        except (ValueError, IndexError):
            if self._play_now_visible:
                self.btn_play_now.configure(state='disabled',
                                            fg_color='#555555', text_color='#888888')
            return

        # Show "Play Now" button — disable if selected track is already playing
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        if playlist_idx == self.current_index and self.is_playing and not self.is_paused:
            self.btn_play_now.configure(text=f'\u25b6  Playing \u2014 {title[:40]}',
                                        state='disabled',
                                        fg_color='#555555', text_color='#888888')
        else:
            self.btn_play_now.configure(text=f'\u25b6  Play Now \u2014 {title[:40]}',
                                        state='normal',
                                        fg_color='#f1c40f', text_color='#000000')
        if not self._play_now_visible:
            self.btn_play_now.pack(fill='x', padx=20, pady=(0, 6), after=self._controls_frame)
            self._play_now_visible = True

    def _play_now_click(self):
        """Play the currently selected track immediately."""
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
        self._last_action = 'switching'
        self.vlc_player.stop()
        self.current_index = playlist_idx
        loaded = self._load(playlist_idx)
        if loaded:
            self.vlc_player.play()
            self.is_playing = True
            self.is_paused = False
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._playback_start_time = time.time()
            self._play_recorded = False
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()
        # Disable Play Now button after clicking it
        title = self.playlist[playlist_idx].get('title', self.playlist[playlist_idx]['basename'])
        self.btn_play_now.configure(text=f'\u25b6  Playing \u2014 {title[:40]}',
                                    state='disabled',
                                    fg_color='#555555', text_color='#888888')

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
            self._play_started_at = time.time()
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
            # Guard: don't auto-advance within 1.5s of play being issued (VLC async startup)
            if time.time() - self._play_started_at < 1.5:
                pass
            elif self.playlist and len(self.display_indices) > 1:
                self._next_track()
            elif self.playlist:
                self.stop()

        self.after(500, self._poll)


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()

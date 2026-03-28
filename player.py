
"""
A music player using CustomTkinter + VLC

Layout:
- Top bar: hamburger menu + now-playing title (big, bold)
- Left sidebar: genre groups treeview with settings gear
- Center: tag filter bar + track list
- Right: tag editor panel + volume slider
- Bottom: big play/stop buttons + scrub bar
"""

import os
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
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
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player_config.xml')


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
        self._length_filter = 'All'  # active length filter label

        # Genre groups: {group_name: [genre1, genre2, ...]}
        self._genre_groups = {}
        self._all_tags = set()
        self._all_voters = set()  # known voter names

        # Default length filter durations (in seconds) — configurable in settings
        self._length_filter_durations = [
            ('< 2 min', 0, 120),
            ('2 – 4 min', 120, 240),
            ('4 – 7 min', 240, 420),
            ('> 7 min', 420, None),
        ]

        # VLC
        self.vlc_instance = vlc.Instance()
        self.vlc_player = self.vlc_instance.media_list_player_new()
        self.vlc_media_list = self.vlc_instance.media_list_new()

        # Play tracking
        self._playback_start_time = None
        self._play_recorded = False

        # Play queue: list of playlist indices
        self._play_queue = []

        # Saved playlists: {name: [file_path, ...]}
        self._playlists = {}
        self._active_playlist = None  # name of currently active playlist filter

        # Debounce timer for search
        self._search_debounce_id = None

        # Guard to prevent _on_select re-entry during _apply_filter
        self._applying_filter = False

        self._init_database()

        self._build_ui()
        self._load_tracks_from_db()
        self._refresh_playlist_listbox()
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
        if 'length' not in columns:
            con.execute("ALTER TABLE tracks ADD COLUMN length REAL")
            con.commit()

        # Settings key-value store
        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
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

        # Backfill missing track lengths
        if MutagenFile is not None:
            cur = con.execute("SELECT id, file_path FROM tracks WHERE length IS NULL")
            rows_to_fill = cur.fetchall()
            if rows_to_fill:
                for track_id, fpath in rows_to_fill:
                    length = None
                    try:
                        audio = MutagenFile(fpath)
                        if audio is not None and audio.info is not None:
                            length = audio.info.length
                    except Exception:
                        pass
                    if length is not None:
                        con.execute("UPDATE tracks SET length = ? WHERE id = ?", (length, track_id))
                con.commit()

        con.close()
        self._load_genre_groups()

    def _load_genre_groups(self):
        """Load genre groups from XML config file (falling back to DB for migration)."""
        if os.path.exists(CONFIG_PATH):
            self._load_config_from_xml()
            return
        # Migrate from DB if XML doesn't exist yet
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT id, group_name FROM genre_groups ORDER BY sort_order, group_name")
        groups = cur.fetchall()
        self._genre_groups = {}
        for gid, gname in groups:
            cur.execute("SELECT genre FROM genre_group_members WHERE group_id = ? ORDER BY sort_order, genre", (gid,))
            self._genre_groups[gname] = [r[0] for r in cur.fetchall()]
        con.close()
        # Write initial XML config
        self._save_config_to_xml()

    def _load_config_from_xml(self):
        """Load all settings from the XML config file."""
        tree = ET.parse(CONFIG_PATH)
        root = tree.getroot()
        # Genre groups
        self._genre_groups = {}
        groups_el = root.find('genre_groups')
        if groups_el is not None:
            for group_el in groups_el.findall('group'):
                gname = group_el.get('name', '')
                members = [m.text for m in group_el.findall('member') if m.text]
                self._genre_groups[gname] = members
        # Length filter durations
        durations_el = root.find('length_filter_durations')
        if durations_el is not None:
            durations = []
            for dur_el in durations_el.findall('duration'):
                label = dur_el.get('label', '')
                lo = dur_el.get('lo')
                hi = dur_el.get('hi')
                lo = int(lo) if lo else None
                hi = int(hi) if hi else None
                durations.append((label, lo, hi))
            if durations:
                self._length_filter_durations = durations
        # Playlists
        playlists_el = root.find('playlists')
        if playlists_el is not None:
            self._playlists = {}
            for pl_el in playlists_el.findall('playlist'):
                name = pl_el.get('name', '')
                paths = [t.text for t in pl_el.findall('track') if t.text]
                self._playlists[name] = paths

    def _save_config_to_xml(self):
        """Save all settings to the XML config file."""
        root = ET.Element('music_player_config')
        # Genre groups
        groups_el = ET.SubElement(root, 'genre_groups')
        for gname, members in self._genre_groups.items():
            group_el = ET.SubElement(groups_el, 'group', name=gname)
            for member in members:
                m_el = ET.SubElement(group_el, 'member')
                m_el.text = member
        # Length filter durations
        durations_el = ET.SubElement(root, 'length_filter_durations')
        for label, lo, hi in self._length_filter_durations:
            attrs = {'label': label}
            if lo is not None:
                attrs['lo'] = str(lo)
            if hi is not None:
                attrs['hi'] = str(hi)
            ET.SubElement(durations_el, 'duration', **attrs)
        # Playlists
        playlists_el = ET.SubElement(root, 'playlists')
        for name, paths in self._playlists.items():
            pl_el = ET.SubElement(playlists_el, 'playlist', name=name)
            for path in paths:
                t_el = ET.SubElement(pl_el, 'track')
                t_el.text = path
        # Write with indentation
        ET.indent(root)
        tree = ET.ElementTree(root)
        tree.write(CONFIG_PATH, encoding='unicode', xml_declaration=True)

    def _save_genre_groups(self):
        self._save_config_to_xml()

    def _save_length_filter_durations(self):
        self._save_config_to_xml()

    def _load_tracks_from_db(self):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute(
            "SELECT file_path, title, play_count, first_played, last_played, "
            "file_created, genre, comment, length FROM tracks ORDER BY title"
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
             file_created, genre, comment, length) in rows:
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
                'length': length,
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

    def _ensure_track_in_db(self, path, title='', genre='Unknown', comment='', length=None):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created, length FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            try:
                file_created = datetime.fromtimestamp(os.path.getctime(path), tz=timezone.utc).isoformat()
            except OSError:
                file_created = None
            cur.execute(
                "INSERT INTO tracks (file_path, title, file_created, genre, comment, length) VALUES (?, ?, ?, ?, ?, ?)",
                (path, title, file_created, genre, comment, length)
            )
            con.commit()
            con.close()
            return (0, None, None, file_created, length)
        # If length was not stored yet, update it
        if row[4] is None and length is not None:
            cur.execute("UPDATE tracks SET length = ? WHERE file_path = ?", (length, path))
            con.commit()
            con.close()
            return (row[0], row[1], row[2], row[3], length)
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

    @staticmethod
    def _format_duration(seconds):
        """Format seconds as M:SS or H:MM:SS."""
        if seconds is None:
            return '—'
        seconds = int(seconds)
        if seconds < 0:
            return '—'
        if seconds >= 3600:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            s = seconds % 60
            return f'{h}:{m:02d}:{s:02d}'
        m = seconds // 60
        s = seconds % 60
        return f'{m}:{s:02d}'

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

        # ═══ OUTER LAYOUT: content column + full-height volume strip ═══
        _outer = ctk.CTkFrame(self, fg_color='transparent')
        _outer.pack(fill='both', expand=True)

        # ── VOLUME STRIP (full-height right edge) ──
        vol_strip = ctk.CTkFrame(_outer, width=70, fg_color='#1e1e2e', corner_radius=0)
        vol_strip.pack(side='right', fill='y')
        vol_strip.pack_propagate(False)

        self.btn_mute = ctk.CTkButton(vol_strip, text='\U0001f50a', width=56, height=36,
                                      font=ctk.CTkFont(size=20), fg_color='transparent',
                                      command=self._toggle_mute)
        self.btn_mute.pack(pady=(12, 4))

        self.vol = tk.DoubleVar(value=0.8)
        self._muted = False
        self._pre_mute_vol = 0.8
        self.vol_slider = ctk.CTkSlider(vol_strip, from_=0.0, to=1.0, variable=self.vol,
                                        orientation='vertical', command=self._on_volume,
                                        height=300, width=26,
                                        button_length=20,
                                        button_color='#00bcd4', button_hover_color='#26c6da',
                                        progress_color='#00bcd4')
        self.vol_slider.pack(fill='y', expand=True, padx=10, pady=6)

        self.lbl_vol_pct = ctk.CTkLabel(vol_strip, text='80%',
                                         font=ctk.CTkFont(size=12, weight='bold'))
        self.lbl_vol_pct.pack(pady=(4, 12))

        self._on_volume()

        # ── CONTENT COLUMN (everything else) ──
        _content = ctk.CTkFrame(_outer, fg_color='transparent')
        _content.pack(side='left', fill='both', expand=True)

        # ═══ TOP BAR ═══
        top_bar = ctk.CTkFrame(_content, height=50, fg_color='#1a1a2e')
        top_bar.pack(fill='x')
        top_bar.pack_propagate(False)

        self.btn_menu = ctk.CTkButton(top_bar, text='\u2630', width=45, height=36,
                                      font=ctk.CTkFont(size=20), command=self._show_menu)
        self.btn_menu.pack(side='left', padx=(10, 6), pady=7)

        self.lbl_now_playing = ctk.CTkLabel(top_bar, text='\u266b  Not Playing',
                                            font=ctk.CTkFont(size=20, weight='bold'))
        self.lbl_now_playing.pack(side='left', fill='x', expand=True, padx=10)

        # ── Like / Dislike buttons (top-right) ──
        self._btn_thumbs_down = ctk.CTkButton(
            top_bar, text='\U0001f44e', width=50, height=36,
            font=ctk.CTkFont(size=22), fg_color='#c0392b', hover_color='#e74c3c',
            command=lambda: self._ask_voter_and_vote(-1))
        self._btn_thumbs_down.pack(side='right', padx=(4, 10), pady=7)

        self._btn_thumbs_up = ctk.CTkButton(
            top_bar, text='\U0001f44d', width=50, height=36,
            font=ctk.CTkFont(size=22), fg_color='#27ae60', hover_color='#2ecc71',
            command=lambda: self._ask_voter_and_vote(+1))
        self._btn_thumbs_up.pack(side='right', padx=0, pady=7)

        self._lbl_rating = ctk.CTkLabel(top_bar, text='\u2014',
                                         font=ctk.CTkFont(size=20, weight='bold'),
                                         text_color='#888888', width=40)
        self._lbl_rating.pack(side='right', padx=(4, 4), pady=7)

        self.load_progress = ctk.CTkProgressBar(top_bar, mode='determinate', width=200)
        self.load_progress.set(0)
        self.lbl_load = ctk.CTkLabel(top_bar, text='', font=ctk.CTkFont(size=10))

        # ═══ SCRUB BAR (under Now Playing) ═══
        scrub_frame = ctk.CTkFrame(_content, fg_color='#1a1a2e')
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
        self._controls_frame = ctk.CTkFrame(_content, fg_color='#1a1a2e')
        self._controls_frame.pack(fill='x')

        btn_row = ctk.CTkFrame(self._controls_frame, fg_color='transparent')
        btn_row.pack(fill='x', padx=20, pady=(6, 10))
        btn_row.columnconfigure(0, weight=2)
        btn_row.columnconfigure(1, weight=1)
        btn_row.columnconfigure(2, weight=0)

        self.btn_play = ctk.CTkButton(btn_row, text='\u25b6', height=50,
                                      font=ctk.CTkFont(size=28), command=self.play_pause,
                                      fg_color='#1f6aa5', hover_color='#1a5a8a')
        self.btn_play.grid(row=0, column=0, sticky='ew', padx=(0, 3))

        self.btn_stop = ctk.CTkButton(btn_row, text='\u23f9', height=50,
                                      font=ctk.CTkFont(size=28), command=self.stop,
                                      fg_color='#c0392b', hover_color='#e74c3c')
        self.btn_stop.grid(row=0, column=1, sticky='ew', padx=(3, 3))

        # Speed control
        speed_frame = ctk.CTkFrame(btn_row, fg_color='#2b2b2b', corner_radius=8)
        speed_frame.grid(row=0, column=2, sticky='ns', padx=(3, 0))

        ctk.CTkLabel(speed_frame, text='Speed', font=ctk.CTkFont(size=9),
                     text_color='#888888').pack(pady=(4, 0))
        self._speed_var = tk.DoubleVar(value=1.0)
        self._speed_label = ctk.CTkLabel(speed_frame, text='1.0×', font=ctk.CTkFont(size=11, weight='bold'))
        self._speed_label.pack(pady=(0, 2))
        speed_down = ctk.CTkButton(speed_frame, text='−', width=28, height=20,
                                    font=ctk.CTkFont(size=14), fg_color='#3b3b3b',
                                    command=self._speed_down)
        speed_down.pack(side='left', padx=(4, 2), pady=(0, 4))
        speed_up = ctk.CTkButton(speed_frame, text='+', width=28, height=20,
                                  font=ctk.CTkFont(size=14), fg_color='#3b3b3b',
                                  command=self._speed_up)
        speed_up.pack(side='left', padx=(2, 4), pady=(0, 4))

        # ═══ PLAY NOW BAR (under play controls, hidden until track selected) ═══
        self._play_bar = ctk.CTkFrame(_content, fg_color='transparent')
        self.btn_play_now = ctk.CTkButton(self._play_bar, text='\u25b6  Play Now', height=44,
                                          font=ctk.CTkFont(size=20, weight='bold'),
                                          fg_color='#f1c40f', hover_color='#f39c12',
                                          text_color='#000000',
                                          command=self._play_now_click)
        self.btn_play_now.pack(side='left', fill='x', expand=True, padx=(0, 3))
        self.btn_play_next = ctk.CTkButton(self._play_bar, text='\u23ed  Play Next', height=44,
                                           font=ctk.CTkFont(size=16, weight='bold'),
                                           fg_color='#e67e22', hover_color='#d35400',
                                           text_color='#000000',
                                           command=self._play_next_click)
        self.btn_play_next.pack(side='left', fill='x', padx=(3, 0))
        self._play_now_visible = False

        self._tag_buttons = []

        # ═══ MAIN AREA: Browse + Queue ═══
        main_area = ctk.CTkFrame(_content, fg_color='transparent')
        main_area.pack(fill='both', expand=True, padx=6, pady=(10, 4))

        # ── PLAY QUEUE PANEL (right side of browse area) ──
        queue_panel = ctk.CTkFrame(main_area, width=200, fg_color='#2b2b2b', corner_radius=8)
        queue_panel.pack(side='right', fill='y', padx=(4, 0))
        queue_panel.pack_propagate(False)

        queue_header = ctk.CTkFrame(queue_panel, fg_color='transparent')
        queue_header.pack(fill='x', padx=6, pady=(6, 2))
        self._queue_title_lbl = ctk.CTkLabel(queue_header, text='Queue (0)',
                     font=ctk.CTkFont(size=12, weight='bold'))
        self._queue_title_lbl.pack(side='left')
        ctk.CTkButton(queue_header, text='✕', width=24, height=22,
                      font=ctk.CTkFont(size=12), fg_color='transparent',
                      hover_color='#3b3b3b', command=self._clear_queue).pack(side='right')

        self._queue_listbox = tk.Listbox(
            queue_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._queue_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self._queue_listbox.bind('<Button-3>', self._on_queue_right_click)

        queue_btn_row = ctk.CTkFrame(queue_panel, fg_color='transparent')
        queue_btn_row.pack(fill='x', padx=4, pady=(0, 6))
        ctk.CTkButton(queue_btn_row, text='▲', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_move_up).pack(side='left', padx=2)
        ctk.CTkButton(queue_btn_row, text='▼', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_move_down).pack(side='left', padx=2)
        ctk.CTkButton(queue_btn_row, text='🗑', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_remove_selected).pack(side='right', padx=2)
        ctk.CTkButton(queue_btn_row, text='🎲', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._random_queue_dialog).pack(side='right', padx=2)

        # ── BROWSE PANEL (fills remaining space) ──
        browse = ctk.CTkFrame(main_area, fg_color='#2b2b2b', corner_radius=8)
        browse.pack(side='right', fill='both', expand=True)

        # ── LEFT SIDEBAR (genre + playlist panels) ──
        left_sidebar = ctk.CTkFrame(main_area, width=170, fg_color='transparent')
        left_sidebar.pack(side='left', fill='y', padx=(0, 4))
        left_sidebar.pack_propagate(False)

        # ── GENRE LISTBOX ──
        genre_panel = ctk.CTkFrame(left_sidebar, fg_color='#2b2b2b', corner_radius=8)
        genre_panel.pack(fill='both', expand=True, pady=(0, 4))

        genre_header = ctk.CTkFrame(genre_panel, fg_color='transparent')
        genre_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(genre_header, text='Genre',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(side='left')
        ctk.CTkButton(
            genre_header, text='\u2699', width=24, height=22,
            font=ctk.CTkFont(size=12), fg_color='transparent',
            hover_color='#3b3b3b', command=self._open_settings
        ).pack(side='right')

        self._genre_listbox = tk.Listbox(
            genre_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._genre_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self._genre_listbox.bind('<<ListboxSelect>>', self._on_genre_listbox_select)

        # ── PLAYLIST PANEL ──
        playlist_panel = ctk.CTkFrame(left_sidebar, fg_color='#2b2b2b', corner_radius=8)
        playlist_panel.pack(fill='both', expand=True)

        playlist_header = ctk.CTkFrame(playlist_panel, fg_color='transparent')
        playlist_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(playlist_header, text='Playlists',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(side='left')
        ctk.CTkButton(playlist_header, text='+', width=24, height=22,
                      font=ctk.CTkFont(size=14), fg_color='transparent',
                      hover_color='#3b3b3b', command=self._create_playlist).pack(side='right')

        self._playlist_listbox = tk.Listbox(
            playlist_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._playlist_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self._playlist_listbox.bind('<<ListboxSelect>>', self._on_playlist_select)
        self._playlist_listbox.bind('<Button-3>', self._on_playlist_right_click)

        # ── Filter Row 1: Rating + Liked by ──
        filter_row1 = ctk.CTkFrame(browse, fg_color='transparent')
        filter_row1.pack(fill='x', padx=8, pady=(8, 2))
        filter_row1.columnconfigure(1, weight=1)   # rating dropdown
        filter_row1.columnconfigure(3, weight=2)   # liked-by dropdown
        filter_row1.columnconfigure(4, weight=0)   # spacer

        _dd_style = dict(height=26, font=ctk.CTkFont(size=11),
                         fg_color='#3b3b3b', button_color='#4a4a4a',
                         button_hover_color='#555555',
                         dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
                         dropdown_text_color='#dce4ee')

        ctk.CTkLabel(filter_row1, text='Rating', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=0, sticky='w', padx=(0, 4))
        self._rating_filter_var = tk.StringVar(value='All')
        rating_vals = ['All', '≥ 1', '≥ 2', '≥ 3', '≥ 5', '≥ 10', '≤ -1', '≤ -3', '= 0']
        self._rating_filter_dropdown = ctk.CTkOptionMenu(
            filter_row1, variable=self._rating_filter_var,
            values=rating_vals, command=self._on_rating_filter, **_dd_style)
        self._rating_filter_dropdown.grid(row=0, column=1, sticky='ew', padx=(0, 10))

        ctk.CTkLabel(filter_row1, text='Liked by', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=2, sticky='w', padx=(0, 4))
        self._liked_by_var = tk.StringVar(value='All')
        self._liked_by_dropdown = ctk.CTkOptionMenu(
            filter_row1, variable=self._liked_by_var,
            values=['All'], command=self._on_liked_by_filter, **_dd_style)
        self._liked_by_dropdown.grid(row=0, column=3, sticky='ew', padx=(0, 6))

        # Reset button
        self._btn_reset_filters = ctk.CTkButton(
            filter_row1, text='✕ Reset', width=60, height=24,
            font=ctk.CTkFont(size=10), fg_color='transparent',
            border_width=1, border_color='#555555',
            hover_color='#3b3b3b', text_color='#999999',
            command=self._reset_all_filters)
        self._btn_reset_filters.grid(row=0, column=5, padx=(4, 0))

        # ── Filter Row 2: First Played + Last Played + File Created + Length ──
        filter_row2 = ctk.CTkFrame(browse, fg_color='transparent')
        filter_row2.pack(fill='x', padx=8, pady=(0, 4))
        filter_row2.columnconfigure(1, weight=1)
        filter_row2.columnconfigure(3, weight=1)
        filter_row2.columnconfigure(5, weight=1)
        filter_row2.columnconfigure(7, weight=1)

        ctk.CTkLabel(filter_row2, text='First Played', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=0, sticky='w', padx=(0, 4))
        self._first_played_var = tk.StringVar(value='All')
        self._first_played_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._first_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_first_played_filter, **_dd_style)
        self._first_played_dropdown.grid(row=0, column=1, sticky='ew', padx=(0, 10))

        ctk.CTkLabel(filter_row2, text='Last Played', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=2, sticky='w', padx=(0, 4))
        self._last_played_var = tk.StringVar(value='All')
        self._last_played_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._last_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_last_played_filter, **_dd_style)
        self._last_played_dropdown.grid(row=0, column=3, sticky='ew', padx=(0, 10))

        ctk.CTkLabel(filter_row2, text='File Created', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=4, sticky='w', padx=(0, 4))
        self._file_created_var = tk.StringVar(value='All')
        self._file_created_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._file_created_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_file_created_filter, **_dd_style)
        self._file_created_dropdown.grid(row=0, column=5, sticky='ew', padx=(0, 10))

        ctk.CTkLabel(filter_row2, text='Length', font=ctk.CTkFont(size=11, weight='bold')).grid(row=0, column=6, sticky='w', padx=(0, 4))
        self._length_filter_var = tk.StringVar(value='All')
        self._length_filter_dropdown = ctk.CTkOptionMenu(
            filter_row2, variable=self._length_filter_var,
            values=self._get_length_filter_values(), command=self._on_length_filter, **_dd_style)
        self._length_filter_dropdown.grid(row=0, column=7, sticky='ew')

        # Track list section
        tree_frame = ctk.CTkFrame(browse, fg_color='transparent')
        tree_frame.pack(fill='both', expand=True, padx=6, pady=(0, 6))

        # Tag filter bar — scrollable multi-row wrapping layout
        self.tag_bar_frame = ctk.CTkScrollableFrame(
            tree_frame, fg_color='#2b2b2b', corner_radius=6,
            height=70, orientation='vertical')
        self.tag_bar_frame.pack(fill='x', pady=(0, 4))

        # Search box (below tags)
        self._search_var = tk.StringVar()
        self._search_var.trace_add('write', lambda *_: self._debounced_search())
        self._search_entry = ctk.CTkEntry(tree_frame, textvariable=self._search_var,
                                           placeholder_text='\U0001f50d  Search title, comment, tags, liked by\u2026',
                                           height=30, font=ctk.CTkFont(size=12))
        self._search_entry.pack(fill='x', pady=(0, 4))

        # Track count label
        self._track_count_lbl = ctk.CTkLabel(tree_frame, text='0 tracks',
                                              font=ctk.CTkFont(size=10),
                                              text_color='#888888', anchor='w')
        self._track_count_lbl.pack(fill='x', pady=(0, 2))

        self._all_columns = ('Title', 'Length', 'Rating', 'Comment', 'Tags', 'Liked By', 'Disliked By',
                              'Plays', 'First Played', 'Last Played', 'File Created')
        self.tree = ttk.Treeview(tree_frame,
                                 columns=self._all_columns,
                                 show='headings', height=18)
        self.tree.column('Title', width=200, anchor='w')
        self.tree.column('Length', width=55, anchor='center')
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

        # Populate the genre listbox
        self._genre_listbox.delete(0, 'end')
        for v in values:
            self._genre_listbox.insert('end', v)
        # Select "All" by default
        self._genre_listbox.selection_clear(0, 'end')
        self._genre_listbox.selection_set(0)
        self._genre_listbox.see(0)

    def _on_genre_listbox_select(self, event=None):
        """Handle genre listbox selection."""
        sel = self._genre_listbox.curselection()
        if not sel:
            return
        choice = self._genre_listbox.get(sel[0])
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

    def _on_length_filter(self, choice):
        self._length_filter_var.set(choice)
        self._apply_filter()
        self._build_tag_bar()

    def _get_length_filter_values(self):
        """Return dropdown values list from the configurable length filter durations."""
        return ['All'] + [lbl for lbl, lo, hi in self._length_filter_durations]

    def _rebuild_length_filter_dropdown(self):
        """Rebuild the length filter dropdown with current duration labels."""
        if hasattr(self, '_length_filter_dropdown'):
            self._length_filter_dropdown.configure(values=self._get_length_filter_values())
            self._length_filter_var.set('All')

    def _reset_all_filters(self):
        """Reset all filter dropdowns back to 'All'."""
        self._rating_filter_var.set('All')
        self._rating_threshold = None
        self._liked_by_var.set('All')
        self._liked_by_filter = None
        self._first_played_var.set('All')
        self._last_played_var.set('All')
        self._file_created_var.set('All')
        self._length_filter_var.set('All')
        self._active_genre = 'All'
        if hasattr(self, '_genre_listbox'):
            self._genre_listbox.selection_clear(0, 'end')
            self._genre_listbox.selection_set(0)
            self._genre_listbox.see(0)
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
        length_frame = ctk.CTkFrame(tab_container, fg_color='transparent')

        active_tab = [None]
        tab_buttons = {}

        def show_tab(name):
            if active_tab[0] == name:
                return
            active_tab[0] = name
            genre_frame.pack_forget()
            tags_frame.pack_forget()
            length_frame.pack_forget()
            for btn in tab_buttons.values():
                btn.configure(fg_color='transparent')
            if name == 'genres':
                genre_frame.pack(fill='both', expand=True)
            elif name == 'tags':
                tags_frame.pack(fill='both', expand=True)
            elif name == 'length':
                length_frame.pack(fill='both', expand=True)
            tab_buttons[name].configure(fg_color='#1f6aa5')

        btn_tab_genres = ctk.CTkButton(tab_bar, text='Genres', height=30,
                                        font=ctk.CTkFont(size=12, weight='bold'),
                                        fg_color='#1f6aa5', border_width=1, border_color='#555555',
                                        command=lambda: show_tab('genres'))
        btn_tab_genres.pack(side='left', padx=(0, 4))
        tab_buttons['genres'] = btn_tab_genres
        btn_tab_tags = ctk.CTkButton(tab_bar, text='Tags', height=30,
                                      font=ctk.CTkFont(size=12, weight='bold'),
                                      fg_color='transparent', border_width=1, border_color='#555555',
                                      command=lambda: show_tab('tags'))
        btn_tab_tags.pack(side='left', padx=(0, 4))
        tab_buttons['tags'] = btn_tab_tags
        btn_tab_length = ctk.CTkButton(tab_bar, text='Length', height=30,
                                        font=ctk.CTkFont(size=12, weight='bold'),
                                        fg_color='transparent', border_width=1, border_color='#555555',
                                        command=lambda: show_tab('length'))
        btn_tab_length.pack(side='left')
        tab_buttons['length'] = btn_tab_length

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

        # ═══════════════ LENGTH TAB ═══════════════
        ctk.CTkLabel(length_frame, text='Length Filter Durations',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(6, 2))
        ctk.CTkLabel(length_frame, text='Configure the duration ranges for the Length filter dropdown.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 6))

        # Working copy of durations: list of [label, lo_seconds_or_None, hi_seconds_or_None]
        working_durations = [list(d) for d in self._length_filter_durations]

        length_content = ctk.CTkScrollableFrame(length_frame)
        length_content.pack(fill='both', expand=True)

        def _secs_to_min_str(secs):
            if secs is None:
                return ''
            m = secs // 60
            s = secs % 60
            return f'{m}:{s:02d}' if s else str(m)

        def _parse_min_str(text):
            """Parse 'M' or 'M:SS' to seconds, or None if empty."""
            text = text.strip()
            if not text:
                return None
            parts = text.split(':')
            try:
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                return int(parts[0]) * 60
            except ValueError:
                return None

        def rebuild_length_tab():
            for w in length_content.winfo_children():
                w.destroy()

            for i, dur in enumerate(working_durations):
                row = ctk.CTkFrame(length_content, fg_color='#2b2b2b', corner_radius=8)
                row.pack(fill='x', pady=2)
                row.columnconfigure(1, weight=1)
                row.columnconfigure(3, weight=0)
                row.columnconfigure(5, weight=0)

                ctk.CTkLabel(row, text='Label', font=ctk.CTkFont(size=11),
                             text_color='#888888').grid(row=0, column=0, padx=(8, 4), pady=6)
                lbl_var = tk.StringVar(value=dur[0])
                lbl_entry = ctk.CTkEntry(row, textvariable=lbl_var, width=120, height=26,
                                          font=ctk.CTkFont(size=11))
                lbl_entry.grid(row=0, column=1, sticky='ew', padx=(0, 8), pady=6)

                ctk.CTkLabel(row, text='From', font=ctk.CTkFont(size=11),
                             text_color='#888888').grid(row=0, column=2, padx=(0, 4), pady=6)
                lo_var = tk.StringVar(value=_secs_to_min_str(dur[1]))
                lo_entry = ctk.CTkEntry(row, textvariable=lo_var, width=50, height=26,
                                         font=ctk.CTkFont(size=11),
                                         placeholder_text='min')
                lo_entry.grid(row=0, column=3, padx=(0, 8), pady=6)

                ctk.CTkLabel(row, text='To', font=ctk.CTkFont(size=11),
                             text_color='#888888').grid(row=0, column=4, padx=(0, 4), pady=6)
                hi_var = tk.StringVar(value=_secs_to_min_str(dur[2]))
                hi_entry = ctk.CTkEntry(row, textvariable=hi_var, width=50, height=26,
                                         font=ctk.CTkFont(size=11),
                                         placeholder_text='min')
                hi_entry.grid(row=0, column=5, padx=(0, 4), pady=6)

                ctk.CTkButton(row, text='\U0001f5d1', width=30, height=24, fg_color='transparent',
                              command=lambda idx=i: delete_duration(idx)).grid(row=0, column=6, padx=(0, 4), pady=6)

                # Bind changes
                def _on_change(idx=i, lv=lbl_var, lov=lo_var, hiv=hi_var):
                    working_durations[idx] = [lv.get(), _parse_min_str(lov.get()), _parse_min_str(hiv.get())]
                lbl_var.trace_add('write', lambda *_, f=_on_change: f())
                lo_var.trace_add('write', lambda *_, f=_on_change: f())
                hi_var.trace_add('write', lambda *_, f=_on_change: f())

            if not working_durations:
                ctk.CTkLabel(length_content, text='No duration ranges configured.',
                             font=ctk.CTkFont(size=11), text_color='#666666').pack(pady=20)

        def delete_duration(idx):
            working_durations.pop(idx)
            rebuild_length_tab()

        def add_duration():
            working_durations.append(['New Range', 0, 300])
            rebuild_length_tab()

        rebuild_length_tab()

        length_btn_row = ctk.CTkFrame(length_frame, fg_color='transparent')
        length_btn_row.pack(fill='x', pady=(6, 0))
        ctk.CTkButton(length_btn_row, text='+ Add Range', command=add_duration).pack(side='left', padx=4)

        ctk.CTkLabel(length_frame, text='Enter times as minutes (e.g. "2") or M:SS (e.g. "4:30").\n'
                     'Leave From or To empty for open-ended ranges.',
                     font=ctk.CTkFont(size=10), text_color='#666666').pack(pady=(4, 0))

        # ═══════════════ BOTTOM BUTTONS ═══════════════
        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=10, pady=10)
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)

        def snapshot_settings():
            """Copy current config XML with a datestamp."""
            if not os.path.exists(CONFIG_PATH):
                messagebox.showinfo('Snapshot', 'No config file found yet.', parent=dialog)
                return
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            base, ext = os.path.splitext(CONFIG_PATH)
            dest = f'{base}_{stamp}{ext}'
            shutil.copy2(CONFIG_PATH, dest)
            messagebox.showinfo('Snapshot', f'Settings snapshot saved:\n{os.path.basename(dest)}', parent=dialog)

        ctk.CTkButton(btn_row, text='\U0001f4be Snapshot Settings', fg_color='#2d6a4f',
                      hover_color='#40916c', command=snapshot_settings).pack(side='left', padx=4)

        def show_all_genres():
            """Display all genres detected in the library."""
            genre_dialog = ctk.CTkToplevel(dialog)
            genre_dialog.title('All Detected Genres')
            genre_dialog.geometry('350x450')
            genre_dialog.transient(dialog)
            genre_dialog.after(100, genre_dialog.grab_set)

            ctk.CTkLabel(genre_dialog, text='All Detected Genres',
                         font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(12, 2))
            ctk.CTkLabel(genre_dialog, text=f'{len(self.genres)} genres found in library',
                         font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 8))

            genre_list = ctk.CTkScrollableFrame(genre_dialog, fg_color='#1a1a2e')
            genre_list.pack(fill='both', expand=True, padx=16, pady=(0, 8))

            for i, genre in enumerate(sorted(self.genres), 1):
                # Count tracks with this genre
                count = sum(1 for e in self.playlist if e.get('genre') == genre)
                row = ctk.CTkFrame(genre_list, fg_color='#2b2b2b' if i % 2 == 0 else '#252535',
                                   corner_radius=4)
                row.pack(fill='x', pady=1)
                ctk.CTkLabel(row, text=genre, font=ctk.CTkFont(size=11),
                             text_color='#dce4ee').pack(side='left', padx=8, pady=4)
                ctk.CTkLabel(row, text=f'{count} tracks', font=ctk.CTkFont(size=11),
                             text_color='#888888').pack(side='right', padx=8, pady=4)

            ctk.CTkButton(genre_dialog, text='Close', fg_color='#555555', width=100,
                          command=genre_dialog.destroy).pack(pady=(4, 12))

        ctk.CTkButton(btn_row, text='\U0001f3b5 Show Genres', fg_color='#4a4a4a',
                      hover_color='#555555', command=show_all_genres).pack(side='left', padx=4)

        def save_and_close():
            self._genre_groups = working_groups
            self._save_genre_groups()
            self._build_genre_list()
            # Save length filter durations
            valid_durations = [d for d in working_durations if d[0].strip()]
            self._length_filter_durations = [tuple(d) for d in valid_durations]
            self._save_length_filter_durations()
            self._rebuild_length_filter_dropdown()
            self._active_genre = 'All'
            self._apply_filter()
            self._build_tag_bar()
            dialog.destroy()

        ctk.CTkButton(btn_row, text='Save', command=save_and_close).pack(side='right', padx=4)

        show_tab('genres')

    # ── Filter logic ─────────────────────────────────────

    def _debounced_search(self):
        """Debounce search input — waits 200ms after last keystroke before filtering."""
        if self._search_debounce_id is not None:
            self.after_cancel(self._search_debounce_id)
        self._search_debounce_id = self.after(200, self._apply_filter)

    # Column-to-entry-key mapping for sorting
    _SORT_KEYS = {
        'Title': lambda e: (e.get('title') or e['basename']).lower(),
        'Length': lambda e: e.get('length') or 0,
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
        self._applying_filter = True
        try:
            self._apply_filter_inner()
        finally:
            self._applying_filter = False

    def _apply_filter_inner(self):
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

        # Build playlist path set if filtering by playlist
        playlist_paths = None
        if self._active_playlist and self._active_playlist in self._playlists:
            playlist_paths = set(self._playlists[self._active_playlist])

        for idx, entry in enumerate(self.playlist):
            # Playlist filter
            if playlist_paths is not None:
                if entry['path'] not in playlist_paths:
                    continue
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
            # Length filter
            length_label = getattr(self, '_length_filter_var', None)
            if length_label and length_label.get() != 'All':
                track_len = entry.get('length')
                lf_label = length_label.get()
                matched_len = False
                for lbl, lo, hi in self._length_filter_durations:
                    if lbl == lf_label:
                        if track_len is None:
                            matched_len = False
                        elif lo is not None and hi is not None:
                            matched_len = lo <= track_len < hi
                        elif lo is not None:
                            matched_len = track_len >= lo
                        elif hi is not None:
                            matched_len = track_len < hi
                        break
                if not matched_len:
                    continue
            if search_term:
                title_lower = entry.get('title', entry['basename']).lower()
                comment_lower = entry.get('comment', '').lower()
                tags_lower = ' '.join(entry.get('tags', [])).lower()
                liked_lower = ' '.join(entry.get('liked_by', set())).lower()
                if (search_term not in title_lower
                        and search_term not in comment_lower
                        and search_term not in tags_lower
                        and search_term not in liked_lower):
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
            length_str = self._format_duration(entry.get('length'))
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
            row_tags = []
            if idx == self.current_index and self.is_playing:
                row_tags.append(self._now_playing_tag)
            self.tree.insert('', 'end',
                             values=(title, length_str, rating_str, comment, tags_str, liked_str, disliked_str,
                                     plays, first_p, last_p, file_c),
                             tags=tuple(row_tags))
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

        # Update track count
        if hasattr(self, '_track_count_lbl'):
            total = len(self.playlist)
            shown = len(self.display_indices)
            if shown == total:
                self._track_count_lbl.configure(text=f'{total} tracks')
            else:
                self._track_count_lbl.configure(text=f'{shown} of {total} tracks')

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
        length = None
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
            try:
                audio = MutagenFile(path)
                if audio is not None and audio.info is not None:
                    length = audio.info.length
            except Exception:
                pass
        entry = {'path': path, 'title': title, 'basename': os.path.basename(path),
                 'genre': genre, 'comment': comment, 'length': length, 'tags': [],
                 'rating': 0, 'liked_by': set(), 'disliked_by': set()}
        self.playlist.append(entry)
        self.genres.add(genre)
        stats = self._ensure_track_in_db(path, title, genre, comment, length)
        entry['play_count'] = stats[0]
        entry['first_played'] = stats[1]
        entry['last_played'] = stats[2]
        entry['file_created'] = stats[3]
        entry['length'] = stats[4]
        return True

    # ── Playback ─────────────────────────────────────────

    def _update_now_playing_highlight(self):
        """Update the now-playing row tag without rebuilding the treeview."""
        all_items = self.tree.get_children()
        for pos, item in enumerate(all_items):
            tags = list(self.tree.item(item, 'tags'))
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
        # Check play queue first
        queue_next = self._pop_queue()
        if queue_next is not None:
            nxt = queue_next
        elif self.display_indices:
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

    # ── Playback speed ───────────────────────────────────

    def _apply_speed(self):
        """Apply the current speed to VLC."""
        speed = self._speed_var.get()
        mp = self.vlc_player.get_media_player()
        mp.set_rate(speed)
        self._speed_label.configure(text=f'{speed:.1f}×')

    def _speed_up(self):
        cur = self._speed_var.get()
        new = min(cur + 0.1, 3.0)
        self._speed_var.set(round(new, 1))
        self._apply_speed()

    def _speed_down(self):
        cur = self._speed_var.get()
        new = max(cur - 0.1, 0.3)
        self._speed_var.set(round(new, 1))
        self._apply_speed()

    # ── Play queue management ────────────────────────────

    def _refresh_queue_listbox(self):
        """Rebuild the queue listbox from self._play_queue."""
        self._queue_listbox.delete(0, 'end')
        for pl_idx in self._play_queue:
            entry = self.playlist[pl_idx]
            title = entry.get('title', entry['basename'])
            self._queue_listbox.insert('end', title[:40])
        self._queue_title_lbl.configure(text=f'Queue ({len(self._play_queue)})')

    def _add_to_queue(self, playlist_idx):
        """Add a track to the end of the play queue."""
        self._play_queue.append(playlist_idx)
        self._refresh_queue_listbox()

    def _insert_in_queue(self, playlist_idx, position=0):
        """Insert a track at a specific position in the queue."""
        self._play_queue.insert(position, playlist_idx)
        self._refresh_queue_listbox()

    def _pop_queue(self):
        """Pop and return the next track index from the queue, or None."""
        if self._play_queue:
            idx = self._play_queue.pop(0)
            self._refresh_queue_listbox()
            return idx
        return None

    def _clear_queue(self):
        """Clear the entire play queue."""
        self._play_queue.clear()
        self._refresh_queue_listbox()

    def _queue_move_up(self):
        sel = self._queue_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._play_queue[i - 1], self._play_queue[i] = self._play_queue[i], self._play_queue[i - 1]
        self._refresh_queue_listbox()
        self._queue_listbox.selection_set(i - 1)
        self._queue_listbox.see(i - 1)

    def _queue_move_down(self):
        sel = self._queue_listbox.curselection()
        if not sel or sel[0] >= len(self._play_queue) - 1:
            return
        i = sel[0]
        self._play_queue[i + 1], self._play_queue[i] = self._play_queue[i], self._play_queue[i + 1]
        self._refresh_queue_listbox()
        self._queue_listbox.selection_set(i + 1)
        self._queue_listbox.see(i + 1)

    def _queue_remove_selected(self):
        sel = self._queue_listbox.curselection()
        if not sel:
            return
        self._play_queue.pop(sel[0])
        self._refresh_queue_listbox()

    def _on_queue_right_click(self, ev):
        """Context menu for queue items."""
        idx = self._queue_listbox.nearest(ev.y)
        if idx < 0 or idx >= len(self._play_queue):
            return
        self._queue_listbox.selection_clear(0, 'end')
        self._queue_listbox.selection_set(idx)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Remove', command=lambda: self._queue_remove_at(idx))
        menu.add_command(label='Clear Queue', command=self._clear_queue)
        menu.tk_popup(ev.x_root, ev.y_root)

    def _queue_remove_at(self, idx):
        if 0 <= idx < len(self._play_queue):
            self._play_queue.pop(idx)
            self._refresh_queue_listbox()

    def _random_queue_dialog(self):
        """Open a dialog to configure and generate a random play queue."""
        import random as _random

        dialog = ctk.CTkToplevel(self)
        dialog.title('Random Queue Generator')
        dialog.geometry('480x550')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text='Random Queue Generator',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(12, 2))
        ctk.CTkLabel(dialog, text='Configure genre proportions, rating, and recency filters.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 8))

        # Queue size
        size_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        size_frame.pack(fill='x', padx=16, pady=(0, 6))
        ctk.CTkLabel(size_frame, text='Queue size:', font=ctk.CTkFont(size=12)).pack(side='left')
        queue_size_var = tk.IntVar(value=20)
        ctk.CTkEntry(size_frame, textvariable=queue_size_var, width=60, height=28,
                     font=ctk.CTkFont(size=12)).pack(side='left', padx=8)

        # Rating filter
        rating_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        rating_frame.pack(fill='x', padx=16, pady=(0, 6))
        ctk.CTkLabel(rating_frame, text='Min rating:', font=ctk.CTkFont(size=12)).pack(side='left')
        min_rating_var = tk.IntVar(value=0)
        ctk.CTkEntry(rating_frame, textvariable=min_rating_var, width=60, height=28,
                     font=ctk.CTkFont(size=12)).pack(side='left', padx=8)

        # Recency filter
        recency_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        recency_frame.pack(fill='x', padx=16, pady=(0, 8))
        ctk.CTkLabel(recency_frame, text='Not played in last:', font=ctk.CTkFont(size=12)).pack(side='left')
        recency_var = tk.StringVar(value='No filter')
        recency_vals = ['No filter', '1 day', '3 days', '1 week', '2 weeks', '1 month', 'Never played']
        ctk.CTkOptionMenu(recency_frame, variable=recency_var, values=recency_vals,
                          width=140, height=28, font=ctk.CTkFont(size=11),
                          fg_color='#3b3b3b', button_color='#4a4a4a',
                          dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5').pack(side='left', padx=8)

        # Genre proportions
        ctk.CTkLabel(dialog, text='Genre Proportions (weights):',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(anchor='w', padx=16, pady=(4, 2))

        genre_scroll = ctk.CTkScrollableFrame(dialog, fg_color='#1a1a2e')
        genre_scroll.pack(fill='both', expand=True, padx=16, pady=(0, 8))

        genre_weight_vars = {}
        for genre in sorted(self.genres):
            row = ctk.CTkFrame(genre_scroll, fg_color='transparent')
            row.pack(fill='x', pady=1)
            ctk.CTkLabel(row, text=genre, font=ctk.CTkFont(size=11),
                         text_color='#dce4ee', width=180, anchor='w').pack(side='left', padx=(8, 4))
            wvar = tk.IntVar(value=1)
            genre_weight_vars[genre] = wvar
            ctk.CTkEntry(row, textvariable=wvar, width=50, height=24,
                         font=ctk.CTkFont(size=11)).pack(side='left', padx=4)
            # Track count
            count = sum(1 for e in self.playlist if e.get('genre') == genre)
            ctk.CTkLabel(row, text=f'({count})', font=ctk.CTkFont(size=10),
                         text_color='#666666').pack(side='left', padx=4)

        # Buttons
        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=16, pady=(4, 12))

        def generate():
            size = max(1, queue_size_var.get())
            min_rat = min_rating_var.get()
            recency = recency_var.get()

            # Build recency cutoff
            cutoff = None
            if recency == 'Never played':
                cutoff = 'never'
            elif recency != 'No filter':
                days_map = {'1 day': 1, '3 days': 3, '1 week': 7, '2 weeks': 14, '1 month': 30}
                days = days_map.get(recency, 0)
                if days:
                    from datetime import timedelta
                    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()

            # Build genre weights
            weights = {}
            for genre, wvar in genre_weight_vars.items():
                w = max(0, wvar.get())
                if w > 0:
                    weights[genre] = w

            # Collect eligible tracks per genre
            eligible_by_genre = {}
            for idx, entry in enumerate(self.playlist):
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
                eligible_by_genre.setdefault(g, []).append(idx)

            if not eligible_by_genre:
                messagebox.showinfo('Random Queue', 'No tracks match the criteria.', parent=dialog)
                return

            # Build weighted genre distribution
            genre_list = []
            weight_list = []
            for g in eligible_by_genre:
                genre_list.append(g)
                weight_list.append(weights.get(g, 1))

            queue = []
            for _ in range(size):
                if not genre_list:
                    break
                chosen_genre = _random.choices(genre_list, weights=weight_list, k=1)[0]
                pool = eligible_by_genre.get(chosen_genre, [])
                available = [t for t in pool if t not in queue]
                if not available:
                    # Remove exhausted genre
                    gi = genre_list.index(chosen_genre)
                    genre_list.pop(gi)
                    weight_list.pop(gi)
                    continue
                queue.append(_random.choice(available))

            self._play_queue = queue
            self._refresh_queue_listbox()
            dialog.destroy()

        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)
        ctk.CTkButton(btn_row, text='Generate Queue', fg_color='#1f6aa5',
                      command=generate).pack(side='right', padx=4)

    # ── Playlist management ────────────────────────────

    def _refresh_playlist_listbox(self):
        """Rebuild the playlist listbox."""
        self._playlist_listbox.delete(0, 'end')
        self._playlist_listbox.insert('end', '♫  All Tracks')
        for name in sorted(self._playlists.keys()):
            count = len(self._playlists[name])
            self._playlist_listbox.insert('end', f'{name}  ({count})')
        # Highlight active
        if self._active_playlist is None:
            self._playlist_listbox.selection_set(0)
        else:
            names = sorted(self._playlists.keys())
            if self._active_playlist in names:
                self._playlist_listbox.selection_set(names.index(self._active_playlist) + 1)

    def _create_playlist(self):
        name = simpledialog.askstring('New Playlist', 'Playlist name:', parent=self)
        if name and name.strip():
            name = name.strip()
            if name not in self._playlists:
                self._playlists[name] = []
                self._save_config_to_xml()
                self._refresh_playlist_listbox()

    def _on_playlist_select(self, event=None):
        sel = self._playlist_listbox.curselection()
        if not sel:
            return
        if sel[0] == 0:
            self._active_playlist = None
        else:
            names = sorted(self._playlists.keys())
            idx = sel[0] - 1
            if idx < len(names):
                self._active_playlist = names[idx]
            else:
                self._active_playlist = None
        self._apply_filter()
        self._build_tag_bar()

    def _on_playlist_right_click(self, ev):
        idx = self._playlist_listbox.nearest(ev.y)
        if idx < 0:
            return
        self._playlist_listbox.selection_clear(0, 'end')
        self._playlist_listbox.selection_set(idx)
        if idx == 0:
            return  # "All Tracks" has no context menu
        names = sorted(self._playlists.keys())
        pl_idx = idx - 1
        if pl_idx >= len(names):
            return
        pl_name = names[pl_idx]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Rename…', command=lambda: self._rename_playlist(pl_name))
        menu.add_command(label='Delete', command=lambda: self._delete_playlist(pl_name))
        menu.add_separator()
        menu.add_command(label='Load into Queue', command=lambda: self._playlist_to_queue(pl_name))
        menu.tk_popup(ev.x_root, ev.y_root)

    def _rename_playlist(self, old_name):
        new_name = simpledialog.askstring('Rename Playlist', 'New name:',
                                          initialvalue=old_name, parent=self)
        if new_name and new_name.strip() and new_name.strip() != old_name:
            self._playlists[new_name.strip()] = self._playlists.pop(old_name)
            if self._active_playlist == old_name:
                self._active_playlist = new_name.strip()
            self._save_config_to_xml()
            self._refresh_playlist_listbox()

    def _delete_playlist(self, name):
        if messagebox.askyesno('Delete Playlist', f'Delete playlist "{name}"?'):
            self._playlists.pop(name, None)
            if self._active_playlist == name:
                self._active_playlist = None
            self._save_config_to_xml()
            self._refresh_playlist_listbox()
            self._apply_filter()
            self._build_tag_bar()

    def _playlist_to_queue(self, name):
        """Load a playlist's tracks into the play queue."""
        paths = self._playlists.get(name, [])
        path_to_idx = {e['path']: i for i, e in enumerate(self.playlist)}
        for path in paths:
            idx = path_to_idx.get(path)
            if idx is not None:
                self._add_to_queue(idx)

    def _add_selected_to_playlist(self, playlist_name):
        """Add selected treeview tracks to a named playlist."""
        sel = self.tree.selection()
        if not sel:
            return
        all_items = self.tree.get_children()
        for item in sel:
            try:
                idx = list(all_items).index(item)
                playlist_idx = self.display_indices[idx]
                path = self.playlist[playlist_idx]['path']
                if path not in self._playlists[playlist_name]:
                    self._playlists[playlist_name].append(path)
            except (ValueError, IndexError):
                pass
        self._save_config_to_xml()
        self._refresh_playlist_listbox()

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
        menu.add_command(label='\U0001f4cb  Add to Queue', command=lambda: self._add_to_queue(playlist_idx))
        menu.add_separator()
        menu.add_command(label='\u270f  Edit Title\u2026',
                         command=lambda: self._context_edit_title(playlist_idx))
        menu.add_command(label='\U0001f3b5  Change Genre\u2026',
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

        # Playlist submenu
        if self._playlists:
            pl_menu = tk.Menu(menu, tearoff=0)
            for pl_name in sorted(self._playlists.keys()):
                pl_menu.add_command(label=pl_name,
                                    command=lambda n=pl_name: self._add_selected_to_playlist(n))
            menu.add_cascade(label='\U0001f4c1  Add to Playlist', menu=pl_menu)

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

        dialog = ctk.CTkToplevel(self)
        dialog.title('Change Genre')
        dialog.geometry('320x420')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        title = entry.get('title', entry['basename'])
        ctk.CTkLabel(dialog, text='Change Genre',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(12, 2))
        ctk.CTkLabel(dialog, text=title[:50],
                     font=ctk.CTkFont(size=11), text_color='#888888',
                     wraplength=280).pack(pady=(0, 8))

        ctk.CTkLabel(dialog, text='Select an existing genre:',
                     font=ctk.CTkFont(size=11)).pack(anchor='w', padx=16, pady=(0, 2))

        genre_list = ctk.CTkScrollableFrame(dialog, fg_color='#1a1a2e', height=200)
        genre_list.pack(fill='both', expand=True, padx=16, pady=(0, 8))

        selected_var = tk.StringVar(value=current)

        for genre in sorted(self.genres):
            is_current = genre == current
            btn = ctk.CTkButton(genre_list, text=genre, height=28,
                                font=ctk.CTkFont(size=11),
                                fg_color='#1f6aa5' if is_current else '#2b2b2b',
                                hover_color='#1f6aa5',
                                anchor='w',
                                command=lambda g=genre: selected_var.set(g))
            btn.pack(fill='x', pady=1, padx=4)

        ctk.CTkLabel(dialog, text='Or type a new genre:',
                     font=ctk.CTkFont(size=11)).pack(anchor='w', padx=16, pady=(4, 2))
        new_entry = ctk.CTkEntry(dialog, height=28, font=ctk.CTkFont(size=12),
                                  placeholder_text='New genre name…')
        new_entry.pack(fill='x', padx=16, pady=(0, 8))

        def apply_genre():
            typed = new_entry.get().strip()
            new_genre = typed if typed else selected_var.get()
            if not new_genre:
                dialog.destroy()
                return
            entry['genre'] = new_genre
            self.genres.add(new_genre)
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET genre = ? WHERE file_path = ?", (new_genre, entry['path']))
            con.commit()
            con.close()
            self._build_genre_list()
            self._apply_filter()
            dialog.destroy()

        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=16, pady=(0, 12))
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)
        ctk.CTkButton(btn_row, text='Apply', fg_color='#1f6aa5',
                      command=apply_genre).pack(side='right', padx=4)

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
        con.execute("DELETE FROM track_plays WHERE track_id = (SELECT id FROM tracks WHERE file_path = ?)", (path,))
        con.execute("DELETE FROM tracks WHERE file_path = ?", (path,))
        con.commit()
        con.close()
        self._apply_filter()
        self._build_tag_bar()

    def _on_select(self, ev):
        if self._applying_filter:
            return
        sel = self.tree.selection()
        if not sel:
            if self._play_now_visible:
                self.btn_play_now.configure(state='disabled',
                                            fg_color='#555555', text_color='#888888')
                self.btn_play_next.configure(state='disabled',
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
                self.btn_play_next.configure(state='disabled',
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
        self.btn_play_next.configure(state='normal',
                                     fg_color='#e67e22', text_color='#000000')
        if not self._play_now_visible:
            self._play_bar.pack(fill='x', padx=20, pady=(0, 6), after=self._controls_frame)
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

    def _play_next_click(self):
        """Add the currently selected track as the next song in the queue."""
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
        self._insert_in_queue(playlist_idx, 0)
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        self.btn_play_next.configure(text=f'\u23ed  Queued: {title[:25]}',
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
        try:
            self._poll_inner()
        except Exception:
            pass  # never let poll crash kill the event loop
        self.after(500, self._poll)

    def _poll_inner(self):
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


def main():
    app = MusicPlayer()
    app.mainloop()


if __name__ == '__main__':
    main()

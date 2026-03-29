
"""
A music player using CustomTkinter + VLC

Layout:
- Top bar: hamburger menu + now-playing title (big, bold)
- Left sidebar: genre groups treeview with settings gear
- Center: tag filter bar + track list
- Right: tag editor panel + volume slider
- Bottom: big play/stop buttons + scrub bar
"""

import functools
import logging
import os
import shutil
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
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

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player.db')
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'music_player_config.xml')

# ── Performance tracking ─────────────────────────────────

_PERF_LOG_DIR = os.path.dirname(os.path.abspath(__file__))


class PerfTracker:
    """Lightweight performance tracker: timing decorator + stats accumulator."""

    def __init__(self):
        self.stats = {}          # method_name → {calls, total, min, max, last}
        self._ui_callback = None  # set to a callable(method_name, ms) to update UI
        self.last_action = ''    # last user action context for perf logging
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self._log_path = os.path.join(_PERF_LOG_DIR, f'perf_{ts}.log')
        self._logger = logging.getLogger('perf')
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        # File handler — timestamped log
        fh = logging.FileHandler(self._log_path, encoding='utf-8')
        fh.setFormatter(logging.Formatter('%(asctime)s  %(message)s', datefmt='%H:%M:%S'))
        self._logger.addHandler(fh)
        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter('\033[36m[perf]\033[0m %(message)s'))
        self._logger.addHandler(ch)
        self._logger.info(f'Performance log started → {self._log_path}')

    def track(self, method=None, *, quiet=False):
        """Decorator: wraps a method to record its execution time.
        Use @perf.track(quiet=True) to suppress per-call logging."""
        if method is None:
            # Called with arguments: @perf.track(quiet=True)
            return lambda m: self.track(m, quiet=quiet)
        name = method.__qualname__

        @functools.wraps(method)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return method(*args, **kwargs)
            finally:
                elapsed = (time.perf_counter() - t0) * 1000  # ms
                s = self.stats.get(name)
                if s is None:
                    s = {'calls': 0, 'total': 0.0, 'min': float('inf'), 'max': 0.0, 'last': 0.0}
                    self.stats[name] = s
                s['calls'] += 1
                s['total'] += elapsed
                s['last'] = elapsed
                if elapsed < s['min']:
                    s['min'] = elapsed
                if elapsed > s['max']:
                    s['max'] = elapsed
                # Only log noteworthy calls (> 1ms) to reduce noise
                if not quiet and elapsed > 1.0:
                    ctx = f' [{self.last_action}]' if self.last_action else ''
                    self._logger.info(f'{name}: {elapsed:.1f}ms{ctx}')
                if self._ui_callback:
                    try:
                        self._ui_callback(name, elapsed)
                    except Exception:
                        pass
        return wrapper

    def summary(self):
        """Return a formatted summary string of all tracked methods."""
        if not self.stats:
            return 'No performance data collected yet.'
        lines = ['', '═' * 80, '  PERFORMANCE SUMMARY', '═' * 80,
                 f'  {"Method":<45} {"Calls":>6} {"Total":>9} {"Avg":>8} {"Min":>8} {"Max":>8} {"Last":>8}',
                 '  ' + '─' * 78]
        for name in sorted(self.stats, key=lambda n: self.stats[n]['total'], reverse=True):
            s = self.stats[name]
            avg = s['total'] / s['calls'] if s['calls'] else 0
            short = name.split('.')[-1] if '.' in name else name
            lines.append(f'  {short:<45} {s["calls"]:>6} {s["total"]:>8.1f}ms {avg:>7.1f}ms '
                         f'{s["min"]:>7.1f}ms {s["max"]:>7.1f}ms {s["last"]:>7.1f}ms')
        lines.append('═' * 80)
        return '\n'.join(lines)

    def dump(self):
        """Print summary to console and write to log file."""
        text = self.summary()
        self._logger.info(text)
        return text

    def reset(self):
        """Clear all accumulated stats."""
        self.stats.clear()
        self._logger.info('Stats reset')


perf = PerfTracker()


def _add_tooltip(widget, text):
    """Attach a simple hover tooltip to a widget."""
    tip_window = [None]

    def show(event):
        if tip_window[0]:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f'+{x}+{y}')
        lbl = tk.Label(tw, text=text, background='#333333', foreground='#eeeeee',
                       relief='solid', borderwidth=1, font=('Segoe UI', 9),
                       padx=6, pady=3)
        lbl.pack()
        tip_window[0] = tw

    def hide(event):
        tw = tip_window[0]
        if tw:
            tw.destroy()
            tip_window[0] = None

    widget.bind('<Enter>', show, add='+')
    widget.bind('<Leave>', hide, add='+')


class MusicPlayer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title('Python Music Player')
        self.geometry('1400x750')
        self.minsize(900, 500)

        ctk.set_appearance_mode('dark')
        ctk.set_default_color_theme('blue')

        self.playlist = []
        self.display_indices = []
        self._di_reverse = {}  # playlist_idx → display position (O(1) reverse lookup)
        self.genres = set()
        self._path_set = set()  # O(1) duplicate path lookup
        self._path_to_idx = {}  # file_path → playlist index (O(1) reverse lookup)
        self._track_id_cache = {}  # file_path → track_id (avoids repeated DB lookups)
        self._library_root = ''  # absolute path to library root; paths stored relative to this

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
        self._tag_rows = {}  # tag_name → row number (from XML)
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

        # Play queue: list of playlist indices
        self._play_queue = []

        # Saved playlists: {name: [file_path, ...]}
        self._playlists = {}
        self._active_playlist = None  # name of currently active playlist filter

        # Debounce timer for search
        self._search_debounce_id = None

        # Guard to prevent _on_select re-entry during _apply_filter
        self._applying_filter = False

        # Lite mode state
        self._lite_mode = False

        # Play log track map: tree item iid → (track_id, file_path, title)
        self._play_log_track_map = {}

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
                file_path TEXT UNIQUE,
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
        con.execute('''CREATE TABLE IF NOT EXISTS track_tags (
            id INTEGER PRIMARY KEY,
            track_id INTEGER,
            tag TEXT,
            FOREIGN KEY(track_id) REFERENCES tracks(id),
            UNIQUE(track_id, tag)
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS track_votes (
            id INTEGER PRIMARY KEY,
            track_id INTEGER,
            vote INTEGER,
            voter TEXT DEFAULT '',
            voted_at TEXT,
            FOREIGN KEY(track_id) REFERENCES tracks(id)
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS genre_groups (
            id INTEGER PRIMARY KEY,
            group_name TEXT UNIQUE,
            sort_order INTEGER DEFAULT 0
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS genre_group_members (
            id INTEGER PRIMARY KEY,
            group_id INTEGER,
            genre TEXT,
            sort_order INTEGER DEFAULT 0,
            FOREIGN KEY(group_id) REFERENCES genre_groups(id)
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

        # Audit trail
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                action TEXT,
                detail TEXT
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
                abs_fp = self._abs_path(fpath)
                try:
                    tags = MutagenFile(abs_fp, easy=True)
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
                        audio = MutagenFile(self._abs_path(fpath))
                        if audio is not None and audio.info is not None:
                            length = audio.info.length
                    except Exception:
                        pass
                    if length is not None:
                        con.execute("UPDATE tracks SET length = ? WHERE id = ?", (length, track_id))
                con.commit()

        # Backfill missing artist/album from file tags
        if MutagenFile is not None:
            cur = con.execute("SELECT id, file_path FROM tracks WHERE artist IS NULL OR artist = ''")
            rows_to_fill = cur.fetchall()
            if rows_to_fill:
                for track_id, fpath in rows_to_fill:
                    artist = ''
                    album = ''
                    try:
                        tags = MutagenFile(self._abs_path(fpath), easy=True)
                        if tags is not None:
                            artist = tags.get('artist', [''])[0] or ''
                            album = tags.get('album', [''])[0] or ''
                    except Exception:
                        pass
                    if artist or album:
                        con.execute("UPDATE tracks SET artist = ?, album = ? WHERE id = ?",
                                    (artist, album, track_id))
                con.commit()

        con.close()
        self._load_genre_groups()

    # ── Path helpers (relative ↔ absolute) ───────────────

    def _rel_path(self, abs_path):
        """Convert an absolute file path to a path relative to _library_root."""
        if not self._library_root:
            return abs_path
        return os.path.relpath(abs_path, self._library_root)

    def _abs_path(self, rel_path):
        """Convert a relative path back to absolute using _library_root."""
        if not self._library_root:
            return rel_path
        if os.path.isabs(rel_path):
            return rel_path
        return os.path.join(self._library_root, rel_path)

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
        # Library root
        lib_el = root.find('library_root')
        if lib_el is not None and lib_el.text:
            self._library_root = lib_el.text
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
        # Tags (static definitions with optional row assignment)
        tags_el = root.find('tags')
        if tags_el is not None:
            for tag_el in tags_el.findall('tag'):
                name = tag_el.get('name', '').strip().lower()
                if name:
                    self._all_tags.add(name)
                    row = tag_el.get('row')
                    if row is not None:
                        self._tag_rows[name] = int(row)
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
        # Library root
        lib_el = ET.SubElement(root, 'library_root')
        lib_el.text = self._library_root or ''
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
        # Tags (static definitions with row assignments)
        tags_el = ET.SubElement(root, 'tags')
        for tag in sorted(self._all_tags):
            attrs = {'name': tag}
            if tag in self._tag_rows:
                attrs['row'] = str(self._tag_rows[tag])
            ET.SubElement(tags_el, 'tag', **attrs)
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

    # ── Audit trail ──────────────────────────────────────

    def _log_action(self, action, detail=''):
        """Queue an audit log entry and set perf context. Flushed periodically."""
        perf.last_action = action
        now = datetime.now(tz=timezone.utc).isoformat()
        if not hasattr(self, '_audit_queue'):
            self._audit_queue = []
        self._audit_queue.append((now, action, detail))
        # Flush if the batch is large enough
        if len(self._audit_queue) >= 10:
            self._flush_audit_log()

    def _flush_audit_log(self):
        """Write queued audit entries to DB in one transaction."""
        if not hasattr(self, '_audit_queue') or not self._audit_queue:
            return
        batch = self._audit_queue
        self._audit_queue = []
        try:
            con = sqlite3.connect(DB_PATH)
            con.executemany("INSERT INTO audit_log (timestamp, action, detail) VALUES (?, ?, ?)",
                            batch)
            con.commit()
            con.close()
        except Exception:
            pass  # never let audit logging break the app

    def destroy(self):
        """Flush pending audit entries before tearing down."""
        self._flush_audit_log()
        super().destroy()

    def _show_audit_log(self):
        """Show the audit log in a dialog."""
        self._flush_audit_log()
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT timestamp, action, detail FROM audit_log ORDER BY id DESC LIMIT 500")
        rows = cur.fetchall()
        con.close()

        dialog = ctk.CTkToplevel(self)
        dialog.title('Audit Log')
        dialog.geometry('700x500')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text='Audit Log — Recent Actions',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(10, 6))

        tree_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        tree_frame.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        cols = ('Time', 'Action', 'Detail')
        tree = ttk.Treeview(tree_frame, columns=cols, show='headings', height=20)
        tree.heading('Time', text='Time')
        tree.heading('Action', text='Action')
        tree.heading('Detail', text='Detail')
        tree.column('Time', width=150, anchor='w')
        tree.column('Action', width=150, anchor='w')
        tree.column('Detail', width=350, anchor='w')
        tree.pack(side='left', fill='both', expand=True)

        sb = ctk.CTkScrollbar(tree_frame, command=tree.yview)
        sb.pack(side='right', fill='y')
        tree.config(yscrollcommand=sb.set)

        for ts, action, detail in rows:
            try:
                dt = datetime.fromisoformat(ts).astimezone(tz=None)
                display_ts = dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                display_ts = str(ts)[:19]
            tree.insert('', 'end', values=(display_ts, action, detail or ''))

        ctk.CTkButton(dialog, text='Close', command=dialog.destroy,
                      width=100).pack(pady=(0, 10))

    def _load_tracks_from_db(self):
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute(
            "SELECT id, file_path, title, play_count, first_played, last_played, "
            "file_created, genre, comment, length, artist, album FROM tracks ORDER BY title"
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
        for (track_id, path, db_title, play_count, first_played, last_played,
             file_created, genre, comment, length, artist, album) in rows:
            if path in seen:
                continue
            seen.add(path)
            self._track_id_cache[path] = track_id
            vdata = votes_by_path.get(path, {'rating': 0, 'liked_by': set(), 'disliked_by': set()})
            entry = {
                'path': path,
                'title': db_title or os.path.basename(path),
                'basename': os.path.basename(path),
                'artist': artist or '',
                'album': album or '',
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
            self._path_set.add(path)
            self._path_to_idx[path] = len(self.playlist) - 1
            self.genres.add(entry['genre'])

        self._build_genre_list()
        self._rebuild_liked_by_dropdown()
        self._apply_filter()
        self._build_tag_bar()
        self._refresh_play_log()
        self.lbl_now_playing.configure(text=f'{len(self.playlist)} tracks loaded')

    def _ensure_track_in_db(self, path, title='', genre='Unknown', comment='', length=None, artist='', album=''):
        """Ensure a track exists in the DB. path is the relative (stored) path."""
        shared = getattr(self, '_shared_db', None)
        con = shared or sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created, length FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            try:
                file_created = datetime.fromtimestamp(os.path.getctime(self._abs_path(path)), tz=timezone.utc).isoformat()
            except OSError:
                file_created = None
            cur.execute(
                "INSERT INTO tracks (file_path, title, file_created, genre, comment, length, artist, album) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (path, title, file_created, genre, comment, length, artist, album)
            )
            con.commit()
            if not shared:
                con.close()
            return (0, None, None, file_created, length)
        # If length was not stored yet, update it
        if row[4] is None and length is not None:
            cur.execute("UPDATE tracks SET length = ? WHERE file_path = ?", (length, path))
            con.commit()
            if not shared:
                con.close()
            return (row[0], row[1], row[2], row[3], length)
        if not shared:
            con.close()
        return row

    @perf.track
    def _record_play(self, path):
        """Record a play and return (play_count, first_played, last_played)."""
        now = datetime.now(tz=timezone.utc).isoformat()
        track_id = self._get_track_id(path)
        if not track_id:
            return None
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute('INSERT INTO track_plays (track_id, played_at) VALUES (?, ?)', (track_id, now))
        # Increment play_count, set first_played if null, always update last_played
        cur.execute(
            'UPDATE tracks SET play_count = play_count + 1,'
            ' first_played = COALESCE(first_played, ?),'
            ' last_played = ? WHERE id = ?',
            (now, now, track_id))
        # Read back updated stats in the same connection
        cur.execute('SELECT play_count, first_played, last_played FROM tracks WHERE id = ?', (track_id,))
        stats = cur.fetchone()
        con.commit()
        con.close()
        return stats

    def _record_play_immediate(self):
        """Record the play for the current track right now and update the UI."""
        if self.current_index is None:
            return
        path = self.playlist[self.current_index]['path']
        stats = self._record_play(path)
        if stats:
            entry = self.playlist[self.current_index]
            entry['play_count'] = stats[0]
            entry['first_played'] = stats[1]
            entry['last_played'] = stats[2]
        self._update_single_row(self.current_index)
        self._refresh_play_log()

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
        tid = self._track_id_cache.get(path)
        if tid is not None:
            return tid
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("SELECT id FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row:
            self._track_id_cache[path] = row[0]
            return row[0]
        return None

    def _add_tag_to_track(self, playlist_idx, tag):
        entry = self.playlist[playlist_idx]
        tag = tag.strip().lower()
        if not tag:
            return
        if tag in entry.get('tags', []):
            return
        entry.setdefault('tags', []).append(tag)
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
        """Record a +1 or -1 vote for a track, optionally with voter name.
        Each person can only vote once per song per day; they can re-vote on another day."""
        entry = self.playlist[playlist_idx]
        track_id = self._get_track_id(entry['path'])
        if not track_id:
            return
        today_str = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        # Check if this voter already voted on this track today
        cur.execute(
            "SELECT id FROM track_votes WHERE track_id = ? AND voter = ? AND voted_at LIKE ?",
            (track_id, voter, f'{today_str}%'))
        if cur.fetchone():
            con.close()
            who = voter or 'Anonymous'
            messagebox.showinfo('Already Voted',
                                f'{who} has already voted on this track today.\n'
                                'You can vote again tomorrow.')
            return
        now = datetime.now(tz=timezone.utc).isoformat()
        con.execute("INSERT INTO track_votes (track_id, vote, voter, voted_at) VALUES (?, ?, ?, ?)",
                    (track_id, vote, voter, now))
        con.commit()
        con.close()
        vote_label = 'like' if vote > 0 else 'dislike'
        self._log_action(f'vote_{vote_label}', f'{entry["title"]} (voter: {voter or "anonymous"})')
        entry['rating'] = entry.get('rating', 0) + vote
        if voter:
            self._all_voters.add(voter)
            if vote > 0:
                entry.setdefault('liked_by', set()).add(voter)
            else:
                entry.setdefault('disliked_by', set()).add(voter)
        self._update_single_row(playlist_idx)
        self._update_rating_display()
        self._rebuild_liked_by_dropdown()

    def _quick_vote(self, vote):
        """Single-click vote using the voter dropdown value."""
        if self.current_index is None:
            messagebox.showinfo('No track', 'No track is currently playing.')
            return
        selected = self._voter_var.get()
        voter = '' if selected in ('', '(anonymous)') else selected
        self._record_vote(self.current_index, vote, voter)

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
                                        button_length=24,
                                        button_color='#00bcd4', button_hover_color='#80f0ff',
                                        progress_color='#00bcd4',
                                        border_color='#00bcd4', border_width=2)
        self.vol_slider.pack(fill='y', expand=True, padx=10, pady=6)

        self.lbl_vol_pct = ctk.CTkLabel(vol_strip, text='80%',
                                         font=ctk.CTkFont(size=12, weight='bold'))
        self.lbl_vol_pct.pack(pady=(4, 12))

        self._on_volume()

        # Mouse-wheel scrolling adjusts volume anywhere on the strip
        def _vol_scroll(event):
            step = 0.03
            if event.num == 4 or event.delta > 0:      # scroll up
                self.vol.set(min(1.0, self.vol.get() + step))
            elif event.num == 5 or event.delta < 0:     # scroll down
                self.vol.set(max(0.0, self.vol.get() - step))
            self._on_volume()
        for widget in (vol_strip, self.btn_mute, self.vol_slider, self.lbl_vol_pct):
            widget.bind('<MouseWheel>', _vol_scroll)     # Windows / macOS
            widget.bind('<Button-4>', _vol_scroll)       # Linux scroll up
            widget.bind('<Button-5>', _vol_scroll)       # Linux scroll down

        # ── CONTENT COLUMN (everything else) ──
        _content = ctk.CTkFrame(_outer, fg_color='transparent')
        _content.pack(side='left', fill='both', expand=True)

        # ═══ ROW 1 — INFO BAR ═══
        top_bar = ctk.CTkFrame(_content, height=42, fg_color='#1a1a2e')
        top_bar.pack(fill='x')
        top_bar.pack_propagate(False)

        self.btn_menu = ctk.CTkButton(top_bar, text='\u2630', width=40, height=30,
                                      font=ctk.CTkFont(size=18), command=self._show_menu)
        self.btn_menu.pack(side='left', padx=(8, 4), pady=4)

        self.lbl_now_playing = ctk.CTkLabel(top_bar, text='Not Playing',
                                            font=ctk.CTkFont(size=20, weight='bold'),
                                            anchor='w')
        self.lbl_now_playing.pack(side='left', fill='x', expand=True, padx=(12, 8))

        self._lbl_genre = ctk.CTkLabel(top_bar, text='',
                                       font=ctk.CTkFont(size=11),
                                       fg_color='#2b2b2b', corner_radius=6,
                                       text_color='#aaaaaa', width=0)
        self._lbl_genre.pack(side='left', padx=(0, 8), pady=6)

        self._lbl_rating = ctk.CTkLabel(top_bar, text='\u2014',
                                         font=ctk.CTkFont(size=16, weight='bold'),
                                         text_color='#888888', width=36)
        self._lbl_rating.pack(side='left', padx=(4, 4), pady=4)

        # ── Like / Dislike + Voter (right side) ──
        self._btn_thumbs_up = ctk.CTkButton(
            top_bar, text='\U0001f44d', width=40, height=30,
            font=ctk.CTkFont(size=18), fg_color='#f1c40f', hover_color='#f39c12',
            text_color='#000000',
            command=lambda: self._quick_vote(+1))
        self._btn_thumbs_up.pack(side='right', padx=(0, 8), pady=4)
        self._btn_thumbs_up.bind('<Double-1>',
            lambda e: (e.widget.after(1, lambda: self._ask_voter_and_vote(+1)), 'break'))

        self._voter_var = tk.StringVar(value='')
        self._voter_dropdown = ctk.CTkOptionMenu(
            top_bar, variable=self._voter_var,
            values=['(anonymous)'], width=100, height=26,
            font=ctk.CTkFont(size=10),
            fg_color='#3b3b3b', button_color='#4a4a4a',
            dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5')
        self._voter_dropdown.pack(side='right', padx=4, pady=4)
        self._voter_dropdown.set('(anonymous)')

        self._btn_thumbs_down = ctk.CTkButton(
            top_bar, text='\U0001f44e', width=40, height=30,
            font=ctk.CTkFont(size=18), fg_color='#f1c40f', hover_color='#f39c12',
            text_color='#000000',
            command=lambda: self._quick_vote(-1))
        self._btn_thumbs_down.pack(side='right', padx=0, pady=4)
        self._btn_thumbs_down.bind('<Double-1>',
            lambda e: (e.widget.after(1, lambda: self._ask_voter_and_vote(-1)), 'break'))

        self.load_progress = ctk.CTkProgressBar(top_bar, mode='determinate', width=200)
        self.load_progress.set(0)
        self.lbl_load = ctk.CTkLabel(top_bar, text='', font=ctk.CTkFont(size=10))

        # ═══ ROW 2 — CONTROLS BAR (transport + scrub + speed) ═══
        self._controls_frame = ctk.CTkFrame(_content, fg_color='#1a1a2e')
        self._controls_frame.pack(fill='x')

        ctrl_inner = ctk.CTkFrame(self._controls_frame, fg_color='transparent')
        ctrl_inner.pack(fill='x', padx=10, pady=(2, 4))

        self.btn_play = ctk.CTkButton(ctrl_inner, text='\u25b6', width=52, height=34,
                                      font=ctk.CTkFont(size=20), command=self.play_pause,
                                      fg_color='#1f6aa5', hover_color='#1a5a8a')
        self.btn_play.pack(side='left', padx=(0, 3))

        self.btn_stop = ctk.CTkButton(ctrl_inner, text='\u23f9', width=44, height=34,
                                      font=ctk.CTkFont(size=20), command=self.stop,
                                      fg_color='#c0392b', hover_color='#e74c3c')
        self.btn_stop.pack(side='left', padx=(0, 6))

        self.lbl_time_cur = ctk.CTkLabel(ctrl_inner, text='0:00', font=ctk.CTkFont(size=11), width=44)
        self.lbl_time_cur.pack(side='left')

        self._scrub_var = tk.DoubleVar(value=0)
        self._user_scrubbing = False
        self.scrub_slider = ctk.CTkSlider(ctrl_inner, from_=0, to=1.0, variable=self._scrub_var,
                                          command=self._on_scrub, height=16,
                                          button_color='#00bcd4', button_hover_color='#26c6da',
                                          progress_color='#00bcd4')
        self.scrub_slider.pack(side='left', fill='x', expand=True, padx=4)
        self.scrub_slider.set(0)
        self.scrub_slider.bind('<ButtonPress-1>', lambda e: setattr(self, '_user_scrubbing', True))
        self.scrub_slider.bind('<ButtonRelease-1>', self._on_scrub_release)

        self.lbl_time_total = ctk.CTkLabel(ctrl_inner, text='0:00', font=ctk.CTkFont(size=11), width=44)
        self.lbl_time_total.pack(side='left')

        # Speed control
        self._speed_frame = ctk.CTkFrame(ctrl_inner, fg_color='#2b2b2b', corner_radius=8)
        self._speed_frame.pack(side='left', padx=(6, 0))

        ctk.CTkLabel(self._speed_frame, text='Speed', font=ctk.CTkFont(size=8),
                     text_color='#888888').pack(pady=(2, 0))
        self._speed_var = tk.DoubleVar(value=1.0)
        self._speed_label = ctk.CTkLabel(self._speed_frame, text='1.0×', font=ctk.CTkFont(size=10, weight='bold'))
        self._speed_label.pack(pady=(0, 1))
        speed_down = ctk.CTkButton(self._speed_frame, text='\u2212', width=26, height=18,
                                    font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                                    command=self._speed_down)
        speed_down.pack(side='left', padx=(3, 1), pady=(0, 2))
        speed_reset = ctk.CTkButton(self._speed_frame, text='1\u00d7', width=26, height=18,
                                     font=ctk.CTkFont(size=9), fg_color='#3b3b3b',
                                     command=self._speed_reset)
        speed_reset.pack(side='left', padx=1, pady=(0, 2))
        speed_up = ctk.CTkButton(self._speed_frame, text='+', width=26, height=18,
                                  font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                                  command=self._speed_up)
        speed_up.pack(side='left', padx=(1, 3), pady=(0, 2))

        self._auto_reset_speed = tk.BooleanVar(value=True)
        _cb_auto_reset = ctk.CTkCheckBox(self._speed_frame, text='Auto', variable=self._auto_reset_speed,
                                          font=ctk.CTkFont(size=8), width=18, height=14,
                                          checkbox_width=14, checkbox_height=14)
        _cb_auto_reset.pack(pady=(0, 2))

        # ═══ PLAY NOW BAR (under play controls, hidden until track selected) ═══
        self._play_bar = ctk.CTkFrame(_content, fg_color='transparent')
        self.btn_play_now = ctk.CTkButton(self._play_bar, text='\u25b6  Play Now', height=30,
                                          font=ctk.CTkFont(size=15, weight='bold'),
                                          fg_color='#f1c40f', hover_color='#f39c12',
                                          text_color='#000000',
                                          command=self._play_now_click)
        self.btn_play_now.pack(side='left', fill='x', expand=True, padx=(0, 3))
        self.btn_play_next = ctk.CTkButton(self._play_bar, text='\u23ed  Play Next', height=30,
                                           font=ctk.CTkFont(size=13, weight='bold'),
                                           fg_color='#e67e22', hover_color='#d35400',
                                           text_color='#000000',
                                           command=self._play_next_click)
        self.btn_play_next.pack(side='left', fill='x', padx=(3, 0))
        self._play_now_visible = False

        self._tag_buttons = []
        self._tag_btn_map = {}

        # ═══ MAIN AREA: Browse + Queue (resizable via PanedWindow) ═══
        main_area = ctk.CTkFrame(_content, fg_color='transparent')
        main_area.pack(fill='both', expand=True, padx=4, pady=(4, 2))

        # Horizontal PanedWindow: left sidebar | browse | queue/log strip
        self._main_paned = tk.PanedWindow(main_area, orient='horizontal',
                                           bg='#333333', sashwidth=10, sashrelief='raised',
                                           opaqueresize=True, borderwidth=0,
                                           sashpad=2)
        self._main_paned.pack(fill='both', expand=True)

        # ── LEFT SIDEBAR (genre + playlist panels) ──
        self._left_sidebar = ctk.CTkFrame(self._main_paned, width=170, fg_color='transparent')

        # ── BROWSE PANEL (fills centre) ──
        self._browse_panel = ctk.CTkFrame(self._main_paned, fg_color='#2b2b2b', corner_radius=8)
        browse = self._browse_panel

        # ── RIGHT CONTAINER: queue button + queue/log panels ──
        right_wrapper = ctk.CTkFrame(self._main_paned, fg_color='transparent')

        # ── ADD-TO-QUEUE BUTTON (thin vertical strip on left of right wrapper) ──
        self._btn_send_to_queue = ctk.CTkButton(
            right_wrapper, text='›', width=22, height=80,
            font=ctk.CTkFont(size=20, weight='bold'),
            fg_color='#3b3b3b', hover_color='#1f6aa5',
            corner_radius=4, command=self._send_selected_to_queue)
        self._btn_send_to_queue.pack(side='left', fill='y', padx=(0, 2))

        right_container = ctk.CTkFrame(right_wrapper, fg_color='transparent')
        right_container.pack(side='left', fill='both', expand=True)

        # Vertical PanedWindow inside right_container: queue on top, play log on bottom
        self._right_paned = tk.PanedWindow(right_container, orient='vertical',
                                            bg='#333333', sashwidth=10, sashrelief='raised',
                                            opaqueresize=True, borderwidth=0,
                                            sashpad=2)
        self._right_paned.pack(fill='both', expand=True)

        # ── PLAY QUEUE PANEL (top half) ──
        queue_panel = ctk.CTkFrame(self._right_paned, fg_color='#2b2b2b', corner_radius=8)

        queue_header = ctk.CTkFrame(queue_panel, fg_color='transparent')
        queue_header.pack(fill='x', padx=6, pady=(6, 2))
        self._queue_title_lbl = ctk.CTkLabel(queue_header, text='Queue (0)',
                     font=ctk.CTkFont(size=12, weight='bold'))
        self._queue_title_lbl.pack(side='left')
        _btn_clear_queue = ctk.CTkButton(queue_header, text='✕', width=24, height=22,
                      font=ctk.CTkFont(size=12), fg_color='transparent',
                      hover_color='#3b3b3b', command=self._clear_queue)
        _btn_clear_queue.pack(side='right')

        self._queue_listbox = tk.Listbox(
            queue_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._queue_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        self._queue_listbox.bind('<Button-3>', self._on_queue_right_click)
        self._queue_listbox.bind('<Double-1>', self._on_queue_double_click)

        queue_btn_row = ctk.CTkFrame(queue_panel, fg_color='transparent')
        queue_btn_row.pack(fill='x', padx=4, pady=(0, 6))
        _btn_q_up = ctk.CTkButton(queue_btn_row, text='▲', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_move_up)
        _btn_q_up.pack(side='left', padx=2)
        _btn_q_down = ctk.CTkButton(queue_btn_row, text='▼', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_move_down)
        _btn_q_down.pack(side='left', padx=2)
        _btn_q_top = ctk.CTkButton(queue_btn_row, text='⤒', width=30, height=24,
                      font=ctk.CTkFont(size=14), fg_color='#3b3b3b',
                      command=self._queue_jump_to_top)
        _btn_q_top.pack(side='left', padx=2)
        _btn_q_remove = ctk.CTkButton(queue_btn_row, text='🗑', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._queue_remove_selected)
        _btn_q_remove.pack(side='right', padx=2)
        _btn_q_random = ctk.CTkButton(queue_btn_row, text='🎲', width=30, height=24,
                      font=ctk.CTkFont(size=12), fg_color='#3b3b3b',
                      command=self._random_queue_dialog)
        _btn_q_random.pack(side='right', padx=2)

        # ── PLAY LOG PANEL (below queue) ──
        play_log_panel = ctk.CTkFrame(self._right_paned, fg_color='#2b2b2b', corner_radius=8)

        play_log_header = ctk.CTkFrame(play_log_panel, fg_color='transparent')
        play_log_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(play_log_header, text='Play Log',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(side='left')
        _btn_refresh_log = ctk.CTkButton(play_log_header, text='⟳', width=24, height=22,
                      font=ctk.CTkFont(size=12), fg_color='transparent',
                      hover_color='#3b3b3b', command=self._refresh_play_log)
        _btn_refresh_log.pack(side='right')

        log_tree_frame = ctk.CTkFrame(play_log_panel, fg_color='transparent')
        log_tree_frame.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        log_tree_frame.grid_rowconfigure(0, weight=1)
        log_tree_frame.grid_columnconfigure(0, weight=1)

        self._play_log_tree = ttk.Treeview(
            log_tree_frame, columns=('Title', 'Genre'), show='tree headings',
            height=6)
        self._play_log_tree.heading('#0', text='Date', anchor='w')
        self._play_log_tree.heading('Title', text='Title')
        self._play_log_tree.heading('Genre', text='Genre')
        self._play_log_tree.column('#0', width=90, anchor='w')
        self._play_log_tree.column('Title', width=120, anchor='w')
        self._play_log_tree.column('Genre', width=70, anchor='w')
        self._play_log_tree.grid(row=0, column=0, sticky='nsew')
        self._play_log_tree.bind('<Double-1>', self._on_play_log_double_click)

        log_vsb = ctk.CTkScrollbar(log_tree_frame, command=self._play_log_tree.yview)
        log_vsb.grid(row=0, column=1, sticky='ns')
        self._play_log_tree.config(yscrollcommand=log_vsb.set)

        log_hsb = ttk.Scrollbar(log_tree_frame, orient='horizontal', command=self._play_log_tree.xview)
        log_hsb.grid(row=1, column=0, sticky='ew')
        self._play_log_tree.config(xscrollcommand=log_hsb.set)

        # Add panels to the vertical PanedWindow (queue | play log)
        self._right_paned.add(queue_panel, minsize=100, stretch='always')
        self._right_paned.add(play_log_panel, minsize=80, stretch='always')

        # Add panels to the horizontal PanedWindow (left sidebar | browse | right)
        self._main_paned.add(self._left_sidebar, minsize=120, stretch='never', width=170)
        self._main_paned.add(browse, minsize=300, stretch='always')
        self._main_paned.add(right_wrapper, minsize=150, stretch='never', width=240)

        # ── GENRE LISTBOX ──
        genre_panel = ctk.CTkFrame(self._left_sidebar, fg_color='#2b2b2b', corner_radius=8)
        genre_panel.pack(fill='both', expand=True, pady=(0, 4))

        genre_header = ctk.CTkFrame(genre_panel, fg_color='transparent')
        genre_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(genre_header, text='Genre',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(side='left')
        _btn_settings = ctk.CTkButton(
            genre_header, text='\u2699', width=24, height=22,
            font=ctk.CTkFont(size=12), fg_color='transparent',
            hover_color='#3b3b3b', command=self._open_settings
        )
        _btn_settings.pack(side='right')

        self._genre_listbox = tk.Listbox(
            genre_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._genre_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self._genre_listbox.bind('<<ListboxSelect>>', self._on_genre_listbox_select)

        # ── PLAYLIST PANEL ──
        playlist_panel = ctk.CTkFrame(self._left_sidebar, fg_color='#2b2b2b', corner_radius=8)
        playlist_panel.pack(fill='both', expand=True)

        playlist_header = ctk.CTkFrame(playlist_panel, fg_color='transparent')
        playlist_header.pack(fill='x', padx=6, pady=(6, 2))
        ctk.CTkLabel(playlist_header, text='Playlists',
                     font=ctk.CTkFont(size=12, weight='bold')).pack(side='left')
        _btn_new_playlist = ctk.CTkButton(playlist_header, text='+', width=24, height=22,
                      font=ctk.CTkFont(size=14), fg_color='transparent',
                      hover_color='#3b3b3b', command=self._create_playlist)
        _btn_new_playlist.pack(side='right')

        self._playlist_listbox = tk.Listbox(
            playlist_panel, bg='#2b2b2b', fg='#dce4ee',
            selectbackground='#1f6aa5', selectforeground='#ffffff',
            font=('Segoe UI', 10), borderwidth=0, highlightthickness=0,
            activestyle='none', exportselection=False)
        self._playlist_listbox.pack(fill='both', expand=True, padx=4, pady=(0, 6))
        self._playlist_listbox.bind('<<ListboxSelect>>', self._on_playlist_select)
        self._playlist_listbox.bind('<Button-3>', self._on_playlist_right_click)

        # ── Filter area: two rows of dropdowns + full-height Reset button ──
        self._filter_container = ctk.CTkFrame(browse, fg_color='transparent')
        self._filter_container.pack(fill='x', padx=6, pady=(4, 2))

        # Left side: the two filter rows stacked
        filter_left = ctk.CTkFrame(self._filter_container, fg_color='transparent')
        filter_left.pack(side='left', fill='both', expand=True)

        self._filter_row1 = ctk.CTkFrame(filter_left, fg_color='transparent')
        self._filter_row1.pack(fill='x', pady=(0, 1))
        self._filter_row1.columnconfigure(1, weight=1)   # rating dropdown
        self._filter_row1.columnconfigure(3, weight=2)   # liked-by dropdown

        _dd_style = dict(height=24, font=ctk.CTkFont(size=10),
                         fg_color='#3b3b3b', button_color='#4a4a4a',
                         button_hover_color='#555555',
                         dropdown_fg_color='#2b2b2b', dropdown_hover_color='#1f6aa5',
                         dropdown_text_color='#dce4ee')

        self._lbl_rating = ctk.CTkLabel(self._filter_row1, text='Rating', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_rating.grid(row=0, column=0, sticky='w', padx=(0, 4))
        self._rating_filter_var = tk.StringVar(value='All')
        rating_vals = ['All', '≥ 1', '≥ 2', '≥ 3', '≥ 5', '≥ 10', '≤ -1', '≤ -3', '= 0']
        self._rating_filter_dropdown = ctk.CTkOptionMenu(
            self._filter_row1, variable=self._rating_filter_var,
            values=rating_vals, command=self._on_rating_filter, **_dd_style)
        self._rating_filter_dropdown.grid(row=0, column=1, sticky='ew', padx=(0, 10))

        self._lbl_liked_by = ctk.CTkLabel(self._filter_row1, text='Liked by', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_liked_by.grid(row=0, column=2, sticky='w', padx=(0, 4))
        self._liked_by_var = tk.StringVar(value='All')
        self._liked_by_dropdown = ctk.CTkOptionMenu(
            self._filter_row1, variable=self._liked_by_var,
            values=['All'], command=self._on_liked_by_filter, **_dd_style)
        self._liked_by_dropdown.grid(row=0, column=3, sticky='ew', padx=(0, 6))

        self._filter_row2 = ctk.CTkFrame(filter_left, fg_color='transparent')
        self._filter_row2.pack(fill='x', pady=(0, 0))
        self._filter_row2.columnconfigure(1, weight=1)
        self._filter_row2.columnconfigure(3, weight=1)
        self._filter_row2.columnconfigure(5, weight=1)
        self._filter_row2.columnconfigure(7, weight=1)

        self._lbl_first_played = ctk.CTkLabel(self._filter_row2, text='First Played', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_first_played.grid(row=0, column=0, sticky='w', padx=(0, 4))
        self._first_played_var = tk.StringVar(value='All')
        self._first_played_dropdown = ctk.CTkOptionMenu(
            self._filter_row2, variable=self._first_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_first_played_filter, **_dd_style)
        self._first_played_dropdown.grid(row=0, column=1, sticky='ew', padx=(0, 10))

        self._lbl_last_played = ctk.CTkLabel(self._filter_row2, text='Last Played', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_last_played.grid(row=0, column=2, sticky='w', padx=(0, 4))
        self._last_played_var = tk.StringVar(value='All')
        self._last_played_dropdown = ctk.CTkOptionMenu(
            self._filter_row2, variable=self._last_played_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_last_played_filter, **_dd_style)
        self._last_played_dropdown.grid(row=0, column=3, sticky='ew', padx=(0, 10))

        self._lbl_file_created = ctk.CTkLabel(self._filter_row2, text='File Created', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_file_created.grid(row=0, column=4, sticky='w', padx=(0, 4))
        self._file_created_var = tk.StringVar(value='All')
        self._file_created_dropdown = ctk.CTkOptionMenu(
            self._filter_row2, variable=self._file_created_var,
            values=['All', 'Today', 'This Week', 'This Month'], command=self._on_file_created_filter, **_dd_style)
        self._file_created_dropdown.grid(row=0, column=5, sticky='ew', padx=(0, 10))

        self._lbl_length = ctk.CTkLabel(self._filter_row2, text='Length', font=ctk.CTkFont(size=10, weight='bold'))
        self._lbl_length.grid(row=0, column=6, sticky='w', padx=(0, 4))
        self._length_filter_var = tk.StringVar(value='All')
        self._length_filter_dropdown = ctk.CTkOptionMenu(
            self._filter_row2, variable=self._length_filter_var,
            values=self._get_length_filter_values(), command=self._on_length_filter, **_dd_style)
        self._length_filter_dropdown.grid(row=0, column=7, sticky='ew')

        # Reset button — full height, spans both rows
        self._btn_reset_filters = ctk.CTkButton(
            self._filter_container, text='✕\nReset', width=50,
            font=ctk.CTkFont(size=10), fg_color='transparent',
            border_width=1, border_color='#555555',
            hover_color='#3b3b3b', text_color='#999999',
            command=self._reset_all_filters)
        self._btn_reset_filters.pack(side='right', fill='y', padx=(4, 0))

        # Track list section
        self._tree_frame = ctk.CTkFrame(browse, fg_color='transparent')
        self._tree_frame.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        tree_frame = self._tree_frame

        # Tag filter bar — scrollable multi-row wrapping layout
        self._tag_bar_wrapper = ctk.CTkFrame(tree_frame, fg_color='transparent', height=0)
        self._tag_bar_wrapper.pack(fill='x', pady=(0, 2))
        self._tag_bar_wrapper.pack_propagate(False)
        self._tag_bar_visible = False          # starts hidden (0 height)

        self.tag_bar_frame = ctk.CTkScrollableFrame(
            self._tag_bar_wrapper, fg_color='#2b2b2b', corner_radius=6,
            height=50, orientation='vertical')
        self.tag_bar_frame.pack(fill='both', expand=True)

        # Search box (below tags)
        self._search_var = tk.StringVar()
        self._search_var.trace_add('write', lambda *_: self._debounced_search())
        self._search_entry = ctk.CTkEntry(tree_frame, textvariable=self._search_var,
                                           placeholder_text='\U0001f50d  Search title, artist, album, comment, tags, liked by\u2026',
                                           height=26, font=ctk.CTkFont(size=11))
        self._search_entry.pack(fill='x', pady=(0, 2))

        # Perf info — stored for UI callback
        self._perf_text = ''
        def _perf_ui_update(method_name, ms):
            short = method_name.split('.')[-1] if '.' in method_name else method_name
            # Skip poll-related methods from the perf status display
            if 'poll' in short.lower():
                return
            self._perf_text = f'⏱ {short}: {ms:.0f}ms'
            if hasattr(self, '_perf_status_lbl'):
                self._perf_status_lbl.configure(text=self._perf_text)
        perf._ui_callback = _perf_ui_update

        self._all_columns = ('Title', 'Artist', 'Album', 'Length', 'Rating', 'Comment', 'Tags', 'Liked By', 'Disliked By',
                              'Plays', 'First Played', 'Last Played', 'File Created')

        # Grid-based sub-frame for treeview + scrollbars (avoids pack side conflicts)
        tv_wrapper = ctk.CTkFrame(tree_frame, fg_color='transparent')
        tv_wrapper.pack(fill='both', expand=True)
        tv_wrapper.grid_rowconfigure(0, weight=1)
        tv_wrapper.grid_columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(tv_wrapper,
                                 columns=self._all_columns,
                                 show='headings', height=8)
        self.tree.column('Title', width=180, anchor='w')
        self.tree.column('Artist', width=120, anchor='w')
        self.tree.column('Album', width=120, anchor='w')
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
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.tag_configure(self._now_playing_tag, background='#1a3a1a', foreground='#5dff5d')
        self.tree.bind('<Double-1>', self._on_double)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)
        self.tree.bind('<Button-3>', self._on_right_click)

        sb = ctk.CTkScrollbar(tv_wrapper, command=self.tree.yview)
        sb.grid(row=0, column=1, sticky='ns')
        self.tree.config(yscrollcommand=sb.set)

        tree_hsb = ttk.Scrollbar(tv_wrapper, orient='horizontal', command=self.tree.xview)
        tree_hsb.grid(row=1, column=0, sticky='ew')
        self.tree.config(xscrollcommand=tree_hsb.set)

        # ── Status row below track listing (track count + perf) ──
        status_row = ctk.CTkFrame(tree_frame, fg_color='transparent')
        status_row.pack(fill='x', pady=(1, 0))
        self._track_count_lbl = ctk.CTkLabel(status_row, text='0 tracks',
                                              font=ctk.CTkFont(size=10),
                                              text_color='#888888', anchor='w')
        self._track_count_lbl.pack(side='left')
        self._perf_status_lbl = ctk.CTkLabel(status_row, text='',
                                              font=ctk.CTkFont(size=9),
                                              text_color='#666666', anchor='e')
        self._perf_status_lbl.pack(side='right')

        # ── Tooltips for all buttons ──
        _add_tooltip(self.btn_mute, 'Mute / Unmute')
        _add_tooltip(self.btn_menu, 'Menu — Add Files / Folders')
        _add_tooltip(self._btn_thumbs_up, 'Like (double-click for voter picker)')
        _add_tooltip(self._btn_thumbs_down, 'Dislike (double-click for voter picker)')
        _add_tooltip(self._voter_dropdown, 'Select who is voting')
        _add_tooltip(self.btn_play, 'Play / Pause')
        _add_tooltip(self.btn_stop, 'Stop')
        _add_tooltip(self.btn_play_now, 'Play selected track now')
        _add_tooltip(self.btn_play_next, 'Add selected track to front of queue')
        _add_tooltip(speed_down, 'Decrease speed')
        _add_tooltip(speed_reset, 'Reset speed to 1×')
        _add_tooltip(speed_up, 'Increase speed')
        _add_tooltip(_cb_auto_reset, 'Auto-reset speed to 1× when song changes')
        _add_tooltip(_btn_clear_queue, 'Clear queue')
        _add_tooltip(_btn_q_up, 'Move up in queue')
        _add_tooltip(_btn_q_down, 'Move down in queue')
        _add_tooltip(_btn_q_top, 'Jump to top of queue')
        _add_tooltip(_btn_q_remove, 'Remove from queue')
        _add_tooltip(_btn_q_random, 'Random queue generator')
        _add_tooltip(self._btn_send_to_queue, 'Add selected tracks to queue')
        _add_tooltip(_btn_settings, 'Settings')
        _add_tooltip(_btn_new_playlist, 'New playlist')
        _add_tooltip(self._btn_reset_filters, 'Reset all filters')

    # ── Keyboard shortcuts ───────────────────────────────

    def _bind_shortcuts(self):
        self.bind('<space>', lambda e: self.play_pause() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Right>', lambda e: self._next_track() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Left>', lambda e: self._prev_track() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.bind('<Escape>', lambda e: self.stop())
        self.bind('<Control-f>', lambda e: self._focus_search())
        self.bind('<Control-l>', lambda e: self._toggle_lite_mode())
        self.bind('<F11>', lambda e: self._toggle_fullscreen())
        self.bind('<F12>', lambda e: perf.dump())

    def _focus_search(self):
        """Focus the search box."""
        if hasattr(self, '_search_entry'):
            self._search_entry.focus_set()

    def _toggle_fullscreen(self):
        """Toggle fullscreen mode (F11)."""
        current = self.attributes('-fullscreen')
        self.attributes('-fullscreen', not current)

    def _prev_track(self):
        if not self.playlist or not self.display_indices:
            return
        pos = self._di_reverse.get(self.current_index, 0)
        prev_pos = (pos - 1) % len(self.display_indices)
        prev_idx = self.display_indices[prev_pos]
        self._load(prev_idx)
        self.vlc_player.play()
        self.is_playing = True
        self.is_paused = False
        self._last_action = 'playing'
        self._play_started_at = time.time()
        self._record_play_immediate()
        self._log_action('prev_track', self.playlist[prev_idx]['title'] if prev_idx < len(self.playlist) else '')
        self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
        self._update_now_playing()

    # ── Menu ─────────────────────────────────────────────

    def _show_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label='Add Files\u2026', command=self.add_files)
        menu.add_command(label='Add Folder\u2026', command=self.add_folder)
        menu.add_separator()
        lite_label = '\u2713  Lite Mode' if self._lite_mode else '    Lite Mode'
        menu.add_command(label=lite_label, command=self._toggle_lite_mode)
        fs_label = 'Exit Fullscreen' if self.attributes('-fullscreen') else 'Fullscreen (F11)'
        menu.add_command(label=fs_label, command=self._toggle_fullscreen)
        menu.add_separator()
        menu.add_command(label='\U0001f4cb  View Audit Log', command=self._show_audit_log)
        menu.add_separator()
        root_label = f'\U0001f4c1  Library Root: {self._library_root}' if self._library_root else '\U0001f4c1  Set Library Root\u2026'
        menu.add_command(label=root_label, command=self._show_library_root_dialog)
        menu.add_separator()
        menu.add_command(label='\U0001f4be  Snapshot DB', command=self._snapshot_db)
        menu.add_command(label='\U0001f5d1  Drop DB', command=self._drop_db)
        x = self.btn_menu.winfo_rootx()
        y = self.btn_menu.winfo_rooty() + self.btn_menu.winfo_height()
        menu.tk_popup(x, y, 0)

    # ── DB snapshot / drop ───────────────────────────────

    def _snapshot_db(self):
        """Copy the current DB file with a timestamp-SNAPSHOT suffix."""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('Snapshot DB', 'No database file found.')
            return
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        snap_path = f'{DB_PATH}.{stamp}-SNAPSHOT'
        shutil.copy2(DB_PATH, snap_path)
        messagebox.showinfo('Snapshot DB', f'Saved snapshot:\n{os.path.basename(snap_path)}')

    def _drop_db(self):
        """Delete the current DB file after confirmation."""
        if not os.path.exists(DB_PATH):
            messagebox.showinfo('Drop DB', 'No database file found.')
            return
        if not messagebox.askyesno('Drop DB',
                                   'Delete the database?\n\nThis cannot be undone.\n'
                                   'The app will close so the DB can be rebuilt on next launch.'):
            return
        os.remove(DB_PATH)
        self.destroy()

    # ── Library root ─────────────────────────────────────

    def _show_library_root_dialog(self):
        """Show a dialog to set the library root folder and scan it."""
        dialog = ctk.CTkToplevel(self)
        dialog.title('Library Root')
        dialog.geometry('550x220')
        dialog.transient(self)
        dialog.after(100, dialog.grab_set)

        ctk.CTkLabel(dialog, text='Library Root Folder',
                     font=ctk.CTkFont(size=14, weight='bold')).pack(pady=(16, 4))
        ctk.CTkLabel(dialog, text='All tracks are stored relative to this folder.',
                     font=ctk.CTkFont(size=11), text_color='#888888').pack(pady=(0, 10))

        path_var = tk.StringVar(value=self._library_root or '')
        path_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        path_frame.pack(fill='x', padx=20)
        path_entry = ctk.CTkEntry(path_frame, textvariable=path_var, height=32,
                                  font=ctk.CTkFont(size=12))
        path_entry.pack(side='left', fill='x', expand=True, padx=(0, 8))

        def browse():
            folder = filedialog.askdirectory(title='Select library root folder',
                                             initialdir=path_var.get() or None)
            if folder:
                path_var.set(folder)

        ctk.CTkButton(path_frame, text='Browse\u2026', width=80, fg_color='#4a4a4a',
                      hover_color='#555555', command=browse).pack(side='right')

        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=20, pady=(18, 12))

        def save():
            new_root = path_var.get().strip()
            if new_root and not os.path.isdir(new_root):
                messagebox.showerror('Invalid folder', f'"{new_root}" is not a valid directory.',
                                     parent=dialog)
                return
            self._library_root = new_root
            self._save_config_to_xml()
            self._log_action('set_library_root', new_root)
            dialog.destroy()

        def save_and_scan():
            new_root = path_var.get().strip()
            if not new_root or not os.path.isdir(new_root):
                messagebox.showerror('Invalid folder', f'"{new_root}" is not a valid directory.',
                                     parent=dialog)
                return
            self._library_root = new_root
            self._save_config_to_xml()
            self._log_action('set_library_root', new_root)
            dialog.destroy()
            self._scan_library()

        ctk.CTkButton(btn_row, text='Save', fg_color='#555555', width=100,
                      command=save).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_row, text='Save & Scan', width=120,
                      command=save_and_scan).pack(side='left', padx=(0, 8))
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555', width=80,
                      command=dialog.destroy).pack(side='right')

    @perf.track
    def _scan_library(self):
        """Scan the library root folder recursively and add all audio files."""
        if not self._library_root or not os.path.isdir(self._library_root):
            messagebox.showerror('No library root', 'Set a library root folder first.')
            return
        self._log_action('scan_library', self._library_root)
        exts = ('.mp3', '.wav', '.ogg', '.flac')

        self.lbl_now_playing.configure(text='Scanning library\u2026')
        self.update_idletasks()
        audio_files = []
        for root, _, files in os.walk(self._library_root):
            for name in files:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            messagebox.showinfo('No files', 'No supported audio files found in library root.')
            self.lbl_now_playing.configure(text='Not Playing')
            return

        self.load_progress.set(0)
        self.load_progress.pack(side='right', padx=(0, 10), pady=12)
        self.lbl_load.pack(side='right', padx=4, pady=12)

        added = 0
        self._shared_db = sqlite3.connect(DB_PATH)
        for i, abs_path in enumerate(audio_files, 1):
            if self._add_path(abs_path):
                added += 1
            self.load_progress.set(i / total)
            self.lbl_load.configure(text=f'{i}/{total}')
            if i % 25 == 0 or i == total:
                self.update_idletasks()
        self._shared_db.close()
        self._shared_db = None

        self.load_progress.pack_forget()
        self.lbl_load.pack_forget()

        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._build_genre_list()
        self._apply_filter()
        self.lbl_now_playing.configure(text=f'Added {added} tracks ({total} scanned)')

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
        # Do NOT reset tag filters on genre change — preserve user's tag selection
        self._apply_filter()
        self._update_tag_highlights()

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
        self._update_filter_highlights()

    def _on_liked_by_filter(self, choice):
        self._liked_by_filter = None if choice == 'All' else choice
        self._apply_filter()
        self._update_filter_highlights()

    def _on_first_played_filter(self, choice):
        self._first_played_var.set(choice)
        self._apply_filter()
        self._update_filter_highlights()

    def _on_last_played_filter(self, choice):
        self._last_played_var.set(choice)
        self._apply_filter()
        self._update_filter_highlights()

    def _on_file_created_filter(self, choice):
        self._file_created_var.set(choice)
        self._apply_filter()
        self._update_filter_highlights()

    def _on_length_filter(self, choice):
        self._length_filter_var.set(choice)
        self._apply_filter()
        self._update_filter_highlights()

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
        self._update_tag_highlights()
        self._update_filter_highlights()

    def _update_filter_highlights(self):
        """Highlight filter dropdowns that are not set to 'All'."""
        active_color = '#1f6aa5'   # blue tint when filter is active
        default_color = '#3b3b3b'  # normal background
        default_btn = '#4a4a4a'    # normal button color
        active_btn = '#174e7a'     # darker blue for the arrow button
        pairs = [
            (self._rating_filter_var, '_rating_filter_dropdown'),
            (self._liked_by_var, '_liked_by_dropdown'),
            (self._first_played_var, '_first_played_dropdown'),
            (self._last_played_var, '_last_played_dropdown'),
            (self._file_created_var, '_file_created_dropdown'),
            (self._length_filter_var, '_length_filter_dropdown'),
        ]
        any_active = False
        for var, attr in pairs:
            dd = getattr(self, attr, None)
            if dd is None:
                continue
            if var.get() != 'All':
                any_active = True
                dd.configure(fg_color=active_color, button_color=active_btn)
            else:
                dd.configure(fg_color=default_color, button_color=default_btn)
        # Highlight reset button when any filter is active
        if hasattr(self, '_btn_reset_filters'):
            if any_active:
                self._btn_reset_filters.configure(
                    fg_color='#8b0000', border_color='#ff4444',
                    text_color='#ff4444', hover_color='#a52a2a')
            else:
                self._btn_reset_filters.configure(
                    fg_color='transparent', border_color='#555555',
                    text_color='#999999', hover_color='#3b3b3b')

    def _rebuild_liked_by_dropdown(self):
        """Rebuild the liked-by dropdown with current voter names."""
        if hasattr(self, '_liked_by_dropdown'):
            values = ['All'] + sorted(self._all_voters)
            self._liked_by_dropdown.configure(values=values)
        # Also update the voter dropdown in the top bar
        if hasattr(self, '_voter_dropdown'):
            self._voter_dropdown.configure(values=['(anonymous)'] + sorted(self._all_voters))

    # ── Tag filter bar ───────────────────────────────────

    @perf.track
    def _build_tag_bar(self):
        """Build tag buttons from the static _all_tags set. Uses row
        assignments from XML config; tags without a row go on the last row."""
        all_tags = self._all_tags

        # If the tag set hasn't changed, just update highlights
        prev_tags = set(k for k in self._tag_btn_map if k != '__ALL__')
        if all_tags == prev_tags and self._tag_buttons:
            self._update_tag_highlights()
            return

        for btn in self._tag_buttons:
            btn.destroy()
        self._tag_buttons = []
        self._tag_btn_map = {}

        # Size the tag bar — always visible when tags are defined
        if not all_tags:
            self._tag_bar_wrapper.configure(height=0)
            self._tag_bar_visible = False
            return

        # Group tags by row from XML config
        rows_dict = {}  # row_num → [tag_name, ...]
        max_row = 0
        for tag in sorted(all_tags):
            r = self._tag_rows.get(tag, 99)  # unassigned tags go to row 99
            rows_dict.setdefault(r, []).append(tag)
            if r != 99 and r > max_row:
                max_row = r
        # Remap row 99 to max_row + 1 if it exists
        if 99 in rows_dict:
            rows_dict[max_row + 1] = rows_dict.pop(99)

        n_rows = len(rows_dict)
        bar_h = min(n_rows * 30 + 8, 100)
        self._tag_bar_wrapper.configure(height=bar_h)
        self._tag_bar_visible = True

        # Build tag buttons row by row
        for row_num in sorted(rows_dict.keys()):
            tags_in_row = rows_dict[row_num]
            for col, tag in enumerate(tags_in_row):
                is_active = tag in self._active_tags
                btn = ctk.CTkButton(self.tag_bar_frame, text=tag.upper(), height=22, width=70,
                                    font=ctk.CTkFont(size=9),
                                    fg_color='#1f6aa5' if is_active else 'transparent',
                                    border_width=1, border_color='#555555',
                                    command=lambda t=tag: self._on_tag_filter(t))
                btn.grid(row=row_num, column=col, padx=1, pady=1, sticky='ew')
                self._tag_buttons.append(btn)
                self._tag_btn_map[tag] = btn

        # ALL button — styled like Reset, on the right side of the last row
        all_active = not self._active_tags
        last_row = max(rows_dict.keys())
        last_row_len = len(rows_dict[last_row])
        btn_all = ctk.CTkButton(self.tag_bar_frame, text='ALL', height=22, width=46,
                                font=ctk.CTkFont(size=9, weight='bold'),
                                fg_color='transparent',
                                border_width=1,
                                border_color='#1f6aa5' if not all_active else '#555555',
                                text_color='#1f6aa5' if not all_active else '#999999',
                                hover_color='#3b3b3b',
                                command=lambda: self._on_tag_filter('All'))
        btn_all.grid(row=0, column=20, padx=(6, 2), pady=1, sticky='e')
        self._tag_buttons.append(btn_all)
        self._tag_btn_map['__ALL__'] = btn_all

    def _update_tag_highlights(self):
        """Update tag button colours in-place without destroying/recreating."""
        all_active = not self._active_tags
        for tag_key, btn in self._tag_btn_map.items():
            try:
                if tag_key == '__ALL__':
                    btn.configure(
                        fg_color='transparent',
                        border_color='#1f6aa5' if not all_active else '#555555',
                        text_color='#1f6aa5' if not all_active else '#999999')
                else:
                    btn.configure(fg_color='#1f6aa5' if tag_key in self._active_tags else 'transparent')
            except Exception:
                pass

    def _on_tag_filter(self, tag):
        if tag == 'All':
            self._active_tags = set()
        else:
            if tag in self._active_tags:
                self._active_tags.discard(tag)
            else:
                self._active_tags.add(tag)
        # Update tag button highlights in-place (no rebuild) — avoids flash
        self._update_tag_highlights()
        self._apply_filter()

    def _add_new_tag(self, parent_window=None, callback=None):
        """Create a new tag (globally). Optionally apply to selected tracks."""
        tag = simpledialog.askstring('New Tag', 'Enter tag name:',
                                     parent=parent_window or self)
        if tag and tag.strip():
            tag = tag.strip().lower()
            self._all_tags.add(tag)
            self._save_config_to_xml()  # persist new tag to XML
            # Apply to selected tracks if any
            sel = self.tree.selection()
            updated_indices = []
            for item in sel:
                pos = self._item_to_pos(item)
                if pos is not None and pos < len(self.display_indices):
                    pl_idx = self.display_indices[pos]
                    self._add_tag_to_track(pl_idx, tag)
                    updated_indices.append(pl_idx)
            # Update only the affected rows instead of full rebuild
            for pl_idx in updated_indices:
                self._update_single_row(pl_idx)
            # Defer tag bar rebuild to avoid freezing under a modal grab
            self.after(0, self._build_tag_bar)
            if callback:
                callback()

    def _delete_tag_globally(self, tag):
        """Remove a tag from all tracks and from _all_tags."""
        self._all_tags.discard(tag)
        self._save_config_to_xml()  # persist tag removal to XML
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM track_tags WHERE tag = ?", (tag,))
        con.commit()
        con.close()
        for entry in self.playlist:
            if tag in entry.get('tags', []):
                entry['tags'].remove(tag)
        self._active_tags.discard(tag)
        # Defer heavy operations to avoid freezing under a modal grab
        self.after(0, self._apply_filter)
        self.after(0, self._build_tag_bar)

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
        self._save_config_to_xml()  # persist tag rename to XML
        if old_tag in self._active_tags:
            self._active_tags.discard(old_tag)
            self._active_tags.add(new_tag)
        # Defer heavy operations to avoid freezing under a modal grab
        self.after(0, self._apply_filter)
        self.after(0, self._build_tag_bar)

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

            # Pre-compute genre counts in a single pass over the playlist
            genre_counts = {}
            for e in self.playlist:
                g = e.get('genre')
                if g:
                    genre_counts[g] = genre_counts.get(g, 0) + 1

            for i, genre in enumerate(sorted(self.genres), 1):
                count = genre_counts.get(genre, 0)
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
            self._build_tag_bar()  # tags may have been created/deleted in settings
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
        'Artist': lambda e: (e.get('artist') or '').lower(),
        'Album': lambda e: (e.get('album') or '').lower(),
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

    @perf.track
    def _apply_filter(self):
        self._applying_filter = True
        try:
            self._apply_filter_inner()
        finally:
            self._applying_filter = False

    @perf.track
    def _apply_filter_inner(self):
        # Remember which playlist indices were selected
        prev_selected = set()
        all_items = self.tree.get_children()
        if all_items:
            sel = self.tree.selection()
            if sel:
                item_pos = {iid: i for i, iid in enumerate(all_items)}
                for item in sel:
                    pos = item_pos.get(item)
                    if pos is not None and pos < len(self.display_indices):
                        prev_selected.add(self.display_indices[pos])
            self.tree.delete(*all_items)
        self.display_indices = []
        self._di_reverse = {}  # playlist_idx → display position (O(1) reverse lookup)

        genre_filter = self._get_genres_for_filter()
        search_term = self._search_var.get().strip().lower() if hasattr(self, '_search_var') else ''

        # Phase 1: collect matching indices — pre-cache filter values
        matched = []
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        fp_filter_val = self._first_played_var.get() if hasattr(self, '_first_played_var') else 'All'
        lp_filter_val = self._last_played_var.get() if hasattr(self, '_last_played_var') else 'All'
        fc_filter_val = self._file_created_var.get() if hasattr(self, '_file_created_var') else 'All'
        len_filter_val = self._length_filter_var.get() if hasattr(self, '_length_filter_var') else 'All'

        # Pre-build length filter range
        len_lo = len_hi = None
        if len_filter_val != 'All':
            for lbl, lo, hi in self._length_filter_durations:
                if lbl == len_filter_val:
                    len_lo, len_hi = lo, hi
                    break

        # Build playlist path set if filtering by playlist
        playlist_paths = None
        if self._active_playlist and self._active_playlist in self._playlists:
            playlist_paths = set(self._playlists[self._active_playlist])

        active_tags = self._active_tags
        rating_threshold = self._rating_threshold
        liked_by_filter = self._liked_by_filter
        playlist = self.playlist

        for idx in range(len(playlist)):
            entry = playlist[idx]
            # Playlist filter
            if playlist_paths is not None and entry['path'] not in playlist_paths:
                continue
            if genre_filter is not None and entry.get('genre') not in genre_filter:
                continue
            if active_tags:
                track_tags = entry.get('tags')
                if not track_tags or not active_tags.intersection(track_tags):
                    continue
            # Rating threshold filter
            if rating_threshold is not None:
                op, val = rating_threshold
                rating = entry.get('rating', 0)
                if op == '>=' and rating < val:
                    continue
                elif op == '<=' and rating > val:
                    continue
                elif op == '=' and rating != val:
                    continue
            # Liked-by filter
            if liked_by_filter and liked_by_filter not in entry.get('liked_by', set()):
                continue
            # Date filters
            if fp_filter_val != 'All':
                fp = entry.get('first_played')
                try:
                    fp_date = datetime.fromisoformat(fp).date() if fp else None
                except Exception:
                    fp_date = None
                if fp_filter_val == 'Today' and (not fp_date or fp_date != today):
                    continue
                if fp_filter_val == 'This Week' and (not fp_date or fp_date < week_ago):
                    continue
                if fp_filter_val == 'This Month' and (not fp_date or fp_date < month_ago):
                    continue
            if lp_filter_val != 'All':
                lp = entry.get('last_played')
                try:
                    lp_date = datetime.fromisoformat(lp).date() if lp else None
                except Exception:
                    lp_date = None
                if lp_filter_val == 'Today' and (not lp_date or lp_date != today):
                    continue
                if lp_filter_val == 'This Week' and (not lp_date or lp_date < week_ago):
                    continue
                if lp_filter_val == 'This Month' and (not lp_date or lp_date < month_ago):
                    continue
            if fc_filter_val != 'All':
                fc = entry.get('file_created')
                try:
                    fc_date = datetime.fromisoformat(fc).date() if fc else None
                except Exception:
                    fc_date = None
                if fc_filter_val == 'Today' and (not fc_date or fc_date != today):
                    continue
                if fc_filter_val == 'This Week' and (not fc_date or fc_date < week_ago):
                    continue
                if fc_filter_val == 'This Month' and (not fc_date or fc_date < month_ago):
                    continue
            # Length filter
            if len_filter_val != 'All':
                track_len = entry.get('length')
                if track_len is None:
                    continue
                if len_lo is not None and len_hi is not None and not (len_lo <= track_len < len_hi):
                    continue
                elif len_lo is not None and len_hi is None and track_len < len_lo:
                    continue
                elif len_hi is not None and len_lo is None and track_len >= len_hi:
                    continue
            if search_term:
                title_lower = entry.get('title', entry['basename']).lower()
                artist_lower = entry.get('artist', '').lower()
                album_lower = entry.get('album', '').lower()
                comment_lower = entry.get('comment', '').lower()
                tags_lower = ' '.join(entry.get('tags', [])).lower()
                liked_lower = ' '.join(entry.get('liked_by', set())).lower()
                if (search_term not in title_lower
                        and search_term not in artist_lower
                        and search_term not in album_lower
                        and search_term not in comment_lower
                        and search_term not in tags_lower
                        and search_term not in liked_lower):
                    continue
            matched.append(idx)

        # Phase 2: sort if a column is selected
        if self._sort_column and self._sort_column in self._SORT_KEYS:
            key_fn = self._SORT_KEYS[self._sort_column]
            matched.sort(key=lambda i: key_fn(playlist[i]), reverse=self._sort_reverse)

        # Phase 3: build row data list (pure Python — fast)
        _fmt_dur = self._format_duration
        _fmt_ts = self._format_ts
        cur_idx = self.current_index
        is_playing = self.is_playing
        np_tag = self._now_playing_tag
        row_data = []
        for idx in matched:
            entry = playlist[idx]
            title = entry.get('title', entry['basename'])
            artist = entry.get('artist', '')
            album = entry.get('album', '')
            length_str = _fmt_dur(entry.get('length'))
            rating = entry.get('rating', 0)
            rating_str = f'+{rating}' if rating > 0 else str(rating)
            comment = entry.get('comment', '')
            tags_str = ', '.join(sorted(t.upper() for t in entry.get('tags', []))) if entry.get('tags') else '\u2014'
            liked_str = ', '.join(sorted(entry.get('liked_by', set()))) if entry.get('liked_by') else '\u2014'
            disliked_str = ', '.join(sorted(entry.get('disliked_by', set()))) if entry.get('disliked_by') else '\u2014'
            plays = entry.get('play_count', 0)
            first_p = _fmt_ts(entry.get('first_played'), relative=False)
            last_p = _fmt_ts(entry.get('last_played'), relative=True)
            file_c = _fmt_ts(entry.get('file_created'), relative=False)
            row_tags = (np_tag,) if (idx == cur_idx and is_playing) else ()
            row_data.append((idx, (title, artist, album, length_str, rating_str, comment, tags_str,
                                    liked_str, disliked_str, plays, first_p, last_p, file_c),
                             row_tags))

        # Phase 4: insert into treeview in chunks (keeps UI responsive)
        CHUNK = 400
        tree_insert = self.tree.insert
        di_append = self.display_indices.append
        di_reverse = self._di_reverse
        self.tree.configure(selectmode='none')
        pos_counter = 0
        for start in range(0, len(row_data), CHUNK):
            for idx, vals, rtags in row_data[start:start + CHUNK]:
                tree_insert('', 'end', values=vals, tags=rtags)
                di_append(idx)
                di_reverse[idx] = pos_counter
                pos_counter += 1
            if start + CHUNK < len(row_data):
                self.update_idletasks()
        self.tree.configure(selectmode='extended')
        self._invalidate_item_cache()  # treeview contents changed

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
            total = len(playlist)
            shown = len(self.display_indices)
            if shown == total:
                self._track_count_lbl.configure(text=f'{total} tracks')
            else:
                self._track_count_lbl.configure(text=f'{shown} of {total} tracks')

    # ── File management ──────────────────────────────────

    def add_files(self):
        files = filedialog.askopenfilenames(title='Select audio files',
                                            filetypes=[('Audio', '*.mp3 *.wav *.ogg *.flac'), ('All files', '*.*')])
        if files:
            self._log_action('add_files', f'{len(files)} files')
        for f in files:
            self._add_path(f)
        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._build_genre_list()
        self._apply_filter()

    @perf.track
    def add_folder(self):
        folder = filedialog.askdirectory(title='Select folder')
        if not folder:
            return
        self._log_action('add_folder', folder)
        exts = ('.mp3', '.wav', '.ogg', '.flac')

        self.lbl_now_playing.configure(text='Scanning folder\u2026')
        self.update_idletasks()
        audio_files = []
        for root, _, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(exts):
                    audio_files.append(os.path.join(root, name))

        total = len(audio_files)
        if total == 0:
            messagebox.showinfo('No files', 'No supported audio files found in folder')
            self.lbl_now_playing.configure(text='Not Playing')
            return

        self.load_progress.set(0)
        self.load_progress.pack(side='right', padx=(0, 10), pady=12)
        self.lbl_load.pack(side='right', padx=4, pady=12)

        added = 0
        self._shared_db = sqlite3.connect(DB_PATH)
        for i, path in enumerate(audio_files, 1):
            if self._add_path(path):
                added += 1
            self.load_progress.set(i / total)
            self.lbl_load.configure(text=f'{i}/{total}')
            if i % 25 == 0 or i == total:
                self.update_idletasks()
        self._shared_db.close()
        self._shared_db = None

        self.load_progress.pack_forget()
        self.lbl_load.pack_forget()

        if self.current_index is None and self.playlist:
            self.current_index = 0
        self._build_genre_list()
        self._apply_filter()
        self.lbl_now_playing.configure(text=f'Added {added} tracks')

    def _add_path(self, abs_path):
        """Add a track by its absolute path. Stores a relative path internally."""
        rel = self._rel_path(abs_path)
        if rel in self._path_set:
            return False
        title = os.path.basename(abs_path)
        genre = 'Unknown'
        comment = ''
        artist = ''
        album = ''
        length = None
        if MutagenFile is not None:
            try:
                tags = MutagenFile(abs_path, easy=True)
                if tags is not None:
                    title = tags.get('title', [title])[0]
                    genre = tags.get('genre', [genre])[0]
                    comment_val = tags.get('comment', [''])[0]
                    comment = str(comment_val) if comment_val else ''
                    artist = tags.get('artist', [''])[0] or ''
                    album = tags.get('album', [''])[0] or ''
            except Exception:
                pass
            try:
                audio = MutagenFile(abs_path)
                if audio is not None and audio.info is not None:
                    length = audio.info.length
            except Exception:
                pass
        entry = {'path': rel, 'title': title, 'basename': os.path.basename(abs_path),
                 'artist': artist, 'album': album,
                 'genre': genre, 'comment': comment, 'length': length, 'tags': [],
                 'rating': 0, 'liked_by': set(), 'disliked_by': set()}
        self.playlist.append(entry)
        self._path_set.add(rel)
        self._path_to_idx[rel] = len(self.playlist) - 1
        self.genres.add(genre)
        stats = self._ensure_track_in_db(rel, title, genre, comment, length, artist, album)
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
        if not all_items:
            return
        # Only touch the specific item that needs updating, not all items
        if self.current_index is not None and self.is_playing:
            pos = self._di_reverse.get(self.current_index)
            if pos is not None and pos < len(all_items):
                item = all_items[pos]
                tags = list(self.tree.item(item, 'tags'))
                if self._now_playing_tag not in tags:
                    tags.append(self._now_playing_tag)
                    self.tree.item(item, tags=tags)
        # Also clear the tag from the previous now-playing item if needed
        if hasattr(self, '_prev_now_playing_pos'):
            prev_pos = self._prev_now_playing_pos
            if prev_pos is not None and prev_pos < len(all_items):
                item = all_items[prev_pos]
                tags = list(self.tree.item(item, 'tags'))
                if self._now_playing_tag in tags:
                    tags.remove(self._now_playing_tag)
                    self.tree.item(item, tags=tags)
        # Remember current position for next time
        self._prev_now_playing_pos = self._di_reverse.get(self.current_index) if (self.current_index is not None and self.is_playing) else None

    def _item_to_pos(self, item):
        """Convert a treeview item ID to its display position in O(1).
        Returns the index into display_indices, or None if not found."""
        cache = getattr(self, '_item_pos_cache', None)
        if cache is None:
            all_items = self.tree.get_children()
            cache = {iid: i for i, iid in enumerate(all_items)}
            self._item_pos_cache = cache
        return cache.get(item)

    def _invalidate_item_cache(self):
        """Invalidate the item-to-position cache after treeview changes."""
        self._item_pos_cache = None

    @perf.track
    def _update_single_row(self, playlist_idx):
        """Update one row's values in the treeview without a full rebuild."""
        pos = self._di_reverse.get(playlist_idx)
        if pos is None:
            return
        all_items = self.tree.get_children()
        if pos >= len(all_items):
            return
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        artist = entry.get('artist', '')
        album = entry.get('album', '')
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
        self.tree.item(all_items[pos],
                       values=(title, artist, album, length_str, rating_str, comment, tags_str, liked_str, disliked_str,
                               plays, first_p, last_p, file_c))

    @perf.track
    def _load(self, index):
        if index is None or index < 0 or index >= len(self.playlist):
            return False
        path = self._abs_path(self.playlist[index]['path'])
        try:
            media = self.vlc_instance.media_new(path)
            self.vlc_media_list = self.vlc_instance.media_list_new()
            self.vlc_media_list.add_media(media)
            self.vlc_player.set_media_list(self.vlc_media_list)
            self.current_index = index
            for item in self.tree.selection():
                self.tree.selection_remove(item)
            pos = self._di_reverse.get(index)
            if pos is not None:
                all_items = self.tree.get_children()
                if pos < len(all_items):
                    item = all_items[pos]
                    self.tree.selection_set(item)
                    self.tree.see(item)
            return True
        except Exception as e:
            messagebox.showerror('Error', f'Could not load {path}: {e}')
            return False

    def _update_now_playing(self, text=None):
        if text:
            self.lbl_now_playing.configure(text=text)
            self._lbl_genre.configure(text='')
        elif self.current_index is not None:
            entry = self.playlist[self.current_index]
            title = entry.get('title', entry['basename'])
            genre = entry.get('genre', '')
            self.lbl_now_playing.configure(text=title)
            if genre and genre != 'Unknown':
                self._lbl_genre.configure(text=f'  {genre}  ')
            else:
                self._lbl_genre.configure(text='')
        else:
            self.lbl_now_playing.configure(text='Not Playing')
            self._lbl_genre.configure(text='')
        self._update_now_playing_highlight()
        self._update_rating_display()

    def play_pause(self):
        if self.is_playing and not self.is_paused:
            self.vlc_player.pause()
            self.is_paused = True
            self.is_playing = False
            self._last_action = 'paused'
            self._log_action('pause', self.playlist[self.current_index]['title'] if self.current_index is not None else '')
            self.btn_play.configure(text='\u25b6', fg_color='#1f6aa5', hover_color='#1a5a8a')
            self._update_now_playing('Paused')
            return

        if self.is_paused:
            self.vlc_player.play()
            self.is_paused = False
            self.is_playing = True
            self._last_action = 'playing'
            self._play_started_at = time.time()
            self._log_action('resume', self.playlist[self.current_index]['title'] if self.current_index is not None else '')
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
            self._record_play_immediate()
            self._log_action('play', self.playlist[self.current_index]['title'] if self.current_index is not None else '')
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()
        except Exception as e:
            messagebox.showerror('Playback error', str(e))

    def stop(self):
        self._log_action('stop', self.playlist[self.current_index]['title'] if self.current_index is not None else '')
        self.vlc_player.stop()
        self.is_playing = False
        self.is_paused = False
        self._last_action = 'stopped'
        self.btn_play.configure(text='\u25b6', fg_color='#1f6aa5', hover_color='#1a5a8a')
        self.scrub_slider.set(0)
        self.lbl_time_cur.configure(text='0:00')
        self.lbl_time_total.configure(text='0:00')
        self._update_now_playing('Stopped')

    @perf.track
    def _next_track(self):
        if not self.playlist:
            return
        # Auto-reset speed to 1.0× if enabled
        if self._auto_reset_speed.get() and self._speed_var.get() != 1.0:
            self._speed_reset()
        # Check play queue first
        queue_next = self._pop_queue()
        if queue_next is not None:
            nxt = queue_next
        elif self.display_indices:
            pos = self._di_reverse.get(self.current_index, 0)
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
        self._record_play_immediate()
        self._log_action('next_track', self.playlist[nxt]['title'] if nxt < len(self.playlist) else '')
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
        """Apply the current speed to VLC and highlight if not 1×."""
        speed = self._speed_var.get()
        mp = self.vlc_player.get_media_player()
        mp.set_rate(speed)
        self._speed_label.configure(text=f'{speed:.1f}×')
        # Highlight speed box when speed is not 1.0
        if abs(speed - 1.0) > 0.05:
            self._speed_frame.configure(fg_color='#5c2d00', border_width=2, border_color='#ff9800')
            self._speed_label.configure(text_color='#ff9800')
            self._start_speed_throb()
        else:
            self._speed_frame.configure(fg_color='#2b2b2b', border_width=0, border_color='#2b2b2b')
            self._speed_label.configure(text_color='#dce4ee')
            self._stop_speed_throb()

    def _start_speed_throb(self):
        """Start a pulsating throb animation on the speed indicator."""
        if getattr(self, '_speed_throb_id', None) is not None:
            return  # already throbbing
        self._speed_throb_step = 0
        self._speed_throb_tick()

    def _stop_speed_throb(self):
        """Stop the speed throb animation."""
        tid = getattr(self, '_speed_throb_id', None)
        if tid is not None:
            self.after_cancel(tid)
            self._speed_throb_id = None

    def _speed_throb_tick(self):
        """One tick of the throb animation — oscillates colors."""
        if abs(self._speed_var.get() - 1.0) < 0.05:
            self._speed_throb_id = None
            return
        step = getattr(self, '_speed_throb_step', 0)
        # Oscillate between bright and dim using a sine-like 8-step cycle
        cycle = [
            ('#5c2d00', '#ff9800'),   # dim
            ('#6e3500', '#ffad33'),
            ('#804000', '#ffc266'),
            ('#924a00', '#ffd699'),   # bright
            ('#804000', '#ffc266'),
            ('#6e3500', '#ffad33'),
            ('#5c2d00', '#ff9800'),   # dim
            ('#4a2300', '#e68a00'),   # extra dim
        ]
        bg, fg = cycle[step % len(cycle)]
        try:
            self._speed_frame.configure(fg_color=bg, border_color=fg)
            self._speed_label.configure(text_color=fg)
        except Exception:
            self._speed_throb_id = None
            return
        self._speed_throb_step = step + 1
        self._speed_throb_id = self.after(200, self._speed_throb_tick)

    def _speed_up(self):
        cur = self._speed_var.get()
        new = min(cur + 0.1, 3.0)
        self._speed_var.set(round(new, 1))
        self._log_action('speed_change', f'{new:.1f}×')
        self._apply_speed()

    def _speed_down(self):
        cur = self._speed_var.get()
        new = max(cur - 0.1, 0.3)
        self._speed_var.set(round(new, 1))
        self._log_action('speed_change', f'{new:.1f}×')
        self._apply_speed()

    def _speed_reset(self):
        """Reset playback speed to 1.0×."""
        self._speed_var.set(1.0)
        self._log_action('speed_reset', '1.0×')
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

    def _add_multiple_to_queue(self, playlist_indices):
        """Add multiple tracks to the end of the play queue."""
        for idx in playlist_indices:
            self._play_queue.append(idx)
        self._refresh_queue_listbox()

    def _send_selected_to_queue(self):
        """Add all selected tracks from the treeview to the play queue."""
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            pos = self._item_to_pos(item)
            if pos is not None and pos < len(self.display_indices):
                self._play_queue.append(self.display_indices[pos])
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
        self._log_action('clear_queue', f'{len(self._play_queue)} items')
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

    def _queue_jump_to_top(self):
        """Move the selected queue item to the top of the queue."""
        sel = self._queue_listbox.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        item = self._play_queue.pop(i)
        self._play_queue.insert(0, item)
        self._refresh_queue_listbox()
        self._queue_listbox.selection_set(0)
        self._queue_listbox.see(0)

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

    def _on_queue_double_click(self, ev):
        """Double-click a queue item to play it immediately."""
        idx = self._queue_listbox.nearest(ev.y)
        if idx < 0 or idx >= len(self._play_queue):
            return
        playlist_idx = self._play_queue.pop(idx)
        self._refresh_queue_listbox()
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
            self._record_play_immediate()
            self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
            self._update_now_playing()

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

        # Pre-compute genre counts in a single pass
        genre_counts = {}
        for e in self.playlist:
            g = e.get('genre')
            if g:
                genre_counts[g] = genre_counts.get(g, 0) + 1

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
            count = genre_counts.get(genre, 0)
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
            queue_set = set()
            for _ in range(size):
                if not genre_list:
                    break
                chosen_genre = _random.choices(genre_list, weights=weight_list, k=1)[0]
                pool = eligible_by_genre.get(chosen_genre, [])
                available = [t for t in pool if t not in queue_set]
                if not available:
                    # Remove exhausted genre
                    gi = genre_list.index(chosen_genre)
                    genre_list.pop(gi)
                    weight_list.pop(gi)
                    continue
                pick = _random.choice(available)
                queue.append(pick)
                queue_set.add(pick)

            self._play_queue = queue
            self._refresh_queue_listbox()
            dialog.destroy()

        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      command=dialog.destroy).pack(side='right', padx=4)
        ctk.CTkButton(btn_row, text='Generate Queue', fg_color='#1f6aa5',
                      command=generate).pack(side='right', padx=4)

    # ── Play log ──────────────────────────────────────────

    def _refresh_play_log(self):
        """Refresh the play log treeview with play history grouped by date."""
        tree = self._play_log_tree
        tree.delete(*tree.get_children())

        con = sqlite3.connect(DB_PATH)
        cur = con.cursor()
        cur.execute("""
            SELECT tp.played_at, t.title, t.genre, t.id, t.file_path
            FROM track_plays tp
            JOIN tracks t ON t.id = tp.track_id
            ORDER BY tp.played_at DESC
            LIMIT 500
        """)
        rows = cur.fetchall()
        con.close()

        # Build a map of file_path → playlist index for voting
        self._play_log_track_map = {}  # tree item iid → (track_id, file_path, title)

        # Group by date
        date_nodes = {}  # date_str → tree item id
        for played_at, title, genre, track_id, file_path in rows:
            try:
                dt = datetime.fromisoformat(played_at).astimezone(tz=None)
                date_str = dt.strftime('%Y-%m-%d')
                time_str = dt.strftime('%H:%M')
            except Exception:
                date_str = str(played_at)[:10]
                time_str = ''
            if date_str not in date_nodes:
                date_nodes[date_str] = tree.insert('', 'end', text=f'\u2192 {date_str}', open=(len(date_nodes) == 0))
            parent = date_nodes[date_str]
            iid = tree.insert(parent, 'end', text=time_str, values=(title or '?', genre or 'Unknown'))
            self._play_log_track_map[iid] = (track_id, file_path, title or '?')

    def _on_play_log_right_click(self, ev):
        """Show context menu on play log right-click to vote on a played track."""
        item = self._play_log_tree.identify_row(ev.y)
        if not item or item not in self._play_log_track_map:
            return
        self._play_log_tree.selection_set(item)
        track_id, file_path, title = self._play_log_track_map[item]

        # Find the playlist index for this track (O(1) lookup)
        playlist_idx = self._path_to_idx.get(file_path)

        if playlist_idx is None:
            return

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f'\U0001f3b5  {title[:40]}', state='disabled')
        menu.add_separator()

        selected_voter = self._voter_var.get()
        voter = '' if selected_voter in ('', '(anonymous)') else selected_voter

        menu.add_command(label='\U0001f44d  Like',
                         command=lambda: self._record_vote(playlist_idx, +1, voter))
        menu.add_command(label='\U0001f44e  Dislike',
                         command=lambda: self._record_vote(playlist_idx, -1, voter))

        menu.add_separator()
        menu.add_command(label='\u25b6  Play Now',
                         command=lambda: self._context_play(playlist_idx))
        menu.add_command(label='\U0001f4cb  Add to Queue',
                         command=lambda: self._add_multiple_to_queue([playlist_idx]))
        menu.tk_popup(ev.x_root, ev.y_root)

    def _on_play_log_double_click(self, ev):
        """Double-click a play log entry to select the track in the main track listing."""
        item = self._play_log_tree.identify_row(ev.y)
        if not item or item not in self._play_log_track_map:
            return
        track_id, file_path, title = self._play_log_track_map[item]

        # Find the playlist index for this track (O(1) lookup)
        playlist_idx = self._path_to_idx.get(file_path)
        if playlist_idx is None:
            return

        # Find the tree position via the reverse display index map
        pos = self._di_reverse.get(playlist_idx)
        if pos is None:
            return
        children = self.tree.get_children()
        if pos >= len(children):
            return
        tree_iid = children[pos]
        self.tree.selection_set(tree_iid)
        self.tree.see(tree_iid)
        self.tree.focus(tree_iid)

    # ── Lite mode ──────────────────────────────────────────

    def _toggle_lite_mode(self):
        """Toggle lite mode — hides filters, tag bar, and sidebar for a cleaner view."""
        self._lite_mode = not self._lite_mode
        self._log_action('toggle_lite_mode', f'{"on" if self._lite_mode else "off"}')

        if self._lite_mode:
            # Hide filter area and tag bar
            self._filter_container.pack_forget()
            if self._tag_bar_visible:
                self._tag_bar_wrapper.configure(height=0)
            # Hide left sidebar by shrinking it in the paned window
            self._main_paned.paneconfigure(self._left_sidebar, hide=True)
        else:
            # Re-show in correct order: forget all browse children, then re-pack
            for child in self._browse_panel.winfo_children():
                child.pack_forget()
            self._filter_container.pack(fill='x', padx=6, pady=(4, 2))
            self._tree_frame.pack(fill='both', expand=True, padx=4, pady=(0, 4))
            # Show left sidebar
            self._main_paned.paneconfigure(self._left_sidebar, hide=False)

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

    def _playlist_to_queue(self, name):
        """Load a playlist's tracks into the play queue."""
        paths = self._playlists.get(name, [])
        for path in paths:
            idx = self._path_to_idx.get(path)
            if idx is not None:
                self._add_to_queue(idx)

    def _add_selected_to_playlist(self, playlist_name):
        """Add selected treeview tracks to a named playlist."""
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            pos = self._item_to_pos(item)
            if pos is not None and pos < len(self.display_indices):
                playlist_idx = self.display_indices[pos]
                path = self.playlist[playlist_idx]['path']
                if path not in self._playlists[playlist_name]:
                    self._playlists[playlist_name].append(path)
        self._save_config_to_xml()
        self._refresh_playlist_listbox()

    # ── Track selection events ───────────────────────────

    @perf.track
    def _on_right_click(self, ev):
        """Show context menu on right-click."""
        item = self.tree.identify_row(ev.y)
        if not item:
            return
        # Preserve multi-selection: only reset if right-clicked item isn't already selected
        if item not in self.tree.selection():
            self.tree.selection_set(item)

        # Gather all selected playlist indices
        selected_indices = []
        for sel_item in self.tree.selection():
            pos = self._item_to_pos(sel_item)
            if pos is not None and pos < len(self.display_indices):
                selected_indices.append(self.display_indices[pos])
        if not selected_indices:
            return

        playlist_idx = selected_indices[0]
        entry = self.playlist[playlist_idx]
        multi = len(selected_indices) > 1
        menu = tk.Menu(self, tearoff=0)
        if not multi:
            menu.add_command(label='\u25b6  Play', command=lambda: self._context_play(playlist_idx))
        menu.add_command(
            label=f'\U0001f4cb  Add {len(selected_indices)} to Queue' if multi else '\U0001f4cb  Add to Queue',
            command=lambda idxs=selected_indices: self._add_multiple_to_queue(idxs))
        menu.add_separator()
        if not multi:
            menu.add_command(label='\u270f  Edit Title\u2026',
                             command=lambda: self._context_edit_title(playlist_idx))

            # Genre submenu
            genre_menu = tk.Menu(menu, tearoff=0)
            current_genre = entry.get('genre', 'Unknown')
            for genre in sorted(self.genres):
                is_current = genre == current_genre
                label = f'\u2713  {genre}' if is_current else f'     {genre}'
                genre_menu.add_command(label=label,
                    command=lambda g=genre: self._context_set_genre(playlist_idx, g))
            genre_menu.add_separator()
            genre_menu.add_command(label='Other…',
                command=lambda: self._context_edit_genre(playlist_idx))
            menu.add_cascade(label='\U0001f3b5  Genre', menu=genre_menu)

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
            self._record_play_immediate()
            self._log_action('context_play', self.playlist[playlist_idx]['title'])
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
            self._log_action('edit_title', f'{current} → {new_val.strip()}')
            entry['title'] = new_val.strip()
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET title = ? WHERE file_path = ?", (new_val.strip(), entry['path']))
            con.commit()
            con.close()
            self._update_single_row(playlist_idx)

    def _context_set_genre(self, playlist_idx, new_genre):
        """Quick-set genre from the submenu without opening a dialog."""
        entry = self.playlist[playlist_idx]
        self._log_action('set_genre', f'{entry["title"]}: {entry.get("genre","Unknown")} → {new_genre}')
        entry['genre'] = new_genre
        self.genres.add(new_genre)
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE tracks SET genre = ? WHERE file_path = ?", (new_genre, entry['path']))
        con.commit()
        con.close()
        self._build_genre_list()
        # Only need full filter if genre filter is active, otherwise single-row update
        if self._active_genre != 'All':
            self._apply_filter()
        else:
            self._update_single_row(playlist_idx)

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
            if self._active_genre != 'All':
                self._apply_filter()
            else:
                self._update_single_row(playlist_idx)
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
            self._log_action('edit_comment', f'{entry["title"]}: {new_val.strip()[:60]}')
            entry['comment'] = new_val.strip()
            con = sqlite3.connect(DB_PATH)
            con.execute("UPDATE tracks SET comment = ? WHERE file_path = ?", (new_val.strip(), entry['path']))
            con.commit()
            con.close()
            self._update_single_row(playlist_idx)

    def _context_toggle_tag(self, playlist_idx, tag, currently_applied):
        entry = self.playlist[playlist_idx]
        action = 'remove_tag' if currently_applied else 'add_tag'
        self._log_action(action, f'{entry["title"]}: {tag}')
        if currently_applied:
            self._remove_tag_from_track(playlist_idx, tag)
        else:
            self._add_tag_to_track(playlist_idx, tag)
        self._update_single_row(playlist_idx)
        # If tag filter is active, the track list may need to change
        if self._active_tags:
            self._apply_filter()

    def _context_remove(self, playlist_idx):
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        if not messagebox.askyesno('Remove Track', f'Remove "{title}" from the library?\n\n(File will not be deleted)'):
            return
        self._log_action('remove_track', title)
        path = entry['path']
        if self.current_index == playlist_idx:
            self.stop()
            self.current_index = None
        elif self.current_index is not None and self.current_index > playlist_idx:
            self.current_index -= 1
        self.playlist.pop(playlist_idx)
        self._path_set.discard(path)
        self._path_to_idx.pop(path, None)
        # Rebuild path→idx for shifted entries
        for i in range(playlist_idx, len(self.playlist)):
            self._path_to_idx[self.playlist[i]['path']] = i
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM track_tags WHERE track_id = (SELECT id FROM tracks WHERE file_path = ?)", (path,))
        con.execute("DELETE FROM track_plays WHERE track_id = (SELECT id FROM tracks WHERE file_path = ?)", (path,))
        con.execute("DELETE FROM tracks WHERE file_path = ?", (path,))
        con.commit()
        con.close()
        self._apply_filter()

    @perf.track
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
        pos = self._item_to_pos(item)
        if pos is None or pos >= len(self.display_indices):
            if self._play_now_visible:
                self.btn_play_now.configure(state='disabled',
                                            fg_color='#555555', text_color='#888888')
                self.btn_play_next.configure(state='disabled',
                                             fg_color='#555555', text_color='#888888')
            return
        playlist_idx = self.display_indices[pos]

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
            self._play_bar.pack(fill='x', padx=14, pady=(0, 2), after=self._controls_frame)
            self._play_now_visible = True

    @perf.track
    def _play_now_click(self):
        """Play the currently selected track immediately."""
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        pos = self._item_to_pos(item)
        if pos is None or pos >= len(self.display_indices):
            return
        playlist_idx = self.display_indices[pos]
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
            self._record_play_immediate()
            self._log_action('play_now', self.playlist[playlist_idx]['title'])
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
        pos = self._item_to_pos(item)
        if pos is None or pos >= len(self.display_indices):
            return
        playlist_idx = self.display_indices[pos]
        self._log_action('play_next', self.playlist[playlist_idx]['title'])
        self._insert_in_queue(playlist_idx, 0)
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        self.btn_play_next.configure(text=f'\u23ed  Queued: {title[:25]}',
                                     state='disabled',
                                     fg_color='#555555', text_color='#888888')

    @perf.track
    def _on_double(self, ev):
        sel = self.tree.selection()
        if not sel:
            return
        item = sel[0]
        pos = self._item_to_pos(item)
        if pos is None or pos >= len(self.display_indices):
            return
        playlist_idx = self.display_indices[pos]
        entry = self.playlist[playlist_idx]
        title = entry.get('title', entry['basename'])
        artist = entry.get('artist', '')
        album = entry.get('album', '')
        genre = entry.get('genre', '')

        dialog = ctk.CTkToplevel(self)
        dialog.title('Play Track')
        dialog.geometry('440x200')
        dialog.transient(self)
        dialog.configure(fg_color='#1a2a3a')
        dialog.resizable(False, False)

        # Position over the track listing (centre of the treeview)
        self.update_idletasks()
        tree_x = self.tree.winfo_rootx()
        tree_y = self.tree.winfo_rooty()
        tree_w = self.tree.winfo_width()
        tree_h = self.tree.winfo_height()
        dlg_w, dlg_h = 440, 200
        x = tree_x + (tree_w - dlg_w) // 2
        y = tree_y + (tree_h - dlg_h) // 2
        dialog.geometry(f'{dlg_w}x{dlg_h}+{x}+{y}')
        dialog.after(100, dialog.grab_set)

        # Title
        ctk.CTkLabel(dialog, text=title[:70],
                     font=ctk.CTkFont(size=14, weight='bold'),
                     wraplength=400, text_color='#ffffff').pack(pady=(18, 2))
        # Subtitle: artist / album / genre
        sub_parts = [p for p in [artist, album, genre] if p]
        if sub_parts:
            ctk.CTkLabel(dialog, text=' \u2022 '.join(sub_parts)[:80],
                         font=ctk.CTkFont(size=11),
                         text_color='#88aacc', wraplength=400).pack(pady=(0, 8))
        else:
            ctk.CTkFrame(dialog, fg_color='transparent', height=8).pack()

        # Hint label
        ctk.CTkLabel(dialog, text='Enter = Play Now    Shift+Enter = Play Next    Esc = Cancel',
                     font=ctk.CTkFont(size=9), text_color='#667788').pack(pady=(0, 8))

        btn_row = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_row.pack(fill='x', padx=24, pady=(0, 18))

        def play_now():
            dialog.destroy()
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
                self._record_play_immediate()
                self.btn_play.configure(text='\u23f8', fg_color='#27ae60', hover_color='#2ecc71')
                self._update_now_playing()

        def play_next():
            dialog.destroy()
            self._insert_in_queue(playlist_idx, 0)

        ctk.CTkButton(btn_row, text='\u25b6  Play Now', fg_color='#1f6aa5',
                      hover_color='#2980b9', height=34,
                      font=ctk.CTkFont(size=13, weight='bold'),
                      command=play_now).pack(side='left', padx=4, expand=True, fill='x')
        ctk.CTkButton(btn_row, text='\u23ed  Play Next', fg_color='#e67e22',
                      hover_color='#d35400', height=34,
                      font=ctk.CTkFont(size=13, weight='bold'),
                      command=play_next).pack(side='left', padx=4, expand=True, fill='x')
        ctk.CTkButton(btn_row, text='Cancel', fg_color='#555555',
                      hover_color='#666666', height=34,
                      font=ctk.CTkFont(size=13),
                      command=dialog.destroy).pack(side='left', padx=4, expand=True, fill='x')

        # Keyboard shortcuts
        dialog.bind('<Return>', lambda e: play_now() if not (e.state & 0x1) else play_next())
        dialog.bind('<Shift-Return>', lambda e: play_next())
        dialog.bind('<Escape>', lambda e: dialog.destroy())
        dialog.focus_force()

    # ── Poll ─────────────────────────────────────────────

    def _poll(self):
        try:
            self._poll_inner()
        except Exception:
            pass  # never let poll crash kill the event loop
        self.after(500, self._poll)

    @perf.track(quiet=True)
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

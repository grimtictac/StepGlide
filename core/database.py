"""
Database layer — schema init, queries, track CRUD, votes, tags, audit log.
Pure Python, no UI dependencies.
"""

import os
import sqlite3
from datetime import datetime, timezone

from core.perf import perf

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'music_player.db')

try:
    from mutagen import File as MutagenFile
except Exception:
    MutagenFile = None


class Database:
    """All SQLite operations for the music player."""

    def __init__(self, db_path=None, abs_path_fn=None, debug_log_fn=None):
        self.db_path = db_path or DB_PATH
        self._abs_path = abs_path_fn or (lambda p: p)
        self._debug_log = debug_log_fn or (lambda lvl, msg: None)
        self._track_id_cache = {}  # file_path → track_id

    def connect(self):
        return sqlite3.connect(self.db_path)

    # ── Schema ───────────────────────────────────────────

    def init_schema(self):
        """Create all tables and run backfills."""
        self._debug_log('INFO', f'Initializing database: {self.db_path}')
        con = self.connect()
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
        con.commit()

        # Column migrations
        cur = con.execute("PRAGMA table_info(tracks)")
        columns = [row[1] for row in cur.fetchall()]
        for col, sql in [
            ('bpm', "ALTER TABLE tracks ADD COLUMN bpm REAL"),
            ('genre', "ALTER TABLE tracks ADD COLUMN genre TEXT DEFAULT 'Unknown'"),
            ('comment', "ALTER TABLE tracks ADD COLUMN comment TEXT DEFAULT ''"),
            ('length', "ALTER TABLE tracks ADD COLUMN length REAL"),
        ]:
            if col not in columns:
                con.execute(sql)
                con.commit()

        con.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT,
                action TEXT,
                detail TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS track_eq (
                track_id INTEGER PRIMARY KEY,
                preamp REAL DEFAULT 0,
                bands TEXT DEFAULT '',
                FOREIGN KEY(track_id) REFERENCES tracks(id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS queue_state (
                position INTEGER PRIMARY KEY,
                file_path TEXT
            )
        """)
        con.commit()

        # Run backfills
        self._backfill_genres(con)
        self._backfill_lengths(con)
        self._backfill_artist_album(con)
        con.close()

    def _backfill_genres(self, con):
        if MutagenFile is None:
            return
        cur = con.execute("SELECT COUNT(*) FROM tracks WHERE genre != 'Unknown'")
        has_real = cur.fetchone()[0]
        cur = con.execute("SELECT COUNT(*) FROM tracks")
        total = cur.fetchone()[0]
        if has_real > 0 or total == 0:
            return
        cur = con.execute("SELECT id, file_path, title FROM tracks")
        for track_id, fpath, db_title in cur.fetchall():
            genre = 'Unknown'
            comment = ''
            title = db_title
            try:
                tags = MutagenFile(self._abs_path(fpath), easy=True)
                if tags is not None:
                    title = tags.get('title', [db_title or os.path.basename(fpath)])[0]
                    genre = tags.get('genre', ['Unknown'])[0]
                    c = tags.get('comment', [''])[0]
                    comment = str(c) if c else ''
            except Exception as e:
                self._debug_log('WARN', f'Backfill genre/comment failed for {fpath}: {e}')
            con.execute("UPDATE tracks SET genre = ?, comment = ?, title = ? WHERE id = ?",
                        (genre, comment, title, track_id))
        con.commit()

    def _backfill_lengths(self, con):
        if MutagenFile is None:
            return
        cur = con.execute("SELECT id, file_path FROM tracks WHERE length IS NULL")
        rows = cur.fetchall()
        for track_id, fpath in rows:
            length = None
            try:
                audio = MutagenFile(self._abs_path(fpath))
                if audio is not None and audio.info is not None:
                    length = audio.info.length
            except Exception as e:
                self._debug_log('WARN', f'Backfill length failed for {fpath}: {e}')
            if length is not None:
                con.execute("UPDATE tracks SET length = ? WHERE id = ?", (length, track_id))
        if rows:
            con.commit()

    def _backfill_artist_album(self, con):
        if MutagenFile is None:
            return
        cur = con.execute("SELECT id, file_path FROM tracks WHERE artist IS NULL OR artist = ''")
        rows = cur.fetchall()
        for track_id, fpath in rows:
            artist = album = ''
            try:
                tags = MutagenFile(self._abs_path(fpath), easy=True)
                if tags is not None:
                    artist = tags.get('artist', [''])[0] or ''
                    album = tags.get('album', [''])[0] or ''
            except Exception as e:
                self._debug_log('WARN', f'Backfill artist/album failed for {fpath}: {e}')
            if artist or album:
                con.execute("UPDATE tracks SET artist = ?, album = ? WHERE id = ?",
                            (artist, album, track_id))
        if rows:
            con.commit()

    # ── Track CRUD ───────────────────────────────────────

    def load_all_tracks(self):
        """Load all tracks, tags, and votes from DB.
        Returns (tracks_list, all_voters, genres) where tracks_list is a list of dicts."""
        con = self.connect()
        cur = con.cursor()
        cur.execute(
            "SELECT id, file_path, title, play_count, first_played, last_played, "
            "file_created, genre, comment, length, artist, album FROM tracks ORDER BY title"
        )
        rows = cur.fetchall()

        cur.execute("SELECT t.file_path, tt.tag FROM track_tags tt JOIN tracks t ON t.id = tt.track_id")
        tag_rows = cur.fetchall()

        cur.execute("SELECT t.file_path, v.vote, v.voter FROM track_votes v JOIN tracks t ON t.id = v.track_id")
        vote_rows = cur.fetchall()
        con.close()

        tags_by_path = {}
        for fpath, tag in tag_rows:
            tags_by_path.setdefault(fpath, []).append(tag)

        all_voters = set()
        votes_by_path = {}
        for fpath, vote, voter in vote_rows:
            v = votes_by_path.setdefault(fpath, {'rating': 0, 'liked_by': set(), 'disliked_by': set()})
            v['rating'] += vote
            if voter:
                all_voters.add(voter)
                if vote > 0:
                    v['liked_by'].add(voter)
                else:
                    v['disliked_by'].add(voter)

        tracks = []
        genres = set()
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
            tracks.append(entry)
            genres.add(entry['genre'])

        return tracks, all_voters, genres

    def get_track_id(self, path):
        tid = self._track_id_cache.get(path)
        if tid is not None:
            return tid
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT id FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row:
            self._track_id_cache[path] = row[0]
            return row[0]
        return None

    def ensure_track(self, path, title='', genre='Unknown', comment='',
                     length=None, artist='', album='', abs_path_fn=None):
        """Ensure a track exists in the DB. Returns (play_count, first_played, last_played, file_created, length)."""
        abs_fn = abs_path_fn or self._abs_path
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created, length "
                    "FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        if row is None:
            try:
                file_created = datetime.fromtimestamp(
                    os.path.getctime(abs_fn(path)), tz=timezone.utc).isoformat()
            except OSError:
                file_created = None
            cur.execute(
                "INSERT INTO tracks (file_path, title, file_created, genre, comment, length, artist, album) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (path, title, file_created, genre, comment, length, artist, album))
            con.commit()
            con.close()
            return (0, None, None, file_created, length)
        if row[4] is None and length is not None:
            cur.execute("UPDATE tracks SET length = ? WHERE file_path = ?", (length, path))
            con.commit()
            con.close()
            return (row[0], row[1], row[2], row[3], length)
        con.close()
        return row

    # ── Play recording ───────────────────────────────────

    @perf.track
    def record_play(self, path):
        """Record a play event. Returns (play_count, first_played, last_played) or None."""
        now = datetime.now(tz=timezone.utc).isoformat()
        track_id = self.get_track_id(path)
        if not track_id:
            return None
        con = self.connect()
        cur = con.cursor()
        cur.execute('INSERT INTO track_plays (track_id, played_at) VALUES (?, ?)', (track_id, now))
        cur.execute(
            'UPDATE tracks SET play_count = play_count + 1,'
            ' first_played = COALESCE(first_played, ?),'
            ' last_played = ? WHERE id = ?',
            (now, now, track_id))
        con.commit()
        cur.execute('SELECT play_count, first_played, last_played FROM tracks WHERE id = ?', (track_id,))
        result = cur.fetchone()
        con.close()
        return result

    def get_track_stats(self, path):
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT play_count, first_played, last_played, file_created "
                    "FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        return row if row else (0, None, None, None)

    # ── Tags ─────────────────────────────────────────────

    def add_tag(self, path, tag):
        track_id = self.get_track_id(path)
        if track_id:
            con = self.connect()
            con.execute("INSERT OR IGNORE INTO track_tags (track_id, tag) VALUES (?, ?)",
                        (track_id, tag))
            con.commit()
            con.close()

    def remove_tag(self, path, tag):
        track_id = self.get_track_id(path)
        if track_id:
            con = self.connect()
            con.execute("DELETE FROM track_tags WHERE track_id = ? AND tag = ?",
                        (track_id, tag))
            con.commit()
            con.close()

    # ── Votes ────────────────────────────────────────────

    def record_vote(self, path, vote, voter=''):
        """Record a vote. Returns (success: bool, message: str)."""
        track_id = self.get_track_id(path)
        if not track_id:
            return False, 'Track not found in database.'
        today_str = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d')
        con = self.connect()
        cur = con.cursor()
        cur.execute(
            "SELECT id FROM track_votes WHERE track_id = ? AND voter = ? AND voted_at LIKE ?",
            (track_id, voter, f'{today_str}%'))
        if cur.fetchone():
            con.close()
            who = voter or 'Anonymous'
            return False, f'{who} has already voted on this track today.\nYou can vote again tomorrow.'
        now = datetime.now(tz=timezone.utc).isoformat()
        con.execute("INSERT INTO track_votes (track_id, vote, voter, voted_at) VALUES (?, ?, ?, ?)",
                    (track_id, vote, voter, now))
        con.commit()
        con.close()
        return True, ''

    # ── Equalizer ────────────────────────────────────────

    def load_track_eq(self, track_id):
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT preamp, bands FROM track_eq WHERE track_id = ?", (track_id,))
        row = cur.fetchone()
        con.close()
        return row  # (preamp, bands_str) or None

    def save_track_eq(self, track_id, preamp, bands_str):
        con = self.connect()
        con.execute("INSERT OR REPLACE INTO track_eq (track_id, preamp, bands) VALUES (?, ?, ?)",
                    (track_id, preamp, bands_str))
        con.commit()
        con.close()

    def delete_track_eq(self, track_id):
        con = self.connect()
        con.execute("DELETE FROM track_eq WHERE track_id = ?", (track_id,))
        con.commit()
        con.close()

    # ── Queue persistence ────────────────────────────────

    def save_queue(self, file_paths):
        con = self.connect()
        con.execute("DELETE FROM queue_state")
        for i, fp in enumerate(file_paths):
            con.execute("INSERT INTO queue_state (position, file_path) VALUES (?, ?)", (i, fp))
        con.commit()
        con.close()

    def load_queue(self):
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT file_path FROM queue_state ORDER BY position")
        paths = [row[0] for row in cur.fetchall()]
        con.close()
        return paths

    # ── Audit log ────────────────────────────────────────

    def flush_audit_log(self, entries):
        """Write a batch of (timestamp, action, detail) tuples."""
        if not entries:
            return
        try:
            con = self.connect()
            con.executemany("INSERT INTO audit_log (timestamp, action, detail) VALUES (?, ?, ?)",
                            entries)
            con.commit()
            con.close()
        except Exception as e:
            self._debug_log('ERROR', f'flush_audit_log failed: {e}')

    def get_audit_log(self, limit=500):
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT timestamp, action, detail FROM audit_log ORDER BY id DESC LIMIT ?",
                    (limit,))
        rows = cur.fetchall()
        con.close()
        return rows

    # ── Play log ─────────────────────────────────────────

    def get_play_log(self, limit=200):
        """Return recent play events: (track_id, file_path, title, genre, played_at)."""
        con = self.connect()
        cur = con.cursor()
        cur.execute(
            "SELECT tp.track_id, t.file_path, t.title, t.genre, tp.played_at "
            "FROM track_plays tp JOIN tracks t ON t.id = tp.track_id "
            "ORDER BY tp.played_at DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        con.close()
        return rows

    # ── Genre groups (DB migration path) ─────────────────

    def load_genre_groups_from_db(self):
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT id, group_name FROM genre_groups ORDER BY sort_order, group_name")
        groups = cur.fetchall()
        result = {}
        for gid, gname in groups:
            cur.execute("SELECT genre FROM genre_group_members WHERE group_id = ? "
                        "ORDER BY sort_order, genre", (gid,))
            result[gname] = [r[0] for r in cur.fetchall()]
        con.close()
        return result

    # ── Track field updates ──────────────────────────────

    def update_track_field(self, path, field, value):
        """Update a single field on a track row."""
        allowed = {'title', 'genre', 'comment', 'artist', 'album', 'bpm'}
        if field not in allowed:
            return
        con = self.connect()
        con.execute(f"UPDATE tracks SET {field} = ? WHERE file_path = ?", (value, path))
        con.commit()
        con.close()

    def delete_track(self, path):
        """Delete a track and all related data."""
        track_id = self.get_track_id(path)
        if not track_id:
            return
        con = self.connect()
        con.execute("DELETE FROM track_plays WHERE track_id = ?", (track_id,))
        con.execute("DELETE FROM track_tags WHERE track_id = ?", (track_id,))
        con.execute("DELETE FROM track_votes WHERE track_id = ?", (track_id,))
        con.execute("DELETE FROM track_eq WHERE track_id = ?", (track_id,))
        con.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        con.commit()
        con.close()
        self._track_id_cache.pop(path, None)

    def snapshot(self, dest_path):
        """Copy the database file to a snapshot path."""
        import shutil
        shutil.copy2(self.db_path, dest_path)

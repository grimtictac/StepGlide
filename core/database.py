"""
Database layer — schema init, queries, track CRUD, votes, tags, audit log.
Pure Python, no UI dependencies.
"""

import math
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
        self._shared_con = None    # set inside shared_connection() context

    def connect(self):
        # If a shared connection is active (e.g. during startup batch),
        # return a thin proxy that delegates everything but ignores
        # .close(), so existing call sites stay unchanged.
        if self._shared_con is not None:
            return _SharedConnProxy(self._shared_con)
        con = sqlite3.connect(self.db_path)
        # Performance pragmas — applied per connection.
        # journal_mode=WAL persists at the DB level (one-time switch) but
        # setting it here is harmless and ensures it's on for fresh DBs.
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("PRAGMA temp_store=MEMORY")
            con.execute("PRAGMA cache_size=-20000")     # ~20 MB page cache
            con.execute("PRAGMA mmap_size=268435456")   # 256 MB memory-mapped I/O
        except Exception as e:
            self._debug_log('WARN', f'Failed to apply SQLite pragmas: {e}')
        return con

    def shared_connection(self):
        """Context manager that pins one real connection for the duration
        of the block. Any code calling self.connect() while inside will
        receive a proxy that delegates to the shared connection but
        ignores .close(), so existing call sites need no changes.

        Use for batch operations (e.g. startup loads) to avoid the
        overhead of repeatedly opening/closing SQLite connections and
        re-applying pragmas.
        """
        return _SharedConnectionContext(self)

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
            ('waveform', "ALTER TABLE tracks ADD COLUMN waveform BLOB"),
            ('hidden', "ALTER TABLE tracks ADD COLUMN hidden INTEGER DEFAULT 0"),
            # Bitmask of which deferred backfills have already run for
            # this row.  Bit 0 = artist/album, bit 1 = length, bit 2 =
            # genre/comment.  Once set we never re-scan the file —
            # historical behaviour was to retry every launch for any
            # track whose tag legitimately had an empty field, which
            # turned init_schema into a per-launch O(library) mutagen
            # walk.  See run_deferred_backfills().
            ('backfill_done', "ALTER TABLE tracks ADD COLUMN backfill_done INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in columns:
                con.execute(sql)
                con.commit()

        # track_plays column migrations
        cur = con.execute("PRAGMA table_info(track_plays)")
        play_cols = [row[1] for row in cur.fetchall()]
        for col, sql in [
            # Which audio output the play was routed through at the time.
            # 'speaker' = main output (counts toward play_count, default).
            # 'headphones' = preview/cue output (does not count, future use).
            ('via', "ALTER TABLE track_plays ADD COLUMN via TEXT NOT NULL DEFAULT 'speaker'"),
        ]:
            if col not in play_cols:
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

        # Indexes — critical for join performance on large libraries.
        # All joins in load_all_tracks / get_play_log / vote & tag lookups
        # were doing full table scans without these.
        for idx_sql in (
            "CREATE INDEX IF NOT EXISTS idx_track_tags_track_id   ON track_tags(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_track_votes_track_id  ON track_votes(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_track_plays_track_id  ON track_plays(track_id)",
            "CREATE INDEX IF NOT EXISTS idx_track_plays_played_at ON track_plays(played_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_genre_members_group   ON genre_group_members(group_id)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_file_path      ON tracks(file_path)",
            "CREATE INDEX IF NOT EXISTS idx_tracks_title_nocase   ON tracks(title COLLATE NOCASE)",
        ):
            try:
                con.execute(idx_sql)
            except Exception as e:
                self._debug_log('WARN', f'Failed to create index: {e} ({idx_sql})')
        con.commit()

        # Backfills are no longer run here — they used to do a
        # mutagen walk over every row with NULL/empty fields on each
        # launch, blocking the splash for thousands of file reads.
        # init_schema() now stays light; call run_deferred_backfills()
        # from a background thread after the UI is up.
        con.close()

    # ── Deferred backfills (call from a worker thread) ───

    # backfill_done bitmask layout
    _BF_ARTIST_ALBUM = 1 << 0
    _BF_LENGTH       = 1 << 1
    _BF_GENRE        = 1 << 2

    def run_deferred_backfills(self, should_stop=None, progress_cb=None):
        """Run all mutagen-touching backfills.  Safe to call from a
        background thread — opens its own SQLite connection.

        ``should_stop`` is an optional zero-arg callable returning True
        to abort early (e.g. on app shutdown).  ``progress_cb`` is
        called as ``progress_cb(stage, done, total)`` for UI hooks.

        Each row is marked in ``tracks.backfill_done`` once its file
        has been inspected, so subsequent launches skip it entirely
        regardless of whether mutagen returned anything useful.
        """
        if MutagenFile is None:
            return
        try:
            con = sqlite3.connect(self.db_path)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            try:
                self._backfill_artist_album(con, should_stop, progress_cb)
                self._backfill_lengths(con, should_stop, progress_cb)
                self._backfill_genres(con, should_stop, progress_cb)
            finally:
                con.close()
        except Exception as e:
            self._debug_log('ERROR', f'Deferred backfill aborted: {e}')

    def _bf_should_stop(self, should_stop):
        try:
            return bool(should_stop and should_stop())
        except Exception:
            return False

    def _backfill_genres(self, con, should_stop=None, progress_cb=None):
        if MutagenFile is None:
            return
        # Genre/comment backfill is gated on the legacy "no real genres
        # anywhere" condition — a one-time post-migration sweep.
        cur = con.execute("SELECT COUNT(*) FROM tracks WHERE genre != 'Unknown'")
        if cur.fetchone()[0] > 0:
            return
        cur = con.execute(
            "SELECT id, file_path, title FROM tracks "
            "WHERE (backfill_done & ?) = 0",
            (self._BF_GENRE,),
        )
        rows = cur.fetchall()
        total = len(rows)
        if not total:
            return
        for idx, (track_id, fpath, db_title) in enumerate(rows):
            if self._bf_should_stop(should_stop):
                return
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
            con.execute(
                "UPDATE tracks SET genre = ?, comment = ?, title = ?, "
                "backfill_done = backfill_done | ? WHERE id = ?",
                (genre, comment, title, self._BF_GENRE, track_id),
            )
            if (idx + 1) % 100 == 0:
                con.commit()
                if progress_cb:
                    try: progress_cb('genre', idx + 1, total)
                    except Exception: pass
        con.commit()
        if progress_cb:
            try: progress_cb('genre', total, total)
            except Exception: pass

    def _backfill_lengths(self, con, should_stop=None, progress_cb=None):
        if MutagenFile is None:
            return
        cur = con.execute(
            "SELECT id, file_path FROM tracks "
            "WHERE length IS NULL AND (backfill_done & ?) = 0",
            (self._BF_LENGTH,),
        )
        rows = cur.fetchall()
        total = len(rows)
        if not total:
            return
        for idx, (track_id, fpath) in enumerate(rows):
            if self._bf_should_stop(should_stop):
                return
            length = None
            try:
                audio = MutagenFile(self._abs_path(fpath))
                if audio is not None and audio.info is not None:
                    length = audio.info.length
            except Exception as e:
                self._debug_log('WARN', f'Backfill length failed for {fpath}: {e}')
            # Always mark the row scanned; only update length if we got
            # a real value (NULL stays NULL rather than being clobbered
            # to 0, which would skew duration sorts).
            if length is not None:
                con.execute(
                    "UPDATE tracks SET length = ?, "
                    "backfill_done = backfill_done | ? WHERE id = ?",
                    (length, self._BF_LENGTH, track_id),
                )
            else:
                con.execute(
                    "UPDATE tracks SET backfill_done = backfill_done | ? "
                    "WHERE id = ?",
                    (self._BF_LENGTH, track_id),
                )
            if (idx + 1) % 100 == 0:
                con.commit()
                if progress_cb:
                    try: progress_cb('length', idx + 1, total)
                    except Exception: pass
        con.commit()
        if progress_cb:
            try: progress_cb('length', total, total)
            except Exception: pass

    def _backfill_artist_album(self, con, should_stop=None, progress_cb=None):
        if MutagenFile is None:
            return
        cur = con.execute(
            "SELECT id, file_path FROM tracks "
            "WHERE (artist IS NULL OR artist = '') "
            "AND (backfill_done & ?) = 0",
            (self._BF_ARTIST_ALBUM,),
        )
        rows = cur.fetchall()
        total = len(rows)
        if not total:
            return
        for idx, (track_id, fpath) in enumerate(rows):
            if self._bf_should_stop(should_stop):
                return
            artist = album = ''
            try:
                tags = MutagenFile(self._abs_path(fpath), easy=True)
                if tags is not None:
                    artist = tags.get('artist', [''])[0] or ''
                    album = tags.get('album', [''])[0] or ''
            except Exception as e:
                self._debug_log('WARN', f'Backfill artist/album failed for {fpath}: {e}')
            # Mark scanned in every case so we don't re-walk this file
            # on the next launch when it legitimately has no tags.
            if artist or album:
                con.execute(
                    "UPDATE tracks SET artist = ?, album = ?, "
                    "backfill_done = backfill_done | ? WHERE id = ?",
                    (artist, album, self._BF_ARTIST_ALBUM, track_id),
                )
            else:
                con.execute(
                    "UPDATE tracks SET backfill_done = backfill_done | ? "
                    "WHERE id = ?",
                    (self._BF_ARTIST_ALBUM, track_id),
                )
            if (idx + 1) % 100 == 0:
                con.commit()
                if progress_cb:
                    try: progress_cb('artist_album', idx + 1, total)
                    except Exception: pass
        con.commit()
        if progress_cb:
            try: progress_cb('artist_album', total, total)
            except Exception: pass
    # ── Track CRUD ───────────────────────────────────────

    @perf.track
    def load_all_tracks(self, progress_cb=None):
        """Load all tracks, tags, and votes from DB.
        Returns (tracks_list, all_voters, genres) where tracks_list is a list of dicts.
        *progress_cb*, if provided, is called with (current, total) during row iteration."""
        con = self.connect()
        cur = con.cursor()
        cur.execute(
            "SELECT id, file_path, title, play_count, first_played, last_played, "
            "file_created, genre, comment, length, artist, album, hidden FROM tracks ORDER BY title"
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
        total = len(rows)
        for idx, (track_id, path, db_title, play_count, first_played, last_played,
             file_created, genre, comment, length, artist, album, hidden) in enumerate(rows):
            if path in seen:
                continue
            seen.add(path)
            self._track_id_cache[path] = track_id
            vdata = votes_by_path.get(path, {'rating': 0, 'liked_by': set(), 'disliked_by': set()})
            likes = len(vdata['liked_by'])
            dislikes = len(vdata['disliked_by'])
            plays = play_count or 0
            # Bayesian-smoothed score: ratio × engagement factor
            prior = 1
            smoothed = (likes + prior) / (likes + dislikes + 2 * prior)
            engagement = math.log(plays + 1, 10) / math.log(101, 10)  # 0→0, 100→1
            score = smoothed * (0.5 + 0.5 * engagement)  # floor at 50% of ratio
            if bool(hidden):
                score = 0.0
            entry = {
                'path': path,
                'title': db_title or os.path.basename(path),
                'basename': os.path.basename(path),
                'artist': artist or '',
                'album': album or '',
                'genre': genre or 'Unknown',
                'comment': comment or '',
                'play_count': plays,
                'first_played': first_played,
                'last_played': last_played,
                'file_created': file_created,
                'length': length,
                'tags': tags_by_path.get(path, []),
                'rating': vdata['rating'],
                'likes': likes,
                'dislikes': dislikes,
                'score': round(score * 100),   # 0–100 integer
                'liked_by': vdata['liked_by'],
                'disliked_by': vdata['disliked_by'],
                'hidden': bool(hidden),
            }
            tracks.append(entry)
            genres.add(entry['genre'])
            if progress_cb and (idx % 200 == 0 or idx == total - 1):
                progress_cb(idx + 1, total)

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

    @perf.track
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
    def record_play(self, path, via='speaker'):
        """Record a play event.

        Args:
            path: track file path.
            via:  audio output the play was routed through. 'speaker' (main,
                  default — counts toward stats) or 'headphones' (preview/cue,
                  reserved for future use; today only 'speaker' is written).

        Returns (play_count, first_played, last_played) or None.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        track_id = self.get_track_id(path)
        if not track_id:
            return None
        con = self.connect()
        cur = con.cursor()
        cur.execute(
            'INSERT INTO track_plays (track_id, played_at, via) VALUES (?, ?, ?)',
            (track_id, now, via),
        )
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

    @perf.track
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

    # ── Waveform cache ───────────────────────────────────

    def get_waveform(self, path):
        """Return cached waveform blob for *path*, or None."""
        con = self.connect()
        cur = con.cursor()
        cur.execute("SELECT waveform FROM tracks WHERE file_path = ?", (path,))
        row = cur.fetchone()
        con.close()
        if row and row[0]:
            return row[0]
        return None

    def save_waveform(self, path, blob):
        """Store compressed waveform blob for *path*."""
        con = self.connect()
        con.execute("UPDATE tracks SET waveform = ? WHERE file_path = ?",
                    (blob, path))
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

    def set_track_hidden(self, path, hidden, comment=None):
        """Mark a track as hidden (or unhidden).  Optionally update comment."""
        con = self.connect()
        if comment is not None:
            con.execute("UPDATE tracks SET hidden = ?, comment = ? WHERE file_path = ?",
                        (1 if hidden else 0, comment, path))
        else:
            con.execute("UPDATE tracks SET hidden = ? WHERE file_path = ?",
                        (1 if hidden else 0, path))
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
        """Copy the database file to a snapshot path.

        Under WAL mode the on-disk .db file does not necessarily contain
        the most recent committed data — that lives in the .db-wal
        sidecar until a checkpoint folds it back into the main file.
        We checkpoint with TRUNCATE first so the snapshot is a complete,
        self-contained copy of every committed transaction. As a safety
        net we also copy the -wal/-shm sidecar files if they still
        exist, so even an interrupted checkpoint leaves a recoverable
        snapshot set.
        """
        import shutil

        # Force a full checkpoint so all committed data is in the main file.
        try:
            con = self.connect()
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                con.commit()
            finally:
                con.close()
        except Exception as e:
            self._debug_log('WARN', f'Snapshot WAL checkpoint failed: {e}')

        shutil.copy2(self.db_path, dest_path)

        # Belt-and-braces: copy any remaining WAL/SHM sidecars too.
        for suffix in ('-wal', '-shm'):
            src = self.db_path + suffix
            if os.path.exists(src):
                try:
                    shutil.copy2(src, dest_path + suffix)
                except Exception as e:
                    self._debug_log('WARN',
                        f'Snapshot sidecar copy failed ({suffix}): {e}')


class _SharedConnProxy:
    """Proxy around a sqlite3.Connection whose .close() is a no-op.

    Returned by Database.connect() while a shared_connection() context
    is active, so existing call sites that do `con = self.connect(); ...
    con.close()` keep working without needing changes — the real
    connection is held by the context manager and closed at exit.
    """
    __slots__ = ('_real',)

    def __init__(self, real):
        self._real = real

    def close(self):
        # Intentionally no-op; real close happens in the context manager.
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self._real.__enter__()

    def __exit__(self, *args):
        return self._real.__exit__(*args)


class _SharedConnectionContext:
    """Context manager that pins one real sqlite3 connection on a Database."""

    def __init__(self, db):
        self._db = db
        self._owner = False  # True if this context opened the connection

    def __enter__(self):
        if self._db._shared_con is None:
            self._db._shared_con = sqlite3.connect(self._db.db_path)
            try:
                self._db._shared_con.execute("PRAGMA journal_mode=WAL")
                self._db._shared_con.execute("PRAGMA synchronous=NORMAL")
                self._db._shared_con.execute("PRAGMA temp_store=MEMORY")
                self._db._shared_con.execute("PRAGMA cache_size=-20000")
                self._db._shared_con.execute("PRAGMA mmap_size=268435456")
            except Exception as e:
                self._db._debug_log('WARN', f'Shared-conn pragmas failed: {e}')
            self._owner = True
        return self._db._shared_con

    def __exit__(self, exc_type, exc, tb):
        if self._owner:
            try:
                self._db._shared_con.close()
            finally:
                self._db._shared_con = None
        return False

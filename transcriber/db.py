"""SQLite storage for recorded calls and their transcripts.

One row per call. A row is written only once, at the moment the call has been
either transcribed or deliberately skipped — so the presence of a row is what
marks a call as "already handled". That is what makes the poller idempotent
across restarts.

The database lives on a shared volume and is read by the web service at the
same time, so it runs in WAL mode.
"""

import json
import os
import sqlite3
import threading
import time

DB_PATH = os.environ.get("DB_PATH", "/data/calls.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id                   INTEGER PRIMARY KEY,

    -- Stable identifier: the call's path relative to the media root, minus the
    -- file extension. Trunk Recorder guarantees this is unique per call.
    call_key             TEXT NOT NULL UNIQUE,

    short_name           TEXT,
    talkgroup            INTEGER,
    talkgroup_tag        TEXT,   -- short "alpha tag", e.g. GJ Fire Disp
    talkgroup_description TEXT,
    talkgroup_group      TEXT,   -- category, e.g. Fire
    talkgroup_group_tag  TEXT,   -- service tag, e.g. Fire Dispatch

    start_time           INTEGER,  -- unix epoch seconds
    duration             REAL,     -- seconds
    freq                 INTEGER,  -- Hz
    emergency            INTEGER DEFAULT 0,
    encrypted            INTEGER DEFAULT 0,
    sources              TEXT,     -- JSON array of radio (unit) IDs

    audio_path           TEXT,     -- relative to the media root
    json_path            TEXT,

    transcript           TEXT DEFAULT '',
    status               TEXT,     -- 'done' | 'skipped' | 'error'
    status_detail        TEXT,     -- why it was skipped, or the error text

    rtf                  REAL,     -- transcribe seconds / audio seconds
    transcribed_at       INTEGER,
    discord_posted_at    INTEGER,
    created_at           INTEGER
);

CREATE INDEX IF NOT EXISTS idx_calls_start_time ON calls(start_time DESC);
CREATE INDEX IF NOT EXISTS idx_calls_talkgroup  ON calls(talkgroup);

-- Full-text index over transcripts, kept in sync by the triggers below.
CREATE VIRTUAL TABLE IF NOT EXISTS calls_fts
    USING fts5(transcript, content='calls', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS calls_fts_insert AFTER INSERT ON calls BEGIN
    INSERT INTO calls_fts(rowid, transcript) VALUES (new.id, new.transcript);
END;

CREATE TRIGGER IF NOT EXISTS calls_fts_delete AFTER DELETE ON calls BEGIN
    INSERT INTO calls_fts(calls_fts, rowid, transcript)
        VALUES ('delete', old.id, old.transcript);
END;
"""


class CallStore:
    def __init__(self, path=DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        # The Discord worker runs on its own thread and marks rows as posted,
        # so the connection is shared across threads under an explicit lock.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    # -- reads ---------------------------------------------------------------

    def known_call_keys(self):
        """Every call we have already handled, for the poller's skip check."""
        with self._lock:
            rows = self._conn.execute("SELECT call_key FROM calls").fetchall()
        return {r["call_key"] for r in rows}

    def get(self, call_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
        return dict(row) if row else None

    # -- writes --------------------------------------------------------------

    def insert(self, call):
        """Record a handled call. Returns its row id, or None if already present.

        `call` is the dict built by transcriber.build_record().
        """
        fields = (
            "call_key", "short_name", "talkgroup", "talkgroup_tag",
            "talkgroup_description", "talkgroup_group", "talkgroup_group_tag",
            "start_time", "duration", "freq", "emergency", "encrypted",
            "sources", "audio_path", "json_path", "transcript", "status",
            "status_detail", "rtf", "transcribed_at", "created_at",
        )
        values = [call.get(f) for f in fields]
        # sources arrives as a list.
        values[fields.index("sources")] = json.dumps(call.get("sources") or [])
        values[fields.index("created_at")] = int(time.time())

        placeholders = ",".join("?" * len(fields))
        sql = (
            f"INSERT OR IGNORE INTO calls ({','.join(fields)}) "
            f"VALUES ({placeholders})"
        )
        with self._lock:
            cur = self._conn.execute(sql, values)
            self._conn.commit()
        return cur.lastrowid if cur.rowcount else None

    def mark_discord_posted(self, call_id):
        with self._lock:
            self._conn.execute(
                "UPDATE calls SET discord_posted_at = ? WHERE id = ?",
                (int(time.time()), call_id),
            )
            self._conn.commit()

    def already_posted_to_discord(self, call_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT discord_posted_at FROM calls WHERE id = ?", (call_id,)
            ).fetchone()
        return bool(row and row["discord_posted_at"])

    # -- housekeeping --------------------------------------------------------

    def rows_older_than(self, cutoff_epoch):
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, audio_path, json_path FROM calls WHERE start_time < ?",
                (cutoff_epoch,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_rows(self, ids):
        if not ids:
            return
        with self._lock:
            self._conn.executemany("DELETE FROM calls WHERE id = ?",
                                   [(i,) for i in ids])
            self._conn.commit()

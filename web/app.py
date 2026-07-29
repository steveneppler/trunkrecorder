"""Searchable transcript feed for recorded scanner calls.

Read-only view over the SQLite database the transcriber writes. Rdio Scanner
(on port 3000) is the place to *listen*; this is the place to *read* — a live
feed of what was said, and full-text search across everything recorded.

There is no login. Keep this on your home network; see README.md.
"""

import os
import sqlite3
from datetime import datetime

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

DB_PATH = os.environ.get("DB_PATH", "/data/calls.db")
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", "/media")
PAGE_SIZE = 100

app = FastAPI(title="Grand Junction Scanner Transcripts", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def connect():
    """A read-only connection.

    The database file is mounted writable because SQLite's WAL mode needs to
    touch its sidecar files even to read, but query_only makes it impossible for
    this service to change anything.
    """
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def fts_query(raw):
    """Turn what someone typed into an FTS5 expression that will not error.

    Anything the user types is a phrase to look for, not syntax — quoting each
    token means a stray quote or asterisk cannot blow up the query.
    """
    tokens = [t for t in raw.replace('"', " ").split() if t]
    return " AND ".join(f'"{t}"' for t in tokens)


def query_calls(search="", talkgroup=None, group=None, before=None):
    where = []
    params = []
    joins = ""

    if search.strip():
        expression = fts_query(search)
        if expression:
            joins = "JOIN calls_fts ON calls_fts.rowid = calls.id"
            where.append("calls_fts MATCH ?")
            params.append(expression)

    if talkgroup:
        where.append("calls.talkgroup = ?")
        params.append(talkgroup)

    if group:
        where.append("calls.talkgroup_group = ?")
        params.append(group)

    if before:
        where.append("calls.start_time < ?")
        params.append(before)

    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT calls.* FROM calls {joins} {clause} "
        f"ORDER BY calls.start_time DESC LIMIT {PAGE_SIZE}"
    )

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [decorate(dict(r)) for r in rows]


def decorate(row):
    start = row.get("start_time")
    row["time_display"] = (
        datetime.fromtimestamp(start).strftime("%H:%M:%S") if start else "--:--:--"
    )
    row["date_display"] = (
        datetime.fromtimestamp(start).strftime("%a %b %-d") if start else ""
    )
    row["label"] = row.get("talkgroup_tag") or f"TG {row.get('talkgroup')}"
    row["duration_display"] = f"{row.get('duration') or 0:.1f}s"
    row["has_audio"] = bool(row.get("audio_path"))

    if row.get("status") == "skipped":
        row["body"] = f"(skipped — {row.get('status_detail') or 'no reason given'})"
        row["body_muted"] = True
    elif row.get("status") == "error":
        row["body"] = f"(transcription failed — {row.get('status_detail') or 'unknown error'})"
        row["body_muted"] = True
    elif not row.get("transcript"):
        row["body"] = "(no speech detected)"
        row["body_muted"] = True
    else:
        row["body"] = row["transcript"]
        row["body_muted"] = False
    return row


def list_groups():
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT talkgroup_group FROM calls "
            "WHERE talkgroup_group IS NOT NULL AND talkgroup_group != '' "
            "ORDER BY talkgroup_group"
        ).fetchall()
    return [r["talkgroup_group"] for r in rows]


def stats():
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "       SUM(CASE WHEN status='done' AND transcript != '' THEN 1 ELSE 0 END) AS transcribed "
            "FROM calls"
        ).fetchone()
    return {"total": row["total"] or 0, "transcribed": row["transcribed"] or 0}


@app.get("/", response_class=HTMLResponse)
def index(request: Request,
          q: str = Query(""),
          talkgroup: int = Query(None),
          group: str = Query(None)):
    return templates.TemplateResponse(request, "index.html", {
        "calls": query_calls(q, talkgroup, group),
        "groups": list_groups(),
        "stats": stats(),
        "q": q,
        "talkgroup": talkgroup,
        "group": group,
    })


@app.get("/calls", response_class=HTMLResponse)
def calls_partial(request: Request,
                  q: str = Query(""),
                  talkgroup: int = Query(None),
                  group: str = Query(None)):
    """The call list on its own — polled by the page every few seconds."""
    return templates.TemplateResponse(request, "_calls.html", {
        "calls": query_calls(q, talkgroup, group),
    })


@app.get("/audio/{call_id}")
def audio(call_id: int):
    with connect() as conn:
        row = conn.execute(
            "SELECT audio_path FROM calls WHERE id = ?", (call_id,)
        ).fetchone()

    if not row or not row["audio_path"]:
        return HTMLResponse("Not found", status_code=404)

    # audio_path comes from our own database, but resolve it and confirm it is
    # still inside the media root before opening anything.
    path = os.path.realpath(os.path.join(MEDIA_ROOT, row["audio_path"]))
    root = os.path.realpath(MEDIA_ROOT)
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return HTMLResponse("Not found", status_code=404)

    media_type = "audio/mp4" if path.endswith(".m4a") else "audio/wav"
    return FileResponse(path, media_type=media_type)


@app.get("/healthz")
def healthz():
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"ok": True}
    except sqlite3.Error as exc:
        return {"ok": False, "error": str(exc)}

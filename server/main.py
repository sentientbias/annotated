"""Annotated API — shared annotation backend for the Annotated Chrome extension.

Highlight any sentence on the web, dispute it, follow sharp readers,
watch the trending disputes. SQLite-backed, Render-ready.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from html import escape

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("ANNOTATED_DB", os.path.join(os.path.dirname(__file__), "annotated.db"))
VALID_STANCES = {"dispute", "agree", "context"}
HANDLE_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")

app = FastAPI(title="Annotated API", version="0.1.0")


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = db()
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            quote TEXT NOT NULL,
            prefix TEXT DEFAULT '',
            suffix TEXT DEFAULT '',
            stance TEXT NOT NULL,
            comment TEXT NOT NULL,
            handle TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ann_url ON annotations(url);
        CREATE INDEX IF NOT EXISTS idx_ann_quote ON annotations(quote);
        CREATE TABLE IF NOT EXISTS profiles (
            handle TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS follows (
            follower TEXT NOT NULL,
            followee TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (follower, followee)
        );
        """
    )
    con.commit()
    con.close()


init_db()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean_handle(h: str) -> str:
    h = (h or "").strip().lower()
    if not HANDLE_RE.match(h):
        raise HTTPException(400, "handle must be 2-32 chars: letters, numbers, _ . -")
    return h


# ---------- models ----------
class AnnotationIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=20, max_length=600)
    prefix: str = Field(default="", max_length=200)
    suffix: str = Field(default="", max_length=200)
    stance: str
    comment: str = Field(min_length=1, max_length=2000)
    handle: str = Field(min_length=2, max_length=32)


class FollowIn(BaseModel):
    follower: str
    followee: str


# ---------- routes ----------
@app.get("/health")
def health():
    return {"ok": True, "service": "annotated-api"}


@app.post("/annotations", status_code=201)
def post_annotation(a: AnnotationIn):
    stance = a.stance.strip().lower()
    if stance not in VALID_STANCES:
        raise HTTPException(400, f"stance must be one of {sorted(VALID_STANCES)}")
    handle = clean_handle(a.handle)
    con = db()
    con.execute("INSERT OR IGNORE INTO profiles (handle, created_at) VALUES (?, ?)", (handle, now_iso()))
    cur = con.execute(
        "INSERT INTO annotations (url, quote, prefix, suffix, stance, comment, handle, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (a.url.strip(), a.quote.strip(), a.prefix, a.suffix, stance, a.comment.strip(), handle, now_iso()),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {"ok": True, "id": new_id}


@app.get("/annotations")
def get_annotations(url: str = Query(min_length=1, max_length=2000)):
    con = db()
    rows = con.execute(
        "SELECT id, url, quote, prefix, suffix, stance, comment, handle, created_at"
        " FROM annotations WHERE url = ? ORDER BY created_at DESC LIMIT 500",
        (url,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


@app.post("/follow")
def follow(f: FollowIn):
    follower = clean_handle(f.follower)
    followee = clean_handle(f.followee)
    if follower == followee:
        raise HTTPException(400, "cannot follow yourself")
    con = db()
    for h in (follower, followee):
        con.execute("INSERT OR IGNORE INTO profiles (handle, created_at) VALUES (?, ?)", (h, now_iso()))
    con.execute(
        "INSERT OR IGNORE INTO follows (follower, followee, created_at) VALUES (?, ?, ?)",
        (follower, followee, now_iso()),
    )
    con.commit()
    con.close()
    return {"ok": True, "follower": follower, "followee": followee}


@app.get("/profiles/{handle}")
def profile(handle: str):
    handle = clean_handle(handle)
    con = db()
    prow = con.execute("SELECT handle, created_at FROM profiles WHERE handle = ?", (handle,)).fetchone()
    if not prow:
        con.close()
        raise HTTPException(404, "unknown handle")
    count = con.execute("SELECT COUNT(*) c FROM annotations WHERE handle = ?", (handle,)).fetchone()["c"]
    followers = con.execute("SELECT COUNT(*) c FROM follows WHERE followee = ?", (handle,)).fetchone()["c"]
    following = con.execute("SELECT COUNT(*) c FROM follows WHERE follower = ?", (handle,)).fetchone()["c"]
    recent = con.execute(
        "SELECT quote, stance, comment, url, created_at FROM annotations WHERE handle = ?"
        " ORDER BY created_at DESC LIMIT 20",
        (handle,),
    ).fetchall()
    con.close()
    return {
        "handle": handle,
        "created_at": prow["created_at"],
        "annotation_count": count,
        "followers": followers,
        "following": following,
        "recent": [dict(r) for r in recent],
    }


@app.get("/trending", response_class=HTMLResponse)
def trending():
    con = db()
    rows = con.execute(
        "SELECT quote, url, COUNT(*) c,"
        " SUM(CASE WHEN stance='dispute' THEN 1 ELSE 0 END) disputes"
        " FROM annotations GROUP BY quote, url ORDER BY c DESC LIMIT 50"
    ).fetchall()
    con.close()
    items = "".join(
        f"<div class='t'><div class='q'>&ldquo;{escape(r['quote'][:220])}&rdquo;</div>"
        f"<div class='m'>{r['c']} annotations · {r['disputes']} disputes · "
        f"<a href='{escape(r['url'])}'>{escape(r['url'][:60])}</a></div></div>"
        for r in rows
    ) or "<p>No annotations yet. Be the first to dispute something.</p>"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Trending disputes — Annotated</title>"
        "<style>body{font:15px/1.6 system-ui;max-width:720px;margin:0 auto;padding:24px;color:#111}"
        "h1{font-size:22px}.t{border:1px solid #e5e5e5;border-radius:12px;padding:14px 16px;margin:12px 0}"
        ".q{font-style:italic}.m{font-size:13px;color:#666;margin-top:6px}a{color:#2f7fd0}</style>"
        "</head><body><h1>⚑ Trending disputes</h1>" + items + "</body></html>"
    )


@app.get("/stats")
def stats():
    con = db()
    n_ann = con.execute("SELECT COUNT(*) c FROM annotations").fetchone()["c"]
    n_users = con.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
    con.close()
    return {"annotations": n_ann, "handles": n_users}

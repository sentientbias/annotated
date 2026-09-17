"""Annotated API — shared annotation backend for the Annotated Chrome extension.

Highlight any sentence on the web, dispute it, follow sharp readers,
watch the trending disputes. SQLite-backed, Render-ready.
"""
import hmac
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import auth
import clips

DB_PATH = os.environ.get("ANNOTATED_DB", os.path.join(os.path.dirname(__file__), "annotated.db"))
VALID_STANCES = {"dispute", "agree", "context"}
# Optional discourse tags (borrowed from the best-reviewed competitor's taxonomy).
VALID_TAGS = {"fact_check", "steel_man", "receipt", "hot_take"}
HANDLE_RE = re.compile(r"^[a-zA-Z0-9_.-]{2,32}$")
# Handles nobody may claim (case-insensitive; clean_handle lowercases first).
RESERVED_HANDLES = {"admin", "administrator", "support", "annotated", "system", "moderator"}

app = FastAPI(title="Annotated API", version="0.1.0")


def db():
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
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
        CREATE TABLE IF NOT EXISTS clips (
            id TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            source_type TEXT NOT NULL,
            start_sec REAL DEFAULT 0,
            duration_sec REAL DEFAULT 30,
            status TEXT NOT NULL DEFAULT 'processing',
            file_path TEXT DEFAULT '',
            error TEXT DEFAULT '',
            handle TEXT NOT NULL,
            comment TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at);
        CREATE TABLE IF NOT EXISTS clip_comments (
            id TEXT PRIMARY KEY,
            clip_id TEXT NOT NULL,
            handle TEXT NOT NULL,
            text TEXT DEFAULT '',
            audio_url TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_cc_clip ON clip_comments(clip_id);
        CREATE TABLE IF NOT EXISTS annotation_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            annotation_id INTEGER NOT NULL,
            handle TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ar_ann ON annotation_replies(annotation_id);
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            verifier TEXT DEFAULT '',
            provider TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS link_tokens (
            token TEXT PRIMARY KEY,
            handle TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
            id TEXT PRIMARY KEY,
            target_type TEXT DEFAULT '',
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            contact TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        """
    )
    for col in ("x_id", "google_id", "display_name"):
        try:
            con.execute(f"ALTER TABLE profiles ADD COLUMN {col} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
    try:
        con.execute("ALTER TABLE annotations ADD COLUMN tag TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        con.execute("ALTER TABLE clips ADD COLUMN hidden INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    con.commit()
    con.close()


init_db()

# shared connection for the auth/clips routers (they run background threads)
_shared = sqlite3.connect(DB_PATH, check_same_thread=False)
_shared.execute("PRAGMA journal_mode=WAL")
_shared.execute("PRAGMA busy_timeout=5000")
auth.init(_shared)
clips.init(_shared)
app.include_router(auth.router)
app.include_router(clips.router)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean_handle(h: str) -> str:
    h = (h or "").strip().lower()
    if not HANDLE_RE.match(h):
        raise HTTPException(400, "handle must be 2-32 chars: letters, numbers, _ . -")
    if h in RESERVED_HANDLES:
        raise HTTPException(400, "that handle is reserved")
    return h


def rate_limited(con: sqlite3.Connection, table: str, handle: str, max_per_hour: int) -> bool:
    """True when handle already has max_per_hour rows in table within the last hour."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    n = con.execute(
        f"SELECT COUNT(*) c FROM {table} WHERE handle=? AND created_at >= ?",
        (handle, cutoff),
    ).fetchone()["c"]
    return n >= max_per_hour


# ---------- models ----------
class AnnotationIn(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=20, max_length=600)
    prefix: str = Field(default="", max_length=200)
    suffix: str = Field(default="", max_length=200)
    stance: str
    comment: str = Field(min_length=1, max_length=2000)
    handle: str = Field(min_length=2, max_length=32)
    tag: str = Field(default="", max_length=16)  # optional discourse tag


class FollowIn(BaseModel):
    follower: str
    followee: str


class ReplyIn(BaseModel):
    handle: str
    text: str


class HideIn(BaseModel):
    admin_token: str = ""


@app.post("/admin/clips/{clip_id}/hide")
def hide_clip(clip_id: str, h: HideIn):
    """Hide a clip from the public feed (e.g. failed production clips)."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not hmac.compare_digest(h.admin_token, expected):
        raise HTTPException(403, "forbidden")
    con = db()
    cur = con.execute("UPDATE clips SET hidden=1 WHERE id=?", (clip_id,))
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "unknown clip")
    return {"ok": True}


# ---------- routes ----------
@app.get("/health")
def health():
    return {"ok": True, "service": "annotated-api", "version": "0.3.0"}


@app.post("/annotations", status_code=201)
def post_annotation(a: AnnotationIn):
    stance = a.stance.strip().lower()
    if stance not in VALID_STANCES:
        raise HTTPException(400, f"stance must be one of {sorted(VALID_STANCES)}")
    tag = a.tag.strip().lower()
    if tag and tag not in VALID_TAGS:
        raise HTTPException(400, f"tag must be one of {sorted(VALID_TAGS)}")
    handle = clean_handle(a.handle)
    con = db()
    if rate_limited(con, "annotations", handle, 30):
        con.close()
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    con.execute("INSERT OR IGNORE INTO profiles (handle, created_at) VALUES (?, ?)", (handle, now_iso()))
    cur = con.execute(
        "INSERT INTO annotations (url, quote, prefix, suffix, stance, tag, comment, handle, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (a.url.strip(), a.quote.strip(), a.prefix, a.suffix, stance, tag, a.comment.strip(), handle, now_iso()),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {"ok": True, "id": new_id}


@app.get("/annotations")
def get_annotations(url: str = Query(min_length=1, max_length=2000)):
    con = db()
    rows = con.execute(
        "SELECT id, url, quote, prefix, suffix, stance, tag, comment, handle, created_at"
        " FROM annotations WHERE url = ? ORDER BY created_at DESC LIMIT 500",
        (url,),
    ).fetchall()
    ids = [r["id"] for r in rows]
    replies = {}
    if ids:
        for rr in con.execute(
            "SELECT annotation_id, handle, text, created_at FROM annotation_replies"
            f" WHERE annotation_id IN ({','.join('?' * len(ids))})"
            " ORDER BY created_at",
            ids,
        ).fetchall():
            replies.setdefault(rr["annotation_id"], []).append(
                {"handle": rr["handle"], "text": rr["text"], "created_at": rr["created_at"]}
            )
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["replies"] = replies.get(r["id"], [])
        out.append(d)
    return out


@app.post("/annotations/{annotation_id}/replies", status_code=201)
def post_reply(annotation_id: int, r: ReplyIn):
    handle = clean_handle(r.handle)
    text = (r.text or "").strip()[:2000]
    if not text:
        raise HTTPException(400, "text required (1-2000 chars)")
    con = db()
    exists = con.execute("SELECT 1 FROM annotations WHERE id = ?", (annotation_id,)).fetchone()
    if not exists:
        con.close()
        raise HTTPException(404, "annotation not found")
    con.execute("INSERT OR IGNORE INTO profiles (handle, created_at) VALUES (?, ?)", (handle, now_iso()))
    cur = con.execute(
        "INSERT INTO annotation_replies (annotation_id, handle, text, created_at)"
        " VALUES (?, ?, ?, ?)",
        (annotation_id, handle, text, now_iso()),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return {"ok": True, "id": new_id}


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


@app.post("/unfollow")
def unfollow(f: FollowIn):
    follower = clean_handle(f.follower)
    followee = clean_handle(f.followee)
    con = db()
    con.execute("DELETE FROM follows WHERE follower = ? AND followee = ?", (follower, followee))
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
    followers_list = [
        r["follower"]
        for r in con.execute(
            "SELECT follower FROM follows WHERE followee = ? ORDER BY follower LIMIT 100", (handle,)
        ).fetchall()
    ]
    following_list = [
        r["followee"]
        for r in con.execute(
            "SELECT followee FROM follows WHERE follower = ? ORDER BY followee LIMIT 100", (handle,)
        ).fetchall()
    ]
    con.close()
    return {
        "handle": handle,
        "created_at": prow["created_at"],
        "annotation_count": count,
        "followers": followers,
        "following": following,
        "followers_list": followers_list,
        "following_list": following_list,
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


@app.get("/api/feed")
def api_feed(limit: int = Query(default=50, le=200)):
    """Newest annotations + clips, one combined public feed."""
    con = db()
    anns = con.execute(
        "SELECT 'annotation' kind, id, quote, stance, tag, comment, handle, url, created_at"
        " FROM annotations ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    clps = con.execute(
        "SELECT 'clip' kind, id, source_url url, source_type, status, handle, comment,"
        " created_at FROM clips WHERE status='ready' AND COALESCE(hidden,0)=0"
        " ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    con.close()
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    items = [dict(r) for r in anns] + [dict(r) for r in clps]
    for it in items:
        if it["kind"] == "clip":
            it["clip_url"] = f"{base}/c/{it['id']}"
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


@app.get("/feed", response_class=HTMLResponse)
def feed_page():
    items = api_feed(limit=50)
    cards = ""
    for it in items:
        if it["kind"] == "clip":
            badge = "🎬 clip" if it.get("source_type") == "youtube" else "🎙 clip"
            cards += (
                f"<div class='t'><div class='k'>{badge} · {it.get('status')}</div>"
                f"<div class='q'>{escape((it.get('comment') or '')[:220]) or '(no comment)'}</div>"
                f"<div class='m'>@{escape(it['handle'])} · "
                f"<a href='{it['clip_url']}'>open clip</a> · "
                f"<a href='{escape(it['url'])}'>source</a></div></div>"
            )
        else:
            tag_badge = f" · 🏷 {escape(it['tag'].replace('_', ' '))}" if it.get("tag") else ""
            cards += (
                f"<div class='t'><div class='k'>⚑ {escape(it['stance'])}{tag_badge}</div>"
                f"<div class='q'>&ldquo;{escape(it['quote'][:220])}&rdquo;</div>"
                f"<div class='m'>@{escape(it['handle'])} · {escape(it.get('comment','')[:120])} · "
                f"<a href='{escape(it['url'])}'>{escape(it['url'][:60])}</a></div></div>"
            )
    cards = cards or "<p>Nothing yet. Clip a video or dispute a sentence.</p>"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Feed — Annotated</title>"
        "<style>body{font:15px/1.6 system-ui;max-width:720px;margin:0 auto;padding:24px;color:#111}"
        "h1{font-size:22px}.t{border:1px solid #e5e5e5;border-radius:12px;padding:14px 16px;margin:12px 0}"
        ".q{font-style:italic}.m{font-size:13px;color:#666;margin-top:6px}"
        ".k{font-size:12px;color:#999;text-transform:uppercase}a{color:#2f7fd0}</style>"
        "</head><body><h1>Annotated — public feed</h1>" + cards + "</body></html>"
    )

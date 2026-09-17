"""Annotated API — shared annotation backend for the Annotated Chrome extension.

Highlight any sentence on the web, dispute it, follow sharp readers,
watch the trending disputes. SQLite-backed, Render-ready.
"""
import hmac
import ipaddress
import os
import re
import socket
import sqlite3
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
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
        CREATE TABLE IF NOT EXISTS annotation_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            annotation_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            title TEXT DEFAULT '',
            domain TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_as_ann ON annotation_sources(annotation_id);
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
    try:
        con.execute("ALTER TABLE annotations ADD COLUMN hidden INTEGER DEFAULT 0")
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

# demo video + other public statics (server/static/)
_static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/demo", StaticFiles(directory=_static_dir), name="demo")


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


# ---------- receipts: source-link unfurling (SSRF-guarded) ----------
def _public_url_ok(url: str) -> str:
    """Validate a receipt URL: http(s), public IP only. Returns normalized URL."""
    u = (url or "").strip()
    if len(u) > 2000:
        raise ValueError("url too long")
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("only http(s) urls")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise ValueError("unresolvable host")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise ValueError("non-public host")
    return u


def _fetch_title(url: str) -> tuple[str, str]:
    """Fetch a page title for a receipt link. Returns (title, domain). Never raises."""
    def clean(t: str, domain: str) -> str:
        t = re.sub(r"\s+", " ", t or "").strip()[:200]
        # junk titles: empty, bare domain, or "domain.com" style bot walls
        if not t or t.lower().rstrip("/") in (domain.lower(), "http://" + domain.lower(), "https://" + domain.lower()):
            return ""
        return t

    try:
        domain = urllib.parse.urlparse(url).hostname or ""
        req = urllib.request.Request(
            url, headers={"User-Agent": "AnnotatedBot/0.4 (receipt preview)"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype:
                return "", domain
            raw = r.read(1_000_000).decode("utf-8", "replace")
        m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
        title = clean(m.group(1), domain) if m else ""
        if not title:
            og = re.search(
                r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                raw, re.I | re.S,
            ) or re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:title["\']',
                raw, re.I | re.S,
            )
            if og:
                title = clean(html_unescape(og.group(1)), domain)
        return title, domain
    except Exception:
        try:
            return "", urllib.parse.urlparse(url).hostname or ""
        except Exception:
            return "", ""


def html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


def _unfurl_worker(annotation_id: int, urls: list[str]):
    """Background thread: resolve receipt titles without blocking the POST."""
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA busy_timeout=5000")
    try:
        for u in urls:
            try:
                _public_url_ok(u)
            except ValueError:
                continue
            title, domain = _fetch_title(u)
            con.execute(
                "UPDATE annotation_sources SET title=?, domain=? WHERE annotation_id=? AND url=?",
                (title, domain, annotation_id, u),
            )
            con.commit()
    finally:
        con.close()


def get_sources(con: sqlite3.Connection, annotation_ids: list[int]) -> dict[int, list[dict]]:
    """Map annotation_id -> list of receipt dicts."""
    out: dict[int, list[dict]] = {}
    if not annotation_ids:
        return out
    for r in con.execute(
        "SELECT annotation_id, url, title, domain FROM annotation_sources"
        f" WHERE annotation_id IN ({','.join('?' * len(annotation_ids))}) ORDER BY id",
        annotation_ids,
    ).fetchall():
        out.setdefault(r["annotation_id"], []).append(
            {"url": r["url"], "title": r["title"], "domain": r["domain"]}
        )
    return out


# ---------- shared UI helpers ----------
STANCE_COLORS = {"dispute": "#e63c3c", "agree": "#2e9e5b", "context": "#2f7fd0"}


def consensus_counts(con: sqlite3.Connection, url: str) -> dict:
    row = con.execute(
        "SELECT stance, COUNT(*) c FROM annotations WHERE url=? AND COALESCE(hidden,0)=0 GROUP BY stance", (url,)
    ).fetchall()
    counts = {"dispute": 0, "agree": 0, "context": 0}
    for r in row:
        if r["stance"] in counts:
            counts[r["stance"]] = r["c"]
    counts["total"] = counts["dispute"] + counts["agree"] + counts["context"]
    return counts


def meter_html(counts: dict, small: bool = False) -> str:
    """Three-color consensus bar. counts has dispute/agree/context/total keys."""
    total = counts.get("total", 0) or 1
    segs = "".join(
        f"<span style='display:inline-block;height:100%;width:{100*counts[s]/total:.1f}%;"
        f"background:{STANCE_COLORS[s]}' title='{s}: {counts[s]}'></span>"
        for s in ("dispute", "agree", "context")
    )
    h = "6px" if small else "8px"
    label = (
        f"<span style='font-size:11px;color:#666;margin-left:6px'>"
        f"⚑{counts['dispute']} ✓{counts['agree']} ◈{counts['context']}</span>"
    )
    return (
        f"<span style='display:inline-block;width:110px;height:{h};border-radius:99px;"
        f"overflow:hidden;background:#eee;vertical-align:middle'>{segs}</span>{label}"
    )


def x_share_url(text: str, page_url: str) -> str:
    q = urllib.parse.quote
    return f"https://x.com/intent/tweet?text={q(text[:240])}&url={q(page_url)}"


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
    sources: list[str] = Field(default_factory=list, max_length=5)  # receipt links


class FollowIn(BaseModel):
    follower: str
    followee: str


class ReplyIn(BaseModel):
    handle: str
    text: str


class HideIn(BaseModel):
    admin_token: str = ""


class ReunfurlIn(BaseModel):
    admin_token: str = ""
    add_urls: list[str] = Field(default_factory=list, max_length=5)


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


@app.post("/admin/annotations/{annotation_id}/reunfurl")
def reunfurl(annotation_id: int, h: ReunfurlIn):
    """Admin: re-fetch receipt titles for an annotation; optionally attach new receipt URLs."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not hmac.compare_digest(h.admin_token, expected):
        raise HTTPException(403, "forbidden")
    con = db()
    exists = con.execute("SELECT 1 FROM annotations WHERE id=?", (annotation_id,)).fetchone()
    if not exists:
        con.close()
        raise HTTPException(404, "annotation not found")
    urls = [r["url"] for r in con.execute(
        "SELECT url FROM annotation_sources WHERE annotation_id=?", (annotation_id,)
    ).fetchall()]
    for u in (h.add_urls or [])[:5]:
        u = (u or "").strip()
        if not u:
            continue
        try:
            _public_url_ok(u)
        except ValueError:
            continue
        domain = urllib.parse.urlparse(u).hostname or ""
        con.execute(
            "INSERT INTO annotation_sources (annotation_id, url, title, domain, created_at)"
            " VALUES (?, ?, '', ?, ?)",
            (annotation_id, u, domain, now_iso()),
        )
        urls.append(u)
    con.commit()
    con.close()
    _unfurl_worker(annotation_id, urls)  # synchronous: admin use, few URLs
    return {"ok": True, "unfurled": len(urls)}


@app.post("/admin/annotations/{annotation_id}/hide")
def hide_annotation(annotation_id: int, h: HideIn):
    """Hide an annotation from all public surfaces (moderation / QA cleanup)."""
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or not hmac.compare_digest(h.admin_token, expected):
        raise HTTPException(403, "forbidden")
    con = db()
    cur = con.execute("UPDATE annotations SET hidden=1 WHERE id=?", (annotation_id,))
    con.commit()
    con.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "annotation not found")
    return {"ok": True}


# ---------- routes ----------
@app.get("/health")
def health():
    return {"ok": True, "service": "annotated-api", "version": "0.4.0"}


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
    # receipt links: validate + store, unfurl titles in background
    good_urls = []
    for u in (a.sources or [])[:5]:
        u = (u or "").strip()
        if not u:
            continue
        try:
            _public_url_ok(u)
        except ValueError:
            continue
        good_urls.append(u)
    for u in good_urls:
        domain = urllib.parse.urlparse(u).hostname or ""
        con.execute(
            "INSERT INTO annotation_sources (annotation_id, url, title, domain, created_at)"
            " VALUES (?, ?, '', ?, ?)",
            (new_id, u, domain, now_iso()),
        )
    con.commit()
    con.close()
    if good_urls:
        threading.Thread(target=_unfurl_worker, args=(new_id, good_urls), daemon=True).start()
    return {"ok": True, "id": new_id}


@app.get("/annotations")
def get_annotations(url: str = Query(min_length=1, max_length=2000)):
    con = db()
    rows = con.execute(
        "SELECT id, url, quote, prefix, suffix, stance, tag, comment, handle, created_at"
        " FROM annotations WHERE url = ? AND COALESCE(hidden,0)=0 ORDER BY created_at DESC LIMIT 500",
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
    smap = get_sources(con, ids)
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        d["replies"] = replies.get(r["id"], [])
        d["sources"] = smap.get(r["id"], [])
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
    count = con.execute("SELECT COUNT(*) c FROM annotations WHERE handle = ? AND COALESCE(hidden,0)=0", (handle,)).fetchone()["c"]
    followers = con.execute("SELECT COUNT(*) c FROM follows WHERE followee = ?", (handle,)).fetchone()["c"]
    following = con.execute("SELECT COUNT(*) c FROM follows WHERE follower = ?", (handle,)).fetchone()["c"]
    recent = con.execute(
        "SELECT quote, stance, comment, url, created_at FROM annotations WHERE handle = ? AND COALESCE(hidden,0)=0"
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
        " SUM(CASE WHEN stance='dispute' THEN 1 ELSE 0 END) disputes,"
        " SUM(CASE WHEN stance='agree' THEN 1 ELSE 0 END) agrees,"
        " SUM(CASE WHEN stance='context' THEN 1 ELSE 0 END) contexts"
        " FROM annotations WHERE COALESCE(hidden,0)=0 GROUP BY quote, url ORDER BY c DESC LIMIT 50"
    ).fetchall()
    con.close()
    items = "".join(
        f"<div class='t'><div class='q'>&ldquo;{escape(r['quote'][:220])}&rdquo;</div>"
        f"<div style='margin:6px 0'>{meter_html({'dispute': r['disputes'] or 0, 'agree': r['agrees'] or 0, 'context': r['contexts'] or 0, 'total': r['c']}, small=True)}</div>"
        f"<div class='m'>{r['c']} annotations · "
        f"<a href='{escape(r['url'])}'>{escape(r['url'][:60])}</a></div></div>"
        for r in rows
    ) or "<p>No annotations yet. Be the first to dispute something.</p>"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Trending disputes — Annotated</title>"
        f"<style>{SITE_CSS}</style></head><body><div class='wrap'>"
        "<p><a href='/'>⚑ Annotated</a></p><h1>⚑ Trending disputes</h1>" + items + "</div></body></html>"
    )


@app.get("/stats")
def stats():
    con = db()
    n_ann = con.execute("SELECT COUNT(*) c FROM annotations WHERE COALESCE(hidden,0)=0").fetchone()["c"]
    n_users = con.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
    con.close()
    return {"annotations": n_ann, "handles": n_users}


@app.get("/api/consensus")
def api_consensus(url: str = Query(min_length=1, max_length=2000)):
    """Stance counts for a URL — powers the consensus meter."""
    con = db()
    counts = consensus_counts(con, url)
    con.close()
    return {"url": url, **counts}


SITE_CSS = """
body{font:15px/1.6 system-ui,-apple-system,sans-serif;margin:0;color:#111;background:#fff}
.wrap{max-width:760px;margin:0 auto;padding:24px}
.hero{background:#111;color:#fff;padding:64px 24px;text-align:center}
.hero h1{font-size:40px;margin:0 0 8px;letter-spacing:-1px}
.hero h1 .y{color:#ffd640}
.hero p{font-size:18px;color:#bbb;max-width:560px;margin:12px auto}
.cta{display:inline-block;background:#ffd640;color:#111;font-weight:800;font-size:16px;
  border-radius:12px;padding:14px 28px;margin:10px 6px 0;text-decoration:none}
.cta.ghost{background:transparent;color:#ffd640;border:2px solid #ffd640}
.steps{display:flex;gap:16px;margin:36px 0;flex-wrap:wrap}
.step{flex:1;min-width:200px;border:1px solid #e5e5e5;border-radius:14px;padding:18px}
.step .n{font-size:26px}
.step h3{margin:8px 0 4px;font-size:16px}
.step p{font-size:14px;color:#555;margin:0}
.t{border:1px solid #e5e5e5;border-radius:12px;padding:14px 16px;margin:12px 0}
.q{font-style:italic}
.m{font-size:13px;color:#666;margin-top:6px}
.k{font-size:12px;color:#999;text-transform:uppercase}
a{color:#2f7fd0}
.share{display:inline-block;margin-top:8px;font-size:13px;font-weight:700;color:#111;
  background:#ffd640;border-radius:8px;padding:6px 12px;text-decoration:none}
.receipt{display:block;border:1px solid #e5e5e5;border-radius:8px;padding:8px 10px;
  margin:6px 0;font-size:13px;text-decoration:none;color:#111;background:#fafafa}
.receipt:hover{background:#f0f0f0}
.receipt .d{color:#888;font-size:12px}
footer{border-top:1px solid #eee;margin-top:48px;padding:24px;text-align:center;
  font-size:13px;color:#888}
h2.sec{font-size:22px;margin:40px 0 4px}
"""


@app.get("/", response_class=HTMLResponse)
def homepage():
    con = db()
    n_ann = con.execute("SELECT COUNT(*) c FROM annotations WHERE COALESCE(hidden,0)=0").fetchone()["c"]
    n_clips = con.execute(
        "SELECT COUNT(*) c FROM clips WHERE status='ready' AND COALESCE(hidden,0)=0"
    ).fetchone()["c"]
    n_users = con.execute("SELECT COUNT(*) c FROM profiles").fetchone()["c"]
    hot = con.execute(
        "SELECT quote, url, COUNT(*) c FROM annotations WHERE COALESCE(hidden,0)=0 GROUP BY quote, url"
        " ORDER BY c DESC LIMIT 3"
    ).fetchall()
    con.close()
    hot_html = "".join(
        f"<div class='t'><div class='q'>&ldquo;{escape(r['quote'][:160])}&rdquo;</div>"
        f"<div class='m'>{r['c']} annotations · "
        f"<a href='{escape(r['url'])}'>{escape(r['url'][:50])}</a></div></div>"
        for r in hot
    ) or "<p style='color:#888'>No disputes yet — install the extension and fire the first shot.</p>"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Annotated — dispute it, clip it, prove it</title>"
        f"<style>{SITE_CSS}</style></head><body>"
        "<div class='hero'><h1>⚑ <span class='y'>Annotated</span></h1>"
        "<p style='font-size:22px;color:#ffd640;font-weight:700;margin:0'>Dispute it. Clip it. Prove it — with receipts.</p>"
        "<p>Comment sections are sewers. Quotes get ripped out of context and nobody can tell what's true. "
        "Annotate any sentence on the web, clip any video moment, and settle it with receipts.</p>"
        "<a class='cta' href='/install'>Get the Chrome extension</a>"
        "<a class='cta ghost' href='/feed'>Browse the public feed</a>"
        f"<p style='font-size:14px;margin-top:18px'>{n_ann} annotations · {n_clips} clips · {n_users} handles</p></div>"
        "<div class='wrap'>"
        "<div class='steps'>"
        "<div class='step'><div class='n'>⚑</div><h3>Dispute any sentence</h3>"
        "<p>Select text on any article. Pick a stance — dispute, agree, context — tag the discourse, post.</p></div>"
        "<div class='step'><div class='n'>🎬</div><h3>Clip any moment</h3>"
        "<p>Grab up to 90 seconds of any video. The thread opens with the source link and the player.</p></div>"
        "<div class='step'><div class='n'>🧾</div><h3>Bring receipts</h3>"
        "<p>Attach source links to every annotation. The crowd's stance meter shows who's winning.</p></div>"
        "</div>"
        "<h2 class='sec'>Hottest disputes right now</h2>" + hot_html +
        "<h2 class='sec'>Watch the demo</h2>"
        "<video src='/demo/annotated-demo.mp4' controls style='width:100%;border-radius:12px;background:#111'></video>"
        "<h2 class='sec'>How it works</h2>"
        "<p>1. Install the extension. 2. Select a sentence — or hit <b>⚑ Clip 90s</b> on any video. "
        "3. Your annotation lands on a public permalink with a stance meter, receipts, and replies. "
        "4. Share it to X and let the courtroom decide.</p>"
        "<footer>Annotated — the internet's courtroom for quotes. "
        "<a href='/feed'>Feed</a> · <a href='/trending'>Trending</a> · "
        "<a href='https://github.com/sentientbias/annotated'>GitHub</a></footer>"
        "</div></body></html>"
    )


@app.get("/install", response_class=HTMLResponse)
def install_page():
    zip_url = "https://github.com/sentientbias/annotated/archive/refs/heads/master.zip"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Install the Annotated extension</title>"
        "<meta property='og:title' content='Install Annotated — dispute it, clip it, prove it'>"
        "<meta property='og:description' content='Free Chrome extension: annotate any sentence, clip any video moment, bring receipts.'>"
        "<meta name='twitter:card' content='summary'>"
        f"<style>{SITE_CSS}"
        ".stepn{display:inline-block;background:#ffd640;font-weight:800;border-radius:50%;"
        "width:28px;height:28px;line-height:28px;text-align:center;margin-right:8px}"
        "code{background:#f4f4f4;padding:2px 6px;border-radius:6px;font-size:14px}</style>"
        "</head><body><div class='wrap'>"
        "<p><a href='/'>⚑ Annotated</a></p>"
        "<h1>Install the extension <span style='font-size:14px;color:#888'>(60 seconds)</span></h1>"
        "<p>The extension isn't on the Chrome Web Store yet, so you sideload it once — "
        "then it updates itself from the repo.</p>"
        "<div class='t'><span class='stepn'>1</span><b>Download the code</b><br>"
        f"<a class='cta' style='font-size:14px;padding:10px 20px' href='{zip_url}'>⬇ Download annotated.zip</a>"
        "<p class='m'>Unzip it anywhere. Inside you'll find an <code>extension</code> folder — that's the part Chrome needs.</p></div>"
        "<div class='t'><span class='stepn'>2</span><b>Open Chrome's extension page</b><br>"
        "<p class='m'>Type <code>chrome://extensions</code> in the address bar and hit Enter. "
        "Flip <b>Developer mode</b> on (top-right corner).</p></div>"
        "<div class='t'><span class='stepn'>3</span><b>Load it</b><br>"
        "<p class='m'>Click <b>Load unpacked</b> and select the <code>extension</code> folder. "
        "That's it — no build step, no signup.</p></div>"
        "<div class='t'><span class='stepn'>4</span><b>Pin it & check the server</b><br>"
        "<p class='m'>Pin ⚑ Annotated to your toolbar. Click it — the API server should already read "
        "<code>https://annotated-api.onrender.com</code>. If not, paste it in and hit Save.</p></div>"
        "<div class='t'><span class='stepn'>5</span><b>Dispute something</b><br>"
        "<p class='m'>Open any article, select a sentence, hit <b>⚑ Dispute this</b>, pick a stance, "
        "attach a receipt link, post. On YouTube, hit <b>⚑ Clip 90s</b>.</p></div>"
        "<p><a class='cta ghost' style='color:#111;border-color:#111' href='/feed'>See what others posted →</a></p>"
        "<footer>Annotated — the internet's courtroom for quotes. "
        "<a href='/feed'>Feed</a> · <a href='/trending'>Trending</a> · "
        "<a href='https://github.com/sentientbias/annotated'>GitHub</a></footer>"
        "</div></body></html>"
    )


@app.get("/demo-article", response_class=HTMLResponse)
def demo_article():
    """A self-contained demo article so anyone can try the extension instantly —
    no paywall, no heavy trackers. Same-origin, so the extension just works."""
    body = """
    <p class='m'>Demo article · written for trying the Annotated extension — select any sentence to dispute it.</p>
    <h1>AI coding agent startup Factory triples valuation to $5 billion in latest funding round</h1>
    <p class='m'>By Annotated Demo Desk · September 2026 · 3 min read</p>
    <p>Factory, the startup building AI agents that work across the software development lifecycle, has raised $200 million in fresh funding at a $5 billion valuation — roughly triple its previous valuation. The round marks one of the largest AI coding investments of the year and signals that investor appetite for developer tools has not cooled.</p>
    <p>The company says its agents don't just autocomplete code but shepherd tasks across the full lifecycle: planning, writing, testing, and deploying software. Factory claims teams using its platform ship features twice as fast as those relying on conventional coding assistants.</p>
    <p>AI-assisted coding has emerged as one of the most widely adopted uses of generative AI, drawing billions of dollars from investors as businesses seek to speed up software development and improve workforce productivity. Analysts estimate the AI coding tools market will be worth tens of billions within five years, though skeptics note that most enterprise pilots have yet to show durable returns.</p>
    <p>Not everyone is convinced the valuations are justified. Critics argue that coding agents remain unreliable on large codebases and that the current funding frenzy mirrors the chatbot hype cycle of 2023. Factory's backers counter that agentic workflows are already displacing entire categories of software work.</p>
    <p>The round was reported by multiple outlets this week. Whether Factory can turn a $5 billion price tag into lasting enterprise adoption is the question the next eighteen months will answer.</p>
    <div class='t'><b>Try it:</b> select the sentence <i>"ship features twice as fast"</i> above, hit <b>⚑ Dispute this</b>, pick a stance, and attach a receipt link. Your annotation posts to the public feed.</div>
    """
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Factory triples valuation to $5 billion — demo article | Annotated</title>"
        "<meta property='og:title' content='Factory triples valuation to $5 billion (demo article)'>"
        "<meta property='og:description' content='A demo article for trying the Annotated Chrome extension. Select any sentence to dispute it.'>"
        "<meta name='twitter:card' content='summary'>"
        f"<style>{SITE_CSS}"
        ".article{max-width:680px;margin:0 auto}"
        ".article p{font-size:18px;line-height:1.75;margin:0 0 1.2em}"
        ".article h1{font-size:34px;line-height:1.2}"
        "</style></head><body><div class='wrap'><div class='article'>"
        "<p><a href='/'>⚑ Annotated</a> · <a href='/install'>Get the extension</a></p>"
        f"{body}"
        "<footer>Demo article for the Annotated extension. <a href='/feed'>Feed</a> · "
        "<a href='/install'>Install</a></footer>"
        "</div></div></body></html>"
    )


@app.get("/a/{annotation_id}", response_class=HTMLResponse)
def annotation_page(annotation_id: int):
    """Public permalink for one annotation: quote, stance meter, receipts, replies, share."""
    con = db()
    r = con.execute(
        "SELECT id, url, quote, stance, tag, comment, handle, created_at FROM annotations WHERE id=? AND COALESCE(hidden,0)=0",
        (annotation_id,),
    ).fetchone()
    if not r:
        con.close()
        return HTMLResponse("<h2>Annotation not found</h2>", status_code=404)
    counts = consensus_counts(con, r["url"])
    smap = get_sources(con, [r["id"]])
    replies = con.execute(
        "SELECT handle, text, created_at FROM annotation_replies WHERE annotation_id=? ORDER BY created_at",
        (r["id"],),
    ).fetchall()
    con.close()
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    page_url = f"{base}/a/{r['id']}"
    color = STANCE_COLORS.get(r["stance"], "#111")
    tag_badge = f" · 🏷 {escape(r['tag'].replace('_', ' '))}" if r["tag"] else ""
    srcs = smap.get(r["id"], [])
    receipts = "".join(
        f"<a class='receipt' href='{escape(s['url'])}' target=_blank rel=noopener>"
        f"🧾 {escape(s['title'] or s['url'][:80])}<br><span class='d'>{escape(s['domain'])}</span></a>"
        for s in srcs
    )
    replies_html = "".join(
        f"<div class='t'><div class='m'><b>@{escape(x['handle'])}</b> · {escape(x['created_at'][:16].replace('T',' '))}</div>"
        f"<div>{escape(x['text'])}</div></div>"
        for x in replies
    ) or "<p style='color:#888'>No replies yet.</p>"
    share_text = f"I {r['stance']}d this on Annotated ⚑"
    share = x_share_url(share_text, page_url)
    og_title = f"⚑ {r['stance'].upper()} on Annotated"
    og_desc = escape(f"\u201c{r['quote'][:160]}\u201d — @{r['handle']}: {r['comment'][:160]}").replace('"', "'")
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>⚑ {escape(r['stance'])} — Annotated</title>"
        f"<meta property='og:title' content=\"{escape(og_title)}\">"
        f"<meta property='og:description' content=\"{og_desc}\">"
        f"<meta property='og:type' content='article'>"
        f"<meta property='og:url' content=\"{page_url}\">"
        "<meta name='twitter:card' content='summary'>"
        f"<style>{SITE_CSS}</style></head><body><div class='wrap'>"
        "<p><a href='/'>⚑ Annotated</a> · <a href='/feed'>Feed</a></p>"
        f"<div class='t'><div class='k' style='color:{color};font-weight:800'>⚑ {escape(r['stance'].upper())}{tag_badge}</div>"
        f"<div class='q' style='font-size:18px'>&ldquo;{escape(r['quote'])}&rdquo;</div>"
        f"<div class='m'>on <a href='{escape(r['url'])}'>{escape(r['url'][:70])}</a></div>"
        f"<div style='margin-top:10px'><b>Consensus</b><br>{meter_html(counts)}</div></div>"
        f"<div class='t'><div class='m'><b>@{escape(r['handle'])}</b> · {escape(r['created_at'][:16].replace('T',' '))}</div>"
        f"<p>{escape(r['comment'])}</p>"
        + (f"<h4>🧾 Receipts ({len(srcs)})</h4>" + receipts if srcs else "") +
        f"<a class='share' href='{share}' target=_blank rel=noopener>𝕏 Share this dispute</a></div>"
        f"<h2 class='sec'>Replies ({len(replies)})</h2>" + replies_html +
        "</div></body></html>"
    )


@app.get("/api/feed")
def api_feed(limit: int = Query(default=50, le=200)):
    """Newest annotations + clips, one combined public feed."""
    con = db()
    anns = con.execute(
        "SELECT 'annotation' kind, id, quote, stance, tag, comment, handle, url, created_at"
        " FROM annotations WHERE COALESCE(hidden,0)=0 ORDER BY created_at DESC LIMIT ?",
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
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    # stance counts per URL for the consensus meters (one query)
    urls = list({it["url"] for it in items if it["kind"] == "annotation" and it.get("url")})
    meters: dict[str, dict] = {}
    if urls:
        con = db()
        for r in con.execute(
            "SELECT url, stance, COUNT(*) c FROM annotations"
            f" WHERE COALESCE(hidden,0)=0 AND url IN ({','.join('?' * len(urls))}) GROUP BY url, stance",
            urls,
        ).fetchall():
            m = meters.setdefault(r["url"], {"dispute": 0, "agree": 0, "context": 0, "total": 0})
            if r["stance"] in m:
                m[r["stance"]] = r["c"]
                m["total"] += r["c"]
        # receipt counts per annotation
        ann_ids = [it["id"] for it in items if it["kind"] == "annotation"]
        rcounts: dict[int, int] = {}
        if ann_ids:
            for r in con.execute(
                "SELECT annotation_id, COUNT(*) c FROM annotation_sources"
                f" WHERE annotation_id IN ({','.join('?' * len(ann_ids))}) GROUP BY annotation_id",
                ann_ids,
            ).fetchall():
                rcounts[r["annotation_id"]] = r["c"]
        con.close()
    else:
        rcounts = {}
    cards = ""
    for it in items:
        if it["kind"] == "clip":
            badge = "🎬 clip" if it.get("source_type") == "youtube" else "🎙 clip"
            share = x_share_url(f"Watch this clip on Annotated 🎬", it["clip_url"])
            cards += (
                f"<div class='t'><div class='k'>{badge} · {it.get('status')}</div>"
                f"<div class='q'>{escape((it.get('comment') or '')[:220]) or '(no comment)'}</div>"
                f"<div class='m'>@{escape(it['handle'])} · "
                f"<a href='{it['clip_url']}'>open clip</a> · "
                f"<a href='{escape(it['url'])}'>source</a> · "
                f"<a href='{share}' target=_blank rel=noopener>𝕏 share</a></div></div>"
            )
        else:
            tag_badge = f" · 🏷 {escape(it['tag'].replace('_', ' '))}" if it.get("tag") else ""
            m = meters.get(it["url"], {"dispute": 0, "agree": 0, "context": 0, "total": 0})
            rc = rcounts.get(it["id"], 0)
            rc_badge = f" · 🧾 {rc} receipt{'s' if rc != 1 else ''}" if rc else ""
            page_url = f"{base}/a/{it['id']}"
            share = x_share_url(f"I {it['stance']}d this on Annotated ⚑", page_url)
            cards += (
                f"<div class='t'><div class='k'>⚑ {escape(it['stance'])}{tag_badge}{rc_badge}</div>"
                f"<div class='q'>&ldquo;{escape(it['quote'][:220])}&rdquo;</div>"
                f"<div style='margin:6px 0'>{meter_html(m, small=True)}</div>"
                f"<div class='m'>@{escape(it['handle'])} · {escape(it.get('comment','')[:120])} · "
                f"<a href='{escape(it['url'])}'>{escape(it['url'][:60])}</a><br>"
                f"<a href='{page_url}'>permalink</a> · "
                f"<a href='{share}' target=_blank rel=noopener>𝕏 share</a></div></div>"
            )
    cards = cards or "<p>Nothing yet. Clip a video or dispute a sentence.</p>"
    return HTMLResponse(
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Feed — Annotated</title>"
        f"<style>{SITE_CSS}</style>"
        "</head><body><div class='wrap'><p><a href='/'>⚑ Annotated</a></p>"
        "<h1>Public feed</h1>" + cards + "</div></body></html>"
    )

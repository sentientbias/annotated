"""Video/podcast clipping for Annotated.

POST /clips {source_url, source_type, start_sec, duration_sec, handle, comment}
  -> spawns a background worker that cuts a <=90s clip and downscales video
     to 240p, then marks the clip ready.

Clip pages at /c/{id} show the player, source link, a "File a claim" button,
and text + audio comments.
"""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

router = APIRouter()

DATA_DIR = os.environ.get("DATA_DIR", "/data")
CLIP_DIR = os.path.join(DATA_DIR, "clips")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
os.makedirs(CLIP_DIR, exist_ok=True)
os.makedirs(AUDIO_DIR, exist_ok=True)

MAX_CLIP_SEC = 90

db: sqlite3.Connection
_lock = threading.Lock()


def init(conn: sqlite3.Connection):
    global db
    db = conn


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _proxy() -> str:
    """HTTP(S) proxy URL from ANNOTATED_PROXY_URL, or '' when unset.

    Format: http://user:pass@host:port. Routes yt-dlp and the Cobalt API
    around datacenter-IP blocks; ignored when empty.
    """
    return os.environ.get("ANNOTATED_PROXY_URL", "").strip()


def _run(cmd, timeout=600):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _duration_ok(path: str, want: float, tol: float = 5.0) -> bool:
    """True if the media at path is no longer than want+tol seconds."""
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", path], timeout=60)
    try:
        return float((r.stdout or "").strip()) <= want + tol
    except ValueError:
        return False


def _cobalt_fetch(source_url: str, tmpdir: str):
    """Resolve a direct media URL via the cobalt API and fetch it.

    Returns (local_path, error). Empty error means success.

    The full source file is downloaded here (cobalt gives no section
    cutting), so the caller MUST apply -ss/-t in ffmpeg. The download is
    hard-capped with --max-filesize to protect the 1GB persistent disk,
    and the tmpdir is removed by the caller when done.
    """
    import json as _json
    import urllib.request
    api = os.environ.get("COBALT_API_URL", "https://api.cobalt.tools/")
    proxy = _proxy()
    try:
        req = urllib.request.Request(
            api,
            data=_json.dumps({
                "url": source_url,
                "videoQuality": "480",
                "youtubeVideoCodec": "h264",
                "downloadMode": "auto",
            }).encode(),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            resp_cm = opener.open(req, timeout=60)
        else:
            resp_cm = urllib.request.urlopen(req, timeout=60)
        with resp_cm as resp:
            data = _json.load(resp)
    except Exception as e:
        return None, f"cobalt api unreachable: {str(e)[:120]}"
    if data.get("status") not in ("tunnel", "redirect", "local-processing"):
        return None, f"cobalt {data.get('status')}: {str(data.get('error') or data.get('text'))[:120]}"
    media_url = data.get("url")
    if not media_url or not media_url.startswith("https://"):
        return None, "cobalt: no media url"
    out = os.path.join(tmpdir, "src.mp4")
    # Bound the download: never fill the persistent disk with a full source.
    curl = ["curl", "-sL", "--max-time", "300", "--max-filesize", "500M"]
    if proxy:
        curl += ["--proxy", proxy]
    curl += ["-o", out, media_url]
    r = _run(curl, timeout=330)
    if r.returncode == 63:
        return None, "cobalt: source exceeds 500MB cap"
    if r.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) < 1024:
        return None, "cobalt: media fetch failed"
    return out, ""


def _process_clip(clip_id: str):
    with _lock:
        row = db.execute(
            "SELECT source_url, source_type, start_sec, duration_sec FROM clips WHERE id=?",
            (clip_id,),
        ).fetchone()
    if not row:
        return
    source_url, source_type, start_sec, duration_sec = row
    start, dur = max(0.0, float(start_sec or 0)), min(MAX_CLIP_SEC, float(duration_sec or 30))
    end = start + dur

    def fail(msg: str):
        with _lock:
            db.execute("UPDATE clips SET status='failed', error=? WHERE id=?", (msg[:500], clip_id))
            db.commit()

    try:
        if source_type == "youtube":
            tmp = tempfile.mkdtemp()
            proxy = _proxy()
            try:
                section = f"*{_ts(start)}-{_ts(end)}"
                # Strategy 1: yt-dlp, cycling player clients (YouTube bot-walls
                # datacenter IPs on the default web client).
                dl_ok, dl_err = False, ""
                for client in ("android", "web_embedded", "tv", "default,-web", "default"):
                    ytdl = [
                        "yt-dlp", "--download-sections", section,
                        "--extractor-args", f"youtube:player_client={client}",
                        "-f", "bv*[height<=480]+ba/b[height<=480]/b",
                        "--merge-output-format", "mp4",
                        "-o", os.path.join(tmp, "src.%(ext)s"),
                        "--no-playlist", source_url,
                    ]
                    if proxy:
                        ytdl[1:1] = ["--proxy", proxy]
                    r = _run(ytdl)
                    if r.returncode == 0:
                        dl_ok = True
                        break
                    dl_err = (r.stderr or r.stdout)[-300:]
                src = None
                precut = False  # True only if yt-dlp --download-sections already cut it
                if dl_ok:
                    for f in os.listdir(tmp):
                        if f.startswith("src."):
                            src = os.path.join(tmp, f)
                            precut = True
                            break
                # Strategy 2: cobalt API fallback (resolves a direct file URL).
                # Cobalt returns the FULL source, so start/duration MUST be
                # applied in the ffmpeg step below (precut stays False).
                if not src:
                    src, cobalt_err = _cobalt_fetch(source_url, tmp)
                    if not src:
                        return fail("download failed: " + (dl_err or "")[-200:] + " | " + cobalt_err)
                out = os.path.join(CLIP_DIR, f"{clip_id}.mp4")
                ff = ["ffmpeg", "-y"]
                if not precut:
                    # Input seek + duration cap on the full source. Re-encode
                    # after makes the output duration exact.
                    ff += ["-ss", str(start), "-t", str(dur)]
                ff += ["-i", src,
                       "-vf", "scale=426:240",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
                       "-c:a", "aac", "-b:a", "96k",
                       out]
                r = _run(ff)
                if r.returncode != 0 or not os.path.exists(out):
                    return fail("transcode failed: " + ((r.stderr or r.stdout or "")[-300:]))
                # Guard: never publish a clip longer than requested (+5s tolerance).
                if not _duration_ok(out, dur):
                    try:
                        os.remove(out)
                    except OSError:
                        pass
                    return fail("clip duration exceeded requested length")
                with _lock:
                    db.execute(
                        "UPDATE clips SET status='ready', file_path=? WHERE id=?",
                        (f"{clip_id}.mp4", clip_id),
                    )
                    db.commit()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        elif source_type == "podcast":
            out = os.path.join(CLIP_DIR, f"{clip_id}.mp3")
            r = _run([
                "ffmpeg", "-y", "-ss", str(start), "-i", source_url,
                "-t", str(dur), "-c:a", "libmp3lame", "-b:a", "96k", out,
            ])
            if r.returncode != 0 or not os.path.exists(out):
                return fail("audio cut failed")
            with _lock:
                db.execute(
                    "UPDATE clips SET status='ready', file_path=? WHERE id=?",
                    (f"{clip_id}.mp3", clip_id),
                )
                db.commit()
        else:
            return fail("unknown source_type")
    except Exception as e:
        fail(str(e)[:300])


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


@router.post("/clips")
async def create_clip(req: Request):
    body = await req.json()
    source_url = (body.get("source_url") or "").strip()
    source_type = (body.get("source_type") or "").strip()
    handle = (body.get("handle") or "anon").strip()[:32] or "anon"
    comment = (body.get("comment") or "").strip()[:2000]
    try:
        start_sec = max(0.0, float(body.get("start_sec") or 0))
        duration_sec = min(MAX_CLIP_SEC, max(1.0, float(body.get("duration_sec") or 30)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "bad timestamps"}, status_code=400)
    if not source_url or source_type not in ("youtube", "podcast"):
        return JSONResponse({"error": "source_url and source_type (youtube|podcast) required"},
                            status_code=400)
    clip_id = uuid.uuid4().hex[:12]
    with _lock:
        db.execute(
            "INSERT INTO clips (id, source_url, source_type, start_sec, duration_sec,"
            " status, handle, comment, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (clip_id, source_url, source_type, start_sec, duration_sec,
             "processing", handle, comment, _now()),
        )
        db.commit()
    threading.Thread(target=_process_clip, args=(clip_id,), daemon=True).start()
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    return {"clip_id": clip_id, "status": "processing", "clip_url": f"{base}/c/{clip_id}"}


@router.get("/clips/{clip_id}")
def get_clip(clip_id: str):
    row = db.execute(
        "SELECT id, source_url, source_type, start_sec, duration_sec, status,"
        " file_path, error, handle, comment, created_at FROM clips WHERE id=?",
        (clip_id,),
    ).fetchone()
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    keys = ["id", "source_url", "source_type", "start_sec", "duration_sec",
            "status", "file_path", "error", "handle", "comment", "created_at"]
    data = dict(zip(keys, row))
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    if data["status"] == "ready" and data["file_path"]:
        data["media_url"] = f"{base}/media/{data['file_path']}"
        # aliases for extension clients
        data["video_url"] = data["media_url"]
        data["file_url"] = data["media_url"]
        data["playback_url"] = data["media_url"]
    data["clip_url"] = f"{base}/c/{clip_id}"
    data["comments"] = _comments(clip_id, base)
    return data


def _comments(clip_id: str, base: str):
    rows = db.execute(
        "SELECT handle, text, audio_url, created_at FROM clip_comments"
        " WHERE clip_id=? ORDER BY created_at", (clip_id,)
    ).fetchall()
    out = []
    for handle, text, audio_url, created_at in rows:
        c = {"handle": handle, "text": text, "created_at": created_at}
        if audio_url:
            c["audio_url"] = f"{base}{audio_url}"
        out.append(c)
    return out


@router.post("/clips/{clip_id}/comments")
async def add_comment(clip_id: str, req: Request):
    exists = db.execute("SELECT 1 FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    body = await req.json()
    handle = (body.get("handle") or "anon").strip()[:32] or "anon"
    text = (body.get("text") or "").strip()[:2000]
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    with _lock:
        db.execute(
            "INSERT INTO clip_comments (id, clip_id, handle, text, created_at)"
            " VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex[:12], clip_id, handle, text, _now()),
        )
        db.commit()
    return {"ok": True}


@router.post("/clips/{clip_id}/audio")
async def add_audio_comment(clip_id: str, handle: str = Form("anon"),
                            file: UploadFile = File(...)):
    exists = db.execute("SELECT 1 FROM clips WHERE id=?", (clip_id,)).fetchone()
    if not exists:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        return JSONResponse({"error": "audio too large (10MB max)"}, status_code=400)
    name = f"{clip_id}_{uuid.uuid4().hex[:8]}.webm"
    with open(os.path.join(AUDIO_DIR, name), "wb") as f:
        f.write(data)
    with _lock:
        db.execute(
            "INSERT INTO clip_comments (id, clip_id, handle, text, audio_url, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], clip_id, (handle or "anon")[:32],
             "", f"/media/{name}", _now()),
        )
        db.commit()
    return {"ok": True, "audio_url": f"/media/{name}"}


@router.get("/media/{name}")
def serve_media(name: str):
    safe = os.path.basename(name)
    for d in (CLIP_DIR, AUDIO_DIR):
        p = os.path.join(d, safe)
        if os.path.exists(p):
            return FileResponse(p)
    return JSONResponse({"error": "not found"}, status_code=404)


@router.post("/claims")
async def file_claim(req: Request):
    body = await req.json()
    target_type = (body.get("target_type") or "").strip()[:16]
    target_id = (body.get("target_id") or "").strip()[:64]
    reason = (body.get("reason") or "").strip()[:2000]
    contact = (body.get("contact") or "").strip()[:128]
    if not target_id or not reason:
        return JSONResponse({"error": "target_id and reason required"}, status_code=400)
    with _lock:
        db.execute(
            "INSERT INTO claims (id, target_type, target_id, reason, contact, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], target_type, target_id, reason, contact, _now()),
        )
        db.commit()
    return {"ok": True, "message": "Claim received. We review fair-use claims within 5 business days."}


@router.get("/c/{clip_id}", response_class=HTMLResponse)
def clip_page(clip_id: str):
    row = db.execute(
        "SELECT source_url, source_type, start_sec, duration_sec, status,"
        " file_path, handle, comment, created_at FROM clips WHERE id=?",
        (clip_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("<h2>Clip not found</h2>", status_code=404)
    source_url, source_type, start_sec, duration_sec, status, file_path, handle, comment, created_at = row
    base = os.environ.get("PUBLIC_BASE_URL", "https://annotated-api.onrender.com")
    if status == "ready" and file_path:
        media = (f'<video src="/media/{file_path}" controls style="width:100%;border-radius:12px"></video>'
                 if file_path.endswith(".mp4") else
                 f'<audio src="/media/{file_path}" controls style="width:100%"></audio>')
    elif status == "processing":
        media = "<p>Your clip is being cut — refresh in a few seconds.</p>"
    else:
        media = "<p>Clip processing failed. The source may block downloads.</p>"
    comments = _comments(clip_id, base)
    comments_html = "".join(
        f'<div class="c"><b>@{c["handle"]}</b> <span>{c["created_at"]}</span>'
        + (f'<p>{c["text"]}</p>' if c.get("text") else "")
        + (f'<audio src="{c["audio_url"]}" controls></audio>' if c.get("audio_url") else "")
        + "</div>"
        for c in comments
    )
    return HTMLResponse(f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Annotated clip — @{handle}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;padding:20px}}
.c{{border-top:1px solid #eee;padding:10px 0}}.c span{{color:#888;font-size:12px}}
textarea{{width:100%;height:70px}}button{{padding:8px 16px;margin-top:8px;cursor:pointer}}
.top{{display:flex;justify-content:space-between;align-items:center}}
.claim{{border:1px solid #f59e0b;background:#fffbeb;padding:10px;border-radius:8px;margin:16px 0}}
</style></head><body>
<div class=top><h2>Annotated</h2><a href="/feed">Public feed</a></div>
{media}
<p>Clipped by <b>@{handle}</b> · {duration_sec:g}s from {start_sec:g}s ·
<a href="{source_url}" target=_blank rel=noopener>View original source</a></p>
{f"<p>{comment}</p>" if comment else ""}
<div class=claim><b>Fair use?</b> If this clip misuses your content,
<button onclick="fileClaim()">File a claim</button></div>
<h3>Discussion ({len(comments)})</h3>
<div id=comments>{comments_html}</div>
<h4>Add a comment</h4>
<textarea id=ctext placeholder="Add context, a dispute, or a source..."></textarea><br>
<input id=chandle placeholder="handle (or sign in via the extension)">
<button onclick="postComment()">Post</button>
<script>
const ID="{clip_id}";
function postComment(){{
  fetch(`/clips/${{ID}}/comments`,{{method:"POST",
    headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{handle:document.getElementById('chandle').value||'anon',
      text:document.getElementById('ctext').value}})}})
  .then(r=>r.json()).then(d=>d.ok?location.reload():alert(d.error||'failed'));
}}
function fileClaim(){{
  const reason=prompt("Describe your claim (fair-use / takedown request):");
  if(!reason) return;
  fetch("/claims",{{method:"POST",headers:{{"Content-Type":"application/json"}},
    body:JSON.stringify({{target_type:"clip",target_id:ID,reason}})}})
  .then(r=>r.json()).then(d=>alert(d.message||d.error||'done'));
}}
</script></body></html>""")

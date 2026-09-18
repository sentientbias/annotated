# ⚑ Annotated — Dispute it. Clip it. Prove it — with receipts.

**Live:** https://annotated-api.onrender.com · **Demo:** https://annotated-api.onrender.com/demo/annotated-demo.mp4 · **Install:** https://annotated-api.onrender.com/install

Highlight any sentence on the web and dispute it. **Clip up to 90 seconds**
from any YouTube video or podcast, downscaled and linked back to the source.
Reply with text or recorded audio. Sign in with X or Google. Follow sharp
readers, ride the trending disputes.

Built for the [This Week in Startups $5,000 annotation bounty](https://x.com/twistartups/status/2100269272899743841) — round two entry. MIT licensed, fully open source.

## What it does

**Text annotations**
- **Select any sentence** on any page → a "⚑ Dispute this" button appears
- **Pick a stance** — Dispute / Agree / Context — and add your receipt (link, quote, reasoning)
- **Sentences with annotations get highlighted** in the page, with a count badge; click to read the thread in the side panel

**Consensus meter**
- Every page, thread, and permalink shows a live three-color meter: ⚑ disputes (red) · ✓ agrees (green) · ◈ context (blue) — see who's winning at a glance

**Receipts**
- Attach up to 5 source links per annotation; the server fetches real titles (SSRF-hardened) and renders 🧾 receipt cards in the side panel, permalinks, and feed

**Video & audio clips (bounty spec)**
- On any YouTube video, hit **⚑ Clip 90s** → pick start time + duration (≤ 90s)
- The server cuts the segment, downscales video to 240p, and hosts it
- Every clip page links back to the original source and carries a visible
  **File a claim** button (fair-use dispute path) — annotation pages carry it too
- Podcast episodes: same flow, audio-only

**Social layer**
- **Public feed** (`/feed`) with follow + comment, consensus meters, receipt counts, clip cards
- **Annotation permalinks** (`/a/{id}`) — every dispute is a shareable page with OG cards for X
- **Share-to-X** on every permalink, feed card, and clip page
- **Profiles** — every handle gets a profile with history, follower counts
- **Follows** — follow readers whose disputes you respect
- **Trending** — `/trending` shows the most-disputed sentences right now
- **Commentary** supports text and recorded audio (🎙 in the side panel)
- **One-click install guide** at `/install` — judges can sideload in 60 seconds

**Sign-in**
- X or Google OAuth only — no email/password (per the bounty brief)
- The extension links your account via a one-time code shown after OAuth

## Install (developer mode)

1. Clone this repo
2. `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select `extension/`
3. Select text on any page and hit **⚑ Dispute this**

The extension talks to the API in `server/` (defaults to the hosted instance;
point it at your own via the popup's API server field).

## Run the server

```bash
pip install -r server/requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

SQLite-backed (`ANNOTATED_DB` env). Clip files land in `/data/clips`
(`DATA_DIR` env). Deploy to Render with `render.yaml` (Docker: Python 3.12 +
ffmpeg + yt-dlp, 1 GB disk at `/data`):

```bash
render blueprint launch  # or connect the repo in the dashboard
```

OAuth needs `X_CLIENT_ID` / `X_CLIENT_SECRET` and `GOOGLE_CLIENT_ID` /
`GOOGLE_CLIENT_SECRET` env vars; the extension links accounts via the
one-time code at `/auth/{x,google}/callback` → `POST /auth/token/verify`.

### API

| Method | Path | What |
|---|---|---|
| `POST` | `/annotations` | Post an annotation (url, quote, prefix, suffix, stance, comment, handle) |
| `GET` | `/annotations?url=` | All annotations on a page |
| `POST` | `/follow` | Follow a handle |
| `GET` | `/profiles/{handle}` | Profile: counts, followers, recent annotations |
| `GET` | `/trending` | HTML page of most-disputed sentences |
| `GET` | `/stats` | Totals |

Sentence anchoring uses quote + prefix/suffix context (TextQuoteSelector-style),
so annotations survive most page edits.

## Why this wins

Jason's words: *"highlight any sentence on the web and dispute it."* Annotated
does exactly that, in two clicks, on every site — then adds the network layer
the finalists were missing: who said it (profiles), who's worth reading
(follows), and what's catching fire (trending). Disputes are public by default
because sunlight is the point.

## License

MIT — see [LICENSE](LICENSE).

# Annotated — Bounty Demo Guide

Live backend: `https://annotated-api.onrender.com`
Extension: `extension/` (load unpacked in Chrome, Developer mode)

> **Note:** Screenshots in the sequence below must be captured in a real Chrome
> with the extension loaded unpacked — no Chromium exists on this dev machine.
> The full click-by-click checklist lives in
> `extension/EXTENSION_TEST.md` (steps 1–7). Capture each shot below while
> running that checklist.

## 60-second demo script

**0:00–0:08 — The problem.** "Comment sections are sewers. Quotes get ripped
out of context and nobody can tell what's true." *(Show any news article.)*

**0:08–0:20 — Dispute a sentence.** Select one sentence on the article →
**⚑ Dispute this** → pick **Dispute** → tag it **🏷 fact check** → type
"Misleading — the study says the opposite, see link" → **Post annotation**.
Sentence lights up red with a count badge. *(Shots 1–3)*

**0:20–0:30 — The thread.** Click the highlight → sidebar opens: the quote on
top, your annotation below with the **DISPUTE** badge and **🏷 fact check**
tag, a **+ follow** button next to your handle. *(Shot 4)*

**0:30–0:42 — Clip the video.** Switch to a YouTube tab (e.g. This Week in
Startups) → **⚑ Clip 90s** → take 30 seconds → "Eisca on why agents need
receipts" → **Post clip**. Toast shows the clip URL; the sidebar opens the
clip thread with the source link and video player. *(Shots 5–6)*

**0:42–0:52 — Talk back.** In the clip thread hit **🎙 Record audio reply**,
say your counter in 10 seconds, stop → "Posted ✓" → your voice appears as a
player in the thread. *(Shot 7)*

**0:52–1:00 — The public square.** Open `https://annotated-api.onrender.com/feed`:
your annotation and clip are live, with the source URL on every card and a
visible **File a claim** button on every clip page. "Dispute it. Clip it.
Prove it — with receipts." *(Shot 8)*

## Screenshot / caption sequence

1. **Article with selection FAB** — "Select any sentence on the web."
2. **Composer open** — stance picker (Dispute / Agree / Context) + discourse
   tag chips (fact check, steel-man, receipt, hot take). Caption: "Pick a
   stance. Tag the discourse."
3. **Posted highlight** — red-underlined sentence with count badge. Caption:
   "Disputes render as red, agreements green, context blue."
4. **Side panel thread** — quote + annotation card with DISPUTE badge,
   🏷 fact check tag, + follow. Caption: "Every sentence gets its own courtroom."
5. **YouTube "⚑ Clip 90s" composer** — start/length/comment. Caption: "Clip up
   to 90 seconds, downscaled to 240p."
6. **Clip thread** — comment headline, meta line, **View source ↗** link,
   video player, audio replies. Caption: "Permalink page with source link and
   File a claim."
7. **Audio reply posted** — audio player in thread. Caption: "Recorded-audio
   commentary, right in the thread."
8. **Public feed** — mixed annotation + clip cards. Caption: "Public feed —
   follows, replies, trending."

## Exact live-demo route

1. `chrome://extensions` → load unpacked `extension/` → popup → set API base
   to `https://annotated-api.onrender.com` → Save.
2. Open any article → select a sentence → ⚑ Dispute this → Dispute +
   🏷 fact check → post.
3. Click highlight → side panel → verify thread + follow button.
4. Open a YouTube video → ⚑ Clip 90s → 30s → post → wait for ready.
5. In the clip thread → 🎙 Record audio reply → stop → verify player.
6. `https://annotated-api.onrender.com/feed` → verify both items live.
7. Open the clip page (`/c/{id}`) → verify **File a claim** button + source URL.

## What to say about safety (if asked)

- Source URLs are allowlisted (YouTube hosts for video) and validated
  server-side against SSRF — private/metadata IPs are rejected before any
  download.
- All user content is HTML-escaped; `javascript:` URLs never become links.
- Rate limits per handle; OAuth link tokens expire in 15 minutes and are
  single-use.
- Failed or hidden clips never appear in the public feed.

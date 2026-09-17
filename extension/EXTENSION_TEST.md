# Annotated — Extension Manual QA Checklist

Load unpacked in Chrome: `chrome://extensions` → Developer mode → **Load unpacked** → select this `extension/` directory. Point the API base at your server (popup → API server field) before testing.

## Legend

- ✅ **HEADLESS** — verified headless in this repo (curl against a local test server + `node --check` + manifest validation). No browser needed.
- 🖱️ **REAL BROWSER** — needs a real Chrome with the unpacked extension loaded. No chromium exists on this dev machine, so these were not smoke-tested here.

## Test steps

### 1. Dispute flow — select → composer → post → highlight
- **Action:** On any article page, select a sentence (20–600 chars) → click **⚑ Dispute this** → pick a stance → write a comment → Post annotation.
- **Expect:** Toast "Annotation posted ⚑"; the sentence gets a yellow highlight mark with a count badge.
- **Headless part (✅):** `POST /annotations` returns `{"ok":true,"id":N}` (201); `GET /annotations?url=` returns the row with `quote/stance/comment/handle/created_at`. Composer posts `url` via the same `normUrl()` the highlight query uses.
- **Browser part (🖱️):** FAB appears at selection; composer opens; highlight renders via `<mark class="annotated-hit" data-stance="…">` with per-stance underline color (red dispute / green agree / blue context).

### 2. Click highlight → side panel opens with the thread
- **Action:** Click a highlighted sentence.
- **Expect:** Side panel opens, quote at top, one card per annotation with stance badge, `@handle`, timestamp, comment, and a follow button.
- **Headless part (✅):** `background.js` `annotated:open` → `chrome.storage.session` stash → side panel reads it; field reads (`a.handle/a.stance/a.created_at/a.comment/a.quote`) all confirmed present in `GET /annotations` response.
- **Browser part (🖱️):** Actual panel open + render.

### 3. Follow button works
- **Action:** In the side panel thread, click **+ follow** next to another user's annotation.
- **Expect:** Button becomes "following ✓".
- **Headless part (✅):** `POST /follow {"follower","followee"}` → `{"ok":true,"follower","followee"}`; follow row persisted (verified `following: 1` in `GET /profiles/{h}`).
- **Browser part (🖱️):** Click handler, auth header (`X-Annotated-Token`) flow.

### 4. YouTube clip flow — "⚑ Clip 90s" → post → clip thread opens
- **Action:** On a `youtube.com/watch` page, click **⚑ Clip 90s** (bottom-right) → set start/length → add comment → Post clip.
- **Expect:** Toast with clip URL; side panel opens the clip thread showing the clip comment / "Clip by @handle", source link, and (once ready) the video player.
- **Headless part (✅):** `POST /clips` → `{"clip_id","status","clip_url"}` — extension reads `data.id || data.clip_id` and `data.clip_url`; fallback URL fixed to `/c/{id}` (was `/clips/{id}`, which serves JSON, not the page). `GET /clips/{id}` confirmed to have **no `title`** — panel now headlines the clip comment or "Clip by @handle" instead of "Untitled clip". Note: local clip processing needs `yt-dlp` + `ffmpeg` installed; the test box lacks `yt-dlp`, so worker-side cutting was not exercised here.
- **Browser part (🖱️):** FAB on watch pages, composer, `annotated:open-clip` message → panel opens in clip mode, video player once `status=ready`.

### 5. Record an audio reply in the side panel → appears in thread
- **Action:** In a clip thread, click **🎙 Record audio reply** → allow mic → Stop → wait for "Posted ✓".
- **Expect:** The audio player appears in the thread after refresh.
- **Headless part (✅):** **Critical fix verified:** the extension sent the blob as form field `audio`; the server requires it as `file` → old code got HTTP 422 always. Fixed to `fd.append('file', …)`; curl with `file` → `{"ok":true,"audio_url":"/media/…"}` (200). Comments list now renders `text` (was reading nonexistent `comment`), and audio comments render an `<audio>` player from `audio_url`.
- **Browser part (🖱️):** MediaRecorder mic flow, upload, re-render.

### 6. Popup — API base, sign-in buttons, paste link token
- **Action:** Open the popup → set API server → Save. Click **Sign in with X** / **Sign in with Google** → each opens the OAuth start page. Complete sign-in on the web, paste the link token → **Link account**.
- **Expect:** Popup flips to "Signed in as @handle"; Sign out returns to signed-out state.
- **Headless part (✅):** `/auth/x/start` and `/auth/google/start` routes exist; `POST /auth/token/verify {"token"}` → `{"handle","provider","token"}` (404 for unknown tokens) — popup reads `d.handle` and stores `d.token || tok`. (Without OAuth client IDs configured the start routes return 503 JSON — expected; configure `X_CLIENT_ID`/`GOOGLE_CLIENT_ID` on the server for the real flow.)
- **Browser part (🖱️):** Tab opening, full OAuth round-trip, paste-token UX, linked-state persistence.

### 7. Popup annotation count matches page highlights
- **Action:** On a page with highlights, open the popup → "Disputes on this page".
- **Expect:** Number equals the number of annotation rows the content script fetched (each quote's marks show count badges summing consistently).
- **Headless part (✅):** **Fix verified by code:** popup previously stripped only the `#hash`; `content.js` `normUrl()` also strips `utm_*`, `gclid`, `fbclid`, `s`. The same `normUrl()` is now duplicated in `popup.js` (comment-marked to stay in sync), so both query `GET /annotations?url=` with identical URLs. `GET /profiles/{h}` → `annotation_count` confirmed for the "Disputes you've posted" row.
- **Browser part (🖱️):** Visual count-vs-highlights agreement on a real page.

## Regression notes for future runs
- `node --check` on `background.js`, `content.js`, `sidepanel.js`, `popup.js` — must pass with zero errors (verified 2026-09-16).
- `manifest.json` must parse; needs `side_panel.default_path`, permissions `storage`, `sidePanel`, `activeTab`, `scripting`, `<all_urls>` host permissions, and `content_scripts` (js + css) — all present.
- Icons `icon16/48/128.png` exist and are valid PNGs.
- All user content is injected via `esc()` in `sidepanel.js` (kept in the fixes above); the composer quote in `content.js` uses its own `escapeHtml()`.
- Handles are lowercased client-side before post/store to match the server's `clean_handle()` — otherwise the side panel's "you" label and follow-self guard misfire.

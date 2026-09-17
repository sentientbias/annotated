/* Annotated content script: select text -> dispute it -> see others' disputes.
   UI lives in shadow DOM so page CSS can't break it. */
(() => {
  'use strict';
  const API_KEY = 'annotated_api_base';
  const HANDLE_KEY = 'annotated_handle';
  const DEFAULT_API = 'https://annotated-api.onrender.com';

  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const normUrl = (u) => {
    try {
      const x = new URL(u);
      x.hash = '';
      // strip common tracking params
      ['utm_source','utm_medium','utm_campaign','utm_term','utm_content','s','gclid','fbclid']
        .forEach(p => x.searchParams.delete(p));
      return x.toString();
    } catch { return u; }
  };

  async function apiBase() {
    const r = await chrome.storage.sync.get(API_KEY);
    return (r[API_KEY] || DEFAULT_API).replace(/\/+$/, '');
  }
  async function handle() {
    const r = await chrome.storage.sync.get(HANDLE_KEY);
    return r[HANDLE_KEY] || '';
  }
  const TOKEN_KEY = 'annotated_token';
  async function authToken() {
    const r = await chrome.storage.sync.get(TOKEN_KEY);
    return r[TOKEN_KEY] || '';
  }
  async function postJson(url, body) {
    const token = await authToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['X-Annotated-Token'] = token;
    return fetch(url, { method: 'POST', headers, body: JSON.stringify(body) });
  }

  /* ---------- shadow root ---------- */
  const host = document.createElement('div');
  host.id = 'annotated-root';
  document.documentElement.appendChild(host);
  const shadow = host.attachShadow({ mode: 'open' });
  const css = document.createElement('style');
  css.textContent = `
    .afab { position: fixed; z-index: 2147483647; background: #111; color: #ffd640;
      border: 2px solid #ffd640; border-radius: 20px; padding: 8px 14px;
      font: 600 13px/1 system-ui, sans-serif; cursor: pointer; box-shadow: 0 4px 18px rgba(0,0,0,.35); }
    .afab:hover { background: #ffd640; color: #111; }
    .overlay { position: fixed; inset: 0; z-index: 2147483647; background: rgba(0,0,0,.45);
      display: flex; align-items: center; justify-content: center; }
    .card { background: #fff; color: #111; border-radius: 14px; width: min(480px, 92vw);
      max-height: 86vh; overflow: auto; padding: 20px; font: 14px/1.5 system-ui, sans-serif;
      box-shadow: 0 12px 48px rgba(0,0,0,.4); }
    .card h3 { margin: 0 0 4px; font-size: 16px; }
    .quote { background: #f6f6f6; border-left: 4px solid #ffd640; padding: 10px 12px;
      border-radius: 0 8px 8px 0; margin: 10px 0; font-style: italic; }
    .stances { display: flex; gap: 8px; margin: 10px 0; }
    .stance { flex: 1; border: 2px solid #ddd; border-radius: 10px; padding: 10px 6px;
      text-align: center; cursor: pointer; font-weight: 700; font-size: 13px; background: #fff; }
    .stance.sel-dispute { border-color: #e63c3c; background: #fdeeee; }
    .stance.sel-agree { border-color: #2e9e5b; background: #e9f7ef; }
    .stance.sel-context { border-color: #2f7fd0; background: #eaf2fd; }
    .tags { display: flex; gap: 6px; margin: 0 0 10px; flex-wrap: wrap; }
    .tag { font-size: 11px; border: 1px solid #ddd; border-radius: 999px; padding: 4px 10px;
      cursor: pointer; color: #555; background: #fff; }
    .tag.sel { border-color: #111; background: #111; color: #fff; }
    textarea { width: 100%; box-sizing: border-box; min-height: 90px; border: 2px solid #ddd;
      border-radius: 10px; padding: 10px; font: 14px/1.5 system-ui, sans-serif; resize: vertical; }
    input.handle { width: 100%; box-sizing: border-box; border: 2px solid #ddd; border-radius: 10px;
      padding: 10px; font: 14px system-ui, sans-serif; margin-top: 8px; }
    .row { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end; }
    button.btn { border: none; border-radius: 10px; padding: 10px 18px; font-weight: 700;
      font-size: 14px; cursor: pointer; }
    .btn-post { background: #111; color: #ffd640; }
    .btn-post:disabled { opacity: .5; cursor: default; }
    .btn-cancel { background: #eee; color: #333; }
    .meta { font-size: 12px; color: #777; margin-top: 6px; }
    .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%);
      z-index: 2147483647; background: #111; color: #fff; padding: 10px 18px; border-radius: 24px;
      font: 600 13px system-ui, sans-serif; box-shadow: 0 4px 18px rgba(0,0,0,.35); }
  `;
  shadow.appendChild(css);

  let fab = null;
  function hideFab() { if (fab) { fab.remove(); fab = null; } }

  function selectionContext() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const text = norm(sel.toString());
    if (text.length < 20 || text.length > 600) return null;
    const range = sel.getRangeAt(0);
    const rect = range.getBoundingClientRect();
    // context: walk to nearby text for prefix/suffix anchoring
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let full = '', nodes = [], n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue || !norm(n.nodeValue)) continue;
      if (host.contains(n)) continue;
      nodes.push({ node: n, start: full.length, text: n.nodeValue });
      full += n.nodeValue + ' ';
    }
    const idx = full.indexOf(sel.toString().trim().slice(0, 40));
    const prefix = idx > 0 ? norm(full.slice(Math.max(0, idx - 60), idx)) : '';
    const suffixSrc = idx >= 0 ? full.slice(idx + text.length, idx + text.length + 60) : '';
    return { text, prefix, suffix: norm(suffixSrc),
             x: rect.left + window.scrollX, y: rect.bottom + window.scrollY };
  }

  document.addEventListener('mouseup', () => {
    setTimeout(() => {
      hideFab();
      const ctx = selectionContext();
      if (!ctx) return;
      fab = document.createElement('button');
      fab.className = 'afab';
      fab.textContent = '⚑ Dispute this';
      fab.style.left = Math.min(ctx.x, window.innerWidth - 140) + 'px';
      fab.style.top = (ctx.y + 8) + 'px';
      fab.addEventListener('mousedown', (e) => e.preventDefault());
      fab.addEventListener('click', () => { hideFab(); openComposer(ctx); });
      shadow.appendChild(fab);
    }, 60);
  });
  document.addEventListener('mousedown', (e) => {
    if (fab && e.target !== fab) hideFab();
  });

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg;
    shadow.appendChild(t);
    setTimeout(() => t.remove(), 2600);
  }

  function openComposer(ctx) {
    const ov = document.createElement('div');
    ov.className = 'overlay';
    ov.innerHTML = `
      <div class="card">
        <h3>Annotate this sentence</h3>
        <div class="quote">“${escapeHtml(ctx.text)}”</div>
        <div class="stances">
          <div class="stance" data-s="dispute">⚑ Dispute</div>
          <div class="stance" data-s="agree">✓ Agree</div>
          <div class="stance" data-s="context">◈ Context</div>
        </div>
        <div class="tags">
          <div class="tag" data-t="fact_check">🏷 fact check</div>
          <div class="tag" data-t="steel_man">🏷 steel-man</div>
          <div class="tag" data-t="receipt">🏷 receipt</div>
          <div class="tag" data-t="hot_take">🏷 hot take</div>
        </div>
        <textarea placeholder="Why? Add your receipt — link, quote, or reasoning…"></textarea>
        <input class="handle" placeholder="your handle (e.g. anon42)" maxlength="32" />
        <div class="meta">Posted publicly. Be sharp, cite sources, no doxxing.</div>
        <div class="row">
          <button class="btn btn-cancel">Cancel</button>
          <button class="btn btn-post" disabled>Post annotation</button>
        </div>
      </div>`;
    shadow.appendChild(ov);
    let stance = null;
    let tag = '';
    const stances = [...ov.querySelectorAll('.stance')];
    const tags = [...ov.querySelectorAll('.tag')];
    tags.forEach(el => el.addEventListener('click', () => {
      // Single-select toggle: click again to clear.
      tag = (tag === el.dataset.t) ? '' : el.dataset.t;
      tags.forEach(x => x.classList.toggle('sel', x.dataset.t === tag));
    }));
    const postBtn = ov.querySelector('.btn-post');
    const ta = ov.querySelector('textarea');
    const hi = ov.querySelector('.handle');
    stances.forEach(el => el.addEventListener('click', () => {
      stances.forEach(x => x.className = 'stance');
      el.classList.add('sel-' + el.dataset.s);
      stance = el.dataset.s;
      postBtn.disabled = !(ta.value.trim().length > 0);
    }));
    ta.addEventListener('input', () => { postBtn.disabled = !(stance && ta.value.trim().length > 0); });
    handle().then(h => { if (h) hi.value = h; });
    ov.querySelector('.btn-cancel').addEventListener('click', () => ov.remove());
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) ov.remove(); });
    postBtn.addEventListener('click', async () => {
      postBtn.disabled = true; postBtn.textContent = 'Posting…';
      try {
        const base = await apiBase();
        const h = norm(hi.value).toLowerCase() || 'anon'; // server lowercases handles on insert
        await chrome.storage.sync.set({ [HANDLE_KEY]: h });
        const res = await postJson(base + '/annotations', {
          url: normUrl(location.href), quote: ctx.text,
          prefix: ctx.prefix, suffix: ctx.suffix,
          stance, tag, comment: ta.value.trim(), handle: h,
        });
        if (!res.ok) throw new Error('server ' + res.status);
        ov.remove(); toast('Annotation posted ⚑');
        window.getSelection().removeAllRanges();
        setTimeout(renderHighlights, 400);
      } catch (err) {
        postBtn.disabled = false; postBtn.textContent = 'Post annotation';
        toast('Failed: ' + err.message);
      }
    });
    setTimeout(() => ta.focus(), 50);
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  /* ---------- highlight rendering ---------- */
  function findRanges(quote) {
    const ranges = [];
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        if (!n.nodeValue || !norm(n.nodeValue)) return NodeFilter.FILTER_REJECT;
        if (host.contains(n)) return NodeFilter.FILTER_REJECT;
        const p = n.parentElement;
        if (p && (p.closest('script,style,noscript,textarea,input') || p.classList.contains('annotated-hit')))
          return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const target = norm(quote);
    let n;
    while ((n = walker.nextNode())) {
      const hay = norm(n.nodeValue);
      const i = hay.indexOf(target);
      if (i >= 0) {
        // map normalized index back approximately: search raw for a distinctive substring
        const probe = quote.trim().slice(0, 30);
        const ri = n.nodeValue.indexOf(probe);
        if (ri >= 0) {
          const r = document.createRange();
          r.setStart(n, ri);
          r.setEnd(n, Math.min(n.nodeValue.length, ri + quote.trim().length));
          ranges.push(r);
        }
      }
    }
    return ranges;
  }

  async function renderHighlights() {
    document.querySelectorAll('.annotated-hit').forEach(m => {
      const t = document.createTextNode(m.textContent);
      m.replaceWith(t);
    });
    let anns;
    try {
      const base = await apiBase();
      const res = await fetch(base + '/annotations?url=' + encodeURIComponent(normUrl(location.href)));
      if (!res.ok) return;
      anns = await res.json();
    } catch { return; }
    // group by quote
    const byQuote = {};
    for (const a of anns) {
      (byQuote[a.quote] = byQuote[a.quote] || []).push(a);
    }
    for (const [quote, list] of Object.entries(byQuote)) {
      const ranges = findRanges(quote);
      const topStance = ['dispute','agree','context']
        .sort((x, y) => list.filter(a => a.stance === y).length - list.filter(a => a.stance === x).length)[0];
      for (const r of ranges.slice(0, 3)) {
        try {
          const mark = document.createElement('mark');
          mark.className = 'annotated-hit';
          mark.dataset.stance = topStance;
          mark.dataset.count = String(list.length);
          mark.title = list.length + ' annotation' + (list.length > 1 ? 's' : '') + ' — click to read';
          r.surroundContents(mark);
          mark.addEventListener('click', () => {
            chrome.runtime.sendMessage({ type: 'annotated:open', quote, url: normUrl(location.href) });
          });
        } catch { /* overlapping ranges; skip */ }
      }
    }
  }

  /* ---------- YouTube clip capture ---------- */
  function isWatchPage() {
    return /(^|\.)youtube\.com$/.test(location.hostname) && location.pathname === '/watch';
  }
  let clipFab = null;
  function hideClipFab() { if (clipFab) { clipFab.remove(); clipFab = null; } }
  function maybeShowClipFab() {
    hideClipFab();
    if (!isWatchPage()) return;
    clipFab = document.createElement('button');
    clipFab.className = 'afab';
    clipFab.textContent = '⚑ Clip 90s';
    clipFab.style.left = 'auto';
    clipFab.style.top = 'auto';
    clipFab.style.right = '18px';
    clipFab.style.bottom = '18px';
    clipFab.addEventListener('mousedown', (e) => e.preventDefault());
    clipFab.addEventListener('click', openClipComposer);
    shadow.appendChild(clipFab);
  }

  function openClipComposer() {
    const video = document.querySelector('video');
    const startSec = video ? Math.max(0, Math.floor(video.currentTime || 0)) : 0;
    const pageUrl = normUrl(location.href);
    const titleEl = document.querySelector('h1.ytd-watch-metadata yt-formatted-string, h1.title yt-formatted-string');
    const title = norm((titleEl && titleEl.textContent) || document.title.replace(/ - YouTube$/, ''));
    const ov = document.createElement('div');
    ov.className = 'overlay';
    ov.innerHTML = `
      <div class="card">
        <h3>Clip up to 90 seconds</h3>
        <div class="quote">${escapeHtml(title)}</div>
        <div class="meta">${escapeHtml(pageUrl)}</div>
        <div class="row" style="justify-content:flex-start;align-items:flex-end;margin-top:10px;">
          <label style="margin:0;font-size:12px;color:#666;">Start (sec)
            <input id="ac-start" type="number" min="0" value="${startSec}" style="width:90px;padding:8px;border:2px solid #ddd;border-radius:8px;display:block;margin-top:4px;" />
          </label>
          <label style="margin:0 0 0 12px;font-size:12px;color:#666;">Length (sec, max 90)
            <input id="ac-dur" type="number" min="1" max="90" value="30" style="width:90px;padding:8px;border:2px solid #ddd;border-radius:8px;display:block;margin-top:4px;" />
          </label>
        </div>
        <textarea id="ac-comment" placeholder="Why this clip? Add context…" style="margin-top:10px;"></textarea>
        <input id="ac-handle" class="handle" placeholder="your handle (e.g. anon42)" maxlength="32" />
        <div class="meta">Posted publicly. Clips are trimmed server-side and link back to the source video.</div>
        <div class="row">
          <button class="btn btn-cancel">Cancel</button>
          <button class="btn btn-post">Post clip</button>
        </div>
      </div>`;
    shadow.appendChild(ov);
    const startInput = ov.querySelector('#ac-start');
    const durInput = ov.querySelector('#ac-dur');
    const commentInput = ov.querySelector('#ac-comment');
    const handleInput = ov.querySelector('#ac-handle');
    const postBtn = ov.querySelector('.btn-post');
    handle().then(h => { if (h) handleInput.value = h; });
    ov.querySelector('.btn-cancel').addEventListener('click', () => ov.remove());
    ov.addEventListener('mousedown', (e) => { if (e.target === ov) ov.remove(); });
    postBtn.addEventListener('click', async () => {
      postBtn.disabled = true; postBtn.textContent = 'Clipping…';
      const s = Math.max(0, parseInt(startInput.value, 10) || 0);
      const d = Math.min(90, Math.max(1, parseInt(durInput.value, 10) || 30));
      try {
        const base = await apiBase();
        const h = norm(handleInput.value).toLowerCase() || 'anon'; // server lowercases handles on insert
        await chrome.storage.sync.set({ [HANDLE_KEY]: h });
        const res = await postJson(base + '/clips', {
          source_url: pageUrl, source_type: 'youtube',
          start_sec: s, duration_sec: d, handle: h,
          comment: commentInput.value.trim(),
        });
        if (!res.ok) throw new Error('server ' + res.status);
        const data = await res.json();
        const clipId = data.id || data.clip_id;
        const clipUrl = data.clip_url || data.url || (clipId ? base + '/c/' + clipId : base + '/feed');
        ov.remove();
        toast('Clip posted: ' + clipUrl);
        if (clipId) chrome.runtime.sendMessage({ type: 'annotated:open-clip', clip_id: clipId });
      } catch (err) {
        postBtn.disabled = false; postBtn.textContent = 'Post clip';
        toast('Failed: ' + err.message);
      }
    });
    setTimeout(() => commentInput.focus(), 50);
  }

  // initial + SPA navigation
  let lastUrl = location.href;
  renderHighlights();
  maybeShowClipFab();
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      setTimeout(renderHighlights, 800);
      maybeShowClipFab();
    }
  }, 1500);
})();

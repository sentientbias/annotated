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
    const stances = [...ov.querySelectorAll('.stance')];
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
        const h = norm(hi.value) || 'anon';
        await chrome.storage.sync.set({ [HANDLE_KEY]: h });
        const res = await fetch(base + '/annotations', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url: normUrl(location.href), quote: ctx.text,
            prefix: ctx.prefix, suffix: ctx.suffix,
            stance, comment: ta.value.trim(), handle: h,
          }),
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

  // initial + SPA navigation
  let lastUrl = location.href;
  renderHighlights();
  setInterval(() => {
    if (location.href !== lastUrl) { lastUrl = location.href; setTimeout(renderHighlights, 800); }
  }, 1500);
})();

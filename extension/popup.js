(async () => {
  const $ = (id) => document.getElementById(id);
  // Must stay in sync with normUrl() in content.js — both sides must query
  // the same normalized URL or the popup count won't match the page highlights.
  const normUrl = (u) => {
    try {
      const x = new URL(u);
      x.hash = '';
      ['utm_source','utm_medium','utm_campaign','utm_term','utm_content','s','gclid','fbclid']
        .forEach(p => x.searchParams.delete(p));
      return x.toString();
    } catch { return u; }
  };
  const sync = await chrome.storage.sync.get(['annotated_api_base', 'annotated_handle']);
  const base = (sync.annotated_api_base || 'https://annotated-api.onrender.com').replace(/\/+$/, '');
  $('api').value = sync.annotated_api_base || '';
  $('save').addEventListener('click', async () => {
    const v = $('api').value.trim().replace(/\/+$/, '');
    await chrome.storage.sync.set({ annotated_api_base: v || 'https://annotated-api.onrender.com' });
    $('save').textContent = 'Saved ✓';
    setTimeout(() => $('save').textContent = 'Save', 1200);
  });
  $('trend').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: base + '/trending' });
  });
  $('feed').addEventListener('click', (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: base + '/feed' });
  });
  $('xlogin').addEventListener('click', () => {
    chrome.tabs.create({ url: base + '/auth/x/start' });
  });
  $('glogin').addEventListener('click', () => {
    chrome.tabs.create({ url: base + '/auth/google/start' });
  });
  $('link').addEventListener('click', async () => {
    const tok = $('token').value.trim();
    if (!tok) return;
    $('link').textContent = 'Linking…';
    try {
      const r = await fetch(base + '/auth/token/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: tok }),
      });
      if (!r.ok) throw new Error('server ' + r.status);
      const d = await r.json();
      if (!d.handle) throw new Error('no handle returned');
      await chrome.storage.sync.set({ annotated_handle: d.handle, annotated_token: d.token || tok });
      location.reload();
    } catch (e) {
      $('link').textContent = 'Failed — try again';
      setTimeout(() => { $('link').textContent = 'Link account'; }, 1500);
    }
  });
  $('signout').addEventListener('click', async () => {
    await chrome.storage.sync.remove(['annotated_handle', 'annotated_token']);
    location.reload();
  });
  (function renderAuth() {
    const signed = !!(sync.annotated_token && sync.annotated_handle);
    $('signedout').style.display = signed ? 'none' : 'block';
    $('signedin').style.display = signed ? 'block' : 'none';
    if (signed) $('who').textContent = '@' + sync.annotated_handle;
  })();
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = normUrl(tab.url);
    const r = await fetch(base + '/annotations?url=' + encodeURIComponent(url));
    const anns = r.ok ? await r.json() : [];
    $('page').textContent = anns.length;
  } catch { $('page').textContent = '–'; }
  try {
    const h = sync.annotated_handle;
    if (h) {
      const r = await fetch(base + '/profiles/' + encodeURIComponent(h));
      const p = r.ok ? await r.json() : null;
      $('mine').textContent = p ? p.annotation_count : '0';
    } else $('mine').textContent = '0';
  } catch { $('mine').textContent = '–'; }
})();

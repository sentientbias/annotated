(async () => {
  const $ = (id) => document.getElementById(id);
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
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const url = new URL(tab.url); url.hash = '';
    const r = await fetch(base + '/annotations?url=' + encodeURIComponent(url.toString()));
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

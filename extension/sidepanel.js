(async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sess = await chrome.storage.session.get('annotated_panel');
  const ctx = sess.annotated_panel;
  if (!ctx) { $('list').innerHTML = '<span class="empty">Click a highlighted sentence to read its annotations.</span>'; return; }
  $('quote').textContent = '“' + ctx.quote + '”';
  const sync = await chrome.storage.sync.get(['annotated_api_base', 'annotated_handle']);
  const base = (sync.annotated_api_base || '').replace(/\/+$/, '');
  const me = sync.annotated_handle || '';
  try {
    const r = await fetch(base + '/annotations?url=' + encodeURIComponent(ctx.url));
    const anns = (r.ok ? await r.json() : []).filter(a => a.quote === ctx.quote);
    if (!anns.length) { $('list').innerHTML = '<span class="empty">No annotations on this sentence yet — select it and post the first.</span>'; return; }
    $('list').innerHTML = anns.map(a => `
      <div class="ann">
        <button class="follow" data-h="${esc(a.handle)}">${a.handle === me ? 'you' : '+ follow'}</button>
        <span class="badge b-${a.stance}">${a.stance.toUpperCase()}</span>
        <span class="who">${esc(a.handle)}</span><span class="when">${new Date(a.created_at).toLocaleString()}</span>
        <div>${esc(a.comment)}</div>
      </div>`).join('');
    document.querySelectorAll('.follow').forEach(b => b.addEventListener('click', async () => {
      const target = b.dataset.h;
      if (!me || target === me) return;
      await fetch(base + '/follow', { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ follower: me, followee: target }) });
      b.textContent = 'following ✓';
    }));
  } catch (e) {
    $('list').innerHTML = '<span class="empty">Could not reach the annotation server.</span>';
  }
})();

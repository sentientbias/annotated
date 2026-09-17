(async () => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sync = await chrome.storage.sync.get(['annotated_api_base', 'annotated_handle', 'annotated_token']);
  const base = (sync.annotated_api_base || '').replace(/\/+$/, '');
  const me = sync.annotated_handle || '';
  const token = sync.annotated_token || '';
  const authHeaders = (extra) => {
    const h = Object.assign({}, extra || {});
    if (token) h['X-Annotated-Token'] = token;
    return h;
  };

  const sess = await chrome.storage.session.get('annotated_panel');
  const ctx = sess.annotated_panel;
  if (!ctx) { $('list').innerHTML = '<span class="empty">Click a highlighted sentence to read its annotations.</span>'; return; }

  if (ctx.clip_id) { await renderClip(ctx.clip_id); return; }
  await renderQuote(ctx);

  /* ---------- quote mode: annotations on a sentence ---------- */
  async function renderQuote(ctx) {
    $('quote').textContent = '“' + ctx.quote + '”';
    // consensus meter for the whole page
    try {
      const cr = await fetch(base + '/api/consensus?url=' + encodeURIComponent(ctx.url));
      if (cr.ok) {
        const cc = await cr.json();
        const tot = cc.total || 1;
        const seg = (n, c) => `<span style="display:inline-block;height:100%;width:${(100 * n / tot).toFixed(1)}%;background:${c}"></span>`;
        $('quote').insertAdjacentHTML('afterend',
          `<div id="meter" style="margin:-6px 0 12px;font-size:12px;color:#666">` +
          `<span style="display:inline-block;width:110px;height:8px;border-radius:99px;overflow:hidden;background:#eee;vertical-align:middle">` +
          seg(cc.dispute, '#e63c3c') + seg(cc.agree, '#2e9e5b') + seg(cc.context, '#2f7fd0') +
          `</span><span style="margin-left:6px">⚑${cc.dispute} ✓${cc.agree} ◈${cc.context} on this page</span></div>`);
      }
    } catch (e) { /* meter is decorative; never block the thread */ }
    try {
      const r = await fetch(base + '/annotations?url=' + encodeURIComponent(ctx.url));
      const anns = (r.ok ? await r.json() : []).filter(a => a.quote === ctx.quote);
      if (!anns.length) { $('list').innerHTML = '<span class="empty">No annotations on this sentence yet — select it and post the first.</span>'; return; }
      $('list').innerHTML = anns.map(a => `
        <div class="ann">
          <button class="follow" data-h="${esc(a.handle)}">${a.handle === me ? 'you' : '+ follow'}</button>
          <span class="badge b-${a.stance}">${esc(a.stance).toUpperCase()}</span>
          ${a.tag ? `<span class="badge b-tag">🏷 ${esc(a.tag.replace('_', ' '))}</span>` : ''}
          <span class="who">${esc(a.handle)}</span><span class="when">${new Date(a.created_at).toLocaleString()}</span>
          <div>${esc(a.comment)}</div>
          ${(a.sources || []).map(s => `
            <a class="receipt" href="${esc(s.url)}" target="_blank" rel="noopener">🧾 ${esc(s.title || s.url)}
            <span class="d">${esc(s.domain || '')}</span></a>`).join('')}
        </div>`).join('');
      document.querySelectorAll('.follow').forEach(b => b.addEventListener('click', async () => {
        const target = b.dataset.h;
        if (!me || target === me) return;
        await fetch(base + '/follow', { method: 'POST',
          headers: authHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({ follower: me, followee: target }) });
        b.textContent = 'following ✓';
      }));
    } catch (e) {
      $('list').innerHTML = '<span class="empty">Could not reach the annotation server.</span>';
    }
  }

  /* ---------- clip mode: clip thread with audio replies ---------- */
  async function renderClip(clipId) {
    $('panel-title').textContent = 'Clip thread';
    try {
      const r = await fetch(base + '/clips/' + encodeURIComponent(clipId));
      if (!r.ok) throw new Error('server ' + r.status);
      const clip = await r.json();
      // Server has no title field: headline = clip comment, else "Clip by @handle".
      const caption = (clip.comment || '').trim();
      const title = caption || ('Clip by @' + (clip.handle || 'anon'));
      const meta = [
        clip.handle ? '@' + clip.handle : '',
        clip.duration_sec ? Number(clip.duration_sec) + 's clip' : '',
        clip.start_sec != null && clip.start_sec !== '' ? 'from ' + Number(clip.start_sec) + 's' : '',
        clip.status && clip.status !== 'ready' ? clip.status : '',
      ].filter(Boolean).join(' · ');
      const src = clip.source_url || clip.url || '';
      const videoSrc = clip.video_url || clip.file_url || clip.playback_url || '';
      const comments = clip.comments || clip.replies || [];
      $('quote').innerHTML =
        '<div style="font-style:normal;font-weight:700;">' + esc(title) + '</div>' +
        (meta ? '<div style="font-size:12px;color:#666;margin-top:2px;">' + esc(meta) + '</div>' : '') +
        (src ? '<div style="font-size:12px;margin-top:4px;"><a href="' + esc(src) + '" target="_blank" rel="noopener">View source ↗</a></div>' : '') +
        (videoSrc ? '<video controls src="' + esc(videoSrc) + '" style="width:100%;margin-top:8px;border-radius:8px;"></video>' : '');
      $('list').innerHTML = comments.length ? comments.map(c => {
        const text = c.text || c.comment || ''; // server returns "text"
        return `
        <div class="ann">
          <span class="who">${esc(c.handle)}</span><span class="when">${c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
          ${text ? '<div>' + esc(text) + '</div>' : ''}
          ${c.audio_url ? '<audio controls src="' + esc(c.audio_url) + '" style="width:100%;margin-top:6px;"></audio>' : ''}
        </div>`; }).join('')
        : '<span class="empty">No replies yet — record the first.</span>';
      wireRecorder(clipId);
    } catch (e) {
      $('list').innerHTML = '<span class="empty">Could not load this clip.</span>';
    }
  }

  function wireRecorder(clipId) {
    const recDiv = $('recorder');
    const recBtn = $('recbtn');
    const recStatus = $('recstatus');
    recDiv.style.display = 'block';
    let recorder = null, chunks = [], stream = null, timer = null, t0 = 0;

    recBtn.addEventListener('click', async () => {
      if (recorder && recorder.state === 'recording') { recorder.stop(); return; }
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (e) {
        recStatus.textContent = 'Mic unavailable: ' + e.message;
        return;
      }
      chunks = [];
      try {
        recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      } catch (e) {
        recorder = new MediaRecorder(stream);
      }
      recorder.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      recorder.onstop = async () => {
        clearInterval(timer);
        recBtn.textContent = '🎙 Record audio reply';
        recStatus.textContent = 'Uploading…';
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunks, { type: 'audio/webm' });
        const fd = new FormData();
        fd.append('handle', me || 'anon');
        fd.append('file', blob, 'reply.webm'); // server expects the File field named "file"
        try {
          const up = await fetch(base + '/clips/' + encodeURIComponent(clipId) + '/audio', {
            method: 'POST', headers: authHeaders(), body: fd,
          });
          if (!up.ok) throw new Error('server ' + up.status);
          recStatus.textContent = 'Posted ✓';
          setTimeout(() => renderClip(clipId), 600);
        } catch (e) {
          recStatus.textContent = 'Upload failed: ' + e.message;
        }
      };
      recorder.start();
      t0 = Date.now();
      recBtn.textContent = '■ Stop recording';
      recStatus.textContent = 'Recording… 0s';
      timer = setInterval(() => {
        recStatus.textContent = 'Recording… ' + Math.floor((Date.now() - t0) / 1000) + 's';
      }, 500);
    });
  }
})();

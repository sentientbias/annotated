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
    try {
      const r = await fetch(base + '/annotations?url=' + encodeURIComponent(ctx.url));
      const anns = (r.ok ? await r.json() : []).filter(a => a.quote === ctx.quote);
      if (!anns.length) { $('list').innerHTML = '<span class="empty">No annotations on this sentence yet — select it and post the first.</span>'; return; }
      $('list').innerHTML = anns.map(a => `
        <div class="ann">
          <button class="follow" data-h="${esc(a.handle)}">${a.handle === me ? 'you' : '+ follow'}</button>
          <span class="badge b-${a.stance}">${esc(a.stance).toUpperCase()}</span>
          <span class="who">${esc(a.handle)}</span><span class="when">${new Date(a.created_at).toLocaleString()}</span>
          <div>${esc(a.comment)}</div>
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
      const title = clip.title || 'Untitled clip';
      const src = clip.source_url || clip.url || '';
      const videoSrc = clip.video_url || clip.file_url || clip.playback_url || '';
      const comments = clip.comments || clip.replies || [];
      $('quote').innerHTML =
        '<div style="font-style:normal;font-weight:700;">' + esc(title) + '</div>' +
        (src ? '<div style="font-size:12px;margin-top:4px;"><a href="' + esc(src) + '" target="_blank" rel="noopener">View source ↗</a></div>' : '') +
        (videoSrc ? '<video controls src="' + esc(videoSrc) + '" style="width:100%;margin-top:8px;border-radius:8px;"></video>' : '');
      $('list').innerHTML = comments.length ? comments.map(c => `
        <div class="ann">
          <span class="who">${esc(c.handle)}</span><span class="when">${c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
          ${c.comment ? '<div>' + esc(c.comment) + '</div>' : ''}
          ${c.audio_url ? '<audio controls src="' + esc(c.audio_url) + '" style="width:100%;margin-top:6px;"></audio>' : ''}
        </div>`).join('')
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
        fd.append('audio', blob, 'reply.webm');
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

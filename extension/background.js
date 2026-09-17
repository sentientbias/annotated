/* Annotated background service worker */
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'annotated:open') {
    (async () => {
      const tabId = sender.tab && sender.tab.id;
      try {
        await chrome.sidePanel.setOptions({
          tabId, path: 'sidepanel.html', enabled: true,
        });
        await chrome.sidePanel.open({ tabId });
        // stash context for the panel
        await chrome.storage.session.set({
          annotated_panel: { quote: msg.quote, url: msg.url },
        });
      } catch (e) { console.warn('sidePanel failed', e); }
    })();
  }
  if (msg.type === 'annotated:open-clip') {
    (async () => {
      const tabId = sender.tab && sender.tab.id;
      try {
        // stash context before opening, so the panel reads it on load
        await chrome.storage.session.set({
          annotated_panel: { clip_id: msg.clip_id },
        });
        await chrome.sidePanel.setOptions({
          tabId, path: 'sidepanel.html', enabled: true,
        });
        await chrome.sidePanel.open({ tabId });
      } catch (e) { console.warn('sidePanel clip failed', e); }
    })();
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.sync.get('annotated_api_base', (r) => {
    if (!r.annotated_api_base) {
      chrome.storage.sync.set({ annotated_api_base: 'https://annotated-api.onrender.com' });
    }
  });
});

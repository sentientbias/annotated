from playwright.sync_api import sync_playwright
import time
EXT = '/home/hatch/workspace/annotated/extension'
CHROME = '/home/hatch/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome'
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context('/tmp/ann-diag-cors', headless=False,
        executable_path=CHROME,
        args=[f'--disable-extensions-except={EXT}', f'--load-extension={EXT}', '--no-sandbox',
              '--proxy-server=127.0.0.1:8899', '--ignore-certificate-errors'],
        viewport={'width': 1280, 'height': 800})
    pg = ctx.new_page()
    pg.goto('https://www.youtube.com/watch?v=X8SALDwOpTU', wait_until='domcontentloaded', timeout=45000)
    time.sleep(8)
    # fetch from page context (should fail CORS)
    r1 = pg.evaluate("""async () => {
      try {
        const r = await fetch('https://annotated-api.onrender.com/health');
        return 'page-ctx: ' + r.status;
      } catch (e) { return 'page-ctx FAIL: ' + e.message; }
    }""")
    print(r1, flush=True)
    # fetch from the content script's context via the shadow host
    r2 = pg.evaluate("""async () => {
      const h = document.querySelector('#annotated-root');
      if (!h) return 'no annotated-root';
      // content script runs in isolated world; we can't directly call it,
      // but we can check if IT can fetch by dispatching a custom event it listens for
      return 'root-exists';
    }""")
    print(r2, flush=True)
    ctx.close()
print('DIAGDONE', flush=True)

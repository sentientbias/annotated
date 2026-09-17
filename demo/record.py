"""Annotated demo recorder — genuine Chrome QA + 8 screenshots + narrated video.
Loads the unpacked extension in Chrome for Testing, drives the full user flow,
records video, captures 8 screenshots, mixes narration.
"""
from playwright.sync_api import sync_playwright
import time, re, urllib.parse, subprocess, os, shutil, json

EXT = '/home/hatch/workspace/annotated/extension'
CHROME = '/home/hatch/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome'
API = 'https://annotated-api.onrender.com'
ARTICLE = API + '/demo-article'
YT = 'https://www.youtube.com/watch?v=X8SALDwOpTU'
FEED = API + '/feed'
RECEIPT = 'https://devops.com/factory-raises-200m-as-it-builds-agents-across-the-software-lifecycle/'
SHOTS = '/home/hatch/workspace/annotated/demo-shots'
VDIR = '/home/hatch/workspace/annotated/demo-video'
PROFILE = '/home/hatch/workspace/annotated/demo-profile'
COMMENT = ("A $5B headline valuation is not the same as a priced exit. "
           "Private marks move on vibes; revenue and retention are the real receipts.")
REPLY_COMMENT = "Counterpoint: enterprise pilots ARE converting — the question is speed, not if."
AUDIO_REPLY = "This valuation assumes the agents keep working at scale. Watch the churn numbers."

SH = ("(sel) => { const h = document.querySelector('#annotated-root');"
      " return h ? h.shadowRoot.querySelector(sel) : null; }")

BLOCK_HOSTS = ('googlesyndication.com', 'doubleclick.net', 'amazon-adsystem.com',
               'criteo.com', 'outbrain.com', 'taboola.com', 'scorecardresearch.com')

def media_guard(route):
    req = route.request
    host = urllib.parse.urlparse(req.url).hostname or ''
    if any(h in host for h in BLOCK_HOSTS):
        return route.abort()
    if req.resource_type in ('media', 'websocket') or \
       re.search(r'\.(mp4|m3u8|ts|mov|webm|m4s|mpd)(\?|$)', req.url):
        return route.abort()
    return route.continue_()

def goto(page, url, name):
    for i in range(3):
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=45000)
            return
        except Exception as e:
            print(f"   goto {name} attempt {i+1} failed: {str(e).splitlines()[0][:90]}", flush=True)
            time.sleep(4)
    raise RuntimeError(f"goto {name} failed 3x")

def sh_wait(page, sel, timeout=15000):
    # Python-side poll loop (page.wait_for_function with a string trips YouTube's CSP)
    end = time.time() + timeout / 1000
    while time.time() < end:
        try:
            if page.evaluate(f"({SH})({json.dumps(sel)}) !== null"):
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"sh_wait timeout: {sel}")

def sh_click(page, sel):
    page.evaluate(f"({SH})({json.dumps(sel)}).click()")

def sh_fill(page, sel, text):
    page.evaluate(f"""() => {{ const el = ({SH})({json.dumps(sel)}); el.focus();
        el.value = {json.dumps(text)}; el.dispatchEvent(new Event('input', {{bubbles: true}})); }}""")

def main():
    os.makedirs(SHOTS, exist_ok=True)
    os.makedirs(VDIR, exist_ok=True)
    for f in os.listdir(VDIR):
        os.remove(os.path.join(VDIR, f))
    print("== 0. token ==", flush=True)
    subprocess.run(['curl', '-s', '-X', 'POST', API + '/auth/token',
                    '-H', 'Content-Type: application/json',
                    '-d', '{"handle":"demobot"}', '-o', '/dev/null'], check=False)

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, executable_path=CHROME,
            args=[f'--disable-extensions-except={EXT}', f'--load-extension={EXT}',
                  '--no-sandbox', '--proxy-server=127.0.0.1:8899',
                  '--ignore-certificate-errors',
                  '--use-fake-device-for-media-stream',
                  '--use-fake-ui-for-media-stream',
                  '--autoplay-policy=no-user-gesture-required'],
            viewport={'width': 1280, 'height': 800},
            record_video_dir=VDIR, record_video_size={'width': 1280, 'height': 800})
        ctx.route('**/*', media_guard)
        page = ctx.new_page()

        print("== 1. homepage ==", flush=True)
        goto(page, API + '/', 'homepage')
        time.sleep(6)
        page.screenshot(path=f"{VDIR}/v-home.png")

        print("== 2. demo article ==", flush=True)
        goto(page, ARTICLE, 'demo-article')
        page.wait_for_selector('.article p', timeout=30000)
        time.sleep(3)

        print("== 3. select sentence -> FAB ==", flush=True)
        sel_txt = page.evaluate("""() => {
          const ps = [...document.querySelectorAll('.article p')];
          const target = ps.find(p => p.textContent.includes('twice as fast')) || ps[1];
          const key = 'twice as fast';
          const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
          let node, tnode = null, toff = 0;
          while ((node = walker.nextNode())) {
            const i = node.textContent.indexOf(key);
            if (i >= 0) { tnode = node; toff = i; break; }
          }
          if (!tnode) return 'NOTFOUND';
          const t = tnode.textContent;
          let s = t.lastIndexOf('. ', toff); s = (s < 0 ? 0 : s + 2);
          let e = t.indexOf('.', toff + key.length); e = (e < 0 ? t.length : e + 1);
          const range = document.createRange();
          range.setStart(tnode, s); range.setEnd(tnode, e);
          const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
          const r = range.getBoundingClientRect();
          window.scrollTo({top: r.top + window.scrollY - 260, behavior: 'instant'});
          document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
          return t.slice(s, e).slice(0, 70);
        }""")
        print("   selected:", sel_txt, flush=True)
        sh_wait(page, '.afab')
        time.sleep(1.5)
        page.screenshot(path=f"{SHOTS}/shot-1-fab.png")

        print("== 4. composer ==", flush=True)
        sh_click(page, '.afab')
        sh_wait(page, '.overlay .card')
        time.sleep(1)
        sh_click(page, ".stance[data-s='dispute']")
        time.sleep(0.6)
        sh_click(page, ".tag[data-t='fact_check']")
        time.sleep(0.6)
        sh_fill(page, '.overlay textarea', COMMENT)
        time.sleep(0.8)
        sh_fill(page, '#ac-receipt', RECEIPT)
        time.sleep(0.8)
        sh_fill(page, '#ac-handle', 'demobot')
        time.sleep(1.5)
        page.screenshot(path=f"{SHOTS}/shot-2-composer.png")
        sh_click(page, '.btn-post')
        sh_wait(page, '.toast')
        print("   toast:", page.evaluate(f"({SH})('.toast').textContent"), flush=True)
        time.sleep(4)
        page.screenshot(path=f"{SHOTS}/shot-3-highlight.png")

        print("== 5. open thread via highlight ==", flush=True)
        page.evaluate("""() => {
          const marks = document.querySelectorAll('mark.annotated-hit');
          marks[marks.length - 1].scrollIntoView({block: 'center'});
        }""")
        time.sleep(1.5)
        page.evaluate("""() => {
          const marks = document.querySelectorAll('mark.annotated-hit');
          marks[marks.length - 1].click();
        }""")
        time.sleep(7)
        panels = [pg for pg in ctx.pages if 'sidepanel.html' in pg.url]
        if panels:
            panels[-1].screenshot(path=f"{SHOTS}/shot-4-sidepanel.png")
            print("   side panel shot captured", flush=True)
            # follow the author
            try:
                panels[-1].evaluate("""() => {
                  const b = [...document.querySelectorAll('button.follow')]
                    .find(x => x.textContent.includes('+ follow'));
                  if (b) b.click();
                }""")
                time.sleep(1.5)
                ftxt = panels[-1].evaluate("""() => {
                  const b = [...document.querySelectorAll('button.follow')][0];
                  return b ? b.textContent : 'none';
                }""")
                print("   follow button now:", ftxt, flush=True)
            except Exception as e:
                print("   follow step skipped:", str(e)[:80], flush=True)
            panels[-1].close()

        print("== 6. youtube clip ==", flush=True)
        goto(page, YT, 'youtube')
        time.sleep(6)
        page.evaluate("""() => {
          const v = document.querySelector('video');
          if (v) { v.currentTime = Math.min(30, (v.duration || 90) / 3); }
          window.scrollTo(0, 400);
        }""")
        time.sleep(2)
        sh_wait(page, '.afab', timeout=25000)
        fab_txt = page.evaluate(f"({SH})('.afab').textContent")
        print("   fab text:", fab_txt, flush=True)
        sh_click(page, '.afab')
        sh_wait(page, '.overlay .card', timeout=15000)
        time.sleep(1.5)
        page.screenshot(path=f"{SHOTS}/shot-5-clip-composer.png")
        sh_fill(page, '#ac-comment', 'TWiST on the AI coding boom — worth clipping.')
        time.sleep(0.5)
        sh_fill(page, '#ac-handle', 'demobot')
        time.sleep(1)
        sh_click(page, '.btn-post')
        try:
            sh_wait(page, '.toast', timeout=90000)
            print("   clip toast:", page.evaluate(f"({SH})('.toast').textContent"), flush=True)
        except Exception as e:
            print("   clip toast timeout:", str(e)[:80], flush=True)
        time.sleep(5)
        # side panel auto-opens in clip mode after posting
        print("   waiting for clip side panel...", flush=True)
        clip_panel = None
        for _ in range(30):
            time.sleep(2)
            cands = [pg for pg in ctx.pages if 'sidepanel.html' in pg.url]
            if cands:
                clip_panel = cands[-1]
                break
        if clip_panel:
            time.sleep(4)
            clip_panel.screenshot(path=f"{SHOTS}/shot-6-clip-thread.png")
            print("   clip thread shot captured", flush=True)

            print("== 7. audio reply ==", flush=True)
            try:
                clip_panel.evaluate("() => document.querySelector('#recbtn').click()")
                time.sleep(4)
                status1 = clip_panel.evaluate("() => document.querySelector('#recstatus').textContent")
                print("   rec status:", status1, flush=True)
                clip_panel.evaluate("() => document.querySelector('#recbtn').click()")
                time.sleep(6)
                status2 = clip_panel.evaluate("() => document.querySelector('#recstatus').textContent")
                print("   after stop:", status2, flush=True)
                time.sleep(3)
                print("   audio reply attempted", flush=True)
            except Exception as e:
                print("   audio step issue:", str(e)[:100], flush=True)
            clip_panel.screenshot(path=f"{SHOTS}/shot-7-audio-reply.png")
            clip_panel.close()
        else:
            print("   no clip panel appeared", flush=True)

        print("== 8. popup check ==", flush=True)
        try:
            # open the popup page directly via its extension URL
            bg = None
            for _ in range(10):
                for pg in ctx.pages:
                    if pg.url.startswith('chrome-extension://'):
                        bg = pg; break
                if bg: break
                time.sleep(1)
            workers = ctx.service_workers
            ext_id = None
            if workers:
                ext_id = workers[0].url.split('/')[2]
            else:
                # fallback: derive from any extension page
                for pg in ctx.pages:
                    if pg.url.startswith('chrome-extension://'):
                        ext_id = pg.url.split('/')[2]; break
            print("   extension id:", ext_id, flush=True)
            if ext_id:
                pop = ctx.new_page()
                pop.goto(f'chrome-extension://{ext_id}/popup.html')
                time.sleep(2)
                api_val = pop.evaluate("() => document.querySelector('input') ? document.querySelector('input').value : 'no-input'")
                print("   popup api field:", api_val, flush=True)
                pop.screenshot(path=f"{SHOTS}/shot-popup.png")
                pop.close()
        except Exception as e:
            print("   popup check skipped:", str(e)[:100], flush=True)

        print("== 9. feed ==", flush=True)
        goto(page, FEED, 'feed')
        time.sleep(5)
        page.screenshot(path=f"{SHOTS}/shot-8-feed.png", full_page=False)

        print("== 10. wrap ==", flush=True)
        for pg in ctx.pages:
            try:
                pg.close()
            except Exception:
                pass
        ctx.close()
    print("RECORD_OK", flush=True)

if __name__ == '__main__':
    main()

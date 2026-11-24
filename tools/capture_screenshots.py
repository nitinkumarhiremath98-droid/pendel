"""Capture the PWA / Play Store screenshots listed in web/manifest.webmanifest.

  python tools/capture_screenshots.py                          # a temporary local server with the demo songs
  python tools/capture_screenshots.py --base https://pendel.live   # or your live site

Writes web/screenshots/mobile-home.png (1080x2340, a 360x780 phone at 3x) and web/screenshots/desktop-home.png
(1280x800). Needs Playwright and Chrome/Chromium:  pip install playwright && python -m playwright install chromium
"""
import argparse, asyncio, os, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "screenshots")
SHOTS = [  # file, path, viewport, scale
    ("mobile-home.png", "/", {"width": 360, "height": 780}, 3),
    ("desktop-home.png", "/", {"width": 1280, "height": 800}, 1),
]


async def capture(base, chrome):
    from playwright.async_api import async_playwright
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        kw = {"executable_path": chrome} if chrome else {}
        browser = await p.chromium.launch(args=["--no-sandbox"], **kw)
        for name, path, viewport, scale in SHOTS:
            ctx = await browser.new_context(viewport=viewport, device_scale_factor=scale, is_mobile=scale > 1, has_touch=scale > 1,
                                            color_scheme="dark")
            page = await ctx.new_page()
            await page.goto(base + path, wait_until="networkidle")
            await page.wait_for_selector(".rail .card")
            await page.wait_for_timeout(1500)  # covers and the hero fade in
            await page.screenshot(path=os.path.join(OUT, name))
            print("wrote", os.path.relpath(os.path.join(OUT, name), ROOT))
            await ctx.close()
        await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="site to capture (default: a temporary local server with the demo songs)")
    ap.add_argument("--chrome", default=os.environ.get("CHROME_PATH"), help="path to Chrome, if Playwright's own Chromium isn't installed")
    a = ap.parse_args()
    if a.base:
        return asyncio.run(capture(a.base.rstrip("/"), a.chrome))
    tmp = tempfile.mkdtemp(prefix="pendel-shots-")
    port = "8841"
    env = {**os.environ, "DATABASE_PATH": os.path.join(tmp, "shots.db"), "DATABASE_URL": "", "PORT": port, "SEED_DEMO": "1", "YOUTUBE_API_KEY": ""}
    srv = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                urllib.request.urlopen(base + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.2)
        asyncio.run(capture(base, a.chrome))
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()

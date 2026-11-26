"""Browser test for the Now Playing screen (Playwright + Chrome): for YouTube songs the album art and the video
sit side by side near the top, the video keeps YouTube's 200x200 px minimum and is never hidden, and the
controls stay above the fold on phones.

    python tests/e2e_nowplaying.py
"""
import asyncio, os, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_autonext as E  # same temporary server set-up, fake YouTube API and songs

RECT = """(() => { const r = (s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect();
    return { x: Math.round(b.left), y: Math.round(b.top), w: Math.round(b.width), h: Math.round(b.height) }; };
  const d = document.getElementById('yt-dock');
  return { dock: r('#yt-dock'), frame: r('#yt-dock .yt-frame-wrap'), slot: r('.np-video-slot'), art: r('.fp-art'), title: r('.fp-title'),
           controls: r('.fp-controls'), bottom: r('.fp-bottom'), inNp: d.classList.contains('in-np'), hidden: d.hidden,
           vh: innerHeight, vw: innerWidth, sw: document.querySelector('.fp-body').scrollWidth }; })()"""


async def main():
    from playwright.async_api import async_playwright
    fails = []
    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox", "--autoplay-policy=no-user-gesture-required"])
        for name, vp in [("phone 360", {"width": 360, "height": 780}), ("phone 390", {"width": 390, "height": 844}), ("desktop", {"width": 1366, "height": 768})]:
            page = await (await b.new_context(viewport=vp)).new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
            await page.goto(E.BASE + "/")
            await page.wait_for_selector(".rail .card")
            # an audio-file song first: no video, the artwork keeps its full size
            await page.evaluate("""async () => { const { player } = await import('/js/player.js'); player.setAutoplay(false);
                const s = (await (await fetch('/api/songs?limit=50')).json()).songs.find(x => x.source === 'pendel');
                await player.play(s, { queue: [s] }); }""")
            await page.evaluate("document.dispatchEvent(new Event('rf:open-fullplayer'))"); await page.wait_for_timeout(700)
            m = await page.evaluate(RECT)
            check(m["slot"]["w"] == 0 and m["art"]["w"] >= 250, f"{name}: audio song: no video slot, large artwork ({m['art']['w']}px)")
            await page.evaluate("document.querySelector('[data-action=close-fullplayer]').click()")
            # a YouTube song
            await page.evaluate("""async () => { window.__ytLen = 600; const { player } = await import('/js/player.js');
                const s = (await (await fetch('/api/songs/search?q=kannada%20love%20song%207')).json()).songs[0];
                await player.play(s, { queue: [s], mode: 'full' }); }""")
            for _ in range(40):
                if await page.evaluate("!document.getElementById('yt-dock').hidden"): break
                await page.wait_for_timeout(200)
            t0 = await page.evaluate("window.__ytp.t")
            await page.evaluate("document.dispatchEvent(new Event('rf:open-fullplayer'))"); await page.wait_for_timeout(900)
            m = await page.evaluate(RECT)
            same_row = abs((m["art"]["y"] + m["art"]["h"] / 2) - (m["frame"]["y"] + m["frame"]["h"] / 2)) < 20
            check(m["inNp"] and (m["dock"]["x"], m["dock"]["y"]) == (m["slot"]["x"], m["slot"]["y"]), f"{name}: the video is pinned beside the artwork {m['dock']} / slot {m['slot']}")
            check(m["frame"]["w"] >= 200 and m["frame"]["h"] >= 200 and not m["hidden"], f"{name}: video stays visible at YouTube's 200x200 minimum ({m['frame']['w']}x{m['frame']['h']})")
            check(m["art"]["x"] + m["art"]["w"] <= m["frame"]["x"] and same_row and m["art"]["w"] >= 76, f"{name}: artwork on the left, video on the right, same row (art {m['art']['w']}px)")
            check(m["title"]["y"] > m["frame"]["y"] + m["frame"]["h"] and m["bottom"]["y"] + m["bottom"]["h"] <= m["vh"], f"{name}: title, controls and Lyrics/Queue below the row and above the fold")
            check(m["sw"] <= m["vw"], f"{name}: nothing overflows sideways ({m['sw']} <= {m['vw']})")
            check(await page.evaluate("window.__ytp.t") >= t0 and await page.evaluate("window.__ytp.state") == 1, f"{name}: the video keeps playing (it is not reloaded)")
            await page.screenshot(path=os.path.join(E.TMP, f"nowplaying-{name.replace(' ', '')}.png"))
            if vp["width"] < 900:
                await page.click("[data-action=toggle-fp-lyrics]"); await page.wait_for_timeout(400)
                m = await page.evaluate(RECT)
                check(not m["inNp"] and not m["hidden"] and m["frame"]["w"] >= 200, f"{name}: lyrics view: the video moves back to its corner, still visible")
                await page.click("[data-action=toggle-fp-lyrics]")
            await page.evaluate("document.querySelector('[data-action=close-fullplayer]').click()"); await page.wait_for_timeout(300)
            m = await page.evaluate(RECT)
            check(not m["inNp"] and not m["hidden"], f"{name}: closing Now Playing returns the video to its corner")
            check(not errors, f"{name}: no page errors {errors}")
            await page.context.close()
        await b.close()
    return fails


if __name__ == "__main__":
    E.setup_db()
    srv = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=E.ROOT, env=E.ENV, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(E.BASE + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.2)
        fails = asyncio.run(main())
    finally:
        srv.terminate()
    print(f"screenshots: {E.TMP}")
    sys.exit(1 if fails else 0)

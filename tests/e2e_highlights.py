"""Browser test for Song Highlights (Playwright + Chrome): 42 s windows on the chorus, the progress bar, and
auto-advance to the next highlight when the window ends.

    python tests/e2e_highlights.py
"""
import asyncio, os, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_autonext as E  # same temporary server set-up


async def main():
    from playwright.async_api import async_playwright
    fails = []
    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
        page = await (await b.new_context(viewport={"width": 1280, "height": 860})).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
        await page.goto(E.BASE + "/highlights/love")
        await page.wait_for_selector("#hl-go")
        check("40 seconds" in await page.inner_text(".hl-start-card"), "start card describes 40-second highlights")
        cards = await page.evaluate("""async () => { const { songs } = await import('/js/ui.js');
            return [...document.querySelectorAll('.hl-slide[data-i]')].map(el => { const s = songs.get(el.dataset.id);
              return { label: el.querySelector('.hl-time span:last-child').textContent, apple: s.highlight.source === 'provider' }; }); }""")
        own = [c["label"] for c in cards if not c["apple"]]
        check(own and all(l.endswith("· 42s") for l in own), f"every PENDEL / YouTube card shows a 42 s window: {[l for l in own if not l.endswith('· 42s')] or own[:3]}")
        check(all(c["label"].endswith("· 30s") for c in cards if c["apple"]), "Apple Music previews keep their official 30 s length")
        await page.click("#hl-go")
        await page.wait_for_timeout(1500)
        st = "(async () => { const { player } = await import('/js/player.js'); const a = document.getElementById('audio-player'); return { mode: player.current?.mode, len: player.length, start: player.current?.start, t: a.currentTime, paused: a.paused, title: player.current?.song?.title, offset: player.current?.stream?.song_offset }; })()"
        s1 = await page.evaluate(st)
        check(s1["mode"] == "highlight" and abs(s1["len"] - 42) < 0.1 and not s1["paused"], f"plays the 42 s highlight: {s1}")
        check(s1["offset"] is not None and s1["offset"] > 20, f"the window starts on the chorus, not the intro (song time {s1['offset']}s)")
        # progress bar moves smoothly and is exposed to screen readers
        await page.evaluate("document.getElementById('audio-player').currentTime = 21")
        await page.wait_for_timeout(700)
        bar = await page.evaluate("(() => { const el = document.querySelector('.hl-slide[data-i=\"0\"] .hl-progress'); return { w: parseFloat(el.firstElementChild.style.width), now: el.getAttribute('aria-valuenow'), max: el.getAttribute('aria-valuemax'), tr: getComputedStyle(el.firstElementChild).transitionProperty }; })()")
        check(45 < bar["w"] < 56 and bar["max"] == "42" and bar["now"] in ("21", "22") and "width" in bar["tr"], f"progress bar at half way, smooth, labelled: {bar}")
        ly = await page.evaluate("""(() => { const box = document.querySelector('.hl-slide[data-i="0"] .hl-lyrics'), on = box.querySelector('.ln.on');
            if (!on) return null; const b = box.getBoundingClientRect(), r = on.getBoundingClientRect();
            return { lines: box.querySelectorAll('.ln').length, scrolled: box.scrollTop > 0, visible: r.top >= b.top - 1 && r.bottom <= b.bottom + 1 }; })()""")
        check(ly and ly["lines"] > 6 and ly["scrolled"] and ly["visible"], f"half way through, the sung lyric line is scrolled into view: {ly}")
        await page.screenshot(path=os.path.join(E.TMP, "highlights_mid.png"))
        # the window ends -> next highlight starts by itself
        await page.evaluate("document.getElementById('audio-player').currentTime = 41.6")
        nxt = None
        for _ in range(30):
            nxt = await page.evaluate(st)
            if nxt["title"] != s1["title"]:
                break
            await page.wait_for_timeout(200)
        check(nxt["title"] != s1["title"] and nxt["mode"] == "highlight" and abs(nxt["len"] - 42) < 0.1, f"auto-advances to the next 42 s highlight: {s1['title']} -> {nxt['title']}")
        # a YouTube catalogue song: the embedded player jumps straight to the window (30 % into a 4-minute song)
        await page.evaluate("window.__ytLen = 600")
        yt = await page.evaluate("""async () => { const { player, media } = await import('/js/player.js');
            const s = (await (await fetch('/api/songs/search?q=kannada%20love%20song%203')).json()).songs[0];
            await player.play(s, { queue: [s], mode: 'highlight' }); await new Promise(r => setTimeout(r, 400));
            return { engine: media.engine, t: window.__ytp.t, start: player.current.start, end: player.current.end, vars: window.__ytp.o.playerVars }; }""")
        check(yt["engine"] == "youtube" and yt["start"] == 72 and yt["end"] == 114 and 71.9 < yt["t"] < 73, f"YouTube highlight starts at 72 s and stops at 114 s: {yt}")
        await page.screenshot(path=os.path.join(E.TMP, "highlights.png"))
        check(not errors, f"no page errors: {errors}")
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
    print(f"screenshot: {E.TMP}")
    sys.exit(1 if fails else 0)

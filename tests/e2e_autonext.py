"""Browser test for the YouTube mini-player and auto-advance (Playwright + Chrome).

YouTube's real IFrame API is replaced by a small fake that plays each video for
two seconds and then fires ENDED, so we can check, offline and quickly, that:
  * the docked player is visible and at least 200x200 px, the bar thumbnail is 64x64
  * ENDED moves to the next song, several times in a row
  * when the queue runs out, autoplay continues with similar songs
  * an unplayable video (embedding disabled) is skipped instead of stopping playback
  * with autoplay off, playback stops at the end of the queue

    python tests/e2e_autonext.py          (starts its own server on a temporary database)
"""
import asyncio, os, subprocess, sys, tempfile, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
PORT = 8833
BASE = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="pendel-e2e-")
ENV = {**os.environ, "DATABASE_PATH": os.path.join(TMP, "e2e.db"), "DATABASE_URL": "", "PORT": str(PORT), "PENDEL_FAKE_WORLD": "1",
       "WORLD_WARMUP": "0", "YOUTUBE_API_KEY": "", "PENDEL_QUIET": "1"}

FAKE_YT = r"""
(() => {
  const S = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };
  window.__played = []; window.__bad = new Set();
  class Player {
    constructor(id, o) {
      this.o = o; this.t = 0; this.state = -1;
      const f = document.createElement("iframe"); f.id = id; f.setAttribute("width", "100%"); f.setAttribute("height", "100%");
      document.getElementById(id).replaceWith(f);
      setTimeout(() => { o.events.onReady?.(); this._load(o.videoId, o.playerVars?.start || 0, o.playerVars?.autoplay); }, 20);
    }
    _set(s) { this.state = s; this.o.events.onStateChange?.({ data: s, target: this }); }
    _load(v, start, play) {
      clearInterval(this.iv); this.vid = v; this.t = start || 0; this._set(S.UNSTARTED);
      if (window.__bad.has(v)) { setTimeout(() => this.o.events.onError?.({ data: 150, target: this }), 50); return; }
      if (play) this.playVideo(); else this._set(S.CUED);
    }
    playVideo() {
      if (this.state === S.PLAYING) return;
      window.__played.push(this.vid); this._set(S.BUFFERING); this._set(S.PLAYING);
      clearInterval(this.iv);
      this.iv = setInterval(() => { this.t += 0.25; if (this.t >= 2) { clearInterval(this.iv); this._set(S.ENDED); } }, 250);
    }
    pauseVideo() { clearInterval(this.iv); this._set(S.PAUSED); }
    stopVideo() { clearInterval(this.iv); this._set(S.UNSTARTED); }
    loadVideoById(o) { this._load(o.videoId, o.startSeconds, true); }
    cueVideoById(o) { this._load(o.videoId, o.startSeconds, false); }
    seekTo(t) { this.t = t; } getCurrentTime() { return this.t; } getDuration() { return 2; }
    getVideoData() { return { video_id: this.vid }; } setVolume() {} mute() {} unMute() {}
  }
  window.YT = { Player, PlayerState: S };
  window.onYouTubeIframeAPIReady?.();
})();
"""


def setup_db():
    os.environ.update({k: v for k, v in ENV.items() if k != "PATH"})
    from server import db, youtube_catalog as y
    db.init()
    y.materialise([{"video_id": f"knVid{i:06d}", "title": f"Kannada Love Song {i}", "channel": f"Label {i % 3}", "channel_id": f"UC{i % 3}",
                    "duration": 240, "year": 2024, "lang": "kn", "moods": ["love"]} for i in range(12)])
    return [y.song_id(f"knVid{i:06d}") for i in range(12)]


async def main(ids):
    from playwright.async_api import async_playwright
    fails = []
    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
        page = await (await b.new_context(viewport={"width": 1440, "height": 900})).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=FAKE_YT))
        await page.goto(BASE + "/")
        await page.wait_for_selector(".rail .card")
        play = """async ([ids, i]) => { const { player } = await import('/js/player.js'); player.pause(); window.__played = [];
                    const songs = await Promise.all(ids.map(id => fetch('/api/songs/' + id).then(r => r.json()).then(d => d.song)));
                    await player.play(songs[i], { queue: songs, index: i }); }"""

        # 1. ENDED -> next, three times in a row, inside one queue
        await page.evaluate(play, [ids[:3], 0])
        await page.wait_for_timeout(900)
        await page.screenshot(path=os.path.join(TMP, "mini_player.png"))
        st0 = await page.evaluate("navigator.mediaSession.playbackState")
        check(st0 == "playing", f"lock-screen state playing while a song plays: {st0}")
        await page.wait_for_timeout(8100)
        played = await page.evaluate("window.__played")
        check(played[:3] == ["knVid000000", "knVid000001", "knVid000002"], f"queue advances on ENDED: {played}")

        # 2. sizes: docked player >= 200x200 and visible, bar thumbnail 64x64
        box = await page.evaluate("(() => { const r = document.querySelector('#yt-frame').getBoundingClientRect(); const d = document.getElementById('yt-dock'); return {w: r.width, h: r.height, hidden: d.hidden, vis: getComputedStyle(d).visibility} })()")
        art = await page.evaluate("(() => { const r = document.querySelector('.pl-art').getBoundingClientRect(); return [r.width, r.height] })()")
        check(box["w"] >= 200 and box["h"] >= 200 and not box["hidden"], f"YouTube player visible and >= 200x200: {box}")
        check(art == [64, 64], f"bar thumbnail is 64x64: {art}")
        ms = await page.evaluate("({t: navigator.mediaSession.metadata?.title, album: navigator.mediaSession.metadata?.album, art: (navigator.mediaSession.metadata?.artwork || []).map(a => a.sizes), state: navigator.mediaSession.playbackState})")
        check(ms["t"] and ms["t"].startswith("Kannada Love Song") and ms["album"] and len(ms["art"]) >= 4, f"lock-screen title, album and artwork sizes: {ms}")

        # 3. single song + autoplay: keeps going with similar songs after the queue ends
        await page.evaluate(play, [ids[5:6], 0])
        await page.wait_for_timeout(7500)
        played = await page.evaluate("window.__played")
        check(len(played) >= 3 and played[0] == "knVid000005" and len(set(played)) == len(played), f"autoplay continues after a one-song queue: {played}")

        # 4. an unplayable video is skipped
        await page.evaluate("window.__bad.add('knVid000007')")
        await page.evaluate(play, [[ids[7], ids[8]], 0])
        await page.wait_for_timeout(3500)
        played = await page.evaluate("window.__played")
        check(played[:1] == ["knVid000008"], f"embedding-disabled video skipped: {played}")

        # 5. autoplay off: stops at the end of the queue
        await page.evaluate("(async () => { const { player } = await import('/js/player.js'); player.setAutoplay(false); })()")
        await page.evaluate(play, [ids[9:10], 0])
        await page.wait_for_timeout(6000)
        played = await page.evaluate("window.__played")
        check(played == ["knVid000009"], f"autoplay off stops after the queue: {played}")
        st = await page.evaluate("navigator.mediaSession.playbackState")
        check(st == "paused", f"lock-screen state paused at queue end: {st}")
        sw = await page.evaluate("fetch('/sw.js').then(r => r.status)"); mf = await page.evaluate("fetch('/manifest.webmanifest').then(r => r.json()).then(m => [m.display, m.icons.length])")
        check(sw == 200 and mf[0] == "standalone" and mf[1] >= 6, f"PWA manifest + service worker served: {sw} {mf}")

        # 6. dock buttons: wide and other side
        await page.click("[data-dock=size]"); await page.click("[data-dock=side]")
        cls = await page.evaluate("document.getElementById('yt-dock').className")
        check("wide" in cls and "left" in cls, f"dock can be widened and moved: {cls}")
        # 7. instant search: results while typing (debounced), clear button, play all / add all to queue
        reqs = []
        page.on("request", lambda r: reqs.append(r.url) if "/api/songs/search" in r.url else None)
        await page.goto(BASE + "/search")
        await page.wait_for_selector(".isearch input")
        await page.type(".isearch input", "kannada love", delay=40)
        await page.wait_for_selector(".isearch-results .row[data-song-row]", timeout=5000)
        rows = await page.locator(".isearch-results .row[data-song-row]").count()
        check(rows >= 5, f"instant search shows results while typing: {rows} rows")
        check(len(reqs) <= 2, f"typing is debounced (requests sent: {len(reqs)})")
        check("q=kannada%20love" in page.url, f"URL follows the query: {page.url}")
        await page.evaluate("(async () => { const { player } = await import('/js/player.js'); player.pause(); player.queue = []; player.index = -1; })()")
        await page.click(".isearch [data-action=queue-all]")
        qlen = await page.evaluate("(async () => (await import('/js/player.js')).player.queue.length)()")
        check(qlen == rows, f"add all to queue: {qlen} of {rows}")
        await page.click(".isearch-clear")
        await page.wait_for_timeout(300)
        left = await page.locator(".isearch-results .row[data-song-row]").count()
        val = await page.input_value(".isearch input")
        check(left == 0 and val == "", f"clear button empties the box and results: {left} rows, value {val!r}")
        await page.fill(".isearch input", "zzzqqq"); await page.wait_for_timeout(600)
        check(await page.locator(".isearch-results .empty").count() == 1, "no-match message")
        # 8. songs with a direct audio file play in the native <audio> player (no YouTube), and advance on "ended"
        import json as _j
        def api(m, path, body=None, tok=None):
            h = {"Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})}
            with urllib.request.urlopen(urllib.request.Request(BASE + path, data=_j.dumps(body).encode() if body is not None else None, method=m, headers=h)) as r:
                return _j.loads(r.read())
        adm = api("POST", "/api/auth/login", {"email": "admin@pendel.local", "password": "Admin@12345"})["token"]
        demo = [x for x in api("GET", "/api/songs?limit=50")["songs"] if x["source"] == "pendel" and x["highlight"]][:2]
        for yt_id, d in zip(ids[10:12], demo):
            clip = api("GET", f"/api/stream/{d['id']}?kind=highlight")["url"]  # a short licensed demo file
            api("PATCH", f"/api/admin/songs/{yt_id}", {"audio_url": BASE + clip}, adm)
        await page.evaluate("(async () => { const { player } = await import('/js/player.js'); player.setAutoplay(false); })()")
        await page.evaluate(play, [ids[10:12], 0])
        await page.wait_for_timeout(1500)
        st = await page.evaluate("({dock: document.getElementById('yt-dock').hidden, src: document.getElementById('audio-player').getAttribute('src') || '', paused: document.getElementById('audio-player').paused, ms: navigator.mediaSession.playbackState, title: navigator.mediaSession.metadata?.title})")
        check(st["dock"] and "/media/" in st["src"] and not st["paused"] and st["ms"] == "playing", f"direct audio plays natively, no YouTube player: {st}")
        first = st["src"]
        await page.evaluate("(() => { const a = document.getElementById('audio-player'); a.currentTime = Math.max(0, a.duration - 0.5); })()")
        await page.wait_for_timeout(2500)
        st2 = await page.evaluate("({src: document.getElementById('audio-player').getAttribute('src') || '', title: navigator.mediaSession.metadata?.title})")
        check(st2["src"] != first and st2["title"] == "Kannada Love Song 11", f"native 'ended' moves to the next song: {st2}")
        check(not errors, f"no page errors: {errors}")
        await b.close()
    return fails


if __name__ == "__main__":
    ids = setup_db()
    srv = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.2)
        fails = asyncio.run(main(ids))
    finally:
        srv.terminate()
    print(f"screenshot: {os.path.join(TMP, 'mini_player.png')}")
    sys.exit(1 if fails else 0)

"""Browser test for "Hey Pendel" voice control (Playwright + Chrome).

The browser's SpeechRecognition is replaced by a fake that lets the test "say" phrases, so every command is
checked against the real player without a microphone:

    python tests/e2e_voice.py
"""
import asyncio, json, os, subprocess, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import e2e_autonext as E  # same temporary server set-up, fake YouTube API and songs

FAKE_SPEECH = r"""
(() => {
  window.__recs = [];
  class FakeRecognition {
    constructor() { this.continuous = false; this.interimResults = false; this.lang = ""; window.__recs.push(this); }
    start() { this.running = true; this.starts = (this.starts || 0) + 1; window.__rec = this; setTimeout(() => this.onstart?.(), 0); }
    stop() { this.running = false; setTimeout(() => this.onend?.(), 0); }
    abort() { this.running = false; setTimeout(() => this.onend?.(), 0); }
  }
  window.webkitSpeechRecognition = window.SpeechRecognition = FakeRecognition;
  window.__say = (text, isFinal = true) => {
    const r = window.__rec;
    const result = Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal });
    r.onresult?.({ resultIndex: 0, results: [result] });
  };
  window.__spoken = [];
  window.speechSynthesis.speak = (u) => window.__spoken.push(u.text);
})();
"""

PARSE_CASES = {
    "pause": "pause", "pause the song": "pause", "stop": "pause", "stop music": "pause",
    "play": "resume", "resume": "resume", "continue": "resume",
    "next": "next", "next song": "next", "change the song": "next", "change song": "next", "skip": "next", "skip track": "next",
    "previous": "prev", "previous song": "prev", "last song": "prev", "go back": "prev",
    "forward": "seek:10", "skip forward": "seek:10", "ahead": "seek:10", "fast forward": "seek:10",
    "forward 10 seconds": "seek:10", "10 sec forward": "seek:10", "forward twenty seconds": "seek:20", "skip 30 seconds": "seek:30",
    "forward 1 minute": "seek:60",
    "backward": "seek:-10", "back": "seek:-10", "rewind": "seek:-10", "10 sec backward": "seek:-10", "go back 10 seconds": "seek:-10",
    "rewind 5 seconds": "seek:-5",
    "repeat": "repeat-on", "repeat the song": "repeat-on", "repeat this song": "repeat-on", "loop": "repeat-on", "repeat on": "repeat-on",
    "repeat off": "repeat-off", "stop repeat": "repeat-off", "unloop": "repeat-off", "disable repeat": "repeat-off",
    "play kesariya": "play-song:kesariya", "play test track": "play-song:test track", "please play vennela daari": "play-song:vennela daari",
}
WAKE_CASES = {
    "hey pendel pause": "pause", "Hey Pendel, next song": "next song", "hi pendel play kesariya": "play kesariya",
    "pendel stop": "stop", "hey bundle skip": "skip", "hey candle forward 10 seconds": "forward 10 seconds",
    "hey pendle repeat": "repeat", "next song": None, "candle light": None, "hey pendel": "",
}


async def main(ids):
    from playwright.async_api import async_playwright
    fails = []
    def check(ok, msg):
        print(("PASS " if ok else "FAIL ") + msg)
        if not ok:
            fails.append(msg)
    def api(m, path, body=None, tok=None):
        h = {"Content-Type": "application/json", **({"Authorization": "Bearer " + tok} if tok else {})}
        with urllib.request.urlopen(urllib.request.Request(E.BASE + path, data=json.dumps(body).encode() if body is not None else None, method=m, headers=h)) as r:
            return json.loads(r.read())
    # three songs that stream from licensed demo audio files, so the native <audio> player is used
    adm = api("POST", "/api/auth/login", {"email": "admin@pendel.local", "password": "Admin@12345"})["token"]
    demo = [x for x in api("GET", "/api/songs?limit=50")["songs"] if x["source"] == "pendel" and x["highlight"]][:3]
    for yt_id, d in zip(ids[:3], demo):
        api("PATCH", f"/api/admin/songs/{yt_id}", {"audio_url": E.BASE + api("GET", f"/api/stream/{d['id']}?kind=highlight")["url"]}, adm)

    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
        page = await (await b.new_context(viewport={"width": 1440, "height": 900})).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_SPEECH)
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
        await page.goto(E.BASE + "/")
        await page.wait_for_selector(".rail .card")

        # parsing
        got = await page.evaluate("""async (cases) => { const v = await import('/js/voice.js'); const out = {};
            for (const c of Object.keys(cases)) { const r = v.parseCommand(c); out[c] = r.action + (r.action === 'seek' ? ':' + r.seconds : r.action === 'play-song' ? ':' + r.query : ''); }
            return out; }""", PARSE_CASES)
        bad = {k: (v, PARSE_CASES[k]) for k, v in got.items() if v != PARSE_CASES[k]}
        check(not bad, f"all {len(PARSE_CASES)} spoken commands map to the right action {bad or ''}")
        got = await page.evaluate("async (cases) => { const v = await import('/js/voice.js'); const o = {}; for (const c of Object.keys(cases)) o[c] = v.afterWake(c); return o; }", WAKE_CASES)
        bad = {k: (v, WAKE_CASES[k]) for k, v in got.items() if v != WAKE_CASES[k]}
        check(not bad, f"wake phrase detection and stripping {bad or ''}")

        # button: off -> listening
        btn = page.locator("#voice-btn")
        check(await btn.is_visible(), "mic button shown in the header")
        await btn.click()
        st = await page.evaluate("({state: document.getElementById('voice-btn').dataset.state, pref: localStorage.getItem('rf_voice'), lang: window.__rec.lang, cont: window.__rec.continuous, interim: window.__rec.interimResults})")
        check(st == {"state": "listening", "pref": "1", "lang": "en-IN", "cont": True, "interim": True}, f"click starts continuous en-IN listening: {st}")

        play = """async ([ids, i]) => { const { player } = await import('/js/player.js'); player.setAutoplay(false);
                    const songs = await Promise.all(ids.map(id => fetch('/api/songs/' + id).then(r => r.json()).then(d => d.song)));
                    await player.play(songs[i], { queue: songs, index: i }); }"""
        await page.evaluate(play, [ids[:3], 0])
        await page.wait_for_timeout(1200)
        state = "(() => { const a = document.getElementById('audio-player'); return { t: a.currentTime, paused: a.paused, vol: a.volume, title: navigator.mediaSession.metadata?.title }; })()"

        # wake word heard (interim) -> music ducked, then restored
        before = (await page.evaluate(state))["vol"]
        await page.evaluate("window.__say('hey pendel', false)")
        ducked = (await page.evaluate(state))["vol"]
        check(abs(ducked - before * 0.2) < 0.01 and await btn.get_attribute("data-state") == "triggered", f"wake word ducks music to 20% ({before} -> {ducked}) and shows 'triggered'")
        await page.evaluate("window.__say('hey pendel pause', true)")
        await page.wait_for_timeout(300)
        check((await page.evaluate(state))["paused"], "“Hey Pendel pause” pauses")
        await page.wait_for_timeout(1500)
        check(abs((await page.evaluate(state))["vol"] - before) < 0.01, "volume restored after ducking")
        await page.evaluate("window.__say('hey pendel play', true)"); await page.wait_for_timeout(400)
        check(not (await page.evaluate(state))["paused"], "“Hey Pendel play” resumes")

        # seeking
        await page.evaluate("document.getElementById('audio-player').currentTime = 2")
        await page.evaluate("window.__say('hey pendel forward 10 seconds', true)"); await page.wait_for_timeout(300)
        t1 = (await page.evaluate(state))["t"]
        await page.evaluate("window.__say('hey pendel 5 sec backward', true)"); await page.wait_for_timeout(300)
        t2 = (await page.evaluate(state))["t"]
        check(11.5 < t1 < 13.5 and 6.5 < t2 < 8.5, f"forward +10s then back -5s: {t1:.1f}s -> {t2:.1f}s")
        pill = await page.locator("#voice-pill").text_content()
        check("Back −5s" in pill, f"status pill shows the action: {pill!r}")

        # repeat
        await page.evaluate("window.__say('hey pendel repeat this song', true)"); await page.wait_for_timeout(200)
        rep = await page.evaluate("(async () => [(await import('/js/player.js')).player.repeat, document.querySelector('[data-action=repeat]').getAttribute('aria-label')])()")
        check(rep == ["one", "Repeat: one"], f"repeat on (player + button synced): {rep}")
        await page.evaluate("window.__say('hey pendel repeat off', true)"); await page.wait_for_timeout(200)
        rep = await page.evaluate("(async () => (await import('/js/player.js')).player.repeat)()")
        check(rep == "off", f"repeat off: {rep}")

        # next / previous
        t0 = (await page.evaluate(state))["title"]
        await page.evaluate("window.__say('hey pendel change the song', true)"); await page.wait_for_timeout(1200)
        t1 = (await page.evaluate(state))["title"]
        await page.evaluate("window.__say('hey pendel previous song', true)"); await page.wait_for_timeout(1200)
        t2 = (await page.evaluate(state))["title"]
        check(t0 == "Kannada Love Song 0" and t1 == "Kannada Love Song 1" and t2 == "Kannada Love Song 0", f"next then previous: {t0} -> {t1} -> {t2}")

        # wake word, a pause, then the command in the next phrase
        await page.evaluate("window.__say('hey pendel', true)"); await page.wait_for_timeout(200)
        await page.evaluate("window.__say('next song', true)"); await page.wait_for_timeout(1200)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 1", "“Hey Pendel” … “next song” as two phrases")
        # no wake phrase: ignored
        await page.evaluate("window.__say('next song', true)"); await page.wait_for_timeout(800)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 1", "commands without the wake phrase are ignored")

        # play a song by name, with a misheard wake word
        await page.evaluate("window.__spoken = []; window.__say('hey bundle play kannada love song 7', true)")
        for _ in range(45):  # free plan: a short ad break may come first
            if await page.evaluate("navigator.mediaSession.metadata?.title") == "Kannada Love Song 7":
                break
            await page.wait_for_timeout(200)
        await page.wait_for_timeout(500)
        await page.wait_for_timeout(300)
        now = await page.evaluate("(async () => { const { player } = await import('/js/player.js'); return { title: player.current?.song?.title, playing: player.playing }; })()")
        spoken = await page.evaluate("window.__spoken")
        check(now["title"] == "Kannada Love Song 7" and now["playing"], f"“Hey Pendel play <name>” searches and plays: {now['title']}")
        check("Playing Kannada Love Song 7" in spoken, f"spoken confirmation: {spoken}")
        await page.evaluate("window.__spoken = []; window.__say('hey pendel play zzqx nothing matches', true)")
        await page.wait_for_timeout(1200)
        check("Sorry, I couldn't find that song." in await page.evaluate("window.__spoken"), "not found: toast + spoken apology")

        # keeps listening: the browser ends recognition -> restarted
        n0 = await page.evaluate("window.__rec.starts")
        await page.evaluate("window.__rec.onend()"); await page.wait_for_timeout(500)
        check(await page.evaluate("window.__rec.starts") > n0, "recognition restarts by itself after it ends")
        # transient errors are silent
        toasts0 = await page.locator(".toast").count()
        await page.evaluate("window.__rec.onerror({error: 'no-speech'}); window.__rec.onerror({error: 'aborted'})")
        check(await page.locator(".toast").count() == toasts0 and await btn.get_attribute("data-state") != "off", "no-speech / aborted are ignored")
        # permission denied: voice off + one toast
        await page.evaluate("window.__rec.onerror({error: 'not-allowed'})"); await page.wait_for_timeout(200)
        check(await btn.get_attribute("data-state") == "off" and "Microphone blocked" in (await page.locator(".toast").last.text_content()), "mic blocked: turns off with a toast")

        # turning it off by click, and the preference
        await btn.click(); await btn.click()
        check(await page.evaluate("localStorage.getItem('rf_voice')") == "0" and await btn.get_attribute("data-state") == "off", "click again turns it off and remembers")
        # lock-screen controls are still wired
        ms = await page.evaluate("navigator.mediaSession.metadata?.album")
        check(ms is not None, f"lock-screen metadata still set (album {ms!r})")
        # the server lets this page use the microphone
        with urllib.request.urlopen(E.BASE + "/") as r:
            pp = r.headers.get("Permissions-Policy")
        check("microphone=(self)" in pp, f"Permissions-Policy allows the microphone for PENDEL: {pp}")
        check(not errors, f"no page errors: {errors}")
        await page.screenshot(path=os.path.join(E.TMP, "voice.png"))
        await b.close()
    return fails


if __name__ == "__main__":
    ids = E.setup_db()
    srv = subprocess.Popen([sys.executable, "-m", "server.app"], cwd=E.ROOT, env=E.ENV, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(E.BASE + "/api/config", timeout=1); break
            except Exception:
                time.sleep(0.2)
        fails = asyncio.run(main(ids))
    finally:
        srv.terminate()
    print(f"screenshot: {os.path.join(E.TMP, 'voice.png')}")
    sys.exit(1 if fails else 0)

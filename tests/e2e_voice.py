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
  window.__idx = 0;
  // speak one phrase: interim=true sends it as live (not yet final) text of the current phrase
  window.__say = (text, isFinal = true) => {
    const r = window.__rec;
    const results = [];
    results[window.__idx] = Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal });
    r.onresult?.({ resultIndex: window.__idx, results });
    if (isFinal) window.__idx += 1;
  };
  window.__gum = [];
  const real = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  window.__streams = [];
  navigator.mediaDevices.getUserMedia = async (c) => { window.__gum.push(c); const s = await real(c); window.__streams.push(s); return s; };
  window.__osc = 0;
  const osc = BaseAudioContext.prototype.createOscillator;
  BaseAudioContext.prototype.createOscillator = function () { window.__osc += 1; return osc.call(this); };
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
    "forward 10": "seek:10", "backward 10": "seek:-10", "back 10": "seek:-10", "another song": "next",
    "play the song": "resume", "play music": "resume", "play something": "resume", "play some music": "resume",
    "play another song": "next", "pause the song": "pause", "skip 15 seconds": "seek:15", "rewind 20 seconds": "seek:-20",
    "loop on": "repeat-on", "stop repeating": "repeat-off", "play believer": "play-song:believer",
    "volume up": "volume:0.2", "louder": "volume:0.2", "volume down": "volume:-0.2", "quieter": "volume:-0.2",
    "mute": "mute", "unmute": "unmute", "volume 50 percent": "volume-set:0.5",
}
WAKE_CASES = {
    "hey pendel pause": "pause", "Hey Pendel, next song": "next song", "hi pendel play kesariya": "play kesariya",
    "pendel stop": "stop", "hey bundle skip": "skip", "hey candle forward 10 seconds": "forward 10 seconds",
    "hey pendle repeat": "repeat", "next song": None, "hey pendel": "",
    "ok pendel next": "next", "yo pendel pause": "pause", "hey pedal pause": "pause", "hi pandal forward 10": "forward 10",
    "hey pencil next song": "next song", "okay panel skip": "skip", "hey handle repeat": "repeat", "heypendel stop": "stop",
    "pedal change the song": "change the song", "pendel": "", "i saw the panel": None, "turn the handle slowly": None,
    "heyy pendel next": "next", "hey panda pause": "pause", "ok pendle play kesariya": "play kesariya",
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
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox",
                                                                                     "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        page = await (await b.new_context(viewport={"width": 1440, "height": 900})).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.add_init_script(FAKE_SPEECH)
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
        await page.goto(E.BASE + "/")
        await page.wait_for_selector(".rail .card")

        # parsing
        got = await page.evaluate("""async (cases) => { const v = await import('/js/voice.js'); const out = {};
            for (const c of Object.keys(cases)) { const r = v.parseCommand(c); out[c] = r.action + (r.action === 'seek' ? ':' + r.seconds : r.action === 'play-song' ? ':' + r.query : r.action === 'volume' ? ':' + r.delta : r.action === 'volume-set' ? ':' + r.value : ''); }
            return out; }""", PARSE_CASES)
        bad = {k: (v, PARSE_CASES[k]) for k, v in got.items() if v != PARSE_CASES[k]}
        check(not bad, f"all {len(PARSE_CASES)} spoken commands map to the right action {bad or ''}")
        got = await page.evaluate("async (cases) => { const v = await import('/js/voice.js'); const o = {}; for (const c of Object.keys(cases)) o[c] = v.afterWake(c); return o; }", WAKE_CASES)
        bad = {k: (v, WAKE_CASES[k]) for k, v in got.items() if v != WAKE_CASES[k]}
        check(not bad, f"wake phrase detection and stripping {bad or ''}")

        # microphone already allowed (fake media UI) and never turned off: standby starts without a tap
        btn = page.locator("#voice-btn")
        check(await btn.is_visible(), "mic button shown in the header")
        await page.wait_for_timeout(500)
        check(await btn.get_attribute("data-state") == "listening", "auto-enabled on load when the microphone permission is already granted")
        # button: off, then on again
        await btn.click(); await page.wait_for_timeout(200)
        check(await btn.get_attribute("data-state") == "off" and await page.evaluate("localStorage.getItem('rf_voice')") == "0", "tap turns it off and remembers")
        await btn.click()
        await page.wait_for_timeout(500)
        st = await page.evaluate("({state: document.getElementById('voice-btn').dataset.state, pref: localStorage.getItem('rf_voice'), lang: window.__rec.lang, cont: window.__rec.continuous, interim: window.__rec.interimResults})")
        check(st == {"state": "listening", "pref": "1", "lang": "en-IN", "cont": True, "interim": True}, f"click starts continuous en-IN listening: {st}")
        gum = await page.evaluate("window.__gum")
        check(all(g["audio"] == {"echoCancellation": True, "noiseSuppression": True, "autoGainControl": True} for g in gum),
              f"any permission request uses echo cancellation, noise suppression, auto gain: {gum}")
        live = await page.evaluate("window.__streams.flatMap(s => s.getTracks()).filter(t => t.readyState !== 'ended').length")
        check(live == 0, f"no extra microphone stream is held open, so SpeechRecognition owns the mic ({live} live tracks)")

        # nothing loaded yet: "play something" starts popular music instead of failing
        await page.evaluate("window.__say('hey pendel play something', true)")
        started = None
        for _ in range(50):  # free plan: a short ad break may come first
            started = await page.evaluate("(async () => (await import('/js/player.js')).player.current?.song?.title || null)()")
            if started: break
            await page.wait_for_timeout(200)
        check(bool(started), f"“play something” with nothing loaded starts a popular song: {started}")
        await page.wait_for_timeout(2000)  # let the spoken "Playing …" reply finish (it dips the music too)

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
        check(abs(ducked - before * 0.15) < 0.01 and await btn.get_attribute("data-state") == "triggered", f"wake word ducks music to 15% ({before} -> {ducked}) and shows 'triggered'")
        pill = await page.locator("#voice-pill").text_content()
        check(pill == "🎙️ Listening for command...", f"pill shows the active window: {pill!r}")
        check(await page.evaluate("window.__osc") >= 2, f"two-tone activation chime played ({await page.evaluate('window.__osc')} oscillators)")
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

        # real time: quick commands run from the live (interim) text, once; "play" waits for the final text
        await page.evaluate("window.__say('hey pendel pause', false)"); await page.wait_for_timeout(250)
        check((await page.evaluate(state))["paused"], "“pause” runs from live speech, before the phrase ends")
        await page.evaluate("window.__say('hey pendel pause', true)")
        await page.evaluate("window.__say('hey pendel play', false)"); await page.wait_for_timeout(250)
        check((await page.evaluate(state))["paused"], "a bare “play” waits (it may become “play <song>”)")
        await page.evaluate("window.__say('hey pendel play', true)"); await page.wait_for_timeout(300)
        check(not (await page.evaluate(state))["paused"], "…and resumes on the final text")
        await page.evaluate("document.getElementById('audio-player').currentTime = 2")
        await page.evaluate("window.__say('hey pendel forward 10', false)"); await page.wait_for_timeout(250)
        await page.evaluate("window.__say('hey pendel forward 10 seconds', false)")
        await page.evaluate("window.__say('hey pendel forward 10 seconds', true)"); await page.wait_for_timeout(300)
        t = (await page.evaluate(state))["t"]
        check(11.5 < t < 13.5, f"“forward 10” from live speech, applied once despite 3 updates: {t:.1f}s")
        await page.evaluate("window.__say('hey pendel backward 10', false)"); await page.wait_for_timeout(250)
        t = (await page.evaluate(state))["t"]
        check(1.5 < t < 3.5, f"“backward 10” from live speech: {t:.1f}s")
        await page.evaluate("window.__say('hey pendel backward 10', true)")
        # bare phonetic wake word works for real commands; lyrics with "panel" are ignored
        await page.evaluate("window.__say('pedal pause', true)"); await page.wait_for_timeout(250)
        check((await page.evaluate(state))["paused"], "“pedal pause” (misheard wake word) pauses")
        await page.evaluate("window.__say('hey pendel resume', true)"); await page.wait_for_timeout(250)
        pill_before = await page.locator("#voice-pill").text_content()
        await page.evaluate("window.__say('panel of stars shining in the night', true)"); await page.wait_for_timeout(250)
        check(await page.locator("#voice-pill").text_content() == pill_before and not (await page.evaluate(state))["paused"], "song lyrics containing “panel” don't trigger anything")

        # next / previous
        t0 = (await page.evaluate(state))["title"]
        await page.evaluate("window.__say('hey pendel change the song', false)"); await page.wait_for_timeout(300)
        await page.evaluate("window.__say('hey pendel change the song', true)"); await page.wait_for_timeout(1200)
        t1 = (await page.evaluate(state))["title"]
        await page.evaluate("window.__say('hey pendel previous song', true)"); await page.wait_for_timeout(1200)
        t2 = (await page.evaluate(state))["title"]
        check(t0 == "Kannada Love Song 0" and t1 == "Kannada Love Song 1" and t2 == "Kannada Love Song 0", f"“change the song” (live + final = one skip), then previous: {t0} -> {t1} -> {t2}")

        # wake word, a pause, then the command in the next phrase
        await page.evaluate("window.__say('hey pendel', true)"); await page.wait_for_timeout(200)
        await page.evaluate("window.__say('next song', true)")
        for _ in range(60):  # free plan: an ad break may come before the next full song
            if (await page.evaluate(state))["title"] == "Kannada Love Song 1": break
            await page.wait_for_timeout(200)
        await page.wait_for_timeout(300)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 1", "“Hey Pendel” … “next song” as two phrases")
        # no wake phrase: ignored
        await page.evaluate("window.__say('next song', true)"); await page.wait_for_timeout(800)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 1", "commands without the wake phrase are ignored")
        # the window shows live words, and stays open across phrases
        await page.evaluate("window.__say('hey pendel', true)"); await page.wait_for_timeout(100)
        await page.evaluate("window.__say('play', false)"); await page.wait_for_timeout(100)
        pill = await page.locator("#voice-pill").text_content()
        check(pill == "🎙️ Heard: play", f"live words shown while listening: {pill!r}")
        await page.evaluate("window.__say('pause', true)"); await page.wait_for_timeout(300)
        check((await page.evaluate(state))["paused"], "command from a later phrase inside the 7 s window runs")
        await page.evaluate("window.__say('hey pendel resume', true)"); await page.wait_for_timeout(300)
        # the window closes after 7 s
        await page.evaluate("window.__say('hey pendel', true)"); await page.wait_for_timeout(7400)
        st = await page.evaluate("(async () => (await import('/js/voice.js')).voiceState())()")
        await page.evaluate("window.__say('pause', true)"); await page.wait_for_timeout(300)
        check(not st["active"] and not (await page.evaluate(state))["paused"] and abs((await page.evaluate(state))["vol"] - before) < 0.01,
              f"after 7 s of silence it returns to standby, volume restored, and plain words are ignored ({st})")

        # volume
        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(0.5))()")
        await page.evaluate("window.__say('hey pendel volume up', true)"); await page.wait_for_timeout(200)
        v1 = (await page.evaluate(state))["vol"]
        await page.evaluate("window.__say('hey pendel quieter', true)"); await page.wait_for_timeout(1300)  # 1.2 s repeat guard
        await page.evaluate("window.__say('hey pendel turn it down', true)"); await page.wait_for_timeout(200)
        v2 = (await page.evaluate(state))["vol"]
        pill = await page.locator("#voice-pill").text_content()
        slider = await page.evaluate("document.querySelector('.volume').value")
        check(abs(v1 - 0.7) < 0.01 and abs(v2 - 0.3) < 0.01 and pill == "🎙️ Volume 30%" and slider == "30", f"volume up/down ±20% with the % shown: {v1} -> {v2}, {pill!r}, slider {slider}")
        await page.evaluate("window.__say('hey pendel mute', true)"); await page.wait_for_timeout(200)
        muted = await page.evaluate("document.getElementById('audio-player').muted")
        await page.evaluate("window.__say('hey pendel unmute', true)"); await page.wait_for_timeout(200)
        check(muted and not await page.evaluate("document.getElementById('audio-player').muted"), "mute / unmute")
        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(" + str(before) + "))()")

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

        # one-song queue: "next" still moves on (similar / popular songs are added)
        await page.evaluate(play, [ids[2:3], 0]); await page.wait_for_timeout(900)
        await page.evaluate("window.__say('hey pendel next song', true)")
        moved = None
        for _ in range(40):
            moved = await page.evaluate("(async () => { const { player } = await import('/js/player.js'); return [player.queue.length, player.current?.song?.title]; })()")
            if moved[0] > 1 and moved[1] != "Kannada Love Song 2":
                break
            await page.wait_for_timeout(200)
        check(moved[0] > 1 and moved[1] != "Kannada Love Song 2", f"“next song” with a one-song queue still advances: {moved}")

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

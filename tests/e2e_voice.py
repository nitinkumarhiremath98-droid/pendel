"""Browser test for tap-to-talk voice control (Playwright + Chrome): Mic Off by default, tap the mic, say one
command, the mic switches itself off.

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
    "search arijit singh": "search:arijit singh", "search for love songs": "search:love songs", "find kesariya": "search:kesariya",
    "stop listening": "voice-off", "cancel": "voice-off",
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
        errors, console_errors = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        await page.add_init_script(FAKE_SPEECH)
        await page.route("https://www.youtube.com/iframe_api", lambda r: r.fulfill(status=200, content_type="text/javascript", body=E.FAKE_YT))
        await page.goto(E.BASE + "/")
        await page.wait_for_selector(".rail .card")

        # parsing
        got = await page.evaluate("""async (cases) => { const v = await import('/js/voice.js'); const out = {};
            for (const c of Object.keys(cases)) { const r = v.parseCommand(c); out[c] = r.action + (r.action === 'seek' ? ':' + r.seconds : (r.action === 'play-song' || r.action === 'search') ? ':' + r.query : r.action === 'volume' ? ':' + r.delta : r.action === 'volume-set' ? ':' + r.value : ''); }
            return out; }""", PARSE_CASES)
        bad = {k: (v, PARSE_CASES[k]) for k, v in got.items() if v != PARSE_CASES[k]}
        check(not bad, f"all {len(PARSE_CASES)} spoken commands map to the right action {bad or ''}")
        got = await page.evaluate("async (cases) => { const v = await import('/js/voice.js'); const o = {}; for (const c of Object.keys(cases)) o[c] = v.afterWake(c); return o; }", WAKE_CASES)
        bad = {k: (v, WAKE_CASES[k]) for k, v in got.items() if v != WAKE_CASES[k]}
        check(not bad, f"an optional “Hey Pendel” in front is stripped {bad or ''}")

        vstate = lambda: page.evaluate("document.documentElement.dataset.voice")
        btn = page.locator("#voice-btn")
        pill = page.locator("#voice-pill")
        ptext = lambda: pill.text_content()
        info = """(() => { const b = document.getElementById('voice-btn'); const r = window.__rec;
            return { state: b.dataset.state, pressed: b.getAttribute('aria-pressed'), title: b.title, label: b.querySelector('.voice-label').textContent,
                     icon: b.querySelector('use').getAttribute('href').split('#')[1], recs: window.__recs.length, running: !!(r && r.running),
                     cont: r?.continuous, interim: r?.interimResults, lang: r?.lang }; })()"""

        # Mic Off on open: nothing listening, no prompt, nothing on screen
        await page.wait_for_timeout(500)
        st = await page.evaluate(info)
        check(await btn.is_visible() and st["state"] == "off" and st["pressed"] == "false" and st["label"] == "Mic Off" and st["icon"] == "mic-off"
              and "Mic Off" in st["title"], f"Mic Off button in the header: {st}")
        check(st["recs"] == 0 and await page.evaluate("window.__gum.length") == 0 and await pill.is_hidden(),
              f"no speech recognition, no microphone request and no pill until the mic is tapped ({st['recs']} recognisers)")

        # tap ON: listening, live pill, chime; tap again: OFF at once
        await btn.click(); await page.wait_for_timeout(150)
        st = await page.evaluate(info)
        check(st["state"] == "listening" and st["pressed"] == "true" and st["label"] == "Mic On" and st["icon"] == "mic" and st["title"] == "Mic On — Tap to stop"
              and st["running"] and st["cont"] is False and st["interim"] is True and st["lang"] == "en-IN", f"tap: Mic On, one-phrase en-IN recognition with live text {st}")
        check(await ptext() == "Mic On — Tap to stop" and await pill.is_visible(), "pill says “Mic On — Tap to stop”")
        check(await page.evaluate("window.__osc") >= 2, "a short chime confirms the mic is on")
        await btn.click(); await page.wait_for_timeout(150)
        st = await page.evaluate(info)
        check(st["state"] == "off" and not st["running"] and await ptext() == "Mic off", f"tap again: Mic Off, recognition stopped at once {st}")
        await page.evaluate("window.__say('pause', true)"); await page.wait_for_timeout(200)
        check(await vstate() == "off", "speech after Mic Off is ignored")

        async def speak(text, interim=None, wait=300):
            await btn.click(); await page.wait_for_timeout(100)
            if interim:
                await page.evaluate("t => window.__say(t, false)", interim); await page.wait_for_timeout(100)
            if text is not None:
                await page.evaluate("t => window.__say(t, true)", text)
            await page.wait_for_timeout(wait)

        # nothing loaded yet: "play something" starts popular music, then the mic is off again
        await speak("play something", wait=200)
        started = None
        for _ in range(50):  # free plan: a short ad break may come first
            started = await page.evaluate("(async () => (await import('/js/player.js')).player.current?.song?.title || null)()")
            if started: break
            await page.wait_for_timeout(200)
        st = await page.evaluate(info)
        check(bool(started) and st["state"] == "off" and not st["running"], f"“play something” starts a popular song ({started}) and the mic turns itself off")
        await page.wait_for_timeout(2000)  # spoken "Playing …" reply finished

        play = """async ([ids, i]) => { const { player } = await import('/js/player.js'); player.setAutoplay(false);
                    const songs = await Promise.all(ids.map(id => fetch('/api/songs/' + id).then(r => r.json()).then(d => d.song)));
                    await player.play(songs[i], { queue: songs, index: i }); }"""
        await page.evaluate(play, [ids[:3], 0])
        await page.wait_for_timeout(1200)
        state = "(() => { const a = document.getElementById('audio-player'); return { t: a.currentTime, paused: a.paused, vol: a.volume, title: navigator.mediaSession.metadata?.title }; })()"

        # music dips while the mic is on; "pause" runs from the live text; one command, then off
        before = (await page.evaluate(state))["vol"]
        await btn.click(); await page.wait_for_timeout(100)
        ducked = (await page.evaluate(state))["vol"]
        check(abs(ducked - before * 0.15) < 0.01, f"music dips to 15% while listening ({before} -> {ducked})")
        await page.evaluate("window.__say('pause', false)"); await page.wait_for_timeout(300)
        st, a = await page.evaluate(info), await page.evaluate(state)
        check(a["paused"] and st["state"] == "off" and not st["running"] and abs(a["vol"] - before) < 0.01, f"“pause” pauses, mic off, volume back ({a['vol']})")
        check(await ptext() == "Paused", f"confirmation: {await ptext()!r}")
        await page.evaluate("window.__say('next song', true)"); await page.wait_for_timeout(300)
        t_after = (await page.evaluate(state))["title"]
        check(t_after == "Kannada Love Song 0" and (await page.evaluate(state))["paused"], f"only one command per tap: later speech is ignored ({t_after})")
        await page.wait_for_timeout(1700)
        check(await pill.is_hidden(), "the pill slides away after the confirmation")

        await speak("play")
        check(not (await page.evaluate(state))["paused"], "“play” resumes")
        await speak("hey pendel pause")
        check((await page.evaluate(state))["paused"], "a leading “Hey Pendel” is fine (stripped)")
        await speak("resume")

        await speak("next song", wait=200)
        for _ in range(60):  # free plan: an ad break may come before the next full song
            if (await page.evaluate(state))["title"] == "Kannada Love Song 1": break
            await page.wait_for_timeout(200)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 1", "“next song”")
        await speak("previous song", wait=800)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 0", "“previous song”")

        await page.evaluate("document.getElementById('audio-player').currentTime = 3")
        await speak("forward 10 seconds")
        t1 = (await page.evaluate(state))["t"]
        await speak("backward 5 seconds")
        t2 = (await page.evaluate(state))["t"]
        check(t1 > 12.5 and abs((t1 - t2) - 5) < 1, f"forward 10 / backward 5: 3 -> {t1:.1f} -> {t2:.1f}")

        await speak("repeat this song")
        r1 = await page.evaluate("(async () => (await import('/js/player.js')).player.repeat)()")
        await speak("repeat off")
        r2 = await page.evaluate("(async () => (await import('/js/player.js')).player.repeat)()")
        check((r1, r2) == ("one", "off"), f"repeat on / off: {r1}, {r2}")

        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(0.5))()")
        await speak("volume up")
        v1 = (await page.evaluate(state))["vol"]
        await speak("quieter"); await speak("turn it down")
        v2 = (await page.evaluate(state))["vol"]
        p = await ptext()
        check(abs(v1 - 0.7) < 0.01 and abs(v2 - 0.3) < 0.01 and p == "Volume 30%", f"volume up / down by 20% with the % shown: {v1} -> {v2}, {p!r}")
        await speak("mute")
        m1 = await page.evaluate("document.getElementById('audio-player').muted")
        await speak("unmute")
        check(m1 and not await page.evaluate("document.getElementById('audio-player').muted"), "mute / unmute")
        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(" + str(before) + "))()")

        # play by name waits for the whole name; YouTube catalogue songs obey the same commands
        await page.evaluate("window.__ytLen = 600; window.__spoken = []")
        await speak(None, interim="play kannada", wait=200)
        live = await ptext()
        check(live == "“play kannada”" and await vstate() == "listening", f"live transcript while speaking: {live!r}")
        await page.evaluate("window.__say('play kannada love song 7', true)")
        for _ in range(60):
            if (await page.evaluate(state))["title"] == "Kannada Love Song 7": break
            await page.wait_for_timeout(200)
        check((await page.evaluate(state))["title"] == "Kannada Love Song 7" and await vstate() == "off", "“play kannada love song 7” plays it, then mic off")
        check("Playing Kannada Love Song 7" in await page.evaluate("window.__spoken"), "spoken confirmation")
        yts = "(async () => { const { player, media } = await import('/js/player.js'); const y = window.__ytp; return { engine: media.engine, playing: player.playing, state: y.state, t: y.t, vol: y.vol, muted: y.muted }; })()"
        check((await page.evaluate(yts))["engine"] == "youtube", "Kannada Love Song 7 plays in the YouTube player")
        await page.wait_for_timeout(2000)
        await speak("pause"); y1 = await page.evaluate(yts)
        await speak("play the song"); y2 = await page.evaluate(yts)
        check(y1["state"] == 2 and y2["state"] == 1 and y2["playing"], f"YouTube: pause then “play the song”: {y1['state']} -> {y2['state']}")
        await page.evaluate("window.__ytp.t = 30")
        await speak("forward 20 seconds"); f = (await page.evaluate(yts))["t"]
        await speak("backward 10 seconds"); g = (await page.evaluate(yts))["t"]
        check(49 < f < 52 and 39 < g < 42, f"YouTube: forward 20 / back 10: 30 -> {f:.1f} -> {g:.1f}")
        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(0.6))()")
        await speak("volume down"); v = (await page.evaluate(yts))["vol"]
        await speak("mute"); mm = (await page.evaluate(yts))["muted"]
        await speak("unmute")
        check(v == 40 and mm and not (await page.evaluate(yts))["muted"], f"YouTube: volume 60 -> {v}, mute, unmute")
        await page.evaluate("(async () => (await import('/js/player.js')).setVolume(" + str(before) + "))()")

        # search
        await speak("search kannada love", wait=600)
        check(await page.evaluate("location.pathname + location.search") == "/search?q=kannada%20love", f"“search …” opens search: {await page.evaluate('location.href')}")

        # errors end quietly in the pill: no toast banners
        toasts0 = await page.locator(".toast").count()
        await btn.click(); await page.wait_for_timeout(100)
        await page.evaluate("window.__rec.onerror({error: 'no-speech'})"); await page.wait_for_timeout(150)
        p1, s1 = await ptext(), await vstate()
        await btn.click(); await page.wait_for_timeout(100)
        await page.evaluate("window.__rec.onerror({error: 'not-allowed'})"); await page.wait_for_timeout(150)
        p2, s2 = await ptext(), await vstate()
        check(s1 == "off" and "Didn't hear anything" in p1 and s2 == "off" and "Microphone blocked" in p2 and await page.locator(".toast").count() == toasts0,
              f"no-speech / not-allowed: mic off with a short note, no error banner ({p1!r}, {p2!r})")
        await btn.click(); await page.wait_for_timeout(100)
        await page.evaluate("window.__rec.onend()"); await page.wait_for_timeout(150)
        check(await vstate() == "off", "the browser ending the phrase with nothing said switches the mic off")
        await btn.click(); await page.wait_for_timeout(100)
        await page.keyboard.press("Escape"); await page.wait_for_timeout(150)
        check(await vstate() == "off", "Escape turns the mic off")
        await btn.click(); await page.wait_for_timeout(100)
        await page.evaluate("window.__say('blah blah blah', true)"); await page.wait_for_timeout(200)
        check(await vstate() == "off" and "Didn't catch that" in await ptext(), "an unknown command: “Didn't catch that”, mic off")
        await btn.click(); await page.wait_for_timeout(8400)
        st = await page.evaluate(info)
        check(st["state"] == "off" and not st["running"], "the mic switches itself off after 8 s of silence")
        check(await page.evaluate("(async () => (await import('/js/voice.js')).voiceState())()") == {"isMicEnabled": False, "handled": False, "heard": "", "state": "off"},
              "no timers or state left over")

        # mobile: icon-only button
        await page.set_viewport_size({"width": 390, "height": 844}); await page.wait_for_timeout(200)
        box = await btn.bounding_box()
        check(box and box["width"] <= 40 and await page.locator("#voice-btn .voice-label").is_hidden(), f"phones: compact mic icon ({box and box['width']}px)")
        await page.set_viewport_size({"width": 1440, "height": 900})
        check(not errors, f"no page errors: {errors}")
        own = [m for m in console_errors if "net::ERR_" not in m]  # the test sandbox has no internet: outside images fail to load
        check(not own, f"no console errors: {own}")
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

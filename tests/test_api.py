"""API test suite. Runs against a throwaway database.

    python -m unittest tests.test_api -v
"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request

TMP = tempfile.mkdtemp(prefix="pendel-test-")
os.environ.update({"DATABASE_PATH": os.path.join(TMP, "test.db"), "PORT": "8799", "PENDEL_QUIET": "1", "PAYMENT_PROVIDERS": "sandbox",
                   "PENDEL_FAKE_WORLD": "1", "WORLD_WARMUP": "0", "YOUTUBE_API_KEY": "",
                   # never touch a real database from .env: TEST_DATABASE_URL=postgresql://... runs the suite on PostgreSQL
                   "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", ""), "MONGO_URI": "", "SECRET_KEY": ""})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app, db, config, security  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

BASE = "http://127.0.0.1:8799"
re_junk = re.compile(r"trailer|jukebox", re.I).search


def call(method, path, body=None, token=None, raw=None, ctype=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); headers["Content-Type"] = "application/json"
    if raw is not None:
        data = raw; headers["Content-Type"] = ctype
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def multipart(fields, files):
    """(body, content-type) for a multipart/form-data upload."""
    b = "----pendeltest" + os.urandom(6).hex()
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, (fname, ctype, data) in files.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n"
    return out + f"--{b}--\r\n".encode(), f"multipart/form-data; boundary={b}"


def wav_bytes(seconds=45, rate=8000):
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(rate); w.writeframes(b"\x80" * rate * seconds)
    return buf.getvalue()


PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class PENDELAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if db.BACKEND == "postgres":
            db.run("DROP SCHEMA public CASCADE")
            db.run("CREATE SCHEMA public")
        db.init()
        security.register_limiter.limit = 1000  # many sign-ups from one test IP
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 8799), app.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.admin = call("POST", "/api/auth/login", {"email": "admin@pendel.local", "password": "Admin@12345"})[1]["token"]
        st, d = call("POST", "/api/auth/register", {"email": "t@example.com", "password": "Passw0rd!", "display_name": "Tester", "preferred_languages": ["te", "hi"]})
        assert st == 200, d
        cls.user = d["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(TMP, ignore_errors=True)

    def test_home_has_all_rails(self):
        st, d = call("GET", "/api/home")
        self.assertEqual(st, 200)
        keys = [r["key"] for r in d["rails"]]
        for k in ["trending", "popular", "recent", "top20", "recommended", "sad", "love", "heartbreak", "romantic", "feelings", "emotional",
                  "motivational", "devotional", "dj", "party", "dance", "friendship", "melody", "old-classic", "new-generation",
                  "instrumental", "background", "independent", "international"]:
            self.assertIn(k, keys)
        self.assertEqual(len(next(r for r in d["rails"] if r["key"] == "top20")["songs"]), 20)

    def test_every_song_has_language_and_valid_highlight(self):
        for s in db.q("SELECT s.id, s.language_code, h.start_sec, h.end_sec, s.duration_sec FROM songs s LEFT JOIN song_highlights h ON h.song_id=s.id WHERE s.status='published' AND s.source='pendel'"):
            self.assertTrue(s["language_code"])
            self.assertIsNotNone(s["start_sec"])
            self.assertTrue(40 <= s["end_sec"] - s["start_sec"] <= 45, s)
            self.assertGreater(s["start_sec"], 20, "highlight must not default to the opening")
            self.assertLessEqual(s["end_sec"], s["duration_sec"] - 5, "ends at least 5 s before the song does")

    def test_auto_highlight_follows_synced_lyrics_and_upgrade(self):
        # a song with a direct audio link gets a structural 42 s window, then moves onto the chorus once lyrics arrive
        body = {"title": "Hook Finder", "artist_id": db.one("SELECT id FROM artists LIMIT 1")["id"], "language_code": "en",
                "audio_url": "https://cdn.example.com/hook.mp3", "duration_sec": 200,
                "license": {"licensor": "Test", "license_type": "direct", "allow_free_full_stream": True, "allow_lyrics": True}}
        st, d = call("POST", "/api/admin/songs", body, token=self.admin)
        self.assertEqual(st, 200, d)
        sid = d["song"]["id"]
        db.run("UPDATE songs SET duration_sec=200 WHERE id=?", (sid,))
        call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": "https://cdn.example.com/hook.mp3"}, token=self.admin)
        h = db.one("SELECT * FROM song_highlights WHERE song_id=?", (sid,))
        self.assertEqual((h["start_sec"], h["end_sec"], h["source"]), (60.0, 102.0, "auto"), "30 % into a 200 s song, 42 s long")
        lrc = "\n".join(f"[{m:02d}:{s_:02d}.00] {t}" for m, s_, t in [(0, 20, "verse one"), (0, 30, "verse two"), (0, 50, "we are the chorus"),
                                                                     (0, 56, "singing all night"), (1, 30, "verse three"), (1, 50, "we are the chorus"), (2, 40, "we are the chorus")])
        self.assertEqual(call("PUT", f"/api/admin/songs/{sid}/lyrics", {"text": lrc, "source": "Test"}, token=self.admin)[0], 200)
        h = db.one("SELECT * FROM song_highlights WHERE song_id=?", (sid,))
        self.assertEqual((h["start_sec"], h["end_sec"]), (49.4, 91.4), "starts on the first chorus, just before the first word")
        sug = call("GET", f"/api/admin/songs/{sid}/highlight/suggest", token=self.admin)[1]["suggestion"]
        self.assertEqual((sug["start"], sug["method"]), (49.4, "lyrics-chorus"))
        # the one-time upgrade moves old 25 s automatic windows and keeps hand-set ones
        auto_sid = db.one("SELECT h.song_id FROM song_highlights h JOIN songs s ON s.id=h.song_id WHERE s.is_demo=1 AND s.slug='rise-again'")["song_id"]
        db.run("UPDATE song_highlights SET start_sec=74.18, end_sec=100.36, source='auto' WHERE song_id=?", (auto_sid,))  # the old 26 s window
        db.run("UPDATE song_highlights SET start_sec=70.1, end_sec=95.1, source='manual' WHERE song_id=?", (sid,))
        db.run("DELETE FROM settings WHERE key='highlights_version'")
        db.upgrade_highlights()
        a = db.one("SELECT h.start_sec, h.end_sec, h.source, h.preview_asset_id FROM song_highlights h WHERE h.song_id=?", (auto_sid,))
        self.assertEqual((a["start_sec"], a["end_sec"], a["source"]), (34.31, 76.31, "editorial"), "Rise Again: its first chorus (“We rise again”)")
        self.assertTrue(a["preview_asset_id"], "the demo's re-cut 42 s preview file is kept")
        self.assertEqual(db.one("SELECT start_sec, end_sec FROM song_highlights WHERE song_id=?", (sid,)), {"start_sec": 70.1, "end_sec": 95.1})

    def test_highlight_window_rules(self):
        from server import highlights as H
        self.assertEqual(H.DEFAULT_LEN, 42)
        for dur, at in [(200, 60), (300, 90), (420, 105), (80, 12)]:  # 30 % / 25 % / 15 %
            w = H.smart_window(dur)
            self.assertAlmostEqual(w["start"], at, delta=0.01, msg=dur); self.assertAlmostEqual(w["end"] - w["start"], 42, delta=0.01)
        w = H.smart_window(60)                  # short: clamped so it ends 5 s before the end
        self.assertEqual((w["start"], w["end"]), (9.0, 51.0))
        w = H.smart_window(80, peak=70)
        self.assertEqual((w["start"], w["end"]), (33.0, 75.0), "never closer than 5 s to the end")
        self.assertEqual(H.smart_window(30), {"start": 0.0, "end": 30.0, "method": "heuristic", "confidence": 0.2})
        self.assertEqual(H.smart_window(240, peak=100)["start"], 95.0, "5 s before the most-replayed peak")
        self.assertEqual(H.smart_window(240, peak=230)["start"], 193.0, "clamped to the end")
        lines = [{"t": 12, "text": "Intro line"}, {"t": 20, "text": "Verse"}, {"t": 40, "text": "Oh oh, the hook!"}, {"t": 70, "text": "verse"},
                 {"t": 90, "text": "oh oh the hook"}, {"t": 150, "text": "Oh oh, the HOOK"}]
        self.assertEqual(H.smart_window(240, lyrics=lines)["start"], 39.4)
        self.assertEqual(H.smart_window(240, lyrics=[{"t": 33, "text": "only once"}])["method"], "lyrics-vocals")
        self.assertIsNone(H.validate_window(10, 52, 200)); self.assertIsNotNone(H.validate_window(10, 35, 200))
        self.assertIsNone(H.validate_window(0, 30, 30), "a 30 s song plays whole")

    def test_highlight_feed_and_preview_asset(self):
        st, d = call("GET", "/api/highlights?feed=sad")
        self.assertEqual(st, 200)
        self.assertTrue(d["items"])
        it = d["items"][0]
        self.assertEqual(it["stream"]["mode"], "preview")
        self.assertAlmostEqual(it["stream"]["end"] - it["stream"]["start"], 42, delta=0.05)
        self.assertIn("sig=", it["stream"]["url"])
        self.assertTrue(all("sad" in [c["slug"] for c in i["categories"]] for i in d["items"]))
        # range request on the signed URL
        req = urllib.request.Request(BASE + it["stream"]["url"], headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 206); self.assertEqual(len(r.read()), 100)

    def test_unsigned_audio_is_forbidden(self):
        try:
            urllib.request.urlopen(BASE + "/media/audio/full/neon-pulse.mp3")
            self.fail("expected 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_premium_only_song_is_restricted_for_free(self):
        st, d = call("GET", "/api/stream/focus-tide?kind=full", token=self.user)
        self.assertEqual(d["mode"], "restricted")
        self.assertEqual(d["preview"]["mode"], "preview")

    def test_language_page_and_search(self):
        st, d = call("GET", "/api/languages/te")
        self.assertEqual(st, 200)
        self.assertTrue(all(s["language"]["code"] == "te" for sec in d["sections"] for s in sec["songs"]))
        st, d = call("GET", "/api/search?q=tamil")
        self.assertTrue(d["songs"] and all(s["language"]["code"] == "ta" for s in d["songs"]))
        st, d = call("GET", "/api/search?q=" + urllib.parse.quote("बिखर"))
        self.assertTrue(d["lyrics_matches"])
        st, d = call("GET", "/api/search/suggest?q=neo")
        self.assertEqual(d["suggestions"][0]["label"], "Neon Pulse")

    def test_lyrics_and_translation(self):
        st, d = call("GET", "/api/songs/dil-ki-raah/lyrics")
        self.assertTrue(d["available"] and d["synced"])
        self.assertEqual(d["translations"][0]["language"], "en")
        st, d = call("GET", "/api/songs/raga-at-dawn/lyrics")
        self.assertFalse(d["available"])

    def test_auth_validation(self):
        self.assertEqual(call("POST", "/api/auth/register", {"email": "bad", "password": "x", "display_name": "x"})[0], 400)
        self.assertEqual(call("POST", "/api/auth/register", {"email": "t@example.com", "password": "Passw0rd!", "display_name": "Dup"})[0], 409)
        self.assertEqual(call("POST", "/api/auth/login", {"email": "t@example.com", "password": "wrong"})[0], 401)
        self.assertEqual(call("GET", "/api/me/favourites")[0], 401)
        self.assertEqual(call("GET", "/api/me/favourites", token="garbage.token")[0], 401)

    def test_favourites_history_recommendations_auto_playlists(self):
        st, s = call("GET", "/api/songs/toota-sitara")
        sid = s["song"]["id"]
        self.assertEqual(call("PUT", f"/api/me/favourites/{sid}", token=self.user)[0], 200)
        self.assertEqual(call("POST", "/api/me/history", {"song_id": sid, "seconds": 95, "context": "full", "completed": True}, token=self.user)[0], 200)
        st, d = call("GET", "/api/me/favourites", token=self.user)
        self.assertIn(sid, [x["id"] for x in d["songs"]])
        st, d = call("GET", "/api/me/auto-playlists/sad", token=self.user)
        self.assertIn(sid, [x["id"] for x in d["songs"]])
        st, d = call("GET", "/api/me/recommendations?mood=heartbreak", token=self.user)
        self.assertTrue(d["songs"])
        self.assertIn("heartbreak", [c["slug"] for c in d["songs"][0]["categories"]])

    def test_playlist_lifecycle(self):
        ids = [s["id"] for s in call("GET", "/api/songs?limit=3")[1]["songs"]]
        st, d = call("POST", "/api/playlists", {"title": "Road trip", "song_ids": ids[:2]}, token=self.user)
        pid = d["playlist"]["id"]
        self.assertEqual(call("POST", f"/api/playlists/{pid}/songs", {"song_id": ids[2]}, token=self.user)[1]["added"], True)
        self.assertEqual(call("PUT", f"/api/playlists/{pid}/order", {"song_ids": [ids[2], ids[0], ids[1]]}, token=self.user)[0], 200)
        self.assertEqual([s["id"] for s in call("GET", f"/api/playlists/{pid}", token=self.user)[1]["songs"]], [ids[2], ids[0], ids[1]])
        self.assertEqual(call("GET", f"/api/playlists/{pid}")[0], 404, "private playlist hidden from others")
        call("PATCH", f"/api/playlists/{pid}", {"is_public": True}, token=self.user)
        self.assertEqual(call("GET", f"/api/playlists/{pid}")[0], 200)
        self.assertEqual(call("POST", f"/api/playlists/{pid}/follow", {}, token=self.admin)[0], 200)
        self.assertEqual(call("PATCH", f"/api/playlists/{pid}", {"title": "hijack"}, token=self.admin)[0], 403)
        self.assertEqual(call("DELETE", f"/api/playlists/{pid}/songs/{ids[0]}", token=self.user)[0], 200)
        self.assertEqual(call("DELETE", f"/api/playlists/{pid}", token=self.user)[0], 200)

    def test_skip_limit_for_free_users(self):
        st, d = call("POST", "/api/auth/register", {"email": "skip@example.com", "password": "Passw0rd!", "display_name": "Skipper"})
        tok = d["token"]
        res = [call("POST", "/api/me/skip", {}, token=tok)[1]["allowed"] for _ in range(7)]
        self.assertEqual(res, [True] * 6 + [False])

    def test_sandbox_subscription_flow(self):
        st, d = call("POST", "/api/auth/register", {"email": "pay@example.com", "password": "Passw0rd!", "display_name": "Payer"})
        tok = d["token"]
        st, co = call("POST", "/api/subscriptions/checkout", {"plan_id": "premium-monthly", "provider": "sandbox"}, token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(call("POST", "/api/subscriptions/checkout", {"plan_id": "premium-monthly", "provider": "razorpay"}, token=tok)[0], 400)
        st, v = call("POST", "/api/subscriptions/verify", {"payment_id": co["payment_id"], "outcome": "success", "method": "upi"}, token=tok)
        self.assertEqual(v["user"]["plan"], "premium")
        self.assertEqual(call("GET", "/api/stream/focus-tide?kind=full", token=tok)[1]["mode"], "full")
        self.assertTrue(call("POST", "/api/me/skip", {}, token=tok)[1]["allowed"])

    def test_admin_permissions(self):
        self.assertEqual(call("GET", "/api/admin/stats", token=self.user)[0], 403)
        st, d = call("POST", "/api/auth/register", {"email": "analyst@example.com", "password": "Passw0rd!", "display_name": "Ana"})
        uid = d["user"]["id"]
        self.assertEqual(call("PUT", f"/api/admin/admins/{uid}", {"role": "analyst"}, token=self.admin)[0], 200)
        tok = call("POST", "/api/auth/login", {"email": "analyst@example.com", "password": "Passw0rd!"})[1]["token"]
        self.assertEqual(call("GET", "/api/admin/stats", token=tok)[0], 200)
        self.assertEqual(call("GET", "/api/admin/songs", token=tok)[0], 403)
        self.assertEqual(call("PATCH", "/api/admin/settings", {"free_skips_per_hour": 1}, token=tok)[0], 403)

    def test_admin_song_upload_highlight_lyrics_takedown(self):
        artist = call("POST", "/api/admin/artists", {"name": "Test Band", "country": "India"}, token=self.admin)[1]["artist"]
        st, d = call("POST", "/api/admin/songs", {"title": "No Licence", "artist_id": artist["id"], "language_code": "te"}, token=self.admin)
        self.assertEqual(st, 400, "licensing is mandatory")
        body = {"title": "Test Track", "artist_id": artist["id"], "language_code": "te", "categories": ["love", "melody"], "genre": "Pop",
                "license": {"licensor": "Test Records", "license_type": "label", "allow_free_full_stream": True, "allow_lyrics": True}}
        st, d = call("POST", "/api/admin/songs", body, token=self.admin)
        self.assertEqual(st, 200, d)
        sid = d["song"]["id"]
        self.assertEqual(call("PATCH", f"/api/admin/songs/{sid}", {"status": "published"}, token=self.admin)[0], 400, "can't publish without audio")
        with open(os.path.join(config.MEDIA_DIR, "audio/full/dil-ki-raah.mp3"), "rb") as f:
            audio = f.read()
        st, up = call("POST", f"/api/admin/songs/{sid}/audio", raw=audio, ctype="audio/mpeg", token=self.admin)
        self.assertEqual(st, 200, up)
        self.assertTrue(up["highlight_applied"])
        hl = up["song"]["highlight"]
        self.assertAlmostEqual(hl["end"] - hl["start"], 42, delta=0.05)
        self.assertGreater(hl["start"], 20, "not the intro")
        self.assertEqual(call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 10, "end": 35}, token=self.admin)[0], 400, "25 s is too short now")
        self.assertEqual(call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 10, "end": 60}, token=self.admin)[0], 400, "50 s is too long")
        st, d = call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 70.1, "end": 112.1}, token=self.admin)
        self.assertEqual((d["song"]["highlight"]["start"], d["song"]["highlight"]["source"]), (70.1, "manual"))
        st, d = call("PUT", f"/api/admin/songs/{sid}/lyrics", {"text": "[01:10.10] first line\n[01:25.00] second line", "source": "Test Records lyric licence"}, token=self.admin)
        self.assertEqual(db.one("SELECT source FROM song_highlights WHERE song_id=?", (sid,))["source"], "manual", "adding lyrics never moves a hand-set highlight")
        self.assertEqual((st, d["synced"]), (200, True))
        self.assertEqual(call("PATCH", f"/api/admin/songs/{sid}", {"status": "published"}, token=self.admin)[0], 200)
        st, feed = call("GET", "/api/highlights?feed=love&limit=30")
        item = next(i for i in feed["items"] if i["id"] == sid)
        self.assertEqual(item["lyrics_excerpt"][0]["text"], "first line")
        self.assertEqual(item["stream"]["mode"], "preview")
        # takedown hides it everywhere and blocks streaming
        self.assertEqual(call("POST", f"/api/admin/songs/{sid}/takedown", {"claimant": "Rights Co", "reason": "Ref 123"}, token=self.admin)[0], 200)
        self.assertEqual(call("GET", f"/api/stream/{sid}")[0], 451)
        self.assertNotIn(sid, [s["id"] for s in call("GET", "/api/songs?limit=100")[1]["songs"]])
        self.assertEqual(call("POST", f"/api/admin/songs/{sid}/restore", {"reason": "withdrawn"}, token=self.admin)[0], 200)

    def test_csv_import(self):
        csv_text = "title,artist,language_code,categories,licensor,license_type,allow_lyrics\nCSV Song,CSV Artist,ta,sad;melody,Indie Dist,distributor,0\nBad,X,zz,,L,label,0\n"
        st, d = call("POST", "/api/admin/import/csv", {"csv": csv_text, "dry_run": True}, token=self.admin)
        self.assertEqual((d["imported"], d["failed"]), (1, 1))
        st, d = call("POST", "/api/admin/import/csv", {"csv": csv_text}, token=self.admin)
        self.assertEqual(d["imported"], 1)
        self.assertEqual(db.one("SELECT status FROM songs WHERE id=?", (d["results"][0]["song_id"],))["status"], "draft")

    def test_uploads_reject_wrong_types(self):
        self.assertEqual(call("PUT", "/api/me/avatar", raw=b"<svg onload=alert(1)>", ctype="image/svg+xml", token=self.user)[0], 415)
        self.assertEqual(call("PUT", "/api/me/avatar", raw=b"not a png", ctype="image/png", token=self.user)[0], 415)

    def test_seo_page_and_sitemap(self):
        with urllib.request.urlopen(BASE + "/song/dil-ki-raah") as r:
            page = r.read().decode()
        self.assertIn("<title>Dil Ki Raah - Kabir Lights", page)
        self.assertIn('"@type": "MusicRecording"', page)
        with urllib.request.urlopen(BASE + "/sitemap.xml") as r:
            self.assertIn("/language/te", r.read().decode())


    # ------------------------------------------------------------------ legal & privacy rights
    def test_legal_pages_are_served_with_titles(self):
        for path, title in [("/legal", "Legal"), ("/legal/terms", "Terms of Service"), ("/legal/privacy", "Privacy Policy"),
                            ("/legal/copyright", "Copyright &amp; DMCA Policy"), ("/legal/refunds", "Refund &amp; Cancellation Policy"),
                            ("/legal/contact", "Contact &amp; Grievance Officer")]:
            with urllib.request.urlopen(BASE + path) as r:
                page = r.read().decode()
            self.assertIn(f"<title>{title} | PENDEL</title>", page, path)
            self.assertIn('id="view"', page)
        with urllib.request.urlopen(BASE + "/js/legal.js") as r:
            js = r.read().decode()
        for needle in ("[Your Name]", "[Your Contact Email]", "[Bengaluru / Karnataka, India]", "Section 79", "17 U.S.C.", "24–36 hours",
                       "Digital Personal Data Protection Act, 2023", "100% of the master", "non-refundable", "YouTube Terms of Service"):
            self.assertIn(needle, js)
        with urllib.request.urlopen(BASE + "/sw.js") as r:
            self.assertIn("/js/legal.js", r.read().decode())

    def test_export_my_data(self):
        self.assertEqual(call("GET", "/api/me/export")[0], 401)
        st, d = call("POST", "/api/auth/register", {"email": "export@example.com", "password": "Passw0rd!", "display_name": "Exporter", "preferred_languages": ["te"]})
        tok = d["token"]
        sid = call("GET", "/api/songs/toota-sitara")[1]["song"]["id"]
        call("PUT", f"/api/me/favourites/{sid}", token=tok)
        call("POST", "/api/me/history", {"song_id": sid, "seconds": 40, "context": "full"}, token=tok)
        call("POST", "/api/playlists", {"title": "Mine", "song_ids": [sid]}, token=tok)
        st, x = call("GET", "/api/me/export", token=tok)
        self.assertEqual(st, 200)
        self.assertEqual(x["account"]["email"], "export@example.com")
        self.assertEqual(x["account"]["preferred_languages"], ["te"])
        self.assertNotIn("password_hash", json.dumps(x))
        self.assertEqual(len(x["favourites"]), 1)
        self.assertGreaterEqual(len(x["listening_history"]), 1)
        self.assertEqual(x["playlists"][0]["title"], "Mine")
        self.assertEqual(len(x["playlists"][0]["songs"]), 1)

    def test_delete_my_account_anonymises_and_keeps_payments(self):
        st, d = call("POST", "/api/auth/register", {"email": "gone@example.com", "password": "Passw0rd!", "display_name": "Gone"})
        tok, uid = d["token"], d["user"]["id"]
        sid = call("GET", "/api/songs/toota-sitara")[1]["song"]["id"]
        call("PUT", f"/api/me/favourites/{sid}", token=tok)
        call("POST", "/api/playlists", {"title": "Bye", "song_ids": [sid]}, token=tok)
        co = call("POST", "/api/subscriptions/checkout", {"plan_id": "premium-monthly", "provider": "sandbox"}, token=tok)[1]
        call("POST", "/api/subscriptions/verify", {"payment_id": co["payment_id"], "outcome": "success", "method": "upi"}, token=tok)
        self.assertEqual(call("DELETE", "/api/me", {"password": "wrong"}, token=tok)[0], 400)
        self.assertEqual(call("DELETE", "/api/me", {"password": "Passw0rd!"}, token=self.admin)[0], 400, "admins can't self-delete")
        self.assertEqual(call("DELETE", "/api/me", {"password": "Passw0rd!"}, token=tok)[0], 200)
        self.assertEqual(call("GET", "/api/auth/me", token=tok)[1].get("user"), None)
        self.assertEqual(call("POST", "/api/auth/login", {"email": "gone@example.com", "password": "Passw0rd!"})[0], 401)
        u = db.one("SELECT * FROM users WHERE id=?", (uid,))
        self.assertEqual((u["display_name"], u["password_hash"], u["is_active"]), ("Deleted user", None, 0))
        self.assertNotIn("gone@", u["email"])
        self.assertEqual(db.one("SELECT COUNT(*) n FROM favourites WHERE user_id=?", (uid,))["n"], 0)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM playlists WHERE owner_id=?", (uid,))["n"], 0)
        self.assertEqual(db.one("SELECT COUNT(*) n FROM payments WHERE user_id=?", (uid,))["n"], 1, "payment record kept for tax law")
        self.assertEqual(db.one("SELECT COUNT(*) n FROM subscriptions WHERE user_id=? AND status='active'", (uid,))["n"], 0)
        st, d = call("POST", "/api/auth/register", {"email": "gone@example.com", "password": "Passw0rd!", "display_name": "Back"})
        self.assertEqual(st, 200, "the email can be used again")

    # ------------------------------------------------------------------ v2: world catalogue + mood sessions
    def test_world_language_page_tags_language(self):
        st, d = call("GET", "/api/languages/ta")
        world = next(sec for sec in d["sections"] if sec["key"] == "world")["songs"]
        self.assertTrue(world)
        self.assertTrue(all(s["language"]["code"] == "ta" and s["is_external"] and s["source"] == "itunes" for s in world))
        self.assertEqual(world[0]["highlight"]["source"], "provider")
        self.assertFalse(world[0]["premium_only"])

    def test_world_language_filter_is_strict(self):
        st, d = call("GET", "/api/languages/kn")
        world = next(sec for sec in d["sections"] if sec["key"] == "world")["songs"]
        self.assertTrue(world)
        self.assertTrue(all(s["genre"] == "Kannada" for s in world), {s["genre"] for s in world})
        every_kn = [s for sec in d["sections"] for s in sec["songs"] if s["is_external"]]
        self.assertTrue(all(s["language"]["code"] == "kn" for s in every_kn))

    def test_fix_languages_repairs_wrong_labels(self):
        from server import external
        sid = call("GET", "/api/world/mood?lang=te&mood=love")[1]["songs"][0]["id"]
        db.run("UPDATE songs SET language_code='kn' WHERE id=?", (sid,))  # simulate a v2.0 mislabel
        self.assertGreaterEqual(external.fix_languages(), 1)
        self.assertEqual(db.one("SELECT language_code FROM songs WHERE id=?", (sid,))["language_code"], "te")

    def test_world_song_streams_preview_and_no_lyrics(self):
        sid = call("GET", "/api/world/mood?lang=hi&mood=love")[1]["songs"][0]["id"]
        st, hl = call("GET", f"/api/stream/{sid}?kind=highlight")
        self.assertEqual((hl["mode"], hl["provider"]), ("preview", "Apple Music"))
        st, full = call("GET", f"/api/stream/{sid}?kind=full")
        self.assertEqual((full["mode"], full["reason"]), ("restricted", "external_full"))
        self.assertIn("youtube.com/results", full["links"]["youtube"])
        self.assertFalse(call("GET", f"/api/songs/{sid}/lyrics")[1]["available"])

    def test_world_songs_work_with_favourites_and_playlists(self):
        sid = call("GET", "/api/world/mood?lang=te&mood=sad")[1]["songs"][0]["id"]
        self.assertEqual(call("PUT", f"/api/me/favourites/{sid}", token=self.user)[0], 200)
        st, d = call("POST", "/api/playlists", {"title": "World mix", "song_ids": [sid]}, token=self.user)
        self.assertEqual(d["songs"][0]["id"], sid)
        self.assertIn(sid, [x["id"] for x in call("GET", "/api/me/auto-playlists/sad", token=self.user)[1]["songs"]])

    def test_world_in_search_feed_home(self):
        st, d = call("GET", "/api/search?q=bollywood%20track")
        self.assertTrue(any(s["is_external"] for s in d["songs"]))
        st, d = call("GET", "/api/highlights?feed=devotional&lang=kn")
        self.assertTrue(any(i["is_external"] for i in d["items"]))
        self.assertTrue(all("devotional" in [c["slug"] for c in i["categories"]] for i in d["items"]))
        st, d = call("GET", "/api/home")
        chart = next(r for r in d["rails"] if r["key"] == "world-chart")
        self.assertEqual(len(chart["songs"]), 30)

    def test_one_hour_highlight_session(self):
        st, d = call("GET", "/api/sessions/mood?mood=sad&langs=hi,te&minutes=60&mode=highlights")
        self.assertEqual(st, 200, d)
        self.assertTrue(d["complete"])
        self.assertGreaterEqual(d["total_seconds"], 3600)
        self.assertLess(d["total_seconds"], 3600 + 31)
        langs = [s["language"]["code"] for s in d["songs"][:6]]
        self.assertIn("hi", langs); self.assertIn("te", langs)
        self.assertTrue(all("sad" in [c["slug"] for c in s["categories"]] for s in d["songs"]))
        self.assertEqual(len({s["id"] for s in d["songs"]}), len(d["songs"]), "no repeats")

    def test_full_session_explains_missing_youtube(self):
        st, d = call("GET", "/api/sessions/mood?mood=sad&langs=ko&minutes=60&mode=full")
        self.assertFalse(d["complete"])
        self.assertIn("YouTube API key", d["note"])
        self.assertEqual(call("GET", "/api/sessions/mood?mood=nope&mode=full")[0], 400)

    def test_youtube_full_song_and_custom_highlight(self):
        from server import config as cfg
        cfg.YOUTUBE_API_KEY = "test-key"
        try:
            sid = call("GET", "/api/world/mood?lang=pa&mood=party")[1]["songs"][0]["id"]
            st, full = call("GET", f"/api/stream/{sid}?kind=full")
            self.assertEqual((full["mode"], full["video_id"]), ("youtube", "abcDEF12345"))
            st, d = call("GET", "/api/sessions/mood?mood=party&langs=pa&minutes=30&mode=full")
            self.assertTrue(d["complete"], d["note"])
            # admin sets a video by link and a custom 42 s window on it
            self.assertEqual(call("PATCH", f"/api/admin/songs/{sid}", {"youtube_id": "https://youtu.be/XYZxyz12345"}, token=self.admin)[0], 200)
            self.assertEqual(call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 60, "end": 102}, token=self.admin)[0], 200)
            st, hl = call("GET", f"/api/stream/{sid}?kind=highlight")
            self.assertEqual((hl["mode"], hl["video_id"], hl["start"], hl["end"]), ("youtube", "XYZxyz12345", 60.0, 102.0))
            self.assertEqual(call("PATCH", f"/api/admin/songs/{sid}", {"youtube_id": "not a video"}, token=self.admin)[0], 400)
        finally:
            cfg.YOUTUBE_API_KEY = ""

    # ------------------------------------------------------------------ v3: YouTube full-song catalogue
    def test_youtube_import_and_full_song_playback(self):
        from server import config as cfg, youtube_catalog as ytc
        self.assertEqual(call("POST", "/api/admin/youtube/starter", {}, token=self.admin)[0], 400, "needs a key")
        cfg.YOUTUBE_API_KEY = "test-key"
        units_before = ytc.usage()["units"]
        try:
            st, d = call("POST", "/api/admin/youtube/import", {"source": "kannada sad songs", "lang": "kn", "mood": "sad", "max": 300}, token=self.admin)
            self.assertEqual(st, 200, d)
            ytc._q.join()
            st, info = call("GET", "/api/admin/youtube", token=self.admin)
            job = next(j for j in info["jobs"] if j["id"] == d["job_id"])
            self.assertEqual(job["status"], "done", job)
            self.assertGreater(job["added"], 100)
            self.assertLessEqual(info["usage"]["units"] - units_before, 20, "playlist imports must use cheap 1-unit calls")
            kn = call("GET", "/api/languages/kn")[1]
            full = next(sec for sec in kn["sections"] if sec["key"] == "youtube")["songs"]
            self.assertTrue(full and all(x["source"] == "youtube" and x["language"]["code"] == "kn" for x in full))
            self.assertFalse(any(re_junk(x["title"]) for x in full))
            sid = full[0]["id"]
            st, f = call("GET", f"/api/stream/{sid}?kind=full")
            self.assertEqual(f["mode"], "youtube"); self.assertEqual(f["start"], 0)
            st, h = call("GET", f"/api/stream/{sid}?kind=highlight")
            self.assertEqual(h["mode"], "youtube"); self.assertAlmostEqual(h["end"] - h["start"], 42, delta=0.05)
            self.assertLessEqual(h["end"], full[0]["duration"] - 5)
            self.assertIn("sad", [c["slug"] for c in full[0]["categories"]])
        finally:
            cfg.YOUTUBE_API_KEY = ""

    def test_full_song_session_uses_youtube_without_key(self):
        from server import youtube_catalog as ytc
        ytc.materialise([{"video_id": f"vid{i:08d}", "title": f"Kannada Love Song {i}", "channel": "Label", "channel_id": "UCx", "duration": 240,
                          "year": 2024, "lang": "kn", "moods": ["love"]} for i in range(30)])
        st, d = call("GET", "/api/sessions/mood?mood=love&langs=kn&minutes=60&mode=full")
        self.assertTrue(d["complete"], d["note"])
        self.assertTrue(any(x["source"] == "youtube" for x in d["songs"]))

    def test_youtube_snapshot_round_trip(self):
        from server import youtube_catalog as ytc
        ytc.materialise([{"video_id": "snapVID0001", "title": "Telugu Melody", "channel": "Label", "channel_id": "UCy", "duration": 200,
                          "year": 2023, "lang": "te", "moods": ["melody"]}])
        st, r = call("POST", "/api/admin/youtube/snapshot", {}, token=self.admin)
        self.assertGreaterEqual(r["songs"], 1)
        sid = ytc.song_id("snapVID0001")
        db.run("DELETE FROM songs WHERE id=?", (sid,))
        self.assertGreaterEqual(ytc.load_snapshot(), 1)
        row = db.one("SELECT language_code, youtube_id FROM songs WHERE id=?", (sid,))
        self.assertEqual((row["language_code"], row["youtube_id"]), ("te", "snapVID0001"))
        os.remove(ytc.SNAPSHOT)

    def test_youtube_quota_guard(self):
        from server import config as cfg, youtube_catalog as ytc
        cfg.YOUTUBE_API_KEY = "test-key"
        try:
            db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"yt_units_{ytc._today()}", "9500"))
            with self.assertRaises(ytc.QuotaExceeded):
                ytc.call("videos", {"id": "x"})
        finally:
            cfg.YOUTUBE_API_KEY = ""
            db.run("DELETE FROM settings WHERE key LIKE 'yt_units_%'")

    def test_world_offline_degrades_gracefully(self):
        from server import external
        real = external.FETCH
        def boom(url):
            raise OSError("network down")
        external.FETCH = boom
        try:
            t = time.time()
            st, d = call("GET", "/api/world/mood?lang=th&mood=melody")
            self.assertEqual((st, d["songs"]), (200, []))
            self.assertLess(time.time() - t, 5)
            self.assertIn("network down", call("GET", "/api/world/status")[1]["last_error"])
        finally:
            external.FETCH = real
            external._down_until[0] = 0

    def test_instant_song_search(self):
        # case-insensitive partial match on title, ranked: the exact/prefix title match comes first
        st, d = call("GET", "/api/songs/search?q=VENNELA")
        self.assertEqual(st, 200, d)
        self.assertEqual(d["songs"][0]["title"], "Vennela Daari")
        # artist + language words together (every word must match something)
        st, d = call("GET", "/api/songs/search?q=aarav%20telugu")
        self.assertTrue(d["songs"] and all(x["artist"]["name"] == "Aarav Kiran" for x in d["songs"]), d)
        # mood names and native language names match too
        st, d = call("GET", "/api/songs/search?q=" + urllib.parse.quote("తెలుగు"))
        self.assertTrue(d["songs"] and all(x["language"]["code"] == "te" for x in d["songs"]))
        # limits, empty and odd input
        st, d = call("GET", "/api/songs/search?q=a&limit=5")
        self.assertLessEqual(len(d["songs"]), 5)
        self.assertEqual(call("GET", "/api/songs/search?q=a&limit=abc")[0], 200)
        self.assertEqual(call("GET", "/api/songs/search?q=")[1]["songs"], [])
        self.assertEqual(call("GET", "/api/songs/search?q=%25%25")[1]["songs"], [], "% is plain text, not a wildcard")
        self.assertEqual(call("GET", "/api/songs/search?q=" + "x" * 101)[0], 400)
        # the old /api/songs/<id> route still works
        sid = call("GET", "/api/songs?limit=1")[1]["songs"][0]["id"]
        self.assertEqual(call("GET", f"/api/songs/{sid}")[0], 200)

    def test_direct_audio_url_plays_natively(self):
        from server import youtube_catalog as ytc
        ytc.materialise([{"video_id": "dirAudio001", "title": "Direct Audio Song", "channel": "Indie Label", "channel_id": "UCdir",
                          "duration": 200, "year": 2025, "lang": "or", "moods": ["love"]}])
        sid = ytc.song_id("dirAudio001")
        # before: YouTube embed
        self.assertEqual(call("GET", f"/api/stream/{sid}")[1]["mode"], "youtube")
        # only admins may set it
        self.assertEqual(call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": "https://cdn.example.com/a.mp3"}, token=self.user)[0], 403)
        # YouTube audio, bad schemes and non-audio files are refused
        for bad in ("https://www.youtube.com/watch?v=dirAudio001", "https://rr3---sn-abc.googlevideo.com/videoplayback?id=1",
                    "http://cdn.example.com/a.mp3", "javascript:alert(1)", "https://cdn.example.com/page.html"):
            st, d = call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": bad}, token=self.admin)
            self.assertEqual(st, 400, (bad, d))
        st, d = call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": "https://cdn.example.com/songs/direct.m4a",
                                                          "cover_url": "https://cdn.example.com/covers/direct.jpg"}, token=self.admin)
        self.assertEqual(st, 200, d)
        card = d["song"]
        self.assertEqual((card["audio_url"], card["audio_format"], card["playback"]), ("https://cdn.example.com/songs/direct.m4a", "m4a", "audio"))
        self.assertEqual(card["cover_url"], "https://cdn.example.com/covers/direct.jpg")
        # after: native audio, full song and a highlight range on the same file
        st, full = call("GET", f"/api/stream/{sid}")
        self.assertEqual((full["mode"], full["url"], full["format"]), ("full", "https://cdn.example.com/songs/direct.m4a", "m4a"))
        st, hl = call("GET", f"/api/stream/{sid}?kind=highlight")
        self.assertEqual(hl["mode"], "range")
        self.assertEqual(hl["url"], full["url"])
        # listings carry the new fields
        found = call("GET", "/api/songs/search?q=direct%20audio%20song")[1]["songs"][0]
        self.assertEqual((found["audio_url"], found["playback"]), (full["url"], "audio"))
        # a format can be given for links without an extension; removing the link goes back to YouTube
        st, d = call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": "https://cdn.example.com/stream/123", "audio_format": "opus"}, token=self.admin)
        self.assertEqual((st, d["song"]["audio_format"]), (200, "opus"))
        call("PATCH", f"/api/admin/songs/{sid}", {"audio_url": None}, token=self.admin)
        self.assertEqual(call("GET", f"/api/stream/{sid}")[1]["mode"], "youtube")

    def test_csv_import_with_audio_url(self):
        csv_text = ("title,artist,language_code,licensor,license_type,allow_free_full_stream,audio_url,cover_url,duration_sec,categories\n"
                    "Indie Night Drive,Indie Artist Test,or,Indie Artist Test,direct,1,https://cdn.example.com/indie/night.mp3,https://cdn.example.com/indie/night.jpg,215,love\n")
        st, d = call("POST", "/api/admin/import/csv", {"csv": csv_text}, token=self.admin)
        self.assertEqual((st, d.get("imported")), (200, 1), d)
        song = call("GET", "/api/songs/search?q=indie%20night%20drive")[1]["songs"][0]
        self.assertEqual((song["status"], song["playback"], song["duration"]), ("published", "audio", 215))
        s = call("GET", f"/api/stream/{song['id']}")[1]
        self.assertEqual((s["mode"], s["url"]), ("full", "https://cdn.example.com/indie/night.mp3"))
        # YouTube links are refused in imports too
        bad = csv_text.replace("https://cdn.example.com/indie/night.mp3", "https://youtu.be/abcdefghijk").replace("Indie Night Drive", "Other")
        self.assertEqual(call("POST", "/api/admin/import/csv", {"csv": bad}, token=self.admin)[1].get("imported", 0), 0)

    def test_upload_track_local_and_r2(self):
        audio = ("demo-song.wav", "audio/wav", wav_bytes())
        form = {"title": "Upload Test Song", "artist": "Upload Test Artist", "language_code": "kn", "moods": "love",
                "rights_confirmed": "1", "duration": "45"}
        body, ct = multipart(form, {"audio": audio, "cover": ("c.png", "image/png", PNG_1PX)})
        # admins only
        self.assertEqual(call("POST", "/api/songs/upload", raw=body, ctype=ct)[0], 401)
        self.assertEqual(call("POST", "/api/songs/upload", raw=body, ctype=ct, token=self.user)[0], 403)
        # the rights promise is required, and the file must really be audio
        b2, c2 = multipart({**form, "rights_confirmed": "0"}, {"audio": audio})
        self.assertEqual(call("POST", "/api/songs/upload", raw=b2, ctype=c2, token=self.admin)[0], 400)
        b3, c3 = multipart(form, {"audio": ("fake.mp3", "audio/mpeg", b"<html>not audio</html>")})
        self.assertEqual(call("POST", "/api/songs/upload", raw=b3, ctype=c3, token=self.admin)[0], 415)
        b4, c4 = multipart(form, {"audio": ("song.exe", "application/octet-stream", wav_bytes(1))})
        self.assertEqual(call("POST", "/api/songs/upload", raw=b4, ctype=c4, token=self.admin)[0], 415)
        # no R2 settings: stored locally, published, plays natively
        st, d = call("POST", "/api/songs/upload", raw=body, ctype=ct, token=self.admin)
        self.assertEqual((st, d.get("stored_in")), (200, "local"), d)
        song = d["song"]
        self.assertEqual((song["status"], song["playback"], song["duration"], song["audio_format"]), ("published", "audio", 45, "wav"))
        self.assertTrue(song["highlight"])
        lic = db.one("SELECT licensor, license_type, allow_free_full_stream FROM licensing WHERE song_id=?", (song["id"],))
        self.assertEqual((lic["licensor"], lic["license_type"], lic["allow_free_full_stream"]), ("Upload Test Artist", "direct", 1))
        s = call("GET", f"/api/stream/{song['id']}")[1]
        self.assertEqual((s["mode"], s["url"]), ("full", song["audio_url"]))
        with urllib.request.urlopen(BASE + song["audio_url"]) as r:
            self.assertEqual(r.read()[:4], b"RIFF")
        found = call("GET", "/api/songs/search?q=upload%20test%20song")[1]["songs"][0]
        self.assertTrue(found["audio_url"] and found["cover_url"])
        listed = [x for x in call("GET", "/api/songs?q=Upload%20Test%20Song&limit=10")[1]["songs"] if x["id"] == song["id"]]
        self.assertTrue(listed and listed[0]["audio_url"] == song["audio_url"])
        # with R2 settings: uploaded to the bucket, public R2 URL saved (the S3 PUT is captured instead of sent)
        from server import config as cfg, storage as stmod
        sent = []
        real_put = stmod.S3Storage.put
        stmod.S3Storage.put = lambda self, key, data, ctype="", cache_control=None: sent.append((self.bucket, key, ctype, len(data), self.region)) or key
        saved = {k: getattr(cfg, k) for k in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_PUBLIC_URL")}
        try:
            cfg.R2_ENDPOINT_URL, cfg.R2_ACCESS_KEY_ID, cfg.R2_SECRET_ACCESS_KEY = "https://acc123.r2.cloudflarestorage.com", "AK", "SK"
            cfg.R2_BUCKET_NAME, cfg.R2_PUBLIC_URL = "pendel-songs", "https://pub-abc.r2.dev"
            b5, c5 = multipart({**form, "title": "R2 Test Song"}, {"audio": audio, "cover": ("c.png", "image/png", PNG_1PX)})
            st, d = call("POST", "/api/songs/upload", raw=b5, ctype=c5, token=self.admin)
            self.assertEqual((st, d.get("stored_in")), (200, "r2"), d)
            self.assertEqual([(b, k.split("/")[0], c, r) for b, k, c, n, r in sent],
                             [("pendel-songs", "songs", "audio/wav", "auto"), ("pendel-songs", "covers", "image/png", "auto")])
            self.assertTrue(d["song"]["audio_url"].startswith("https://pub-abc.r2.dev/songs/r2-test-song-") and d["song"]["audio_url"].endswith(".wav"))
            self.assertTrue(d["song"]["cover_url"].startswith("https://pub-abc.r2.dev/covers/"))
            self.assertEqual(call("GET", f"/api/stream/{d['song']['id']}")[1]["url"], d["song"]["audio_url"])
        finally:
            stmod.S3Storage.put = real_put
            for k, v in saved.items():
                setattr(cfg, k, v)

    def test_s3_signature_matches_aws_test_vectors(self):
        import datetime
        from server.storage import S3Storage
        s = S3Storage(bucket="examplebucket", access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                      endpoint="https://examplebucket.s3.amazonaws.com", region="us-east-1")
        s._canonical_path = lambda key: "/" + key  # AWS's examples use virtual-hosted-style URLs
        t = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc)
        self.assertTrue(s.presign("test.txt", 86400, now=t).endswith("aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"))
        h = s._request("GET", "test.txt", b"", None, {"range": "bytes=0-9"}, now=t, send=False)
        self.assertTrue(h["Authorization"].endswith("f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"))

    # ------------------------------------------------------------------ v4: persistence, resumable imports, protected admin
    def test_admin_import_routes_are_protected(self):
        routes = [("GET", "/api/admin/youtube"), ("GET", "/api/admin/youtube/progress"), ("POST", "/api/admin/youtube/import"),
                  ("POST", "/api/admin/youtube/starter"), ("POST", "/api/admin/youtube/snapshot"), ("POST", "/api/admin/youtube/refresh"),
                  ("POST", "/api/admin/youtube/cancel"), ("GET", "/api/admin/youtube/snapshot/raw"), ("GET", "/api/admin/lookups"),
                  ("DELETE", "/api/admin/songs/sng_nope"), ("GET", "/api/admin/stats")]
        for m, path in routes:
            self.assertEqual(call(m, path, {} if m != "GET" else None)[0], 401, f"anonymous {m} {path}")
            self.assertEqual(call(m, path, {} if m != "GET" else None, token=self.user)[0], 403, f"listener {m} {path}")
        self.assertEqual(call("GET", "/api/admin/youtube/progress", token=self.admin)[0], 200)

    def test_import_resumes_from_checkpoint_after_restart(self):
        from server import config as cfg, youtube_catalog as ytc
        cfg.YOUTUBE_API_KEY = "test-key"
        real = ytc.import_playlist
        try:
            # simulate the server dying after the first page of the first playlist
            def die_after_one_page(pl, lang, mood, max_videos, job=None, token=None, seen=0):
                real(pl, lang, mood, 50, job, token, seen)
                raise SystemError("server restarted")
            ytc.import_playlist = die_after_one_page
            st, d = call("POST", "/api/admin/youtube/import", {"source": "telugu love songs", "lang": "te", "max": 300}, token=self.admin)
            self.assertEqual(st, 200, d)
            ytc._q.join()
            row = db.one("SELECT * FROM youtube_imports WHERE id=?", (d["job_id"],))
            self.assertEqual(row["seen"], 50)
            self.assertTrue(json.loads(row["checkpoint"])["playlists"])
            db.run("UPDATE youtube_imports SET status='running', error=NULL WHERE id=?", (d["job_id"],))  # as if killed mid-run
            searches_before = ytc.usage()["searches"]
            ytc.import_playlist = real
            self.assertGreaterEqual(ytc.resume_unfinished(), 1)
            ytc._q.join()
            row = db.one("SELECT * FROM youtube_imports WHERE id=?", (d["job_id"],))
            self.assertEqual(row["status"], "done", row)
            self.assertGreater(row["seen"], 50)
            self.assertEqual(ytc.usage()["searches"], searches_before, "resuming must not repeat the 100-unit search")
            p = call("GET", "/api/admin/youtube/progress", token=self.admin)[1]
            self.assertIn(d["job_id"], [j["id"] for j in p["jobs"]])
            self.assertFalse(p["busy"])
        finally:
            ytc.import_playlist = real
            cfg.YOUTUBE_API_KEY = ""

    def test_cancel_queued_imports(self):
        from server import youtube_catalog as ytc
        jid = db.new_id("yti")
        ytc._save_job({"id": jid, "label": "x", "source": "x", "lang": "kn", "mood": None, "max": 50, "status": "queued",
                       "seen": 0, "added": 0, "created_at": db.now(), "checkpoint": {}})
        st, r = call("POST", "/api/admin/youtube/cancel", {}, token=self.admin)
        self.assertEqual(st, 200)
        self.assertGreaterEqual(r["cancelled"], 1)
        self.assertEqual(db.one("SELECT status FROM youtube_imports WHERE id=?", (jid,))["status"], "cancelled")


if __name__ == "__main__":
    import urllib.parse  # noqa
    unittest.main(verbosity=2)

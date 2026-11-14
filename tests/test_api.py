"""API test suite. Runs against a throwaway database.

    python -m unittest tests.test_api -v
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request

TMP = tempfile.mkdtemp(prefix="pendel-test-")
os.environ.update({"DATABASE_PATH": os.path.join(TMP, "test.db"), "PORT": "8799", "PENDEL_QUIET": "1", "PAYMENT_PROVIDERS": "sandbox"})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import app, db, config, security  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

BASE = "http://127.0.0.1:8799"


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


class PENDELAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        for s in db.q("SELECT s.id, s.language_code, h.start_sec, h.end_sec, s.duration_sec FROM songs s LEFT JOIN song_highlights h ON h.song_id=s.id WHERE s.status='published'"):
            self.assertTrue(s["language_code"])
            self.assertIsNotNone(s["start_sec"])
            self.assertTrue(20 <= s["end_sec"] - s["start_sec"] <= 30)
            self.assertGreater(s["start_sec"], 20, "highlight must not default to the opening")
            self.assertLessEqual(s["end_sec"], s["duration_sec"])

    def test_highlight_feed_and_preview_asset(self):
        st, d = call("GET", "/api/highlights?feed=sad")
        self.assertEqual(st, 200)
        self.assertTrue(d["items"])
        it = d["items"][0]
        self.assertEqual(it["stream"]["mode"], "preview")
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
        self.assertTrue(20 <= hl["end"] - hl["start"] <= 30)
        self.assertEqual(call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 10, "end": 50}, token=self.admin)[0], 400)
        st, d = call("PUT", f"/api/admin/songs/{sid}/highlight", {"start": 79.1, "end": 104.1}, token=self.admin)
        self.assertEqual((d["song"]["highlight"]["start"], d["song"]["highlight"]["source"]), (79.1, "manual"))
        st, d = call("PUT", f"/api/admin/songs/{sid}/lyrics", {"text": "[01:19.10] first line\n[01:25.00] second line", "source": "Test Records lyric licence"}, token=self.admin)
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


if __name__ == "__main__":
    import urllib.parse  # noqa
    unittest.main(verbosity=2)

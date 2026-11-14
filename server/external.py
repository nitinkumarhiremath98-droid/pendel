"""World catalogue: real songs from Apple's public iTunes Search API, with full
songs played through YouTube embeds.

How it fits PENDEL
------------------
* iTunes Search API (free, no key) gives millions of tracks with metadata,
  artwork and an official 30-second preview clip. Those previews are what
  PENDEL plays as the highlight of a world-catalogue song.
* Tracks are "materialised" into the normal `songs` table the first time they
  appear (id `sng_it<trackId>`, source='itunes'). From then on favourites,
  playlists, history, search, mood sessions and recommendations treat them like
  any other song. Audio is never copied: the preview asset points at Apple's CDN.
* Full songs: if YOUTUBE_API_KEY is set, PENDEL finds the official video with the
  YouTube Data API (cached per song) and plays it in a visible YouTube embed,
  which is what YouTube's terms require. Admins can also set a video id and a
  custom 20-30 s highlight window by hand.
* Everything is cached in the `external_cache` table (12 h) because Apple rate
  limits the API (~20 calls/minute). A slow background warm-up fills the cache.

Language tagging is best effort: Apple has no language field, so PENDEL uses the
language it searched for and Apple's genre names (e.g. "Telugu", "Tamil",
"Bollywood") to label tracks.
"""
import json, re, threading, time, urllib.error, urllib.parse, urllib.request

from . import config, db

ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
APPLE_RSS = "https://rss.applemarketingtools.com/api/v2/{country}/music/most-played/{limit}/songs.json"
YOUTUBE_SEARCH = "https://www.googleapis.com/youtube/v3/search"
CACHE_TTL = 12 * 3600

# language code -> (search word, Apple storefront, genre names that identify the language)
LANGS = {
    "te": ("telugu", "IN", ["telugu"]), "ta": ("tamil", "IN", ["tamil"]), "kn": ("kannada", "IN", ["kannada"]),
    "ml": ("malayalam", "IN", ["malayalam"]), "hi": ("hindi", "IN", ["bollywood", "hindi"]),
    "bn": ("bengali", "IN", ["bengali"]), "mr": ("marathi", "IN", ["marathi"]), "pa": ("punjabi", "IN", ["punjabi"]),
    "gu": ("gujarati", "IN", ["gujarati"]), "or": ("odia", "IN", ["odia", "oriya"]), "as": ("assamese", "IN", ["assamese"]),
    "ur": ("urdu", "IN", ["urdu", "ghazal", "qawwali"]),
    "en": ("", "US", ["pop", "rock", "hip-hop/rap", "r&b/soul", "alternative", "dance", "country", "electronic", "singer/songwriter"]),
    "es": ("latino", "MX", ["latin", "latino", "pop latino", "urbano latino", "regional mexicano", "música mexicana"]),
    "fr": ("french", "FR", ["french pop", "variété française", "chanson"]), "de": ("deutsch", "DE", ["deutschpop", "schlager", "german pop"]),
    "ar": ("arabic", "AE", ["arabic", "arabic pop", "khaleeji"]), "ko": ("k-pop", "KR", ["k-pop", "korean"]),
    "ja": ("j-pop", "JP", ["j-pop", "anime", "japanese"]), "zh": ("mandopop", "TW", ["mandopop", "c-pop", "chinese"]),
    "pt": ("brasil", "BR", ["mpb", "sertanejo", "brazilian", "pagode", "funk brasileiro"]), "it": ("italiana", "IT", ["italian pop", "italian"]),
    "ru": ("russian", "RU", ["russian pop", "russian"]), "tr": ("turkish", "TR", ["turkish pop", "turkish"]),
    "id": ("indonesia", "ID", ["indonesian pop", "indonesian", "dangdut"]), "th": ("thai", "TH", ["thai pop", "thai"]),
}
INDIAN = {"te", "ta", "kn", "ml", "hi", "bn", "mr", "pa", "gu", "or", "as", "ur"}

MOODS = {
    "trending": "hits", "sad": "sad", "love": "love", "heartbreak": "heartbreak", "romantic": "romantic",
    "feelings": "emotional", "emotional": "emotional", "motivational": "motivational", "devotional": "devotional",
    "dj": "dj remix", "party": "party", "dance": "dance", "friendship": "friendship", "melody": "melody",
    "old-classic": "old classic", "new-generation": "latest", "instrumental": "instrumental", "background": "lofi",
    "independent": "indie", "international": "top hits",
}

status = {"last_error": None, "last_ok": None, "calls": 0}
_lock = threading.Lock()


def enabled():
    return config.WORLD_CATALOG


# --------------------------------------------------------------------------- HTTP + cache

def http_get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "PENDEL/2.0 (music discovery app)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


FETCH = http_get_json  # tests replace this with a fake
if __import__("os").environ.get("PENDEL_FAKE_WORLD"):
    from tests.fake_world import fetch as FETCH  # noqa: F811  (offline testing only)


_down_until = [0.0]  # after a network failure, don't retry for a while so pages never hang


def cached_json(url, ttl=CACHE_TTL):
    row = db.one("SELECT body, fetched_at FROM external_cache WHERE url=?", (url,))
    if row and time.time() - row["fetched_at"] < ttl:
        return json.loads(row["body"])
    if time.time() < _down_until[0]:
        return json.loads(row["body"]) if row else None
    try:
        status["calls"] += 1
        data = FETCH(url)
        db.run("INSERT INTO external_cache(url, body, fetched_at) VALUES(?,?,?) ON CONFLICT(url) DO UPDATE SET body=excluded.body, fetched_at=excluded.fetched_at",
               (url, json.dumps(data, ensure_ascii=False), int(time.time())))
        status["last_ok"] = int(time.time())
        return data
    except Exception as e:  # network down, rate limited, bad JSON: fall back to stale cache
        status["last_error"] = f"{type(e).__name__}: {e}"[:200]
        if not (isinstance(e, urllib.error.HTTPError) and e.code in (400, 404)):
            _down_until[0] = time.time() + 120
        return json.loads(row["body"]) if row else None


# --------------------------------------------------------------------------- iTunes

def _itunes_url(term, country, limit):
    return ITUNES_SEARCH + "?" + urllib.parse.urlencode({"term": term, "media": "music", "entity": "song", "limit": limit, "country": country})


def normalise(r):
    if r.get("kind") != "song" or not r.get("previewUrl") or not r.get("trackId"):
        return None
    art = r.get("artworkUrl100") or r.get("artworkUrl60") or ""
    year = None
    if r.get("releaseDate", "")[:4].isdigit():
        year = int(r["releaseDate"][:4])
    return {
        "track_id": int(r["trackId"]), "title": r.get("trackName") or "Untitled", "artist": r.get("artistName") or "Unknown artist",
        "artist_id": r.get("artistId"), "album": r.get("collectionName"), "album_id": r.get("collectionId"),
        "genre": r.get("primaryGenreName"), "year": year, "duration": round((r.get("trackTimeMillis") or 0) / 1000, 2),
        "cover": art.replace("100x100bb", "600x600bb").replace("60x60bb", "600x600bb"), "preview": r["previewUrl"],
        "url": r.get("trackViewUrl"), "explicit": r.get("trackExplicitness") == "explicit",
    }


def detect_language(genre, fallback=None):
    g = (genre or "").lower()
    for code in ("te", "ta", "kn", "ml", "bn", "mr", "pa", "gu", "or", "as", "ur", "hi"):
        if any(alias == g or alias in g for alias in LANGS[code][2]):
            return code
    for code in ("ko", "ja", "zh", "es", "pt", "fr", "de", "ar", "it", "ru", "tr", "id", "th"):
        if any(alias == g for alias in LANGS[code][2]):
            return code
    return fallback


def search_itunes(term, country="IN", limit=50):
    data = cached_json(_itunes_url(term, country, limit))
    out = []
    for r in (data or {}).get("results", []):
        t = normalise(r)
        if t:
            out.append(t)
    return out


def belongs_to(t, lang):
    """Strict check: does Apple's genre, or the album/title text, say this track is in `lang`?"""
    word, _, genres = LANGS[lang]
    g = (t.get("genre") or "").lower()
    if genres and any(a == g or a in g for a in genres):
        return True
    return bool(word) and word.split()[0] in f"{t.get('album') or ''} {t.get('title') or ''}".lower()


def mood_tracks(lang, mood, limit=40):
    """Real songs for a language + mood. Only tracks that clearly belong to the language are kept:
    an empty list is better than songs in the wrong language. Returns song ids (materialised)."""
    if not enabled() or lang not in LANGS:
        return []
    word, country, _ = LANGS[lang]
    mood_word = MOODS.get(mood or "trending", mood or "hits")
    terms = [f"{word} {mood_word} songs", f"{word} {mood_word}", f"{word} songs", f"{word} film songs"] if word else [f"{mood_word} songs", mood_word]
    seen, keep = set(), []
    for term in dict.fromkeys(t.strip() for t in terms):
        for t in search_itunes(term, country, 100):
            if t["track_id"] in seen:
                continue
            seen.add(t["track_id"])
            if belongs_to(t, lang):
                keep.append({**t, "lang": lang})
        if len(keep) >= limit:
            break
    return materialise(keep[:limit], mood=None if mood in (None, "trending") else mood)


def search(q, limit=25):
    if not enabled() or len(q.strip()) < 2:
        return []
    tracks = search_itunes(q.strip(), config.WORLD_COUNTRY, limit)
    return materialise(tracks)


def charts(country=None, limit=50):
    """Apple Music most-played chart for a storefront, with previews looked up in one call."""
    if not enabled():
        return []
    country = (country or config.WORLD_COUNTRY).lower()
    feed = cached_json(APPLE_RSS.format(country=country, limit=limit), ttl=6 * 3600)
    ids = [r.get("id") for r in ((feed or {}).get("feed") or {}).get("results", []) if r.get("id")]
    if not ids:
        return []
    data = cached_json(ITUNES_LOOKUP + "?" + urllib.parse.urlencode({"id": ",".join(ids[:200]), "country": country.upper(), "entity": "song"}))
    by_id = {}
    for r in (data or {}).get("results", []):
        t = normalise(r)
        if t:
            by_id[str(t["track_id"])] = t
    ordered = [by_id[i] for i in ids if i in by_id]
    return materialise(ordered)


# --------------------------------------------------------------------------- materialise into the catalogue

def _slug(s):
    return re.sub(r"[^\w]+", "-", (s or "").lower(), flags=re.UNICODE).strip("-")[:50] or "x"


def materialise(tracks, mood=None):
    """Upsert tracks into artists/albums/songs/audio_assets. Returns the song ids in the same order."""
    if not tracks:
        return []
    ids, t0 = [], db.now()
    cats = {r["slug"]: r["id"] for r in db.q("SELECT id, slug FROM categories")}
    with _lock, db.tx():
        for t in tracks:
            sid = f"sng_it{t['track_id']}"
            code = t.get("lang") or detect_language(t["genre"], None)
            aid = f"art_it{t['artist_id'] or abs(hash(t['artist'])) % 10**9}"
            db.run("INSERT OR IGNORE INTO artists(id,slug,name,image_key,is_independent,is_verified,created_at,source) VALUES(?,?,?,?,0,0,?,'itunes')",
                   (aid, f"it-{_slug(t['artist'])}-{aid[6:]}", t["artist"], t["cover"], t0))
            alid = None
            if t["album_id"]:
                alid = f"alb_it{t['album_id']}"
                db.run("INSERT OR IGNORE INTO albums(id,slug,title,artist_id,cover_key,release_year,label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (alid, f"it-{_slug(t['album'])}-{t['album_id']}", t["album"] or t["title"], aid, t["cover"], t["year"], "Apple Music", t0))
            existing = db.one("SELECT language_code FROM songs WHERE id=?", (sid,))
            if not existing:
                db.run("""INSERT INTO songs(id,slug,title,artist_id,album_id,language_code,genre_id,release_year,duration_sec,cover_key,is_explicit,
                          status,play_count,is_demo,created_at,updated_at,source,external_id,external_url)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,'published',0,0,?,?,'itunes',?,?)""",
                       (sid, f"it-{t['track_id']}-{_slug(t['title'])}", t["title"], aid, alid, code or "und", db.genre_id(t["genre"]),
                        t["year"], t["duration"], t["cover"], int(t["explicit"]), t0, t0, str(t["track_id"]), t["url"]))
            elif code and existing["language_code"] != code and (existing["language_code"] == "und" or t.get("lang")):
                db.run("UPDATE songs SET language_code=? WHERE id=?", (code, sid))
            db.run("""INSERT INTO audio_assets(id,song_id,kind,storage_key,mime_type,duration_sec,created_at) VALUES(?,?,'preview',?,'audio/mp4',30,?)
                      ON CONFLICT(id) DO UPDATE SET storage_key=excluded.storage_key""", (f"aud_it{t['track_id']}", sid, t["preview"], t0))
            if mood in cats:
                db.run("INSERT OR IGNORE INTO song_categories VALUES(?,?)", (sid, cats[mood]))
            final_lang = code or (existing or {}).get("language_code")
            if final_lang and final_lang not in INDIAN and final_lang != "und" and "international" in cats:
                db.run("INSERT OR IGNORE INTO song_categories VALUES(?,?)", (sid, cats["international"]))
            ids.append(sid)
    return ids


def fix_languages():
    """Re-check every world song's language with the strict rule (repairs labels saved by v2.0)."""
    fixed = 0
    rows = db.q("""SELECT s.id, s.title, s.language_code, g.name genre, al.title album FROM songs s
                   LEFT JOIN genres g ON g.id=s.genre_id LEFT JOIN albums al ON al.id=s.album_id
                   WHERE s.source='itunes' AND s.language_code<>'und'""")
    with _lock, db.tx():
        for r in rows:
            t = {"genre": r["genre"], "album": r["album"], "title": r["title"]}
            if r["language_code"] in LANGS and belongs_to(t, r["language_code"]):
                continue
            new = detect_language(r["genre"], None) or "und"
            if new != r["language_code"]:
                db.run("UPDATE songs SET language_code=? WHERE id=?", (new, r["id"]))
                fixed += 1
    return fixed


# --------------------------------------------------------------------------- YouTube (full songs)

def youtube_search_url(song):
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": f"{song['title']} {song['artist_name']}"})


def youtube_id(song):
    """Video id for a song: admin-set, cached, or looked up (needs YOUTUBE_API_KEY). '' = searched, none found."""
    if song.get("youtube_id"):
        return song["youtube_id"]
    if song.get("youtube_id") == "" or not config.YOUTUBE_API_KEY:
        return None
    url = YOUTUBE_SEARCH + "?" + urllib.parse.urlencode({
        "part": "snippet", "type": "video", "videoEmbeddable": "true", "maxResults": 1, "regionCode": config.WORLD_COUNTRY,
        "q": f"{song['title']} {song['artist_name']} official audio", "key": config.YOUTUBE_API_KEY})
    data = cached_json(url, ttl=30 * 86400)
    items = (data or {}).get("items") or []
    vid = items[0].get("id", {}).get("videoId", "") if items else ""
    if data is not None:  # only remember a miss if YouTube actually answered
        db.run("UPDATE songs SET youtube_id=? WHERE id=?", (vid, song["id"]))
    return vid or None


# --------------------------------------------------------------------------- background warm-up

def warm_up():
    """Fill the cache slowly (Apple allows ~20 calls/minute) so pages open instantly."""
    if not enabled() or not config.WORLD_WARMUP:
        return

    def run():
        time.sleep(5)
        try:
            charts()
        except Exception:
            pass
        jobs = [(l, m) for m in ("trending", "sad", "love", "devotional", "dj", "romantic", "motivational", "melody")
                for l in ("hi", "te", "ta", "kn", "ml", "pa", "bn", "mr", "en", "es", "ko", "ar", "gu", "or", "as", "ur",
                          "ja", "zh", "fr", "de", "pt", "it", "ru", "tr", "id", "th")]
        for lang, mood in jobs:
            try:
                mood_tracks(lang, mood, 30)
            except Exception:
                pass
            time.sleep(4)

    threading.Thread(target=run, daemon=True, name="world-warmup").start()

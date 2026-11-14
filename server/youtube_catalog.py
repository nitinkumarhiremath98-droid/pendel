"""Full songs in every language, from official YouTube uploads.

PENDEL plays these songs in a visible YouTube embed (never audio-only, never
downloaded), which is what the YouTube API Services policies allow.

Importing is quota-efficient:
  * playlistItems.list / videos.list / channels.list cost 1 unit and return up
    to 50 videos, so 50,000 songs need roughly 2,000 of the free 10,000 daily units;
  * search.list costs 100 units and has its own 100-calls-a-day limit, so it is
    only used to *find playlists or channels*, never per song.

The "starter pack" searches for playlists such as "kannada sad songs" for every
language and mood, so each imported song is tagged with the language and mood
it was found for. Channels (e.g. a music label) can be imported too; their
uploads are filtered to single songs (90 s - 9 min, no trailers/teasers/jukeboxes).

Storage rules: YouTube data must be refreshed or deleted within 30 days. The
catalogue can be exported to catalog/youtube_snapshot.json.gz (commit it so a
host with a temporary disk, like Render's free plan, reloads it on start-up).
On load, a snapshot older than 25 days is refreshed with videos.list when a key
is available, and one older than 30 days is not used without a refresh.
"""
import gzip, hashlib, json, os, queue, re, threading, time, urllib.parse
from datetime import datetime, timezone

from . import config, db, external

API = "https://www.googleapis.com/youtube/v3/"
SNAPSHOT = os.path.join(config.ROOT, "catalog", "youtube_snapshot.json.gz")
DAILY_UNITS, DAILY_SEARCHES = 9500, 95          # stay just under Google's free limits
MIN_SEC, MAX_SEC = 90, 540
JUNK = re.compile(r"trailer|teaser|making|interview|promo\b|full movie|reaction|behind the scenes|press meet|audio launch|"
                  r"pre[- ]?release|jukebox|#shorts|\bscene\b|comedy|dialogue|whatsapp status|first look|glimpse|launch event|"
                  r"live (?:concert|performance)|non[- ]?stop|back to back|mashup|all songs", re.I)
MOOD_WORDS = [
    ("devotional", r"devotional|bhakti|bhajan|aarti|aarathi|stotram|mantra|suprabhatam|kirtan|swamy|swami|shiva|krishna|ganesh|ayyappa|murugan|devi"),
    ("sad", r"\bsad\b|emotional|breakup|heartbreak|dard|bewafa|soga"),
    ("dj", r"\bdj\b|remix|club mix"),
    ("party", r"party|dance number|item song"),
    ("love", r"\blove\b|romantic|prema|pyaar|pyar|ishq|kadhal|kaadhal|preethi|mohabbat"),
    ("motivational", r"motivation|inspir"),
]
NATIVE = {"te": r"[ఀ-౿]", "ta": r"[஀-௿]", "kn": r"[ಀ-೿]", "ml": r"[ഀ-ൿ]", "bn": r"[ঀ-৿]",
          "gu": r"[઀-૿]", "pa": r"[਀-੿]", "or": r"[଀-୿]", "ko": r"[가-힯]", "ja": r"[぀-ヿ]",
          "ar": r"[؀-ۿ]", "th": r"[฀-๿]", "ru": r"[Ѐ-ӿ]"}
LANG_NAMES = {"te": "telugu", "ta": "tamil", "kn": "kannada", "ml": "malayalam", "hi": "hindi", "bn": "bengali", "mr": "marathi",
              "pa": "punjabi", "gu": "gujarati", "or": "odia", "as": "assamese", "ur": "urdu", "en": "english", "es": "spanish",
              "fr": "french", "de": "german", "ar": "arabic", "ko": "korean", "ja": "japanese", "zh": "chinese", "pt": "portuguese",
              "it": "italian", "ru": "russian", "tr": "turkish", "id": "indonesian", "th": "thai"}
INDIAN = ["kn", "te", "ta", "ml", "hi", "pa", "bn", "mr", "gu", "or", "as", "ur"]
INTERNATIONAL = ["en", "es", "ko", "ar", "ja", "zh", "fr", "de", "pt", "it", "ru", "tr", "id", "th"]


class QuotaExceeded(Exception):
    pass


# --------------------------------------------------------------------------- API calls with quota accounting

def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def usage():
    d = _today()
    units = int(db.setting(f"yt_units_{d}") or 0)
    searches = int(db.setting(f"yt_search_{d}") or 0)
    return {"date": d, "units": units, "searches": searches, "units_limit": DAILY_UNITS, "searches_limit": DAILY_SEARCHES}


def _bump(key, n):
    db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+?", (key, str(n), n))


def call(method, params, cost=1):
    if not config.YOUTUBE_API_KEY:
        raise RuntimeError("Add YOUTUBE_API_KEY to your .env file first")
    u = usage()
    if method == "search":
        if u["searches"] >= DAILY_SEARCHES:
            raise QuotaExceeded("Today's YouTube search limit is used up. Imports continue tomorrow.")
        _bump(f"yt_search_{u['date']}", 1)
    elif u["units"] + cost > DAILY_UNITS:
        raise QuotaExceeded("Today's YouTube quota is used up. Imports continue tomorrow.")
    else:
        _bump(f"yt_units_{u['date']}", cost)
    url = API + method + "?" + urllib.parse.urlencode({**params, "key": config.YOUTUBE_API_KEY})
    try:
        return external.FETCH(url)
    except Exception as e:
        body = getattr(e, "read", lambda: b"")()
        if b"quotaExceeded" in (body or b""):
            raise QuotaExceeded("Google reports the daily YouTube quota is used up. Imports continue tomorrow.")
        raise


def parse_duration(iso):
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


# --------------------------------------------------------------------------- finding sources

def resolve(source):
    """Accepts a playlist URL/ID, a channel URL, an @handle, a channel ID, or a plain search phrase."""
    s = source.strip()
    m = re.search(r"[?&]list=([\w-]+)", s) or re.fullmatch(r"((?:PL|OL|UU|RD)[\w-]{10,})", s)
    if m:
        return {"kind": "playlist", "id": m.group(1)}
    m = re.search(r"youtube\.com/channel/(UC[\w-]{20,})", s) or re.fullmatch(r"(UC[\w-]{20,})", s)
    if m:
        return _channel({"id": m.group(1)})
    m = re.search(r"youtube\.com/@([\w.\-]+)", s) or re.fullmatch(r"@([\w.\-]+)", s)
    if m:
        return _channel({"forHandle": "@" + m.group(1)})
    return {"kind": "search", "query": s}


def _channel(params):
    data = call("channels", {"part": "contentDetails,snippet", **params})
    items = data.get("items") or []
    if not items:
        raise ValueError("YouTube channel not found")
    ch = items[0]
    return {"kind": "playlist", "id": ch["contentDetails"]["relatedPlaylists"]["uploads"], "channel": ch["snippet"]["title"]}


def find_playlists(query, n=3, lang=None):
    data = call("search", {"part": "snippet", "type": "playlist", "q": query, "maxResults": 10,
                           **({"relevanceLanguage": lang} if lang and lang not in ("und",) else {})}, cost=100)
    out = []
    for it in data.get("items") or []:
        pid = (it.get("id") or {}).get("playlistId")
        if pid:
            out.append({"id": pid, "title": it.get("snippet", {}).get("title", "")})
        if len(out) >= n:
            break
    return out


# --------------------------------------------------------------------------- importing

def detect_lang(title, default):
    t = title.lower()
    for code, rx in NATIVE.items():
        if re.search(rx, title):
            return code
    for code, name in LANG_NAMES.items():
        if re.search(rf"\b{name}\b", t):
            return code
    if "bollywood" in t:
        return "hi"
    return default


def detect_moods(title):
    return [m for m, rx in MOOD_WORDS if re.search(rx, title, re.I)]


def import_playlist(playlist_id, lang, mood=None, max_videos=500, job=None):
    """Page through a playlist (1 unit / 50 videos), fetch details (1 unit / 50 videos), keep single songs."""
    token, seen, added = None, 0, 0
    while seen < max_videos:
        data = call("playlistItems", {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50, **({"pageToken": token} if token else {})})
        ids = [i["contentDetails"]["videoId"] for i in data.get("items") or [] if i.get("contentDetails", {}).get("videoId")]
        seen += len(ids)
        if ids:
            n = _import_videos(ids, lang, mood)
            added += n
            if job:
                job["seen"] += len(ids); job["added"] += n
                _save_job(job)
        token = data.get("nextPageToken")
        if not token:
            break
    return added


def _import_videos(ids, lang, mood):
    data = call("videos", {"part": "snippet,contentDetails,status", "id": ",".join(ids), "maxResults": 50})
    rows = []
    for v in data.get("items") or []:
        sn, cd, st = v.get("snippet", {}), v.get("contentDetails", {}), v.get("status", {})
        dur = parse_duration(cd.get("duration"))
        title = sn.get("title") or ""
        if not st.get("embeddable", True) or st.get("privacyStatus", "public") != "public":
            continue
        if not (MIN_SEC <= dur <= MAX_SEC) or JUNK.search(title) or sn.get("liveBroadcastContent", "none") != "none":
            continue
        rows.append({"video_id": v["id"], "title": title, "channel": sn.get("channelTitle") or "YouTube", "channel_id": sn.get("channelId") or "",
                     "duration": dur, "year": int((sn.get("publishedAt") or "0000")[:4]) or None,
                     "lang": detect_lang(title, lang), "moods": sorted(set(([mood] if mood else []) + detect_moods(title))),
                     "fetched_at": int(time.time())})
    return materialise(rows)


def song_id(video_id):
    return "sng_yt" + hashlib.md5(video_id.encode()).hexdigest()[:14]


def materialise(rows):
    """Insert/refresh YouTube songs. Returns how many were new."""
    if not rows:
        return 0
    new, t0 = 0, db.now()
    cats = {r["slug"]: r["id"] for r in db.q("SELECT id, slug FROM categories")}
    langs = {r["code"] for r in db.q("SELECT code FROM languages")}
    with external._lock, db.tx():
        for r in rows:
            sid = song_id(r["video_id"])
            aid = "art_yt" + hashlib.md5((r["channel_id"] or r["channel"]).encode()).hexdigest()[:12]
            thumb = f"https://i.ytimg.com/vi/{r['video_id']}/hqdefault.jpg"
            db.run("INSERT OR IGNORE INTO artists(id,slug,name,image_key,created_at,source) VALUES(?,?,?,?,?,'youtube')",
                   (aid, f"yt-{external._slug(r['channel'])}-{aid[6:12]}", r["channel"], thumb, t0))
            lang = r["lang"] if r["lang"] in langs else "und"
            if db.one("SELECT 1 FROM songs WHERE id=?", (sid,)):
                db.run("UPDATE songs SET title=?, duration_sec=?, updated_at=? WHERE id=?", (r["title"], r["duration"], t0, sid))
            else:
                db.run("""INSERT INTO songs(id,slug,title,artist_id,language_code,release_year,duration_sec,cover_key,status,play_count,is_demo,
                          created_at,updated_at,source,external_id,external_url,youtube_id) VALUES(?,?,?,?,?,?,?,?,'published',0,0,?,?,'youtube',?,?,?)""",
                       (sid, f"yt-{external._slug(r['title'])[:40]}-{sid[6:14]}", r["title"], aid, lang, r["year"], r["duration"], thumb, t0, t0,
                        r["video_id"], f"https://www.youtube.com/watch?v={r['video_id']}", r["video_id"]))
                start = max(15.0, min(r["duration"] * 0.33, r["duration"] - 30))  # estimated window: no audio analysis on YouTube songs
                db.run("""INSERT OR IGNORE INTO song_highlights(id,song_id,start_sec,end_sec,source,preview_asset_id,updated_at)
                          VALUES(?,?,?,?, 'auto', NULL, ?)""", (db.new_id("hl"), sid, round(start, 1), round(start + 25, 1), t0))
                new += 1
            for m in r["moods"]:
                if m in cats:
                    db.run("INSERT OR IGNORE INTO song_categories VALUES(?,?)", (sid, cats[m]))
            if lang not in INDIAN and lang != "und" and "international" in cats:
                db.run("INSERT OR IGNORE INTO song_categories VALUES(?,?)", (sid, cats["international"]))
    return new


# --------------------------------------------------------------------------- jobs (one worker thread)

_q = queue.Queue()
_worker = None


def _save_job(job):
    db.run("""INSERT INTO youtube_imports(id,label,source,lang,mood,max_videos,status,seen,added,error,created_at,finished_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, seen=excluded.seen, added=excluded.added,
              error=excluded.error, finished_at=excluded.finished_at""",
           (job["id"], job["label"], job["source"], job["lang"], job.get("mood"), job["max"], job["status"], job["seen"], job["added"],
            job.get("error"), job["created_at"], job.get("finished_at")))


def enqueue(source, lang, mood=None, max_videos=500, label=None):
    job = {"id": db.new_id("yti"), "label": label or source, "source": source, "lang": lang, "mood": mood, "max": max_videos,
           "status": "queued", "seen": 0, "added": 0, "created_at": db.now()}
    _save_job(job)
    _q.put(job)
    _ensure_worker()
    return job["id"]


def _ensure_worker():
    global _worker
    if _worker and _worker.is_alive():
        return
    _worker = threading.Thread(target=_run, daemon=True, name="youtube-import")
    _worker.start()


def _run():
    while True:
        job = _q.get()
        try:
            job["status"] = "running"; _save_job(job)
            src = resolve(job["source"])
            playlists = [src] if src["kind"] == "playlist" else find_playlists(job["source"], 3, job["lang"])
            if not playlists:
                raise ValueError("No playlists found for that search")
            per = max(50, job["max"] // len(playlists))
            for pl in playlists:
                import_playlist(pl["id"], job["lang"], job.get("mood"), per, job)
            job["status"] = "done"
        except QuotaExceeded as e:
            job["status"], job["error"] = "quota", str(e)
        except Exception as e:
            job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"[:300]
        job["finished_at"] = db.now()
        _save_job(job)
        _q.task_done()


ROUNDS = {
    # round: (Indian-language phrases, international phrases); each phrase is one search (100 units, max ~95/day)
    1: ([(None, "superhit songs"), ("love", "love songs"), ("sad", "sad songs"), ("devotional", "devotional songs"),
         ("dj", "dj remix songs"), ("melody", "melody songs")],
        [(None, "top hits songs"), ("love", "love songs")]),
    2: ([("old-classic", "old hit songs"), ("new-generation", "new songs 2026"), ("party", "party songs"), ("romantic", "romantic songs"),
         ("motivational", "motivational songs"), ("friendship", "friendship songs")],
        [("party", "party songs"), ("sad", "sad songs")]),
    3: ([("feelings", "emotional songs"), ("dance", "dance songs"), ("heartbreak", "breakup songs"), ("old-classic", "90s hit songs"),
         ("melody", "evergreen melody songs"), (None, "film songs")],
        [("dance", "dance hits"), ("old-classic", "classic hits")]),
}


def starter_pack(round_no=1):
    """One day's import: playlists for every language x mood. Round 1 alone usually brings tens of thousands of
    full songs; rounds 2 and 3 (on later days, because of the 100-searches-a-day limit) add more."""
    indian, intl = ROUNDS.get(round_no, ROUNDS[1])
    plan = [(f"{LANG_NAMES[c]} {words}", c, mood, 1200) for c in INDIAN for mood, words in indian]
    plan += [(f"{LANG_NAMES[c]} {words}", c, mood, 900) for i, (mood, words) in enumerate(intl) for c in (INTERNATIONAL if i == 0 else INTERNATIONAL[:9])]
    left = DAILY_SEARCHES - usage()["searches"]
    plan = plan[:max(0, left)]
    for q, code, mood, n in plan:
        enqueue(q, code, mood, n, label=f"Starter pack {round_no}: {q}")
    return len(plan)


def jobs(limit=200):
    return db.q("SELECT * FROM youtube_imports ORDER BY created_at DESC LIMIT ?", (limit,))


def resume_unfinished():
    """Re-queue jobs that were queued/running when the server stopped."""
    for j in db.q("SELECT * FROM youtube_imports WHERE status IN ('queued','running') ORDER BY created_at"):
        job = {"id": j["id"], "label": j["label"], "source": j["source"], "lang": j["lang"], "mood": j["mood"], "max": j["max_videos"],
               "status": "queued", "seen": j["seen"], "added": j["added"], "created_at": j["created_at"]}
        _q.put(job)
    if not _q.empty() and config.YOUTUBE_API_KEY:
        _ensure_worker()


# --------------------------------------------------------------------------- snapshot (for hosts with temporary disks)

def export_snapshot():
    rows = db.q("""SELECT s.youtube_id video_id, s.title, a.name channel, s.duration_sec duration, s.release_year year, s.language_code lang,
                   (SELECT group_concat(c.slug) FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE sc.song_id=s.id) moods,
                   h.start_sec hs, h.end_sec he, h.source hsrc
                   FROM songs s JOIN artists a ON a.id=s.artist_id LEFT JOIN song_highlights h ON h.song_id=s.id
                   WHERE s.source='youtube' AND s.status='published'""")
    os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
    payload = {"generated_at": int(time.time()), "songs": rows}
    with gzip.open(SNAPSHOT, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    return {"path": SNAPSHOT, "songs": len(rows), "bytes": os.path.getsize(SNAPSHOT)}


def load_snapshot():
    if not os.path.exists(SNAPSHOT):
        return 0
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as f:
        data = json.load(f)
    age_days = (time.time() - data.get("generated_at", 0)) / 86400
    if age_days > 30 and not config.YOUTUBE_API_KEY:
        print("[pendel] YouTube snapshot is older than 30 days and no YOUTUBE_API_KEY is set to refresh it: not loaded (YouTube data rules).")
        return 0
    existing = {r["youtube_id"] for r in db.q("SELECT youtube_id FROM songs WHERE source='youtube'")}
    rows = [{"video_id": r["video_id"], "title": r["title"], "channel": r["channel"], "channel_id": r["channel"], "duration": r["duration"],
             "year": r["year"], "lang": r["lang"], "moods": [m for m in (r.get("moods") or "").split(",") if m]}
            for r in data["songs"] if r["video_id"] not in existing]
    for i in range(0, len(rows), 2000):
        materialise(rows[i:i + 2000])
    with db.tx():  # keep hand-picked highlights from the snapshot
        for r in data["songs"]:
            if r.get("hsrc") == "manual" and r.get("hs") is not None:
                db.run("UPDATE song_highlights SET start_sec=?, end_sec=?, source='manual' WHERE song_id=?", (r["hs"], r["he"], song_id(r["video_id"])))
    if age_days > 25 and config.YOUTUBE_API_KEY:
        threading.Thread(target=refresh_all, daemon=True, name="youtube-refresh").start()
    return len(rows)


def refresh_all():
    """Re-check every YouTube song (1 unit per 50): update titles, remove deleted or no-longer-embeddable videos."""
    ids = [r["youtube_id"] for r in db.q("SELECT youtube_id FROM songs WHERE source='youtube'")]
    removed = 0
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        try:
            data = call("videos", {"part": "snippet,status", "id": ",".join(batch), "maxResults": 50})
        except Exception:
            return removed
        alive = {}
        for v in data.get("items") or []:
            if v.get("status", {}).get("embeddable", True):
                alive[v["id"]] = v.get("snippet", {}).get("title")
        with db.tx():
            for vid in batch:
                if vid in alive:
                    db.run("UPDATE songs SET title=?, updated_at=? WHERE id=?", (alive[vid], db.now(), song_id(vid)))
                else:
                    db.run("UPDATE songs SET status='draft' WHERE id=?", (song_id(vid),))
                    removed += 1
    return removed


def stats():
    return {"songs": db.one("SELECT COUNT(*) n FROM songs WHERE source='youtube' AND status='published'")["n"],
            "by_language": db.q("""SELECT l.code, l.name, COUNT(*) n FROM songs s JOIN languages l ON l.code=s.language_code
                                   WHERE s.source='youtube' AND s.status='published' GROUP BY l.code ORDER BY n DESC"""),
            "snapshot": ({"exists": True, "bytes": os.path.getsize(SNAPSHOT), "modified": int(os.path.getmtime(SNAPSHOT))}
                         if os.path.exists(SNAPSHOT) else {"exists": False}),
            "usage": usage(), "key": bool(config.YOUTUBE_API_KEY)}

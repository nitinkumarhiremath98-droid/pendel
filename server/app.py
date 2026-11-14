"""PENDEL API + web server (Python standard library only).

Run:  python -m server.app        (from the project root)
"""
import csv, gzip, html, io, json, mimetypes, os, re, secrets, sys, time, traceback, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, highlights, payments, recommend, security
from .storage import storage, is_protected

ROUTES = []

# Windows reads MIME types from the registry, which is often wrong; pin the ones we serve.
for _ext, _type in ((".svg", "image/svg+xml"), (".js", "application/javascript"), (".css", "text/css"), (".mp3", "audio/mpeg"),
                    (".m4a", "audio/mp4"), (".webmanifest", "application/manifest+json"), (".webp", "image/webp"), (".png", "image/png")):
    mimetypes.add_type(_type, _ext)


class ApiError(Exception):
    def __init__(self, status, message, code=None, extra=None):
        super().__init__(message)
        self.status, self.message, self.code, self.extra = status, message, code, extra or {}


def route(method, pattern, auth=None):
    """auth: None (public), 'optional', 'user', or a permission string for admins (e.g. 'content')."""
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth))
        return fn
    return deco


# --------------------------------------------------------------------------- helpers

PERMISSIONS = {
    "super_admin": {"*"},
    "content_manager": {"content", "takedown", "analytics"},
    "moderator": {"users", "playlists", "takedown", "analytics"},
    "analyst": {"analytics"},
}


def has_perm(role, perm):
    p = PERMISSIONS.get(role, set())
    return "*" in p or perm in p


def require(cond, status, message, code=None):
    if not cond:
        raise ApiError(status, message, code)


def s_(v, field, maxlen=200, required=True):
    if v is None or (isinstance(v, str) and not v.strip()):
        require(not required, 400, f"{field} is required")
        return None
    require(isinstance(v, (str, int, float)), 400, f"{field} must be text")
    v = str(v).strip()
    require(len(v) <= maxlen, 400, f"{field} is too long (max {maxlen})")
    return v


def int_(v, field, lo=None, hi=None, required=False):
    if v in (None, ""):
        require(not required, 400, f"{field} is required")
        return None
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ApiError(400, f"{field} must be a whole number")
    require(lo is None or v >= lo, 400, f"{field} must be at least {lo}")
    require(hi is None or v <= hi, 400, f"{field} must be at most {hi}")
    return v


def slugify(s):
    base = re.sub(r"[^\w]+", "-", s.lower(), flags=re.UNICODE).strip("-")[:60] or "item"
    return base


def unique_slug(table, title):
    base = slugify(title)
    slug, n = base, 2
    while db.one(f"SELECT 1 FROM {table} WHERE slug=?", (slug,)):
        slug, n = f"{base}-{n}", n + 1
    return slug


def audit(req, action, entity, entity_id=None, details=None):
    db.run("INSERT INTO audit_log(admin_id,action,entity,entity_id,details,at) VALUES(?,?,?,?,?,?)",
           (req.user["id"] if req.user else None, action, entity, entity_id, json.dumps(details, ensure_ascii=False) if details else None, db.now()))


def user_plan(user_id):
    if not user_id:
        return {"tier": "free", "plan_id": "free", "subscription": None}
    sub = db.one("""SELECT s.*, p.name plan_name, p.tier FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                    WHERE s.user_id=? AND s.status IN ('active','cancelled') AND s.current_period_end > ? AND p.tier='premium'
                    ORDER BY s.current_period_end DESC LIMIT 1""", (user_id, db.now()))
    if sub:
        return {"tier": "premium", "plan_id": sub["plan_id"], "subscription": sub}
    return {"tier": "free", "plan_id": "free", "subscription": None}


def public_user(u):
    if not u:
        return None
    admin = db.one("SELECT role FROM admins WHERE user_id=?", (u["id"],))
    plan = user_plan(u["id"])
    return {
        "id": u["id"], "email": u["email"], "display_name": u["display_name"], "avatar_url": storage.url(u["avatar_key"]),
        "auth_provider": u["auth_provider"], "theme": u["theme"], "created_at": u["created_at"],
        "admin_role": admin["role"] if admin else None, "plan": plan["tier"], "plan_id": plan["plan_id"],
        "plan_renews_at": plan["subscription"]["current_period_end"] if plan["subscription"] else None,
        "plan_cancel_at_period_end": bool(plan["subscription"]["cancel_at_period_end"]) if plan["subscription"] else False,
        "preferred_languages": [r["language_code"] for r in db.q("SELECT language_code FROM user_languages WHERE user_id=?", (u["id"],))],
        "preferred_categories": [r["slug"] for r in db.q("SELECT c.slug FROM user_categories uc JOIN categories c ON c.id=uc.category_id WHERE uc.user_id=?", (u["id"],))],
    }


# --------------------------------------------------------------------------- song serialisation

SONG_SELECT = """
SELECT s.id, s.slug, s.title, s.release_year, s.duration_sec, s.cover_key, s.is_explicit, s.is_featured, s.trending_pin,
       s.status, s.play_count, s.language_code, s.created_at, s.is_demo,
       a.id artist_id, a.slug artist_slug, a.name artist_name,
       al.id album_id, al.slug album_slug, al.title album_title,
       l.name language_name, l.native_name language_native,
       g.name genre_name,
       h.start_sec hl_start, h.end_sec hl_end, h.source hl_source,
       COALESCE(lic.allow_free_full_stream, 0) allow_free_full_stream, COALESCE(lic.allow_lyrics, 0) allow_lyrics,
       COALESCE(lic.allow_offline, 0) allow_offline,
       EXISTS(SELECT 1 FROM lyrics y WHERE y.song_id=s.id AND y.is_translation=0) has_lyrics_row
FROM songs s
JOIN artists a ON a.id=s.artist_id
LEFT JOIN albums al ON al.id=s.album_id
JOIN languages l ON l.code=s.language_code
LEFT JOIN genres g ON g.id=s.genre_id
LEFT JOIN song_highlights h ON h.song_id=s.id
LEFT JOIN licensing lic ON lic.song_id=s.id
"""

TRENDING_SCORE = """((SELECT COUNT(*) FROM listening_history th WHERE th.song_id=s.id AND th.played_at > {since}) * 800
                     + s.play_count * 0.01 + CASE WHEN s.release_year >= {year} THEN 12000 ELSE 0 END)"""


def trending_expr():
    return TRENDING_SCORE.format(since=db.now() - 7 * 86400, year=time.gmtime().tm_year - 1)


def cards(rows, user=None):
    if not rows:
        return []
    ids = [r["id"] for r in rows]
    ph = ",".join("?" * len(ids))
    cats = {}
    for r in db.q(f"SELECT sc.song_id, c.slug, c.name FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE sc.song_id IN ({ph}) ORDER BY c.sort_order", ids):
        cats.setdefault(r["song_id"], []).append({"slug": r["slug"], "name": r["name"]})
    favs = set()
    if user:
        favs = {r["song_id"] for r in db.q(f"SELECT song_id FROM favourites WHERE user_id=? AND song_id IN ({ph})", [user["id"], *ids])}
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "slug": r["slug"], "title": r["title"],
            "artist": {"id": r["artist_id"], "slug": r["artist_slug"], "name": r["artist_name"]},
            "album": {"id": r["album_id"], "slug": r["album_slug"], "title": r["album_title"]} if r["album_id"] else None,
            "language": {"code": r["language_code"], "name": r["language_name"], "native": r["language_native"]},
            "genre": r["genre_name"], "year": r["release_year"], "duration": round(r["duration_sec"] or 0, 2),
            "cover_url": storage.url(r["cover_key"]),
            "categories": cats.get(r["id"], []),
            "highlight": {"start": r["hl_start"], "end": r["hl_end"], "source": r["hl_source"]} if r["hl_start"] is not None else None,
            "has_lyrics": bool(r["has_lyrics_row"] and r["allow_lyrics"]),
            "premium_only": not r["allow_free_full_stream"],
            "is_favourite": r["id"] in favs, "is_explicit": bool(r["is_explicit"]), "is_featured": bool(r["is_featured"]),
            "plays": r["play_count"], "status": r["status"],
        })
    return out


def songs_where(where="1=1", params=(), order="s.play_count DESC", limit=20, offset=0, user=None, include_hidden=False):
    status = "" if include_hidden else " AND s.status='published'"
    rows = db.q(f"{SONG_SELECT} WHERE ({where}){status} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset))
    return cards(rows, user)


def songs_by_ids(ids, user=None):
    if not ids:
        return []
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in db.q(f"{SONG_SELECT} WHERE s.id IN ({ph}) AND s.status='published'", ids)}
    return cards([rows[i] for i in ids if i in rows], user)


def get_song(id_or_slug, include_hidden=False):
    r = db.one(f"{SONG_SELECT} WHERE s.id=? OR s.slug=?", (id_or_slug, id_or_slug))
    require(r and (include_hidden or r["status"] != "draft"), 404, "Song not found")
    return r


def lyric_excerpt(song_id, start, end):
    row = db.one("SELECT lines_json, is_synced FROM lyrics WHERE song_id=? AND is_translation=0", (song_id,))
    lic = db.one("SELECT allow_lyrics FROM licensing WHERE song_id=?", (song_id,))
    if not row or not lic or not lic["allow_lyrics"] or not row["is_synced"]:
        return None
    lines = json.loads(row["lines_json"])
    tr = db.one("SELECT lines_json FROM lyrics WHERE song_id=? AND is_translation=1", (song_id,))
    trl = json.loads(tr["lines_json"]) if tr else []
    out = []
    for i, l in enumerate(lines):
        if start - 0.5 <= l["t"] < end:
            out.append({"t": round(l["t"] - start, 2), "text": l["text"], "tr": trl[i]["text"] if i < len(trl) else None})
    return out[:6] or None


# --------------------------------------------------------------------------- public catalogue

HOME_RAILS = [
    ("sad", "Sad Songs"), ("love", "Love Songs"), ("heartbreak", "Heartbreak Songs"), ("romantic", "Romantic Songs"),
    ("feelings", "Feelings Songs"), ("emotional", "Emotional Songs"), ("motivational", "Motivational Songs"),
    ("devotional", "God & Devotional Songs"), ("dj", "DJ Songs"), ("party", "Party Songs"), ("dance", "Dance Songs"),
    ("friendship", "Friendship Songs"), ("melody", "Melody Songs"), ("old-classic", "Old Classic Songs"),
    ("new-generation", "New Generation Songs"), ("instrumental", "Instrumental Music"), ("background", "Background Music"),
    ("independent", "Independent Artists"), ("international", "International Music"),
]


def in_category(slug):
    return "s.id IN (SELECT sc.song_id FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE c.slug=?)", (slug,)


@route("GET", r"/api/health")
def health(req):
    return {"ok": True, "time": db.now()}


@route("GET", r"/api/config", auth="optional")
def get_config(req):
    return {
        "app_name": config.APP_NAME,
        "google_client_id": config.GOOGLE_CLIENT_ID or None,
        "payment_providers": [p.public_config() for p in payments.enabled_providers()],
        "free_skips_per_hour": int(db.setting("free_skips_per_hour")),
        "ad_every_n_tracks": int(db.setting("free_ad_every_n_tracks")),
        "ad_duration_seconds": int(db.setting("ad_duration_seconds")),
        "storage": storage.name, "ffmpeg": highlights.ffmpeg_available(),
    }


@route("GET", r"/api/home", auth="optional")
def home(req):
    u = req.user
    rails = []
    trending = songs_where(order=f"s.trending_pin IS NULL, s.trending_pin, {trending_expr()} DESC", limit=16, user=u)
    rails.append({"key": "trending", "title": "Trending Now", "subtitle": "What everyone is playing this week", "songs": trending, "highlight_feed": "trending"})
    rails.append({"key": "popular", "title": "Most Popular", "songs": songs_where(order="s.play_count DESC", limit=16, user=u)})
    rails.append({"key": "recent", "title": "Recently Released", "songs": songs_where(order="s.release_year DESC, s.created_at DESC", limit=16, user=u)})
    top = db.q(f"""{SONG_SELECT} WHERE s.status='published' ORDER BY
                   ((SELECT COUNT(*) FROM listening_history t WHERE t.song_id=s.id AND t.played_at > ?) * 1000 + s.play_count) DESC LIMIT 20""",
               (db.now() - 30 * 86400,))
    rails.append({"key": "top20", "title": "Top 20", "subtitle": "The chart, updated live", "songs": cards(top, u), "ranked": True})
    if u:
        rails.append({"key": "recommended", "title": "Recommended For You", "songs": songs_by_ids(recommend.recommend(u["id"], 16), u)})
    else:
        rails.append({"key": "recommended", "title": "Recommended For You", "subtitle": "Sign in to personalise these picks",
                      "songs": songs_by_ids(recommend.recommend(None, 16), u)})
    feeds = {r["slug"] for r in db.q("SELECT slug FROM categories WHERE has_highlight_feed=1")}
    for slug, title in HOME_RAILS:
        w, p = in_category(slug)
        songs = songs_where(w, p, order="s.is_featured DESC, s.play_count DESC", limit=16, user=u)
        rails.append({"key": slug, "title": title, "songs": songs, "category": slug, "highlight_feed": slug if slug in feeds else None})
    made = []
    if u:
        made = [{**m, "songs": songs_by_ids(m.pop("song_ids"), u)} for m in recommend.made_for_you(u["id"])]
    featured = songs_where("s.is_featured=1", order="s.play_count DESC", limit=6, user=u)
    return {"featured": featured, "rails": rails, "made_for_you": made}


@route("GET", r"/api/languages")
def languages(req):
    rows = db.q("""SELECT l.*, (SELECT COUNT(*) FROM songs s WHERE s.language_code=l.code AND s.status='published') song_count
                   FROM languages l WHERE l.is_active=1 ORDER BY l.featured DESC, l.sort_order""")
    return {"languages": rows}


@route("GET", r"/api/languages/(?P<code>[\w-]+)", auth="optional")
def language_detail(req, code):
    lang = db.one("SELECT * FROM languages WHERE code=? AND is_active=1", (code,))
    require(lang, 404, "Language not found")
    u, w = req.user, "s.language_code=?"
    sections = [
        {"key": "trending", "title": f"Trending in {lang['name']}", "songs": songs_where(w, (code,), order=f"{trending_expr()} DESC", limit=16, user=u)},
        {"key": "new", "title": "New Releases", "songs": songs_where(w, (code,), order="s.release_year DESC, s.created_at DESC", limit=16, user=u)},
    ]
    for slug, title in (("sad", "Sad Songs"), ("love", "Love Songs"), ("devotional", "Devotional Songs"), ("dj", "DJ Songs")):
        cw, cp = in_category(slug)
        sections.append({"key": slug, "title": title, "category": slug, "songs": songs_where(f"{w} AND {cw}", (code, *cp), limit=16, user=u)})
    sections.append({"key": "all", "title": "Complete Songs", "songs": songs_where(w, (code,), order="s.title COLLATE NOCASE", limit=100, user=u)})
    artists = db.q("""SELECT a.id, a.slug, a.name, a.image_key, SUM(s.play_count) plays FROM artists a JOIN songs s ON s.artist_id=a.id
                      WHERE s.language_code=? AND s.status='published' GROUP BY a.id ORDER BY plays DESC LIMIT 12""", (code,))
    albums = db.q("""SELECT al.id, al.slug, al.title, al.cover_key, al.release_year, a.name artist_name FROM albums al JOIN artists a ON a.id=al.artist_id
                     WHERE al.id IN (SELECT album_id FROM songs WHERE language_code=? AND status='published') ORDER BY al.release_year DESC LIMIT 12""", (code,))
    return {"language": lang, "sections": sections,
            "artists": [{**a, "image_url": storage.url(a.pop("image_key"))} for a in artists],
            "albums": [{**a, "cover_url": storage.url(a.pop("cover_key"))} for a in albums]}


@route("GET", r"/api/categories")
def categories(req):
    return {"categories": db.q("""SELECT c.*, (SELECT COUNT(*) FROM song_categories sc JOIN songs s ON s.id=sc.song_id
                                   WHERE sc.category_id=c.id AND s.status='published') song_count
                                   FROM categories c WHERE c.is_active=1 ORDER BY c.sort_order""")}


@route("GET", r"/api/categories/(?P<slug>[\w-]+)", auth="optional")
def category_detail(req, slug):
    c = db.one("SELECT * FROM categories WHERE slug=?", (slug,))
    require(c, 404, "Category not found")
    w, p = in_category(slug)
    lang = req.query.get("lang")
    if lang:
        w, p = w + " AND s.language_code=?", (*p, lang)
    return {"category": c, "songs": songs_where(w, p, order="s.play_count DESC", limit=200, user=req.user)}


SORTS = {"popular": "s.play_count DESC", "newest": "s.release_year DESC, s.created_at DESC", "oldest": "s.release_year ASC",
         "title": "s.title COLLATE NOCASE ASC", "duration": "s.duration_sec ASC", "trending": None}


@route("GET", r"/api/songs", auth="optional")
def list_songs(req):
    qd = req.query
    where, params = ["1=1"], []
    if qd.get("q"):
        like = f"%{qd['q'].strip()}%"
        where.append("(s.title LIKE ? OR a.name LIKE ? OR al.title LIKE ?)")
        params += [like, like, like]
    if qd.get("lang"):
        langs = [x for x in qd["lang"].split(",") if x]
        where.append(f"s.language_code IN ({','.join('?' * len(langs))})")
        params += langs
    if qd.get("category"):
        w, p = in_category(qd["category"])
        where.append(w); params += p
    if qd.get("genre"):
        where.append("g.name=?"); params.append(qd["genre"])
    if qd.get("year_from"):
        where.append("s.release_year>=?"); params.append(int_(qd["year_from"], "year_from"))
    if qd.get("year_to"):
        where.append("s.release_year<=?"); params.append(int_(qd["year_to"], "year_to"))
    if qd.get("has_lyrics") == "1":
        where.append("EXISTS(SELECT 1 FROM lyrics y WHERE y.song_id=s.id) AND lic.allow_lyrics=1")
    if qd.get("access") == "free":
        where.append("lic.allow_free_full_stream=1")
    sort = qd.get("sort", "popular")
    order = SORTS.get(sort) or f"{trending_expr()} DESC"
    limit = int_(qd.get("limit"), "limit", 1, 100) or 40
    page = int_(qd.get("page"), "page", 1) or 1
    w = " AND ".join(where)
    total = db.one(f"SELECT COUNT(*) n FROM songs s JOIN artists a ON a.id=s.artist_id LEFT JOIN albums al ON al.id=s.album_id LEFT JOIN genres g ON g.id=s.genre_id LEFT JOIN licensing lic ON lic.song_id=s.id WHERE {w} AND s.status='published'", params)["n"]
    return {"songs": songs_where(w, params, order=order, limit=limit, offset=(page - 1) * limit, user=req.user),
            "total": total, "page": page, "pages": max(1, -(-total // limit)),
            "facets": {"genres": [r["name"] for r in db.q("SELECT DISTINCT g.name FROM genres g JOIN songs s ON s.genre_id=g.id WHERE s.status='published' ORDER BY g.name")],
                       "years": db.one("SELECT MIN(release_year) min, MAX(release_year) max FROM songs WHERE status='published'")}}


@route("GET", r"/api/songs/(?P<sid>[\w-]+)", auth="optional")
def song_detail(req, sid):
    r = get_song(sid)
    if r["status"] == "taken_down":
        raise ApiError(451, "This song has been removed at the request of the rights holder.", "taken_down")
    song = cards([r], req.user)[0]
    lic = db.one("SELECT licensor, license_type, territories, allow_offline FROM licensing WHERE song_id=?", (r["id"],))
    song["license"] = lic
    song["more_by_artist"] = songs_where("s.artist_id=? AND s.id<>?", (r["artist_id"], r["id"]), limit=10, user=req.user)
    cat_ids = [c["slug"] for c in song["categories"]][:2]
    if cat_ids:
        w = " OR ".join(["s.id IN (SELECT sc.song_id FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE c.slug=?)"] * len(cat_ids))
        song["similar"] = songs_where(f"({w}) AND s.id<>?", (*cat_ids, r["id"]), limit=12, user=req.user)
    return {"song": song}


@route("GET", r"/api/songs/(?P<sid>[\w-]+)/lyrics", auth="optional")
def song_lyrics(req, sid):
    r = get_song(sid)
    lic = db.one("SELECT allow_lyrics, licensor FROM licensing WHERE song_id=?", (r["id"],))
    if not lic or not lic["allow_lyrics"]:
        return {"available": False, "reason": "Lyrics are not licensed for this song."}
    main = db.one("SELECT language_code, is_synced, lines_json, source FROM lyrics WHERE song_id=? AND is_translation=0", (r["id"],))
    if not main:
        return {"available": False, "reason": "Lyrics are not available for this song yet."}
    trs = db.q("SELECT language_code, lines_json, source FROM lyrics WHERE song_id=? AND is_translation=1", (r["id"],))
    lang = db.one("SELECT name, native_name FROM languages WHERE code=?", (main["language_code"],))
    return {"available": True, "language": main["language_code"], "language_name": lang["name"] if lang else main["language_code"],
            "synced": bool(main["is_synced"]), "lines": json.loads(main["lines_json"]), "source": main["source"],
            "translations": [{"language": t["language_code"], "lines": json.loads(t["lines_json"]), "source": t["source"]} for t in trs]}


@route("GET", r"/api/highlights", auth="optional")
def highlight_feed(req):
    feed = req.query.get("feed", "trending")
    offset = int_(req.query.get("offset"), "offset", 0) or 0
    limit = int_(req.query.get("limit"), "limit", 1, 30) or 10
    where, params = ["h.start_sec IS NOT NULL"], []
    if feed != "trending" and feed != "all":
        w, p = in_category(feed)
        where.append(w); params += p
    if req.query.get("lang"):
        where.append("s.language_code=?"); params.append(req.query["lang"])
    order = f"{trending_expr()} DESC" if feed in ("trending", "all") else "s.play_count DESC"
    if req.user:  # gently prefer the listener's languages (placeholder sits after the WHERE params)
        order = "(s.language_code IN (SELECT language_code FROM user_languages WHERE user_id=?)) DESC, " + order
        params.append(req.user["id"])
    items = songs_where(" AND ".join(where), params, order=order, limit=limit, offset=offset, user=req.user)
    for it in items:
        it["lyrics_excerpt"] = lyric_excerpt(it["id"], it["highlight"]["start"], it["highlight"]["end"]) if it["has_lyrics"] else None
        it["stream"] = stream_info(it["id"], "highlight", req.user)
    feeds = db.q("SELECT slug, name, color FROM categories WHERE has_highlight_feed=1 ORDER BY sort_order")
    return {"feed": feed, "items": items, "next_offset": offset + len(items) if len(items) == limit else None,
            "feeds": [{"slug": "trending", "name": "Trending", "color": "#7c5cff"}] + feeds}


def stream_info(song_id, kind, user):
    r = db.one("SELECT s.status, s.duration_sec, lic.allow_free_full_stream, h.start_sec, h.end_sec, h.preview_asset_id FROM songs s LEFT JOIN licensing lic ON lic.song_id=s.id LEFT JOIN song_highlights h ON h.song_id=s.id WHERE s.id=?", (song_id,))
    require(r, 404, "Song not found")
    if r["status"] == "taken_down":
        raise ApiError(451, "This song has been removed at the request of the rights holder.", "taken_down")
    require(r["status"] == "published", 404, "Song not found")
    premium = user_plan(user["id"] if user else None)["tier"] == "premium"
    full = db.one("SELECT * FROM audio_assets WHERE song_id=? AND kind='full' ORDER BY quality='high' DESC, created_at DESC", (song_id,))
    if not premium and full and full["quality"] == "high":
        full = db.one("SELECT * FROM audio_assets WHERE song_id=? AND kind='full' AND quality='standard' ORDER BY created_at DESC", (song_id,)) or full
    if kind == "highlight":
        require(r["start_sec"] is not None, 404, "This song has no highlight yet")
        prev = db.one("SELECT * FROM audio_assets WHERE id=?", (r["preview_asset_id"],)) if r["preview_asset_id"] else None
        if prev:
            return {"mode": "preview", "url": storage.url(prev["storage_key"]), "start": 0, "end": round(r["end_sec"] - r["start_sec"], 2),
                    "song_offset": r["start_sec"]}
        require(full, 404, "No audio uploaded for this song")
        return {"mode": "range", "url": storage.url(full["storage_key"]), "start": r["start_sec"], "end": r["end_sec"], "song_offset": 0}
    require(full, 404, "No audio uploaded for this song yet")
    if premium or r["allow_free_full_stream"]:
        return {"mode": "full", "url": storage.url(full["storage_key"]), "start": 0, "end": r["duration_sec"], "quality": full["quality"],
                "premium": premium}
    out = {"mode": "restricted", "reason": "premium_required", "message": "The rights holder has made this full song available to Premium listeners."}
    if r["start_sec"] is not None:
        out["preview"] = stream_info(song_id, "highlight", user)
    return out


@route("GET", r"/api/stream/(?P<sid>[\w-]+)", auth="optional")
def stream(req, sid):
    song = get_song(sid)
    kind = req.query.get("kind", "full")
    require(kind in ("full", "highlight"), 400, "kind must be full or highlight")
    return stream_info(song["id"], kind, req.user)


@route("GET", r"/api/artists/(?P<slug>[\w-]+)", auth="optional")
def artist_detail(req, slug):
    a = db.one("SELECT * FROM artists WHERE slug=? OR id=?", (slug, slug))
    require(a, 404, "Artist not found")
    a["image_url"] = storage.url(a.pop("image_key"))
    songs = songs_where("s.artist_id=?", (a["id"],), order="s.play_count DESC", limit=100, user=req.user)
    albums = [{**al, "cover_url": storage.url(al.pop("cover_key"))} for al in db.q("SELECT id, slug, title, cover_key, release_year FROM albums WHERE artist_id=? ORDER BY release_year DESC", (a["id"],))]
    a["monthly_plays"] = sum(s["plays"] for s in songs)
    return {"artist": a, "songs": songs, "albums": albums}


@route("GET", r"/api/albums/(?P<aid>[\w-]+)", auth="optional")
def album_detail(req, aid):
    al = db.one("SELECT al.*, a.name artist_name, a.slug artist_slug FROM albums al JOIN artists a ON a.id=al.artist_id WHERE al.id=? OR al.slug=?", (aid, aid))
    require(al, 404, "Album not found")
    al["cover_url"] = storage.url(al.pop("cover_key"))
    return {"album": al, "songs": songs_where("s.album_id=?", (al["id"],), order="s.created_at", limit=100, user=req.user)}


# --------------------------------------------------------------------------- search

@route("GET", r"/api/search", auth="optional")
def search(req):
    q = (req.query.get("q") or "").strip()
    if not q:
        return {"q": q, "songs": [], "artists": [], "albums": [], "categories": [], "languages": [], "lyrics_matches": []}
    require(len(q) <= 100, 400, "Search is too long")
    like, pre = f"%{q}%", f"{q}%"
    langs = db.q("SELECT code, name, native_name FROM languages WHERE is_active=1 AND (name LIKE ? OR native_name LIKE ? OR code=?)", (like, like, q.lower()))
    cats = db.q("SELECT slug, name, color FROM categories WHERE is_active=1 AND (name LIKE ? OR slug LIKE ?)", (like, like))
    conds = ["s.title LIKE :like", "a.name LIKE :like", "al.title LIKE :like", "g.name LIKE :like"]
    params = {"like": like, "pre": pre}
    for i, l in enumerate(langs):
        conds.append(f"s.language_code = :l{i}"); params[f"l{i}"] = l["code"]
    for i, c in enumerate(cats):
        conds.append(f"s.id IN (SELECT sc.song_id FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE c.slug=:c{i})"); params[f"c{i}"] = c["slug"]
    rank = "(CASE WHEN s.title LIKE :pre THEN 0 WHEN s.title LIKE :like THEN 1 WHEN a.name LIKE :like THEN 2 WHEN al.title LIKE :like THEN 3 ELSE 4 END)"
    rows = db.q(f"{SONG_SELECT} WHERE ({' OR '.join(conds)}) AND s.status='published' ORDER BY {rank}, s.play_count DESC LIMIT 40", params)
    lyric_rows = db.q(f"""{SONG_SELECT} JOIN lyrics y ON y.song_id=s.id WHERE y.plain_text LIKE :like AND lic.allow_lyrics=1 AND s.status='published'
                          GROUP BY s.id LIMIT 10""", params)
    lyric_matches = []
    for c in cards(lyric_rows, req.user):
        txt = db.q("SELECT plain_text FROM lyrics WHERE song_id=?", (c["id"],))
        line = next((ln for t in txt for ln in t["plain_text"].split("\n") if q.lower() in ln.lower()), None)
        lyric_matches.append({"song": c, "line": line})
    artists = db.q("SELECT id, slug, name, image_key, country FROM artists WHERE name LIKE ? ORDER BY name LIMIT 10", (like,))
    albums = db.q("SELECT al.id, al.slug, al.title, al.cover_key, al.release_year, a.name artist_name FROM albums al JOIN artists a ON a.id=al.artist_id WHERE al.title LIKE ? LIMIT 10", (like,))
    return {"q": q, "songs": cards(rows, req.user),
            "artists": [{**a, "image_url": storage.url(a.pop("image_key"))} for a in artists],
            "albums": [{**a, "cover_url": storage.url(a.pop("cover_key"))} for a in albums],
            "categories": cats, "languages": langs, "lyrics_matches": lyric_matches}


@route("GET", r"/api/search/suggest")
def suggest(req):
    q = (req.query.get("q") or "").strip()
    if len(q) < 1:
        return {"suggestions": []}
    like, pre = f"%{q}%", f"{q}%"
    out = []
    for r in db.q("SELECT s.slug, s.title, a.name artist FROM songs s JOIN artists a ON a.id=s.artist_id WHERE s.status='published' AND s.title LIKE ? ORDER BY s.title LIKE ? DESC, s.play_count DESC LIMIT 5", (like, pre)):
        out.append({"type": "song", "label": r["title"], "sub": r["artist"], "href": f"/song/{r['slug']}"})
    for r in db.q("SELECT slug, name FROM artists WHERE name LIKE ? LIMIT 3", (like,)):
        out.append({"type": "artist", "label": r["name"], "sub": "Artist", "href": f"/artist/{r['slug']}"})
    for r in db.q("SELECT slug, title FROM albums WHERE title LIKE ? LIMIT 2", (like,)):
        out.append({"type": "album", "label": r["title"], "sub": "Album", "href": f"/album/{r['slug']}"})
    for r in db.q("SELECT code, name, native_name FROM languages WHERE is_active=1 AND (name LIKE ? OR native_name LIKE ?) LIMIT 2", (like, like)):
        out.append({"type": "language", "label": r["name"], "sub": r["native_name"], "href": f"/language/{r['code']}"})
    for r in db.q("SELECT slug, name FROM categories WHERE is_active=1 AND name LIKE ? LIMIT 2", (like,)):
        out.append({"type": "category", "label": r["name"], "sub": "Mood & category", "href": f"/category/{r['slug']}"})
    return {"suggestions": out[:12]}


# --------------------------------------------------------------------------- auth

EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_password_strength(pw):
    require(isinstance(pw, str) and 8 <= len(pw) <= 128, 400, "Password must be 8-128 characters")
    require(re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw), 400, "Password needs at least one letter and one number")


def auth_response(u):
    db.run("UPDATE users SET last_login_at=? WHERE id=?", (db.now(), u["id"]))
    return {"token": security.make_token(u["id"], u["token_version"]), "user": public_user(u)}


@route("POST", r"/api/auth/register")
def register(req):
    require(security.register_limiter.allow(req.ip), 429, "Too many sign-ups from this network. Try again later.")
    b = req.json
    email = s_(b.get("email"), "Email", 254).lower()
    require(EMAIL_RX.match(email), 400, "Enter a valid email address")
    name = s_(b.get("display_name"), "Name", 60)
    check_password_strength(b.get("password"))
    require(not db.one("SELECT 1 FROM users WHERE email=?", (email,)), 409, "An account with this email already exists")
    uid = db.new_id("usr")
    db.run("INSERT INTO users(id,email,password_hash,display_name,created_at) VALUES(?,?,?,?,?)",
           (uid, email, security.hash_password(b["password"]), name, db.now()))
    set_prefs(uid, b.get("preferred_languages"), b.get("preferred_categories"))
    return auth_response(db.one("SELECT * FROM users WHERE id=?", (uid,)))


@route("POST", r"/api/auth/login")
def login(req):
    require(security.login_limiter.allow(req.ip), 429, "Too many sign-in attempts. Wait a few minutes and try again.")
    b = req.json
    email = (b.get("email") or "").strip().lower()
    u = db.one("SELECT * FROM users WHERE email=?", (email,))
    if not u or not u["password_hash"] or not security.verify_password(b.get("password") or "", u["password_hash"]):
        raise ApiError(401, "Email or password is incorrect")
    require(u["is_active"], 403, "This account has been suspended. Contact support.")
    return auth_response(u)


@route("POST", r"/api/auth/google")
def google_login(req):
    require(config.GOOGLE_CLIENT_ID, 501, "Google sign-in is not configured on this server")
    cred = s_(req.json.get("credential"), "credential", 4096)
    try:
        with urllib.request.urlopen("https://oauth2.googleapis.com/tokeninfo?id_token=" + urllib.parse.quote(cred), timeout=10) as r:
            info = json.loads(r.read())
    except Exception:
        raise ApiError(401, "Could not verify Google sign-in")
    require(info.get("aud") == config.GOOGLE_CLIENT_ID and info.get("iss") in ("accounts.google.com", "https://accounts.google.com"), 401, "Invalid Google token")
    require(str(info.get("email_verified")).lower() == "true", 401, "Google email is not verified")
    email, sub = info["email"].lower(), info["sub"]
    u = db.one("SELECT * FROM users WHERE google_sub=? OR email=?", (sub, email))
    if u:
        db.run("UPDATE users SET google_sub=? WHERE id=?", (sub, u["id"]))
    else:
        uid = db.new_id("usr")
        db.run("INSERT INTO users(id,email,display_name,auth_provider,google_sub,avatar_key,created_at) VALUES(?,?,?,?,?,?,?)",
               (uid, email, info.get("name") or email.split("@")[0], "google", sub, info.get("picture"), db.now()))
        u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    require(u["is_active"], 403, "This account has been suspended")
    return auth_response(u)


@route("GET", r"/api/auth/me", auth="optional")
def me(req):
    return {"user": public_user(req.user)}


@route("POST", r"/api/auth/logout-all", auth="user")
def logout_all(req):
    db.run("UPDATE users SET token_version=token_version+1 WHERE id=?", (req.user["id"],))
    return {"ok": True}


@route("POST", r"/api/auth/change-password", auth="user")
def change_password(req):
    b = req.json
    u = req.user
    if u["password_hash"]:
        require(security.verify_password(b.get("current_password") or "", u["password_hash"]), 400, "Current password is incorrect")
    check_password_strength(b.get("new_password"))
    db.run("UPDATE users SET password_hash=?, token_version=token_version+1 WHERE id=?", (security.hash_password(b["new_password"]), u["id"]))
    return auth_response(db.one("SELECT * FROM users WHERE id=?", (u["id"],)))


# --------------------------------------------------------------------------- me / profile

def set_prefs(uid, langs, cats):
    if langs is not None:
        require(isinstance(langs, list) and len(langs) <= 40, 400, "preferred_languages must be a list")
        db.run("DELETE FROM user_languages WHERE user_id=?", (uid,))
        for c in langs:
            if db.one("SELECT 1 FROM languages WHERE code=?", (c,)):
                db.run("INSERT OR IGNORE INTO user_languages VALUES(?,?)", (uid, c))
    if cats is not None:
        require(isinstance(cats, list) and len(cats) <= 40, 400, "preferred_categories must be a list")
        db.run("DELETE FROM user_categories WHERE user_id=?", (uid,))
        for slug in cats:
            c = db.one("SELECT id FROM categories WHERE slug=?", (slug,))
            if c:
                db.run("INSERT OR IGNORE INTO user_categories VALUES(?,?)", (uid, c["id"]))


@route("PATCH", r"/api/me", auth="user")
def update_me(req):
    b, uid = req.json, req.user["id"]
    if "display_name" in b:
        db.run("UPDATE users SET display_name=? WHERE id=?", (s_(b["display_name"], "Name", 60), uid))
    if "theme" in b:
        require(b["theme"] in ("dark", "light"), 400, "theme must be dark or light")
        db.run("UPDATE users SET theme=? WHERE id=?", (b["theme"], uid))
    set_prefs(uid, b.get("preferred_languages"), b.get("preferred_categories"))
    return {"user": public_user(db.one("SELECT * FROM users WHERE id=?", (uid,)))}


IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
AUDIO_TYPES = {"audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg",
               "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav", "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/webm": ".webm"}


def save_image(req, prefix):
    ct = req.headers.get("Content-Type", "").split(";")[0].lower()
    require(ct in IMAGE_TYPES, 415, "Upload a PNG, JPEG or WebP image")
    data = req.body
    require(data and len(data) <= 8 * 1024 * 1024, 413, "Image must be under 8 MB")
    sig_ok = data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    require(sig_ok, 415, "File content does not look like a valid image")
    key = f"{prefix}/{secrets.token_hex(8)}{IMAGE_TYPES[ct]}"
    storage.put(key, data, ct)
    return key


@route("PUT", r"/api/me/avatar", auth="user")
def upload_avatar(req):
    key = save_image(req, "uploads/avatars")
    db.run("UPDATE users SET avatar_key=? WHERE id=?", (key, req.user["id"]))
    return {"avatar_url": storage.url(key)}


@route("GET", r"/api/me/history", auth="user")
def get_history(req):
    rows = db.q("""SELECT h.song_id, MAX(h.played_at) last_played, COUNT(*) plays, SUM(h.seconds_listened) secs
                   FROM listening_history h WHERE h.user_id=? GROUP BY h.song_id ORDER BY last_played DESC LIMIT 100""", (req.user["id"],))
    songs = {s["id"]: s for s in songs_by_ids([r["song_id"] for r in rows], req.user)}
    return {"items": [{"song": songs[r["song_id"]], "last_played": r["last_played"], "plays": r["plays"], "seconds": round(r["secs"] or 0)} for r in rows if r["song_id"] in songs]}


@route("POST", r"/api/me/history", auth="user")
def add_history(req):
    b = req.json
    song = get_song(s_(b.get("song_id"), "song_id", 40))
    secs = float(b.get("seconds") or 0)
    require(0 <= secs <= 4 * 3600, 400, "seconds out of range")
    ctx = b.get("context", "full")
    require(ctx in ("full", "highlight"), 400, "context must be full or highlight")
    completed = 1 if b.get("completed") else 0
    db.run("INSERT INTO listening_history(user_id,song_id,context,seconds_listened,completed,played_at) VALUES(?,?,?,?,?,?)",
           (req.user["id"], song["id"], ctx, secs, completed, db.now()))
    if (ctx == "full" and secs >= 30) or (ctx == "highlight" and secs >= 15):
        db.run("UPDATE songs SET play_count=play_count+1 WHERE id=?", (song["id"],))
    return {"ok": True}


@route("DELETE", r"/api/me/history", auth="user")
def clear_history(req):
    db.run("DELETE FROM listening_history WHERE user_id=?", (req.user["id"],))
    return {"ok": True}


@route("GET", r"/api/me/recently-played", auth="user")
def recently_played(req):
    ids = [r["song_id"] for r in db.q("SELECT song_id, MAX(played_at) m FROM listening_history WHERE user_id=? GROUP BY song_id ORDER BY m DESC LIMIT 20", (req.user["id"],))]
    return {"songs": songs_by_ids(ids, req.user)}


@route("GET", r"/api/me/favourites", auth="user")
def get_favs(req):
    ids = [r["song_id"] for r in db.q("SELECT song_id FROM favourites WHERE user_id=? ORDER BY created_at DESC", (req.user["id"],))]
    return {"songs": songs_by_ids(ids, req.user)}


@route("PUT", r"/api/me/favourites/(?P<sid>[\w-]+)", auth="user")
def add_fav(req, sid):
    s = get_song(sid)
    db.run("INSERT OR IGNORE INTO favourites(user_id,song_id,created_at) VALUES(?,?,?)", (req.user["id"], s["id"], db.now()))
    return {"ok": True, "is_favourite": True}


@route("DELETE", r"/api/me/favourites/(?P<sid>[\w-]+)", auth="user")
def del_fav(req, sid):
    s = get_song(sid, include_hidden=True)
    db.run("DELETE FROM favourites WHERE user_id=? AND song_id=?", (req.user["id"], s["id"]))
    return {"ok": True, "is_favourite": False}


@route("GET", r"/api/me/recommendations", auth="user")
def my_recs(req):
    mood = req.query.get("mood")
    return {"songs": songs_by_ids(recommend.recommend(req.user["id"], 30, mood=mood), req.user),
            "made_for_you": [{**m, "songs": songs_by_ids(m.pop("song_ids"), req.user)} for m in recommend.made_for_you(req.user["id"])]}


@route("POST", r"/api/me/skip", auth="user")
def record_skip(req):
    if user_plan(req.user["id"])["tier"] == "premium":
        return {"allowed": True, "remaining": None}
    limit = int(db.setting("free_skips_per_hour"))
    since = db.now() - 3600
    used = db.one("SELECT COUNT(*) n FROM skip_events WHERE user_id=? AND at>?", (req.user["id"], since))["n"]
    if used >= limit:
        oldest = db.one("SELECT MIN(at) t FROM skip_events WHERE user_id=? AND at>?", (req.user["id"], since))["t"]
        return {"allowed": False, "remaining": 0, "resets_in": oldest + 3600 - db.now()}
    db.run("INSERT INTO skip_events(user_id,at) VALUES(?,?)", (req.user["id"], db.now()))
    return {"allowed": True, "remaining": limit - used - 1}


AUTO_PLAYLISTS = [("sad", "My Sad Songs", "sad"), ("love", "My Love Songs", "love"), ("devotional", "My Favourite Devotional Songs", "devotional"),
                  ("dj", "My DJ Collection", "dj"), ("trending", "My Trending Songs", None)]


def auto_playlist_ids(uid, key):
    cat = next((c for k, _, c in AUTO_PLAYLISTS if k == key), "missing")
    require(cat != "missing", 404, "Unknown auto playlist")
    mine = """SELECT song_id FROM favourites WHERE user_id=:u UNION
              SELECT song_id FROM listening_history WHERE user_id=:u GROUP BY song_id HAVING SUM(seconds_listened) >= 20"""
    if cat:
        rows = db.q(f"""SELECT s.id FROM songs s WHERE s.status='published' AND s.id IN ({mine})
                        AND s.id IN (SELECT sc.song_id FROM song_categories sc JOIN categories c ON c.id=sc.category_id WHERE c.slug=:c)
                        ORDER BY s.play_count DESC""", {"u": uid, "c": cat})
    else:
        rows = db.q(f"SELECT s.id FROM songs s WHERE s.status='published' AND s.id IN ({mine}) ORDER BY {trending_expr()} DESC LIMIT 50", {"u": uid})
    return [r["id"] for r in rows]


@route("GET", r"/api/me/auto-playlists", auth="user")
def auto_playlists(req):
    out = []
    for key, title, cat in AUTO_PLAYLISTS:
        ids = auto_playlist_ids(req.user["id"], key)
        out.append({"id": f"auto-{key}", "key": key, "title": title, "count": len(ids), "category": cat,
                    "cover_urls": [s["cover_url"] for s in songs_by_ids(ids[:4])]})
    return {"playlists": out}


@route("GET", r"/api/me/auto-playlists/(?P<key>[\w-]+)", auth="user")
def auto_playlist(req, key):
    ids = auto_playlist_ids(req.user["id"], key)
    title = next(t for k, t, _ in AUTO_PLAYLISTS if k == key)
    return {"playlist": {"id": f"auto-{key}", "title": title, "is_auto": True, "description": "Updated automatically from your favourites and listening."},
            "songs": songs_by_ids(ids, req.user)}


# --------------------------------------------------------------------------- playlists

def load_playlist(pid, user, need_owner=False):
    p = db.one("SELECT p.*, u.display_name owner_name FROM playlists p JOIN users u ON u.id=p.owner_id WHERE p.id=?", (pid,))
    require(p, 404, "Playlist not found")
    is_owner = bool(user and user["id"] == p["owner_id"])
    require(is_owner or p["is_public"], 404, "Playlist not found")
    if need_owner:
        require(is_owner, 403, "Only the owner can change this playlist")
    p["is_owner"] = is_owner
    return p


def playlist_payload(p, user):
    rows = db.q(f"{SONG_SELECT} JOIN playlist_songs ps ON ps.song_id=s.id WHERE ps.playlist_id=? ORDER BY ps.position", (p["id"],))
    available = [r for r in rows if r["status"] == "published"]
    p["song_count"] = len(available)
    p["unavailable_count"] = len(rows) - len(available)
    p["followers"] = db.one("SELECT COUNT(*) n FROM playlist_follows WHERE playlist_id=?", (p["id"],))["n"]
    p["is_following"] = bool(user and db.one("SELECT 1 FROM playlist_follows WHERE user_id=? AND playlist_id=?", (user["id"], p["id"])))
    p["share_url"] = f"{config.PUBLIC_URL}/playlist/shared/{p['share_token']}"
    if not p["is_owner"]:
        p.pop("share_token", None)
    songs = cards(available, user)
    p["cover_urls"] = [s["cover_url"] for s in songs[:4]]
    return {"playlist": p, "songs": songs}


@route("GET", r"/api/me/playlists", auth="user")
def my_playlists(req):
    rows = db.q("""SELECT p.*, (SELECT COUNT(*) FROM playlist_songs ps WHERE ps.playlist_id=p.id) song_count, 1 is_owner FROM playlists p
                   WHERE p.owner_id=? ORDER BY p.updated_at DESC""", (req.user["id"],))
    followed = db.q("""SELECT p.*, u.display_name owner_name, (SELECT COUNT(*) FROM playlist_songs ps WHERE ps.playlist_id=p.id) song_count, 0 is_owner
                       FROM playlist_follows f JOIN playlists p ON p.id=f.playlist_id JOIN users u ON u.id=p.owner_id
                       WHERE f.user_id=? AND p.is_public=1""", (req.user["id"],))
    for p in rows + followed:
        p["cover_urls"] = [storage.url(r["cover_key"]) for r in db.q(
            "SELECT s.cover_key FROM playlist_songs ps JOIN songs s ON s.id=ps.song_id WHERE ps.playlist_id=? ORDER BY ps.position LIMIT 4", (p["id"],))]
    return {"playlists": rows, "followed": followed}


@route("GET", r"/api/playlists/public", auth="optional")
def public_playlists(req):
    rows = db.q("""SELECT p.id, p.title, p.description, u.display_name owner_name,
                   (SELECT COUNT(*) FROM playlist_songs ps WHERE ps.playlist_id=p.id) song_count,
                   (SELECT COUNT(*) FROM playlist_follows f WHERE f.playlist_id=p.id) followers
                   FROM playlists p JOIN users u ON u.id=p.owner_id WHERE p.is_public=1 ORDER BY followers DESC, p.updated_at DESC LIMIT 30""")
    for p in rows:
        p["cover_urls"] = [storage.url(r["cover_key"]) for r in db.q(
            "SELECT s.cover_key FROM playlist_songs ps JOIN songs s ON s.id=ps.song_id WHERE ps.playlist_id=? ORDER BY ps.position LIMIT 4", (p["id"],))]
    return {"playlists": rows}


@route("POST", r"/api/playlists", auth="user")
def create_playlist(req):
    b = req.json
    pid, t = db.new_id("pl"), db.now()
    db.run("INSERT INTO playlists(id,owner_id,title,description,is_public,share_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
           (pid, req.user["id"], s_(b.get("title"), "Title", 80), s_(b.get("description"), "Description", 300, False) or "",
            1 if b.get("is_public") else 0, secrets.token_urlsafe(12), t, t))
    for sid in (b.get("song_ids") or [])[:500]:
        _add_song(pid, sid)
    return playlist_payload(load_playlist(pid, req.user, True), req.user)


@route("GET", r"/api/playlists/shared/(?P<token>[\w-]+)", auth="optional")
def shared_playlist(req, token):
    p = db.one("SELECT p.*, u.display_name owner_name FROM playlists p JOIN users u ON u.id=p.owner_id WHERE p.share_token=?", (token,))
    require(p, 404, "This share link is no longer valid")
    p["is_owner"] = bool(req.user and req.user["id"] == p["owner_id"])
    return playlist_payload(p, req.user)


@route("GET", r"/api/playlists/(?P<pid>pl_\w+)", auth="optional")
def get_playlist(req, pid):
    p = db.one("SELECT p.*, u.display_name owner_name FROM playlists p JOIN users u ON u.id=p.owner_id WHERE p.id=?", (pid,))
    require(p and (p["is_public"] or (req.user and req.user["id"] == p["owner_id"])), 404, "Playlist not found")
    p["is_owner"] = bool(req.user and req.user["id"] == p["owner_id"])
    return playlist_payload(p, req.user)


@route("PATCH", r"/api/playlists/(?P<pid>pl_\w+)", auth="user")
def edit_playlist(req, pid):
    p = load_playlist(pid, req.user, True)
    b = req.json
    title = s_(b["title"], "Title", 80) if "title" in b else p["title"]
    desc = (s_(b["description"], "Description", 300, False) or "") if "description" in b else p["description"]
    pub = (1 if b["is_public"] else 0) if "is_public" in b else p["is_public"]
    db.run("UPDATE playlists SET title=?, description=?, is_public=?, updated_at=? WHERE id=?", (title, desc, pub, db.now(), pid))
    return playlist_payload(load_playlist(pid, req.user, True), req.user)


@route("DELETE", r"/api/playlists/(?P<pid>pl_\w+)", auth="user")
def delete_playlist(req, pid):
    load_playlist(pid, req.user, True)
    db.run("DELETE FROM playlists WHERE id=?", (pid,))
    return {"ok": True}


def _add_song(pid, sid):
    s = get_song(sid)
    pos = db.one("SELECT COALESCE(MAX(position), -1) + 1 p FROM playlist_songs WHERE playlist_id=?", (pid,))["p"]
    cur = db.run("INSERT OR IGNORE INTO playlist_songs(playlist_id,song_id,position,added_at) VALUES(?,?,?,?)", (pid, s["id"], pos, db.now()))
    return cur.rowcount > 0


@route("POST", r"/api/playlists/(?P<pid>pl_\w+)/songs", auth="user")
def playlist_add(req, pid):
    load_playlist(pid, req.user, True)
    added = _add_song(pid, s_(req.json.get("song_id"), "song_id", 40))
    db.run("UPDATE playlists SET updated_at=? WHERE id=?", (db.now(), pid))
    return {"ok": True, "added": added, "message": "Added to playlist" if added else "Already in this playlist"}


@route("DELETE", r"/api/playlists/(?P<pid>pl_\w+)/songs/(?P<sid>[\w-]+)", auth="user")
def playlist_remove(req, pid, sid):
    load_playlist(pid, req.user, True)
    db.run("DELETE FROM playlist_songs WHERE playlist_id=? AND song_id=?", (pid, sid))
    db.run("UPDATE playlists SET updated_at=? WHERE id=?", (db.now(), pid))
    return {"ok": True}


@route("PUT", r"/api/playlists/(?P<pid>pl_\w+)/order", auth="user")
def playlist_order(req, pid):
    load_playlist(pid, req.user, True)
    ids = req.json.get("song_ids")
    current = {r["song_id"] for r in db.q("SELECT song_id FROM playlist_songs WHERE playlist_id=?", (pid,))}
    require(isinstance(ids, list) and set(ids) <= current, 400, "song_ids must list songs already in the playlist")
    rest = [i for i in current if i not in ids]
    with db.tx():
        for pos, sid in enumerate(list(ids) + rest):
            db.run("UPDATE playlist_songs SET position=? WHERE playlist_id=? AND song_id=?", (pos, pid, sid))
        db.run("UPDATE playlists SET updated_at=? WHERE id=?", (db.now(), pid))
    return {"ok": True}


@route("POST", r"/api/playlists/(?P<pid>pl_\w+)/follow", auth="user")
def follow(req, pid):
    p = db.one("SELECT owner_id, is_public FROM playlists WHERE id=?", (pid,))
    require(p and p["is_public"], 404, "Playlist not found")
    require(p["owner_id"] != req.user["id"], 400, "You own this playlist")
    db.run("INSERT OR IGNORE INTO playlist_follows VALUES(?,?,?)", (req.user["id"], pid, db.now()))
    return {"ok": True, "is_following": True}


@route("DELETE", r"/api/playlists/(?P<pid>pl_\w+)/follow", auth="user")
def unfollow(req, pid):
    db.run("DELETE FROM playlist_follows WHERE user_id=? AND playlist_id=?", (req.user["id"], pid))
    return {"ok": True, "is_following": False}


# --------------------------------------------------------------------------- subscriptions

@route("GET", r"/api/plans", auth="optional")
def plans(req):
    rows = db.q("SELECT * FROM plans WHERE is_active=1 ORDER BY sort_order")
    for r in rows:
        r["features"] = json.loads(r.pop("features_json"))
    return {"plans": rows, "providers": [p.public_config() for p in payments.enabled_providers()],
            "current": public_user(req.user)["plan_id"] if req.user else None}


@route("POST", r"/api/subscriptions/checkout", auth="user")
def checkout(req):
    b = req.json
    plan = db.one("SELECT * FROM plans WHERE id=? AND is_active=1 AND tier='premium'", (b.get("plan_id"),))
    require(plan, 404, "Plan not found")
    try:
        provider = payments.get(b.get("provider"))
        order = provider.create_order(plan["price_minor"], plan["currency"], f"rf_{secrets.token_hex(6)}")
    except payments.PaymentError as e:
        raise ApiError(400, str(e))
    sub_id, pay_id, t = db.new_id("sub"), db.new_id("pay"), db.now()
    with db.tx():
        db.run("INSERT INTO subscriptions(id,user_id,plan_id,status,provider,created_at) VALUES(?,?,?,?,?,?)", (sub_id, req.user["id"], plan["id"], "pending", provider.key, t))
        db.run("""INSERT INTO payments(id,user_id,subscription_id,provider,provider_order_id,amount_minor,currency,status,created_at,updated_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?)""", (pay_id, req.user["id"], sub_id, provider.key, order["order_id"], plan["price_minor"], plan["currency"], "created", t, t))
    return {"payment_id": pay_id, "order": order, "provider": provider.public_config(), "plan": {"id": plan["id"], "name": plan["name"]},
            "prefill": {"email": req.user["email"], "name": req.user["display_name"]}}


def activate_payment(pay, provider_payment_id, method):
    plan = db.one("SELECT p.* FROM plans p JOIN subscriptions s ON s.plan_id=p.id WHERE s.id=?", (pay["subscription_id"],))
    t = db.now()
    current = user_plan(pay["user_id"])["subscription"]
    start = max(t, current["current_period_end"]) if current else t
    end = start + (365 if plan["interval"] == "year" else 30) * 86400
    with db.tx():
        db.run("UPDATE payments SET status='paid', provider_payment_id=?, method=?, updated_at=? WHERE id=?", (provider_payment_id, method, t, pay["id"]))
        db.run("UPDATE subscriptions SET status='active', started_at=?, current_period_end=? WHERE id=?", (t, end, pay["subscription_id"]))


@route("POST", r"/api/subscriptions/verify", auth="user")
def verify_payment(req):
    b = req.json
    pay = db.one("SELECT * FROM payments WHERE id=? AND user_id=?", (b.get("payment_id"), req.user["id"]))
    require(pay, 404, "Payment not found")
    if pay["status"] == "paid":
        return {"ok": True, "user": public_user(req.user)}
    require(pay["status"] == "created", 400, "This payment can no longer be completed")
    try:
        result = payments.get(pay["provider"]).verify(pay["provider_order_id"], b)
    except payments.PaymentError as e:
        db.run("UPDATE payments SET status='failed', updated_at=? WHERE id=?", (db.now(), pay["id"]))
        raise ApiError(400, str(e))
    if not result["paid"]:
        db.run("UPDATE payments SET status='failed', updated_at=? WHERE id=?", (db.now(), pay["id"]))
        db.run("UPDATE subscriptions SET status='expired' WHERE id=?", (pay["subscription_id"],))
        raise ApiError(402, "Payment was not completed. You have not been charged.")
    activate_payment(pay, result["payment_id"], result.get("method"))
    return {"ok": True, "user": public_user(db.one("SELECT * FROM users WHERE id=?", (req.user["id"],)))}


@route("POST", r"/api/webhooks/razorpay")
def razorpay_webhook(req):
    require(payments.RazorpayProvider.verify_webhook(req.body, req.headers.get("X-Razorpay-Signature")), 401, "Bad signature")
    ev = req.json
    if ev.get("event") in ("payment.captured", "order.paid"):
        ent = ev.get("payload", {}).get("payment", {}).get("entity", {})
        pay = db.one("SELECT * FROM payments WHERE provider='razorpay' AND provider_order_id=?", (ent.get("order_id"),))
        if pay and pay["status"] == "created":
            activate_payment(pay, ent.get("id"), ent.get("method"))
    return {"ok": True}


@route("POST", r"/api/subscriptions/cancel", auth="user")
def cancel_sub(req):
    sub = user_plan(req.user["id"])["subscription"]
    require(sub, 400, "You don't have an active Premium subscription")
    db.run("UPDATE subscriptions SET cancel_at_period_end=1, status='cancelled' WHERE id=?", (sub["id"],))
    return {"ok": True, "user": public_user(req.user)}


@route("GET", r"/api/me/payments", auth="user")
def my_payments(req):
    return {"payments": db.q("SELECT p.id, p.provider, p.amount_minor, p.currency, p.status, p.method, p.created_at, pl.name plan_name FROM payments p LEFT JOIN subscriptions s ON s.id=p.subscription_id LEFT JOIN plans pl ON pl.id=s.plan_id WHERE p.user_id=? ORDER BY p.created_at DESC", (req.user["id"],))}


# --------------------------------------------------------------------------- admin

@route("GET", r"/api/admin/stats", auth="analytics")
def admin_stats(req):
    t = db.now()
    day = 86400
    totals = {
        "users": db.one("SELECT COUNT(*) n FROM users")["n"],
        "new_users_7d": db.one("SELECT COUNT(*) n FROM users WHERE created_at>?", (t - 7 * day,))["n"],
        "songs": db.one("SELECT COUNT(*) n FROM songs WHERE status='published'")["n"],
        "artists": db.one("SELECT COUNT(*) n FROM artists")["n"],
        "plays_30d": db.one("SELECT COUNT(*) n FROM listening_history WHERE played_at>?", (t - 30 * day,))["n"],
        "listening_hours_30d": round((db.one("SELECT SUM(seconds_listened) n FROM listening_history WHERE played_at>?", (t - 30 * day,))["n"] or 0) / 3600, 1),
        "premium_users": db.one("SELECT COUNT(DISTINCT s.user_id) n FROM subscriptions s JOIN plans p ON p.id=s.plan_id WHERE p.tier='premium' AND s.status IN ('active','cancelled') AND s.current_period_end>?", (t,))["n"],
        "revenue_minor_30d": db.one("SELECT COALESCE(SUM(amount_minor),0) n FROM payments WHERE status='paid' AND provider<>'sandbox' AND created_at>?", (t - 30 * day,))["n"],
        "sandbox_payments_30d": db.one("SELECT COUNT(*) n FROM payments WHERE status='paid' AND provider='sandbox' AND created_at>?", (t - 30 * day,))["n"],
        "playlists": db.one("SELECT COUNT(*) n FROM playlists")["n"],
    }
    hl_users = db.one("""SELECT COUNT(*) n FROM (SELECT DISTINCT user_id, song_id FROM listening_history WHERE context='highlight' AND played_at>?)""", (t - 30 * day,))["n"]
    converted = db.one("""SELECT COUNT(*) n FROM (SELECT DISTINCT h.user_id, h.song_id FROM listening_history h WHERE h.context='highlight' AND h.played_at>?
                          AND EXISTS (SELECT 1 FROM listening_history f WHERE f.user_id=h.user_id AND f.song_id=h.song_id AND f.context='full' AND f.played_at>=h.played_at))""", (t - 30 * day,))["n"]
    totals["highlight_to_full_rate"] = round(100 * converted / hl_users, 1) if hl_users else None
    daily = []
    for i in range(13, -1, -1):
        start = (t // day - i) * day
        n = db.one("SELECT COUNT(*) n, SUM(context='highlight') hl FROM listening_history WHERE played_at>=? AND played_at<?", (start, start + day))
        daily.append({"day": time.strftime("%d %b", time.gmtime(start)), "plays": n["n"], "highlight_plays": n["hl"] or 0})
    top_songs = db.q("""SELECT s.id, s.title, a.name artist, COUNT(*) plays FROM listening_history h JOIN songs s ON s.id=h.song_id JOIN artists a ON a.id=s.artist_id
                        WHERE h.played_at>? GROUP BY s.id ORDER BY plays DESC LIMIT 10""", (t - 30 * day,))
    if not top_songs:
        top_songs = db.q("SELECT s.id, s.title, a.name artist, s.play_count plays FROM songs s JOIN artists a ON a.id=s.artist_id ORDER BY s.play_count DESC LIMIT 10")
    by_lang = db.q("""SELECT l.name, COUNT(h.id) plays, (SELECT COUNT(*) FROM songs s2 WHERE s2.language_code=l.code AND s2.status='published') songs
                      FROM languages l LEFT JOIN songs s ON s.language_code=l.code LEFT JOIN listening_history h ON h.song_id=s.id AND h.played_at>?
                      GROUP BY l.code HAVING songs>0 ORDER BY plays DESC, songs DESC""", (t - 30 * day,))
    return {"totals": totals, "daily": daily, "top_songs": top_songs, "by_language": by_lang}


ADMIN_SONG_FIELDS = ("title", "artist_id", "album_id", "language_code", "genre", "release_year", "is_explicit", "is_featured", "trending_pin", "status", "categories")


@route("GET", r"/api/admin/songs", auth="content")
def admin_songs(req):
    qd = req.query
    where, params = ["1=1"], []
    if qd.get("q"):
        where.append("(s.title LIKE ? OR a.name LIKE ? OR s.id=?)"); params += [f"%{qd['q']}%", f"%{qd['q']}%", qd["q"]]
    if qd.get("status"):
        where.append("s.status=?"); params.append(qd["status"])
    if qd.get("lang"):
        where.append("s.language_code=?"); params.append(qd["lang"])
    rows = songs_where(" AND ".join(where), params, order="s.updated_at DESC, s.created_at DESC", limit=500, include_hidden=True)
    for r in rows:
        r["assets"] = db.q("SELECT kind, quality, storage_key, duration_sec, size_bytes FROM audio_assets WHERE song_id=?", (r["id"],))
        r["license"] = db.one("SELECT * FROM licensing WHERE song_id=?", (r["id"],))
    return {"songs": rows}


def _license_fields(b):
    lic = b.get("license") or {}
    lt = lic.get("license_type", "label")
    require(lt in ("cc0", "royalty_free", "label", "distributor", "direct", "publisher"), 400, "Unknown license type")
    return {"licensor": s_(lic.get("licensor"), "Licensor", 120), "license_type": lt,
            "territories": s_(lic.get("territories"), "Territories", 200, False) or "WORLD",
            "allow_free_full_stream": 1 if lic.get("allow_free_full_stream") else 0, "allow_offline": 1 if lic.get("allow_offline") else 0,
            "allow_lyrics": 1 if lic.get("allow_lyrics") else 0, "valid_from": s_(lic.get("valid_from"), "valid_from", 20, False),
            "valid_until": s_(lic.get("valid_until"), "valid_until", 20, False), "reference": s_(lic.get("reference"), "Reference", 120, False),
            "notes": s_(lic.get("notes"), "Notes", 1000, False)}


def _apply_song_fields(sid, b, creating=False):
    sets, vals = [], []
    if "title" in b or creating:
        sets.append("title=?"); vals.append(s_(b.get("title"), "Title", 150))
    if "artist_id" in b or creating:
        aid = s_(b.get("artist_id"), "Artist", 40)
        require(db.one("SELECT 1 FROM artists WHERE id=?", (aid,)), 400, "Artist not found")
        sets.append("artist_id=?"); vals.append(aid)
    if "album_id" in b:
        alid = s_(b.get("album_id"), "Album", 40, False)
        require(not alid or db.one("SELECT 1 FROM albums WHERE id=?", (alid,)), 400, "Album not found")
        sets.append("album_id=?"); vals.append(alid)
    if "language_code" in b or creating:
        lc = s_(b.get("language_code"), "Language", 10)
        require(db.one("SELECT 1 FROM languages WHERE code=?", (lc,)), 400, "Language not found")
        sets.append("language_code=?"); vals.append(lc)
    if "genre" in b:
        sets.append("genre_id=?"); vals.append(db.genre_id(s_(b.get("genre"), "Genre", 60, False)))
    if "release_year" in b:
        sets.append("release_year=?"); vals.append(int_(b.get("release_year"), "Release year", 1900, 2100))
    for f in ("is_explicit", "is_featured"):
        if f in b:
            sets.append(f"{f}=?"); vals.append(1 if b[f] else 0)
    if "trending_pin" in b:
        sets.append("trending_pin=?"); vals.append(int_(b.get("trending_pin"), "Trending pin", 1, 100))
    if "status" in b:
        require(b["status"] in ("draft", "published"), 400, "Use the takedown action to remove content")
        sets.append("status=?"); vals.append(b["status"])
    if sets:
        db.run(f"UPDATE songs SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, db.now(), sid))
    if "categories" in b:
        require(isinstance(b["categories"], list), 400, "categories must be a list")
        db.run("DELETE FROM song_categories WHERE song_id=?", (sid,))
        for slug in b["categories"]:
            c = db.one("SELECT id FROM categories WHERE slug=?", (slug,))
            require(c, 400, f"Unknown category {slug}")
            db.run("INSERT OR IGNORE INTO song_categories VALUES(?,?)", (sid, c["id"]))
    if "license" in b:
        lf = _license_fields(b)
        if db.one("SELECT 1 FROM licensing WHERE song_id=?", (sid,)):
            db.run(f"UPDATE licensing SET {', '.join(k + '=?' for k in lf)} WHERE song_id=?", (*lf.values(), sid))
        else:
            db.run(f"INSERT INTO licensing(id,song_id,{','.join(lf)}) VALUES(?,?,{','.join('?' * len(lf))})", (db.new_id("lic"), sid, *lf.values()))


@route("POST", r"/api/admin/songs", auth="content")
def admin_create_song(req):
    b = req.json
    require(b.get("license"), 400, "Licensing information is required before a song can be added")
    title = s_(b.get("title"), "Title", 150)
    require(db.one("SELECT 1 FROM artists WHERE id=?", (b.get("artist_id"),)), 400, "Choose an artist")
    require(db.one("SELECT 1 FROM languages WHERE code=?", (b.get("language_code"),)), 400, "Choose a language")
    _license_fields(b)
    sid, t = db.new_id("sng"), db.now()
    with db.tx():
        db.run("INSERT INTO songs(id,slug,title,artist_id,language_code,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
               (sid, unique_slug("songs", title), title, b.get("artist_id"), b.get("language_code"), "draft", t, t))
        _apply_song_fields(sid, {**b, "status": b.get("status", "draft")}, creating=True)
        if b.get("album_id"):
            cover = db.one("SELECT cover_key FROM albums WHERE id=?", (b["album_id"],))
            if cover and cover["cover_key"]:
                db.run("UPDATE songs SET cover_key=? WHERE id=?", (cover["cover_key"], sid))
    audit(req, "create", "song", sid, {"title": b.get("title")})
    return {"song": cards([get_song(sid, True)])[0]}


@route("PATCH", r"/api/admin/songs/(?P<sid>sng_\w+)", auth="content")
def admin_update_song(req, sid):
    get_song(sid, True)
    b = req.json
    if b.get("status") == "published":
        require(db.one("SELECT 1 FROM audio_assets WHERE song_id=? AND kind='full'", (sid,)), 400, "Upload the licensed audio before publishing")
        require(db.one("SELECT 1 FROM licensing WHERE song_id=?", (sid,)) or b.get("license"), 400, "Add licensing information before publishing")
    with db.tx():
        _apply_song_fields(sid, b)
    audit(req, "update", "song", sid, {k: b[k] for k in b if k != "license"})
    return {"song": cards([get_song(sid, True)])[0]}


@route("DELETE", r"/api/admin/songs/(?P<sid>sng_\w+)", auth="content")
def admin_delete_song(req, sid):
    s = get_song(sid, True)
    require(s["status"] == "draft", 400, "Only drafts can be deleted. Use takedown for published songs (keeps the legal record).")
    for a in db.q("SELECT storage_key FROM audio_assets WHERE song_id=?", (sid,)):
        if a["storage_key"].startswith("uploads/"):
            storage.delete(a["storage_key"])
    db.run("DELETE FROM songs WHERE id=?", (sid,))
    audit(req, "delete", "song", sid)
    return {"ok": True}


@route("POST", r"/api/admin/songs/(?P<sid>sng_\w+)/takedown", auth="takedown")
def admin_takedown(req, sid):
    get_song(sid, True)
    b = req.json
    claimant, reason = s_(b.get("claimant"), "Claimant", 200), s_(b.get("reason"), "Reason", 1000)
    with db.tx():
        db.run("UPDATE songs SET status='taken_down', updated_at=? WHERE id=?", (db.now(), sid))
        db.run("INSERT INTO takedowns(id,song_id,claimant,reason,admin_id,action,created_at) VALUES(?,?,?,?,?,?,?)",
               (db.new_id("tkd"), sid, claimant, reason, req.user["id"], "taken_down", db.now()))
    audit(req, "takedown", "song", sid, {"claimant": claimant})
    return {"ok": True}


@route("POST", r"/api/admin/songs/(?P<sid>sng_\w+)/restore", auth="takedown")
def admin_restore(req, sid):
    get_song(sid, True)
    reason = s_(req.json.get("reason"), "Reason", 1000)
    with db.tx():
        db.run("UPDATE songs SET status='published', updated_at=? WHERE id=?", (db.now(), sid))
        db.run("INSERT INTO takedowns(id,song_id,claimant,reason,admin_id,action,created_at) VALUES(?,?,?,?,?,?,?)",
               (db.new_id("tkd"), sid, "-", reason, req.user["id"], "restored", db.now()))
    audit(req, "restore", "song", sid)
    return {"ok": True}


@route("GET", r"/api/admin/takedowns", auth="takedown")
def admin_takedowns(req):
    return {"takedowns": db.q("""SELECT t.*, s.title, u.email admin_email FROM takedowns t JOIN songs s ON s.id=t.song_id LEFT JOIN users u ON u.id=t.admin_id
                                 ORDER BY t.created_at DESC LIMIT 200""")}


@route("POST", r"/api/admin/songs/(?P<sid>sng_\w+)/audio", auth="content")
def admin_upload_audio(req, sid):
    song = get_song(sid, True)
    ct = req.headers.get("Content-Type", "").split(";")[0].lower()
    require(ct in AUDIO_TYPES, 415, "Upload MP3, M4A/AAC, OGG, WAV, FLAC or WebM audio")
    data = req.body
    require(data, 400, "Empty upload")
    quality = req.query.get("quality", "standard")
    require(quality in ("standard", "high"), 400, "quality must be standard or high")
    key = f"uploads/audio/{sid}-{quality}-{secrets.token_hex(4)}{AUDIO_TYPES[ct]}"
    storage.put(key, data, ct)
    local = storage.local_path(key)
    duration = highlights.probe_duration(local) or float(req.query.get("duration") or 0)
    require(duration > 0, 400, "Could not read the audio duration. Install ffmpeg on the server or pass ?duration=<seconds>.")
    with db.tx():
        old = db.q("SELECT id, storage_key FROM audio_assets WHERE song_id=? AND kind='full' AND quality=?", (sid, quality))
        db.run("DELETE FROM audio_assets WHERE song_id=? AND kind='full' AND quality=?", (sid, quality))
        db.run("INSERT INTO audio_assets(id,song_id,kind,storage_key,mime_type,duration_sec,size_bytes,quality,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
               (db.new_id("aud"), sid, "full", key, ct, duration, len(data), quality, db.now()))
        db.run("UPDATE songs SET duration_sec=?, updated_at=? WHERE id=?", (duration, db.now(), sid))
    for o in old:
        if o["storage_key"].startswith("uploads/"):
            storage.delete(o["storage_key"])
    suggestion = highlights.suggest_highlight(local, duration)
    hl = db.one("SELECT * FROM song_highlights WHERE song_id=?", (sid,))
    applied = False
    if not hl or hl["source"] == "auto" or hl["end_sec"] > duration:
        _save_highlight(sid, suggestion["start"], suggestion["end"], "auto", local)
        applied = True
    audit(req, "upload_audio", "song", sid, {"quality": quality, "bytes": len(data)})
    return {"ok": True, "duration": duration, "suggested_highlight": suggestion, "highlight_applied": applied,
            "song": cards([get_song(sid, True)])[0]}


def _save_highlight(sid, start, end, source, src_local=None):
    dur = db.one("SELECT duration_sec FROM songs WHERE id=?", (sid,))["duration_sec"]
    err = highlights.validate_window(start, end, dur)
    require(not err, 400, err or "")
    start, end = round(float(start), 2), round(float(end), 2)
    full = db.one("SELECT storage_key FROM audio_assets WHERE song_id=? AND kind='full' ORDER BY quality='standard' DESC LIMIT 1", (sid,))
    preview_id = None
    if full:
        src = src_local or storage.local_path(full["storage_key"])
        tmp = highlights.temp_path(".mp3")
        try:
            if highlights.render_preview(src, start, end, tmp):
                key = f"uploads/audio/preview-{sid}-{secrets.token_hex(4)}.mp3"
                storage.put_file(key, tmp, "audio/mpeg")
                preview_id = db.new_id("aud")
                old = db.q("SELECT storage_key FROM audio_assets WHERE song_id=? AND kind='preview'", (sid,))
                db.run("DELETE FROM audio_assets WHERE song_id=? AND kind='preview'", (sid,))
                for o in old:
                    if o["storage_key"].startswith("uploads/"):
                        storage.delete(o["storage_key"])
                db.run("INSERT INTO audio_assets(id,song_id,kind,storage_key,duration_sec,size_bytes,created_at) VALUES(?,?,?,?,?,?,?)",
                       (preview_id, sid, "preview", key, end - start, os.path.getsize(tmp), db.now()))
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    if preview_id is None:  # no ffmpeg: player clips the full asset to [start, end]
        db.run("DELETE FROM audio_assets WHERE song_id=? AND kind='preview'", (sid,))
    db.run("""INSERT INTO song_highlights(id,song_id,start_sec,end_sec,source,preview_asset_id,updated_at) VALUES(?,?,?,?,?,?,?)
              ON CONFLICT(song_id) DO UPDATE SET start_sec=excluded.start_sec, end_sec=excluded.end_sec, source=excluded.source,
              preview_asset_id=excluded.preview_asset_id, updated_at=excluded.updated_at""",
           (db.new_id("hl"), sid, start, end, source, preview_id, db.now()))


@route("PUT", r"/api/admin/songs/(?P<sid>sng_\w+)/highlight", auth="content")
def admin_set_highlight(req, sid):
    get_song(sid, True)
    b = req.json
    _save_highlight(sid, b.get("start"), b.get("end"), "manual")
    audit(req, "set_highlight", "song", sid, {"start": b.get("start"), "end": b.get("end")})
    return {"song": cards([get_song(sid, True)])[0]}


@route("GET", r"/api/admin/songs/(?P<sid>sng_\w+)/highlight/suggest", auth="content")
def admin_suggest_highlight(req, sid):
    s = get_song(sid, True)
    full = db.one("SELECT storage_key FROM audio_assets WHERE song_id=? AND kind='full' LIMIT 1", (sid,))
    require(full, 400, "Upload audio first")
    return {"suggestion": highlights.suggest_highlight(storage.local_path(full["storage_key"]), s["duration_sec"])}


@route("GET", r"/api/admin/songs/(?P<sid>sng_\w+)/preview-url", auth="content")
def admin_full_url(req, sid):
    full = db.one("SELECT storage_key FROM audio_assets WHERE song_id=? AND kind='full' ORDER BY quality='standard' DESC LIMIT 1", (sid,))
    require(full, 404, "No audio uploaded")
    return {"url": storage.url(full["storage_key"])}


@route("PUT", r"/api/admin/songs/(?P<sid>sng_\w+)/cover", auth="content")
def admin_song_cover(req, sid):
    get_song(sid, True)
    key = save_image(req, "uploads/covers")
    db.run("UPDATE songs SET cover_key=?, updated_at=? WHERE id=?", (key, db.now(), sid))
    return {"cover_url": storage.url(key)}


def parse_lyrics(text):
    """Accepts LRC ([mm:ss.xx] line) or plain text (one line per row)."""
    lines = []
    for raw in text.replace("\r", "").split("\n"):
        m = re.match(r"^\s*\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]\s*(.*)$", raw)
        if m:
            if m.group(3).strip():
                lines.append({"t": round(int(m.group(1)) * 60 + float(m.group(2)), 2), "text": m.group(3).strip()})
        elif raw.strip() and not re.match(r"^\s*\[[a-z]+:.*\]\s*$", raw, re.I):
            lines.append({"t": None, "text": raw.strip()})
    return lines


@route("PUT", r"/api/admin/songs/(?P<sid>sng_\w+)/lyrics", auth="content")
def admin_lyrics(req, sid):
    s = get_song(sid, True)
    b = req.json
    lic = db.one("SELECT allow_lyrics FROM licensing WHERE song_id=?", (sid,))
    require(lic and lic["allow_lyrics"], 400, "Mark lyrics as licensed in this song's licensing record before adding them")
    source = s_(b.get("source"), "Lyrics licence / source", 200)
    text = s_(b.get("text"), "Lyrics", 20000)
    lines = parse_lyrics(text)
    require(lines, 400, "No lyric lines found")
    with db.tx():
        db.save_lyrics(sid, b.get("language_code") or s["language_code"], lines, False, source)
        if b.get("translation_text"):
            tl = parse_lyrics(b["translation_text"])
            db.save_lyrics(sid, s_(b.get("translation_language"), "Translation language", 10, False) or "en", tl, True, source)
    audit(req, "set_lyrics", "song", sid, {"lines": len(lines)})
    return {"ok": True, "lines": len(lines), "synced": all(l["t"] is not None for l in lines)}


@route("DELETE", r"/api/admin/songs/(?P<sid>sng_\w+)/lyrics", auth="content")
def admin_delete_lyrics(req, sid):
    db.run("DELETE FROM lyrics WHERE song_id=?", (sid,))
    audit(req, "delete_lyrics", "song", sid)
    return {"ok": True}


@route("GET", r"/api/admin/songs/(?P<sid>sng_\w+)/lyrics", auth="content")
def admin_get_lyrics(req, sid):
    def to_lrc(lines):
        return "\n".join((f"[{int(l['t'] // 60):02d}:{l['t'] % 60:05.2f}] " if l.get("t") is not None else "") + l["text"] for l in lines)
    main = db.one("SELECT * FROM lyrics WHERE song_id=? AND is_translation=0", (sid,))
    tr = db.one("SELECT * FROM lyrics WHERE song_id=? AND is_translation=1", (sid,))
    return {"text": to_lrc(json.loads(main["lines_json"])) if main else "", "source": main["source"] if main else "",
            "translation_text": to_lrc(json.loads(tr["lines_json"])) if tr else "", "translation_language": tr["language_code"] if tr else "en"}


@route("POST", r"/api/admin/uploads", auth="content")
def admin_generic_upload(req):
    """Bulk upload step 1: store a file and get back its storage key (referenced from the CSV)."""
    ct = req.headers.get("Content-Type", "").split(";")[0].lower()
    name = urllib.parse.unquote(req.headers.get("X-Filename", "file"))
    if ct in AUDIO_TYPES:
        key = f"uploads/audio/bulk-{secrets.token_hex(6)}{AUDIO_TYPES[ct]}"
        storage.put(key, req.body, ct)
    else:
        key = save_image(req, "uploads/covers")
    return {"key": key, "filename": name}


CSV_COLUMNS = ["title", "artist", "album", "language_code", "genre", "categories", "release_year", "audio_key", "cover_key",
               "highlight_start", "highlight_end", "licensor", "license_type", "allow_free_full_stream", "allow_lyrics", "lyrics_lrc", "lyrics_source"]


@route("GET", r"/api/admin/import/template", auth="content")
def csv_template(req):
    return {"columns": CSV_COLUMNS, "example": ",".join(CSV_COLUMNS) + "\n" +
            'My Song,New Artist,Debut,te,Film Pop,love;melody,2026,uploads/audio/bulk-abc123.mp3,,62.5,88,Example Records,label,0,1,"[00:12.00] first line",Example Records lyric licence'}


@route("POST", r"/api/admin/import/csv", auth="content")
def csv_import(req):
    text = req.json.get("csv") or ""
    dry = bool(req.json.get("dry_run"))
    require(text.strip(), 400, "Paste or upload a CSV file")
    rows = list(csv.DictReader(io.StringIO(text)))
    require(rows, 400, "The CSV has no data rows")
    missing = [c for c in ("title", "artist", "language_code", "licensor", "license_type") if c not in rows[0]]
    require(not missing, 400, f"CSV is missing columns: {', '.join(missing)}")
    results = []
    for i, r in enumerate(rows, start=2):
        try:
            sid = _import_row(req, r, dry)
            results.append({"row": i, "ok": True, "title": r.get("title"), "song_id": sid})
        except ApiError as e:
            results.append({"row": i, "ok": False, "title": r.get("title"), "error": e.message})
    audit(req, "csv_import", "song", None, {"rows": len(rows), "ok": sum(x["ok"] for x in results), "dry_run": dry})
    return {"results": results, "imported": sum(x["ok"] for x in results), "failed": sum(not x["ok"] for x in results), "dry_run": dry}


def _import_row(req, r, dry):
    title = s_(r.get("title"), "title", 150)
    lang = s_(r.get("language_code"), "language_code", 10)
    require(db.one("SELECT 1 FROM languages WHERE code=?", (lang,)), 400, f"Unknown language code '{lang}'")
    cats = [c.strip() for c in (r.get("categories") or "").split(";") if c.strip()]
    for c in cats:
        require(db.one("SELECT 1 FROM categories WHERE slug=?", (c,)), 400, f"Unknown category '{c}'")
    audio_key = (r.get("audio_key") or "").strip()
    if audio_key:
        require(audio_key.startswith("uploads/audio/") and storage.exists(audio_key), 400, f"Audio file '{audio_key}' was not found in storage")
    lic = {"licensor": r.get("licensor"), "license_type": (r.get("license_type") or "").strip(), "allow_free_full_stream": r.get("allow_free_full_stream") in ("1", "true", "yes"),
           "allow_lyrics": r.get("allow_lyrics") in ("1", "true", "yes")}
    _license_fields({"license": lic})
    if dry:
        return None
    with db.tx():
        art_name = s_(r.get("artist"), "artist", 120)
        a = db.one("SELECT id FROM artists WHERE name=? COLLATE NOCASE", (art_name,))
        aid = a["id"] if a else db.new_id("art")
        if not a:
            db.run("INSERT INTO artists(id,slug,name,created_at) VALUES(?,?,?,?)", (aid, unique_slug("artists", art_name), art_name, db.now()))
        alid = None
        if (r.get("album") or "").strip():
            al = db.one("SELECT id FROM albums WHERE title=? AND artist_id=?", (r["album"].strip(), aid))
            alid = al["id"] if al else db.new_id("alb")
            if not al:
                db.run("INSERT INTO albums(id,slug,title,artist_id,release_year,cover_key,created_at) VALUES(?,?,?,?,?,?,?)",
                       (alid, unique_slug("albums", r["album"]), r["album"].strip(), aid, int_(r.get("release_year"), "release_year", 1900, 2100), r.get("cover_key") or None, db.now()))
        sid, t = db.new_id("sng"), db.now()
        db.run("INSERT INTO songs(id,slug,title,artist_id,album_id,language_code,status,cover_key,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (sid, unique_slug("songs", title), title, aid, alid, lang, "draft", r.get("cover_key") or None, t, t))
        _apply_song_fields(sid, {"genre": r.get("genre"), "release_year": r.get("release_year"), "categories": cats, "license": lic})
        if audio_key:
            dur = highlights.probe_duration(storage.local_path(audio_key)) or 0
            db.run("INSERT INTO audio_assets(id,song_id,kind,storage_key,duration_sec,created_at) VALUES(?,?,?,?,?,?)",
                   (db.new_id("aud"), sid, "full", audio_key, dur, t))
            db.run("UPDATE songs SET duration_sec=? WHERE id=?", (dur, sid))
        if (r.get("lyrics_lrc") or "").strip() and lic["allow_lyrics"]:
            db.save_lyrics(sid, lang, parse_lyrics(r["lyrics_lrc"]), False, r.get("lyrics_source") or lic["licensor"])
    if audio_key:
        dur = db.one("SELECT duration_sec FROM songs WHERE id=?", (sid,))["duration_sec"]
        if r.get("highlight_start") and r.get("highlight_end"):
            _save_highlight(sid, r["highlight_start"], r["highlight_end"], "editorial")
        elif dur:
            sug = highlights.suggest_highlight(storage.local_path(audio_key), dur)
            _save_highlight(sid, sug["start"], sug["end"], "auto")
        if dur:
            db.run("UPDATE songs SET status='published' WHERE id=?", (sid,))
    return sid


# artists / albums / categories / languages ----------------------------------------------------

@route("GET", r"/api/admin/artists", auth="content")
def admin_artists(req):
    rows = db.q("SELECT a.*, (SELECT COUNT(*) FROM songs s WHERE s.artist_id=a.id) song_count FROM artists a ORDER BY a.name")
    for a in rows:
        a["image_url"] = storage.url(a["image_key"])
    return {"artists": rows}


@route("POST", r"/api/admin/artists", auth="content")
def admin_create_artist(req):
    b = req.json
    name = s_(b.get("name"), "Name", 120)
    aid = db.new_id("art")
    db.run("INSERT INTO artists(id,slug,name,country,bio,is_independent,is_verified,created_at) VALUES(?,?,?,?,?,?,?,?)",
           (aid, unique_slug("artists", name), name, s_(b.get("country"), "Country", 60, False), s_(b.get("bio"), "Bio", 2000, False),
            1 if b.get("is_independent") else 0, 1 if b.get("is_verified") else 0, db.now()))
    audit(req, "create", "artist", aid, {"name": name})
    return {"artist": db.one("SELECT * FROM artists WHERE id=?", (aid,))}


@route("PATCH", r"/api/admin/artists/(?P<aid>art_\w+)", auth="content")
def admin_update_artist(req, aid):
    require(db.one("SELECT 1 FROM artists WHERE id=?", (aid,)), 404, "Artist not found")
    b = req.json
    for f, ml in (("name", 120), ("country", 60), ("bio", 2000)):
        if f in b:
            db.run(f"UPDATE artists SET {f}=? WHERE id=?", (s_(b[f], f, ml, f == "name"), aid))
    for f in ("is_independent", "is_verified"):
        if f in b:
            db.run(f"UPDATE artists SET {f}=? WHERE id=?", (1 if b[f] else 0, aid))
    audit(req, "update", "artist", aid)
    return {"artist": db.one("SELECT * FROM artists WHERE id=?", (aid,))}


@route("PUT", r"/api/admin/artists/(?P<aid>art_\w+)/image", auth="content")
def admin_artist_image(req, aid):
    key = save_image(req, "uploads/artists")
    db.run("UPDATE artists SET image_key=? WHERE id=?", (key, aid))
    return {"image_url": storage.url(key)}


@route("DELETE", r"/api/admin/artists/(?P<aid>art_\w+)", auth="content")
def admin_delete_artist(req, aid):
    require(not db.one("SELECT 1 FROM songs WHERE artist_id=?", (aid,)), 400, "This artist still has songs. Reassign or remove them first.")
    db.run("DELETE FROM albums WHERE artist_id=?", (aid,))
    db.run("DELETE FROM artists WHERE id=?", (aid,))
    audit(req, "delete", "artist", aid)
    return {"ok": True}


@route("GET", r"/api/admin/albums", auth="content")
def admin_albums(req):
    rows = db.q("SELECT al.*, a.name artist_name, (SELECT COUNT(*) FROM songs s WHERE s.album_id=al.id) song_count FROM albums al JOIN artists a ON a.id=al.artist_id ORDER BY al.created_at DESC")
    for r in rows:
        r["cover_url"] = storage.url(r["cover_key"])
    return {"albums": rows}


@route("POST", r"/api/admin/albums", auth="content")
def admin_create_album(req):
    b = req.json
    title = s_(b.get("title"), "Title", 150)
    aid = s_(b.get("artist_id"), "Artist", 40)
    require(db.one("SELECT 1 FROM artists WHERE id=?", (aid,)), 400, "Artist not found")
    alid = db.new_id("alb")
    db.run("INSERT INTO albums(id,slug,title,artist_id,release_year,label,created_at) VALUES(?,?,?,?,?,?,?)",
           (alid, unique_slug("albums", title), title, aid, int_(b.get("release_year"), "Release year", 1900, 2100), s_(b.get("label"), "Label", 120, False), db.now()))
    audit(req, "create", "album", alid, {"title": title})
    return {"album": db.one("SELECT * FROM albums WHERE id=?", (alid,))}


@route("PATCH", r"/api/admin/albums/(?P<alid>alb_\w+)", auth="content")
def admin_update_album(req, alid):
    require(db.one("SELECT 1 FROM albums WHERE id=?", (alid,)), 404, "Album not found")
    b = req.json
    if "title" in b:
        db.run("UPDATE albums SET title=? WHERE id=?", (s_(b["title"], "Title", 150), alid))
    if "release_year" in b:
        db.run("UPDATE albums SET release_year=? WHERE id=?", (int_(b["release_year"], "Release year", 1900, 2100), alid))
    if "label" in b:
        db.run("UPDATE albums SET label=? WHERE id=?", (s_(b["label"], "Label", 120, False), alid))
    return {"album": db.one("SELECT * FROM albums WHERE id=?", (alid,))}


@route("PUT", r"/api/admin/albums/(?P<alid>alb_\w+)/cover", auth="content")
def admin_album_cover(req, alid):
    require(db.one("SELECT 1 FROM albums WHERE id=?", (alid,)), 404, "Album not found")
    key = save_image(req, "uploads/covers")
    with db.tx():
        db.run("UPDATE albums SET cover_key=? WHERE id=?", (key, alid))
        if req.query.get("apply_to_songs", "1") == "1":
            db.run("UPDATE songs SET cover_key=? WHERE album_id=?", (key, alid))
    return {"cover_url": storage.url(key)}


@route("DELETE", r"/api/admin/albums/(?P<alid>alb_\w+)", auth="content")
def admin_delete_album(req, alid):
    db.run("UPDATE songs SET album_id=NULL WHERE album_id=?", (alid,))
    db.run("DELETE FROM albums WHERE id=?", (alid,))
    audit(req, "delete", "album", alid)
    return {"ok": True}


@route("POST", r"/api/admin/categories", auth="content")
def admin_create_category(req):
    b = req.json
    name = s_(b.get("name"), "Name", 60)
    color = s_(b.get("color"), "Colour", 7, False) or "#7c5cff"
    require(re.match(r"^#[0-9a-fA-F]{6}$", color), 400, "Colour must look like #a1b2c3")
    slug = unique_slug("categories", b.get("slug") or name)
    db.run("INSERT INTO categories(slug,name,color,has_highlight_feed,sort_order) VALUES(?,?,?,?,?)", (slug, name, color, 1 if b.get("has_highlight_feed") else 0, 200))
    audit(req, "create", "category", slug)
    return {"category": db.one("SELECT * FROM categories WHERE slug=?", (slug,))}


@route("PATCH", r"/api/admin/categories/(?P<cid>\d+)", auth="content")
def admin_update_category(req, cid):
    require(db.one("SELECT 1 FROM categories WHERE id=?", (cid,)), 404, "Category not found")
    b = req.json
    if "name" in b:
        db.run("UPDATE categories SET name=? WHERE id=?", (s_(b["name"], "Name", 60), cid))
    if "color" in b:
        require(re.match(r"^#[0-9a-fA-F]{6}$", b["color"] or ""), 400, "Colour must look like #a1b2c3")
        db.run("UPDATE categories SET color=? WHERE id=?", (b["color"], cid))
    for f in ("has_highlight_feed", "is_active"):
        if f in b:
            db.run(f"UPDATE categories SET {f}=? WHERE id=?", (1 if b[f] else 0, cid))
    if "sort_order" in b:
        db.run("UPDATE categories SET sort_order=? WHERE id=?", (int_(b["sort_order"], "sort_order", 0, 10000), cid))
    audit(req, "update", "category", cid)
    return {"category": db.one("SELECT * FROM categories WHERE id=?", (cid,))}


@route("DELETE", r"/api/admin/categories/(?P<cid>\d+)", auth="content")
def admin_delete_category(req, cid):
    db.run("DELETE FROM categories WHERE id=?", (cid,))
    audit(req, "delete", "category", cid)
    return {"ok": True}


@route("GET", r"/api/admin/languages", auth="content")
def admin_languages(req):
    return {"languages": db.q("SELECT l.*, (SELECT COUNT(*) FROM songs s WHERE s.language_code=l.code) song_count FROM languages l ORDER BY l.featured DESC, l.sort_order")}


@route("POST", r"/api/admin/languages", auth="content")
def admin_create_language(req):
    b = req.json
    code = s_(b.get("code"), "Code", 10).lower()
    require(re.match(r"^[a-z]{2,3}(-[a-z0-9]{2,8})?$", code), 400, "Use an ISO 639 code such as 'te' or 'pt-br'")
    require(not db.one("SELECT 1 FROM languages WHERE code=?", (code,)), 409, "That language already exists")
    region = b.get("region", "international")
    require(region in ("indian", "international"), 400, "region must be indian or international")
    db.run("INSERT INTO languages(code,name,native_name,region,featured,sort_order) VALUES(?,?,?,?,?,?)",
           (code, s_(b.get("name"), "Name", 60), s_(b.get("native_name"), "Native name", 60), region, 1 if b.get("featured") else 0, 200))
    audit(req, "create", "language", code)
    return {"language": db.one("SELECT * FROM languages WHERE code=?", (code,))}


@route("PATCH", r"/api/admin/languages/(?P<code>[\w-]+)", auth="content")
def admin_update_language(req, code):
    require(db.one("SELECT 1 FROM languages WHERE code=?", (code,)), 404, "Language not found")
    b = req.json
    for f, ml in (("name", 60), ("native_name", 60)):
        if f in b:
            db.run(f"UPDATE languages SET {f}=? WHERE code=?", (s_(b[f], f, ml), code))
    for f in ("featured", "is_active"):
        if f in b:
            db.run(f"UPDATE languages SET {f}=? WHERE code=?", (1 if b[f] else 0, code))
    audit(req, "update", "language", code)
    return {"language": db.one("SELECT * FROM languages WHERE code=?", (code,))}


# users / admins / playlists / plans / settings ----------------------------------------------

@route("GET", r"/api/admin/users", auth="users")
def admin_users(req):
    q = req.query.get("q", "")
    rows = db.q("""SELECT u.id, u.email, u.display_name, u.auth_provider, u.is_active, u.created_at, u.last_login_at, ad.role admin_role,
                   (SELECT COUNT(*) FROM listening_history h WHERE h.user_id=u.id) plays
                   FROM users u LEFT JOIN admins ad ON ad.user_id=u.id WHERE u.email LIKE ? OR u.display_name LIKE ? ORDER BY u.created_at DESC LIMIT 300""",
                (f"%{q}%", f"%{q}%"))
    for r in rows:
        r["plan"] = user_plan(r["id"])["tier"]
    return {"users": rows}


@route("PATCH", r"/api/admin/users/(?P<uid>usr_\w+)", auth="users")
def admin_update_user(req, uid):
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    require(u, 404, "User not found")
    b = req.json
    if "is_active" in b:
        require(uid != req.user["id"], 400, "You cannot suspend your own account")
        target_admin = db.one("SELECT role FROM admins WHERE user_id=?", (uid,))
        require(not target_admin or req.admin_role == "super_admin", 403, "Only a super admin can suspend another admin")
        db.run("UPDATE users SET is_active=?, token_version=token_version+1 WHERE id=?", (1 if b["is_active"] else 0, uid))
    if "grant_premium_days" in b:
        require(req.admin_role == "super_admin", 403, "Only a super admin can grant Premium")
        days = int_(b["grant_premium_days"], "Days", 1, 3650)
        t = db.now()
        db.run("INSERT INTO subscriptions(id,user_id,plan_id,status,provider,started_at,current_period_end,created_at) VALUES(?,?,?,?,?,?,?,?)",
               (db.new_id("sub"), uid, "premium-monthly", "active", "admin_grant", t, t + days * 86400, t))
    audit(req, "update", "user", uid, b)
    return {"ok": True}


@route("PUT", r"/api/admin/admins/(?P<uid>usr_\w+)", auth="*")
def admin_set_role(req, uid):
    role = req.json.get("role")
    require(role in PERMISSIONS, 400, "Unknown role")
    require(db.one("SELECT 1 FROM users WHERE id=?", (uid,)), 404, "User not found")
    db.run("INSERT INTO admins(user_id,role,created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role", (uid, role, db.now()))
    audit(req, "set_role", "user", uid, {"role": role})
    return {"ok": True}


@route("DELETE", r"/api/admin/admins/(?P<uid>usr_\w+)", auth="*")
def admin_remove_role(req, uid):
    require(uid != req.user["id"], 400, "You cannot remove your own admin access")
    db.run("DELETE FROM admins WHERE user_id=?", (uid,))
    audit(req, "remove_role", "user", uid)
    return {"ok": True}


@route("GET", r"/api/admin/playlists", auth="playlists")
def admin_playlists(req):
    return {"playlists": db.q("""SELECT p.id, p.title, p.is_public, p.created_at, u.email owner_email,
                                 (SELECT COUNT(*) FROM playlist_songs ps WHERE ps.playlist_id=p.id) song_count,
                                 (SELECT COUNT(*) FROM playlist_follows f WHERE f.playlist_id=p.id) followers
                                 FROM playlists p JOIN users u ON u.id=p.owner_id ORDER BY p.created_at DESC LIMIT 500""")}


@route("DELETE", r"/api/admin/playlists/(?P<pid>pl_\w+)", auth="playlists")
def admin_delete_playlist(req, pid):
    db.run("DELETE FROM playlists WHERE id=?", (pid,))
    audit(req, "delete", "playlist", pid)
    return {"ok": True}


@route("PATCH", r"/api/admin/playlists/(?P<pid>pl_\w+)", auth="playlists")
def admin_hide_playlist(req, pid):
    db.run("UPDATE playlists SET is_public=? WHERE id=?", (1 if req.json.get("is_public") else 0, pid))
    audit(req, "visibility", "playlist", pid, req.json)
    return {"ok": True}


@route("GET", r"/api/admin/plans", auth="*")
def admin_plans(req):
    rows = db.q("SELECT * FROM plans ORDER BY sort_order")
    for r in rows:
        r["features"] = json.loads(r.pop("features_json"))
    return {"plans": rows, "settings": {k: db.setting(k) for k in db.DEFAULT_SETTINGS},
            "providers": [{"key": p.key, "label": p.label, "enabled": p.enabled()} for p in payments.PROVIDERS.values()]}


@route("PATCH", r"/api/admin/plans/(?P<pid>[\w-]+)", auth="*")
def admin_update_plan(req, pid):
    require(db.one("SELECT 1 FROM plans WHERE id=?", (pid,)), 404, "Plan not found")
    b = req.json
    if "name" in b:
        db.run("UPDATE plans SET name=? WHERE id=?", (s_(b["name"], "Name", 60), pid))
    if "price_minor" in b:
        db.run("UPDATE plans SET price_minor=? WHERE id=?", (int_(b["price_minor"], "Price", 0, 10_000_000), pid))
    if "features" in b:
        require(isinstance(b["features"], list), 400, "features must be a list")
        db.run("UPDATE plans SET features_json=? WHERE id=?", (json.dumps([s_(f, "Feature", 120) for f in b["features"]]), pid))
    if "is_active" in b:
        db.run("UPDATE plans SET is_active=? WHERE id=?", (1 if b["is_active"] else 0, pid))
    audit(req, "update", "plan", pid, b)
    return admin_plans(req)


@route("PATCH", r"/api/admin/settings", auth="*")
def admin_settings(req):
    for k, v in req.json.items():
        require(k in db.DEFAULT_SETTINGS, 400, f"Unknown setting {k}")
        db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(int_(v, k, 0, 1000))))
    audit(req, "update", "settings", None, req.json)
    return admin_plans(req)


@route("GET", r"/api/admin/audit", auth="*")
def admin_audit(req):
    return {"entries": db.q("SELECT l.*, u.email admin_email FROM audit_log l LEFT JOIN users u ON u.id=l.admin_id ORDER BY l.id DESC LIMIT 300")}


@route("GET", r"/api/admin/lookups", auth="content")
def admin_lookups(req):
    return {"artists": db.q("SELECT id, name FROM artists ORDER BY name"),
            "albums": db.q("SELECT al.id, al.title, al.artist_id FROM albums al ORDER BY al.title"),
            "languages": db.q("SELECT code, name FROM languages WHERE is_active=1 ORDER BY name"),
            "categories": db.q("SELECT slug, name FROM categories ORDER BY sort_order"),
            "genres": [r["name"] for r in db.q("SELECT name FROM genres ORDER BY name")]}


# --------------------------------------------------------------------------- HTTP plumbing

class Request:
    def __init__(self, handler, method, path, query, body):
        self.handler, self.method, self.path, self.query, self.body = handler, method, path, query, body
        self.headers = handler.headers
        self.ip = handler.headers.get("X-Forwarded-For", handler.client_address[0]).split(",")[0].strip()
        self.user, self.admin_role = None, None
        self._json = None

    @property
    def json(self):
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    self._json = json.loads(self.body.decode("utf-8"))
                except Exception:
                    raise ApiError(400, "Request body must be valid JSON")
                if not isinstance(self._json, dict):
                    raise ApiError(400, "Request body must be a JSON object")
        return self._json


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
CSP = ("default-src 'self'; script-src 'self' https://accounts.google.com https://checkout.razorpay.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://accounts.google.com; font-src 'self' https://fonts.gstatic.com data:; "
       "img-src 'self' data: blob: https:; media-src 'self' blob: https:; connect-src 'self' https:; "
       "frame-src https://accounts.google.com https://api.razorpay.com https://checkout.razorpay.com; base-uri 'self'; form-action 'self'")

SEO_ROUTES = re.compile(r"^/(song|artist|language|category|album)/([^/]+)$")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PENDEL"

    def log_message(self, fmt, *args):
        if os.environ.get("PENDEL_QUIET"):
            return
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self): self.dispatch("GET")
    def do_HEAD(self): self.dispatch("HEAD")
    def do_POST(self): self.dispatch("POST")
    def do_PUT(self): self.dispatch("PUT")
    def do_PATCH(self): self.dispatch("PATCH")
    def do_DELETE(self): self.dispatch("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- responses
    def send_bytes(self, status, data, ctype, headers=None, head=False):
        gz = (len(data) > 1024 and ctype.split(";")[0] in ("application/json", "text/html", "text/css", "application/javascript", "image/svg+xml", "application/xml")
              and "gzip" in self.headers.get("Accept-Encoding", ""))
        if gz:
            data = gzip.compress(data, 5)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def send_json(self, status, obj):
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8",
                        {"Cache-Control": "no-store"})

    # ---- dispatch
    def dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if path.startswith("/api/"):
                return self.api(method, path, query)
            if path.startswith("/media/"):
                return self.media(path[len("/media/"):], query, head=method == "HEAD")
            if method not in ("GET", "HEAD"):
                raise ApiError(405, "Method not allowed")
            return self.static(path, head=method == "HEAD")
        except ApiError as e:
            self.send_json(e.status, {"error": e.message, "code": e.code, **e.extra})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send_json(500, {"error": "Something went wrong on our side. Please try again."})
            except Exception:
                pass

    def api(self, method, path, query):
        length = int(self.headers.get("Content-Length") or 0)
        if length > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise ApiError(413, f"Upload is larger than {config.MAX_UPLOAD_MB} MB")
        body = self.rfile.read(length) if length else b""
        req = Request(self, method, path, query, body)
        self.authenticate(req)
        path_matched = False
        for m, rx, fn, auth in ROUTES:
            mt = rx.match(path)
            if not mt:
                continue
            path_matched = True
            if m != method and not (m == "GET" and method == "HEAD"):
                continue
            if auth == "user" or (auth not in (None, "optional")):
                require(req.user, 401, "Please sign in to continue", "auth_required")
            if auth not in (None, "optional", "user"):
                require(req.admin_role and has_perm(req.admin_role, auth), 403, "Your admin role does not allow this action", "forbidden")
            return self.send_json(200, fn(req, **mt.groupdict()))
        raise ApiError(405 if path_matched else 404, "Method not allowed" if path_matched else "Not found")

    def authenticate(self, req):
        h = self.headers.get("Authorization", "")
        if not h.startswith("Bearer "):
            return
        payload = security.read_token(h[7:])
        if not payload:
            return
        u = db.one("SELECT * FROM users WHERE id=?", (payload["uid"],))
        if u and u["is_active"] and u["token_version"] == payload["tv"]:
            req.user = u
            r = db.one("SELECT role FROM admins WHERE user_id=?", (u["id"],))
            req.admin_role = r["role"] if r else None

    # ---- media (local storage)
    def media(self, key, query, head=False):
        if storage.name != "local":
            raise ApiError(404, "Not found")
        if is_protected(key) and not security.verify_media(key, query.get("exp"), query.get("sig")):
            raise ApiError(403, "This audio link has expired. Refresh to continue listening.", "media_expired")
        try:
            path = storage.path(key)
        except ValueError:
            raise ApiError(404, "Not found")
        if not os.path.isfile(path):
            raise ApiError(404, "Not found")
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".svg"):
            ctype = "image/svg+xml"
        headers = {"Accept-Ranges": "bytes", "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                   "Cache-Control": "private, max-age=3600" if is_protected(key) else "public, max-age=604800"}
        rng = self.headers.get("Range")
        start, end, status = 0, size - 1, 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        for k, v in {**SECURITY_HEADERS, **headers}.items():
            self.send_header(k, v)
        self.end_headers()
        if head:
            return
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---- static + SEO
    def static(self, path, head=False):
        if path == "/robots.txt":
            return self.send_bytes(200, f"User-agent: *\nDisallow: /admin\nDisallow: /api/\nSitemap: {config.PUBLIC_URL}/sitemap.xml\n".encode(), "text/plain")
        if path == "/sitemap.xml":
            return self.send_bytes(200, sitemap().encode(), "application/xml")
        rel = path.lstrip("/")
        full = os.path.abspath(os.path.join(config.WEB_DIR, rel))
        if rel and full.startswith(config.WEB_DIR + os.sep) and os.path.isfile(full):
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            if full.endswith(".js"):
                ctype = "application/javascript"
            with open(full, "rb") as f:
                data = f.read()
            etag = '"%x-%x"' % (int(os.path.getmtime(full)), len(data))
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            cache = "no-cache" if full.endswith((".html", "sw.js")) else "public, max-age=300"
            return self.send_bytes(200, data, ctype + ("; charset=utf-8" if ctype.startswith(("text/", "application/javascript")) else ""),
                                   {"ETag": etag, "Cache-Control": cache}, head)
        if "." in os.path.basename(rel):
            raise ApiError(404, "Not found")
        # SPA route -> index.html with per-page SEO tags
        with open(os.path.join(config.WEB_DIR, "index.html"), encoding="utf-8") as f:
            page = f.read()
        page = page.replace("<!--SEO-->", seo_tags(path))
        self.send_bytes(200, page.encode(), "text/html; charset=utf-8", {"Content-Security-Policy": CSP, "Cache-Control": "no-cache"}, head)


def seo_tags(path):
    title, desc, image, jsonld = (f"{config.APP_NAME} - Feel Every Beat",
                                  "Stream music in every language. Discover songs by mood through 20-30 second highlights, then play the full track.", None, None)
    m = SEO_ROUTES.match(path)
    try:
        if m and m.group(1) == "song":
            s = db.one(f"{SONG_SELECT} WHERE s.slug=? AND s.status='published'", (m.group(2),))
            if s:
                title = f"{s['title']} - {s['artist_name']} | {config.APP_NAME}"
                desc = f"Listen to {s['title']} by {s['artist_name']} ({s['language_name']}, {s['release_year'] or ''}). Hear the highlight or stream the full song on {config.APP_NAME}."
                image = storage.url(s["cover_key"])
                jsonld = {"@context": "https://schema.org", "@type": "MusicRecording", "name": s["title"], "byArtist": {"@type": "MusicGroup", "name": s["artist_name"]},
                          "inLanguage": s["language_code"], "duration": f"PT{int(s['duration_sec'] // 60)}M{int(s['duration_sec'] % 60)}S",
                          "inAlbum": {"@type": "MusicAlbum", "name": s["album_title"]} if s["album_title"] else None, "url": f"{config.PUBLIC_URL}{path}"}
        elif m and m.group(1) == "artist":
            a = db.one("SELECT * FROM artists WHERE slug=?", (m.group(2),))
            if a:
                title, desc, image = f"{a['name']} | {config.APP_NAME}", (a["bio"] or f"Songs by {a['name']}")[:160], storage.url(a["image_key"])
                jsonld = {"@context": "https://schema.org", "@type": "MusicGroup", "name": a["name"], "url": f"{config.PUBLIC_URL}{path}"}
        elif m and m.group(1) == "language":
            l = db.one("SELECT * FROM languages WHERE code=?", (m.group(2),))
            if l:
                title, desc = f"{l['name']} songs ({l['native_name']}) | {config.APP_NAME}", f"Trending, new, sad, love, devotional and DJ songs in {l['name']}."
        elif m and m.group(1) == "category":
            c = db.one("SELECT * FROM categories WHERE slug=?", (m.group(2),))
            if c:
                title, desc = f"{c['name']} | {config.APP_NAME}", f"Listen to {c['name'].lower()} from every language, with 20-30 second highlights."
    except Exception:
        traceback.print_exc()
    e = html.escape
    tags = [f"<title>{e(title)}</title>", f'<meta name="description" content="{e(desc)}">', f'<meta property="og:title" content="{e(title)}">',
            f'<meta property="og:description" content="{e(desc)}">', f'<meta property="og:type" content="{"music.song" if m and m.group(1) == "song" else "website"}">',
            f'<link rel="canonical" href="{e(config.PUBLIC_URL + path)}">', '<meta name="twitter:card" content="summary_large_image">']
    if image:
        tags.append(f'<meta property="og:image" content="{e(image if image.startswith("http") else config.PUBLIC_URL + image)}">')
    if jsonld:
        tags.append('<script type="application/ld+json">' + json.dumps({k: v for k, v in jsonld.items() if v}, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    return "\n  ".join(tags)


def sitemap():
    urls = ["/", "/highlights", "/languages", "/songs", "/explore"]
    urls += [f"/song/{r['slug']}" for r in db.q("SELECT slug FROM songs WHERE status='published'")]
    urls += [f"/artist/{r['slug']}" for r in db.q("SELECT slug FROM artists")]
    urls += [f"/language/{r['code']}" for r in db.q("SELECT code FROM languages WHERE is_active=1")]
    urls += [f"/category/{r['slug']}" for r in db.q("SELECT slug FROM categories WHERE is_active=1")]
    body = "".join(f"<url><loc>{html.escape(config.PUBLIC_URL + u)}</loc></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>'


def main():
    db.init()
    srv = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    srv.daemon_threads = True
    print(f"[pendel] {config.APP_NAME} running at http://{'localhost' if config.HOST in ('127.0.0.1', '0.0.0.0') else config.HOST}:{config.PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[pendel] stopped")


if __name__ == "__main__":
    main()

"""Content-based recommendation engine.

A user's taste profile is built from weighted signals:
  favourites (strong), listening time (scaled, recent plays count more),
  completed plays, preferred languages / categories from the profile,
  and an optional mood selected in the UI.
Each candidate song is scored by affinity to its categories, language and
artist, plus a popularity prior; songs played in the last few hours are
pushed down so recommendations stay fresh. Designed so the scoring step can
later be swapped for a collaborative / embedding model behind the same API.
"""
import math, time
from collections import defaultdict

from . import db


def profile(user_id):
    now = time.time()
    cat, lang, artist = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in db.q("""SELECT s.id, s.language_code, s.artist_id, f.created_at FROM favourites f JOIN songs s ON s.id=f.song_id WHERE f.user_id=?""", (user_id,)):
        _add(r, 3.0, cat, lang, artist)
    for r in db.q("""SELECT s.id, s.language_code, s.artist_id, SUM(h.seconds_listened) secs, SUM(h.completed) done, MAX(h.played_at) last
                     FROM listening_history h JOIN songs s ON s.id=h.song_id
                     WHERE h.user_id=? AND h.played_at > ? GROUP BY s.id""", (user_id, int(now) - 90 * 86400)):
        recency = math.exp(-(now - r["last"]) / (14 * 86400))
        w = min(3.0, math.log1p(r["secs"] / 30.0)) * (0.5 + recency) + 0.5 * min(r["done"], 4)
        _add(r, w, cat, lang, artist)
    for r in db.q("SELECT language_code FROM user_languages WHERE user_id=?", (user_id,)):
        lang[r["language_code"]] += 2.5
    for r in db.q("SELECT category_id FROM user_categories WHERE user_id=?", (user_id,)):
        cat[r["category_id"]] += 2.0
    return cat, lang, artist


def _add(r, w, cat, lang, artist):
    lang[r["language_code"]] += w
    artist[r["artist_id"]] += w
    for c in db.q("SELECT category_id FROM song_categories WHERE song_id=?", (r["id"],)):
        cat[c["category_id"]] += w


def _norm(d):
    m = max(d.values()) if d else 0
    return {k: v / m for k, v in d.items()} if m else {}


def recommend(user_id, limit=20, mood=None, exclude_recent=True, language=None):
    cat, lang, artist = (_norm(x) for x in profile(user_id)) if user_id else ({}, {}, {})
    mood_cat = None
    if mood:
        r = db.one("SELECT id FROM categories WHERE slug=?", (mood,))
        mood_cat = r["id"] if r else None
    recent = set()
    if user_id and exclude_recent:
        recent = {r["song_id"] for r in db.q("SELECT song_id FROM listening_history WHERE user_id=? AND played_at > ?", (user_id, int(time.time()) - 6 * 3600))}
    songs = db.q("SELECT id, language_code, artist_id, play_count, release_year FROM songs WHERE status='published'" + (" AND language_code=?" if language else ""),
                 (language,) if language else ())
    sc = defaultdict(list)
    for r in db.q("SELECT song_id, category_id FROM song_categories"):
        sc[r["song_id"]].append(r["category_id"])
    maxpop = max([s["play_count"] for s in songs] or [1]) or 1
    year = time.gmtime().tm_year
    scored = []
    for s in songs:
        cats = sc[s["id"]]
        score = 3.0 * (max((cat.get(c, 0) for c in cats), default=0))
        score += 2.0 * lang.get(s["language_code"], 0)
        score += 1.5 * artist.get(s["artist_id"], 0)
        score += 0.8 * math.log1p(s["play_count"]) / math.log1p(maxpop)
        if s["release_year"] and year - s["release_year"] <= 1:
            score += 0.3
        if mood_cat:
            score += 4.0 if mood_cat in cats else -2.0
        if s["id"] in recent:
            score -= 2.5
        scored.append((score, s["id"]))
    scored.sort(reverse=True)
    return [sid for _, sid in scored[:limit]]


def made_for_you(user_id):
    """A few named mixes for the 'Made For You' shelf."""
    mixes = []
    langs = db.q("""SELECT s.language_code code, l.name, SUM(h.seconds_listened) secs FROM listening_history h JOIN songs s ON s.id=h.song_id
                    JOIN languages l ON l.code=s.language_code WHERE h.user_id=? GROUP BY s.language_code ORDER BY secs DESC LIMIT 2""", (user_id,))
    if not langs:
        langs = db.q("SELECT l.code, l.name FROM user_languages u JOIN languages l ON l.code=u.language_code WHERE u.user_id=? LIMIT 2", (user_id,))
    mixes.append({"key": "daily-mix", "title": "Your Daily Mix", "subtitle": "A blend of what you love", "song_ids": recommend(user_id, 15)})
    for l in langs:
        ids = recommend(user_id, 12, language=l["code"])
        if ids:
            mixes.append({"key": f"lang-{l['code']}", "title": f"{l['name']} Mix", "subtitle": f"Picked for you in {l['name']}", "song_ids": ids})
    top_cat = db.one("""SELECT c.slug, c.name, COUNT(*) n FROM favourites f JOIN song_categories sc ON sc.song_id=f.song_id
                        JOIN categories c ON c.id=sc.category_id WHERE f.user_id=? GROUP BY c.id ORDER BY n DESC LIMIT 1""", (user_id,))
    if top_cat:
        mixes.append({"key": f"mood-{top_cat['slug']}", "title": f"{top_cat['name']} Mix", "subtitle": "Because you keep coming back to these",
                      "song_ids": recommend(user_id, 12, mood=top_cat["slug"])})
    return mixes

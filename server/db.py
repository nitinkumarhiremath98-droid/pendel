"""Database layer: schema, connections and seeding.

Two backends, chosen at start-up:

* PostgreSQL when DATABASE_URL is set (Neon, Supabase, Render Postgres ...).
  Use this on hosts whose disk is wiped on restart, such as Render's free plan:
  users, playlists and every imported song survive restarts and redeploys.
* SQLite (WAL mode) in data/pendel.db otherwise: zero set-up for local development.

The application writes SQLite-flavoured SQL; ``_pg_sql`` translates the few
dialect differences (INSERT OR IGNORE, COLLATE NOCASE, group_concat, LIKE ...)
so every query runs unchanged on both.
"""
import functools, json, os, re, secrets, sqlite3, threading, time

from . import config, pgwire, security

_local = threading.local()

if config.MONGO_URI and not config.DATABASE_URL:
    raise SystemExit("[pendel] MONGO_URI is set, but PENDEL's catalogue is relational (joins, unique keys, transactions) and runs on "
                     "PostgreSQL or SQLite. Create a free PostgreSQL database (for example on neon.com) and set DATABASE_URL instead.")

BACKEND = "postgres" if config.DATABASE_URL else "sqlite"
IntegrityError = (sqlite3.IntegrityError, pgwire.IntegrityError)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT,
  display_name TEXT NOT NULL,
  avatar_key TEXT,
  auth_provider TEXT NOT NULL DEFAULT 'email' CHECK (auth_provider IN ('email','google')),
  google_sub TEXT UNIQUE,
  theme TEXT NOT NULL DEFAULT 'dark' CHECK (theme IN ('dark','light')),
  is_active INTEGER NOT NULL DEFAULT 1,
  token_version INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER
);

CREATE TABLE IF NOT EXISTS admins (
  user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('super_admin','content_manager','moderator','analyst')),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS languages (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  native_name TEXT NOT NULL,
  region TEXT NOT NULL CHECK (region IN ('indian','international')),
  featured INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '#7c5cff',
  has_highlight_feed INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 100,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS genres (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS artists (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  country TEXT,
  bio TEXT,
  image_key TEXT,
  is_independent INTEGER NOT NULL DEFAULT 0,
  is_verified INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS albums (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  artist_id TEXT NOT NULL REFERENCES artists(id),
  cover_key TEXT,
  release_year INTEGER,
  label TEXT,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS songs (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  artist_id TEXT NOT NULL REFERENCES artists(id),
  album_id TEXT REFERENCES albums(id),
  language_code TEXT NOT NULL REFERENCES languages(code),
  genre_id INTEGER REFERENCES genres(id),
  release_year INTEGER,
  duration_sec REAL NOT NULL DEFAULT 0,
  cover_key TEXT,
  is_explicit INTEGER NOT NULL DEFAULT 0,
  is_featured INTEGER NOT NULL DEFAULT 0,
  trending_pin INTEGER,               -- admin-pinned trending rank (lower = higher), NULL = organic
  status TEXT NOT NULL DEFAULT 'published' CHECK (status IN ('draft','published','taken_down')),
  play_count INTEGER NOT NULL DEFAULT 0,
  is_demo INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_songs_lang ON songs(language_code, status);
CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist_id);

CREATE TABLE IF NOT EXISTS song_categories (
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  PRIMARY KEY (song_id, category_id)
);
CREATE INDEX IF NOT EXISTS idx_song_categories_cat ON song_categories(category_id);

CREATE TABLE IF NOT EXISTS audio_assets (
  id TEXT PRIMARY KEY,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('full','preview')),
  storage_key TEXT NOT NULL,
  mime_type TEXT NOT NULL DEFAULT 'audio/mpeg',
  bitrate_kbps INTEGER,
  duration_sec REAL,
  size_bytes INTEGER,
  quality TEXT NOT NULL DEFAULT 'standard' CHECK (quality IN ('standard','high')),
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audio_song ON audio_assets(song_id, kind);

CREATE TABLE IF NOT EXISTS song_highlights (
  id TEXT PRIMARY KEY,
  song_id TEXT NOT NULL UNIQUE REFERENCES songs(id) ON DELETE CASCADE,
  start_sec REAL NOT NULL,
  end_sec REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('auto','manual','editorial')),
  preview_asset_id TEXT REFERENCES audio_assets(id) ON DELETE SET NULL,
  updated_at INTEGER NOT NULL,
  CHECK (start_sec >= 0 AND end_sec - start_sec >= 20 AND end_sec - start_sec <= 30)
);

CREATE TABLE IF NOT EXISTS licensing (
  id TEXT PRIMARY KEY,
  song_id TEXT NOT NULL UNIQUE REFERENCES songs(id) ON DELETE CASCADE,
  licensor TEXT NOT NULL,
  license_type TEXT NOT NULL CHECK (license_type IN ('cc0','royalty_free','label','distributor','direct','publisher')),
  territories TEXT NOT NULL DEFAULT 'WORLD',
  allow_free_full_stream INTEGER NOT NULL DEFAULT 0,
  allow_offline INTEGER NOT NULL DEFAULT 0,
  allow_lyrics INTEGER NOT NULL DEFAULT 0,
  valid_from TEXT,
  valid_until TEXT,
  reference TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS lyrics (
  id TEXT PRIMARY KEY,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  language_code TEXT NOT NULL,
  is_translation INTEGER NOT NULL DEFAULT 0,
  is_synced INTEGER NOT NULL DEFAULT 0,
  lines_json TEXT NOT NULL,          -- [{"t": seconds|null, "text": "..."}]
  plain_text TEXT NOT NULL,          -- for lyric search
  source TEXT,                       -- licensor / provider reference
  created_at INTEGER NOT NULL,
  UNIQUE (song_id, language_code, is_translation)
);

CREATE TABLE IF NOT EXISTS playlists (
  id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_public INTEGER NOT NULL DEFAULT 0,
  share_token TEXT NOT NULL UNIQUE,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_songs (
  playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  position INTEGER NOT NULL,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, song_id)
);

CREATE TABLE IF NOT EXISTS playlist_follows (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, playlist_id)
);

CREATE TABLE IF NOT EXISTS listening_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  context TEXT NOT NULL DEFAULT 'full' CHECK (context IN ('full','highlight')),
  seconds_listened REAL NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  played_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON listening_history(user_id, played_at DESC);
CREATE INDEX IF NOT EXISTS idx_history_song ON listening_history(song_id, played_at DESC);

CREATE TABLE IF NOT EXISTS favourites (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, song_id)
);

CREATE TABLE IF NOT EXISTS user_languages (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  language_code TEXT NOT NULL REFERENCES languages(code) ON DELETE CASCADE,
  PRIMARY KEY (user_id, language_code)
);

CREATE TABLE IF NOT EXISTS user_categories (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, category_id)
);

CREATE TABLE IF NOT EXISTS plans (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  tier TEXT NOT NULL CHECK (tier IN ('free','premium')),
  price_minor INTEGER NOT NULL DEFAULT 0,    -- paise / cents
  currency TEXT NOT NULL DEFAULT 'INR',
  interval TEXT NOT NULL DEFAULT 'month' CHECK (interval IN ('month','year','none')),
  features_json TEXT NOT NULL DEFAULT '[]',
  is_active INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id TEXT NOT NULL REFERENCES plans(id),
  status TEXT NOT NULL CHECK (status IN ('pending','active','cancelled','expired')),
  provider TEXT NOT NULL,
  started_at INTEGER,
  current_period_end INTEGER,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id, status);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  subscription_id TEXT REFERENCES subscriptions(id),
  provider TEXT NOT NULL,
  provider_order_id TEXT,
  provider_payment_id TEXT,
  amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL,
  method TEXT,
  status TEXT NOT NULL CHECK (status IN ('created','paid','failed','refunded')),
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS skip_events (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS takedowns (
  id TEXT PRIMARY KEY,
  song_id TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  claimant TEXT NOT NULL,
  reason TEXT NOT NULL,
  admin_id TEXT REFERENCES users(id),
  action TEXT NOT NULL CHECK (action IN ('taken_down','restored')),
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id TEXT,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT,
  details TEXT,
  at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "free_skips_per_hour": "6",
    "free_ad_every_n_tracks": "4",
    "ad_duration_seconds": "5",
}


def conn():
    """SQLite connection for this thread (SQLite backend only)."""
    c = getattr(_local, "conn", None)
    if c is None:
        os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
        c = sqlite3.connect(config.DATABASE_PATH, timeout=15, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        c.execute("PRAGMA journal_mode = WAL")
        c.execute("PRAGMA busy_timeout = 15000")
        _local.conn = c
    return c


# ------------------------------------------------------------------ PostgreSQL connection pool
# Every HTTP request runs on its own short-lived thread, so connections are borrowed from a
# small pool and handed back when the request ends (release()) or when the thread exits.

_pool, _pool_lock = [], threading.Lock()
POOL_IDLE = 8          # idle connections kept open
POOL_MAX_AGE = 240     # seconds; Neon suspends idle computes after 5 minutes, so don't reuse older ones


class _Lease:
    def __init__(self, c):
        self.c, self.in_tx = c, False

    def __del__(self):  # thread finished without release()
        _give_back(self.c)


def _give_back(c):
    if c is None or c.closed:
        return
    if c.tx_status != "I":
        c.close()
        return
    with _pool_lock:
        if len(_pool) < POOL_IDLE:
            _pool.append((time.time(), c))
            return
    c.close()


def _lease():
    lease = getattr(_local, "pg", None)
    if lease is not None and not lease.c.closed:
        return lease
    c = None
    with _pool_lock:
        while _pool:
            at, cand = _pool.pop()
            if time.time() - at < POOL_MAX_AGE and not cand.closed:
                c = cand
                break
            cand.close()
    if c is None:
        c = pgwire.Connection(config.DATABASE_URL)
    _local.pg = _Lease(c)
    return _local.pg


def release():
    """Return this thread's PostgreSQL connection to the pool (called after each HTTP request)."""
    lease = getattr(_local, "pg", None)
    if lease is not None:
        _local.pg = None
        c, lease.c = lease.c, None
        _give_back(c)


def _pg_exec(sql, params):
    lease = _lease()
    text = _pg_sql(sql)
    try:
        return lease.c.execute(text, params)
    except pgwire.OperationalError:
        if lease.in_tx:
            raise
        # the server restarted or dropped idle connections: every pooled one is suspect, so start fresh
        _local.pg = None
        with _pool_lock:
            stale = [c for _, c in _pool]
            _pool.clear()
        for c in stale:
            c.close()
        return _lease().c.execute(text, params)


@functools.lru_cache(maxsize=2048)
def _pg_sql(sql):
    """Translate PENDEL's SQLite-flavoured SQL to PostgreSQL."""
    s = sql
    if re.match(r"\s*INSERT OR IGNORE INTO", s, re.I):
        s = re.sub(r"INSERT OR IGNORE INTO", "INSERT INTO", s, count=1, flags=re.I).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    s = re.sub(r"([\w.]+)\s+COLLATE\s+NOCASE", r"lower(\1)", s, flags=re.I)
    s = re.sub(r"group_concat\(([^(),]+),\s*('[^']*')\)", r"string_agg(\1, \2)", s, flags=re.I)
    s = re.sub(r"group_concat\(([^(),]+)\)", r"string_agg(\1, ',')", s, flags=re.I)
    s = re.sub(r"\bIFNULL\(", "COALESCE(", s, flags=re.I)
    s = re.sub(r"(?<![\w])LIKE\b", "ILIKE", s)  # SQLite's LIKE ignores case
    return s


def _pg_ddl(script):
    s = re.sub(r"(?m)^\s*PRAGMA[^;]*;", "", script)
    s = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY", s)
    s = re.sub(r"\bINTEGER\b", "BIGINT", s)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\s+COLLATE NOCASE", "", s)
    s = re.sub(r"ADD COLUMN (?!IF NOT EXISTS)", "ADD COLUMN IF NOT EXISTS ", s)
    return s


# ------------------------------------------------------------------ query helpers (both backends)

def q(sql, params=()):
    if BACKEND == "postgres":
        cur = _pg_exec(sql, params)
        cols = cur.columns or []
        return [dict(zip(cols, r)) for r in cur.rows]
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def one(sql, params=()):
    if BACKEND == "postgres":
        rows = q(sql, params)
        return rows[0] if rows else None
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def run(sql, params=()):
    if BACKEND == "postgres":
        return _pg_exec(sql, params)
    return conn().execute(sql, params)


class tx:
    """with tx(): ...  -- a transaction on this thread's connection."""

    def __enter__(self):
        if BACKEND == "postgres":
            lease = _lease()
            lease.c.execute("BEGIN")
            lease.in_tx = True
        else:
            conn().execute("BEGIN IMMEDIATE")

    def __exit__(self, et, ev, tb):
        if BACKEND == "postgres":
            lease = _local.pg
            lease.in_tx = False
            if lease.c.closed:
                _local.pg = None
                return
            try:
                lease.c.execute("COMMIT" if et is None else "ROLLBACK")
            except pgwire.OperationalError:
                _local.pg = None
                if et is None:
                    raise
            return
        conn().execute("COMMIT" if et is None else "ROLLBACK")


def describe():
    """Where data lives, for logs and the admin dashboard (no secrets)."""
    if BACKEND == "postgres":
        c = pgwire.parse_url(config.DATABASE_URL)
        return {"backend": "postgres", "host": c["host"], "database": c["dbname"], "persistent": True}
    return {"backend": "sqlite", "path": os.path.relpath(config.DATABASE_PATH, config.ROOT), "persistent": not config.EPHEMERAL_DISK}


def new_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def now():
    return int(time.time())


def setting(key):
    r = one("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else DEFAULT_SETTINGS.get(key)


MIGRATIONS = [
    # v2: world catalogue (iTunes / YouTube)
    "ALTER TABLE songs ADD COLUMN source TEXT NOT NULL DEFAULT 'pendel'",
    "ALTER TABLE songs ADD COLUMN external_id TEXT",
    "ALTER TABLE songs ADD COLUMN external_url TEXT",
    "ALTER TABLE songs ADD COLUMN youtube_id TEXT",
    "ALTER TABLE artists ADD COLUMN source TEXT NOT NULL DEFAULT 'pendel'",
    "CREATE INDEX IF NOT EXISTS idx_songs_source ON songs(source)",
    "CREATE TABLE IF NOT EXISTS external_cache (url TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at INTEGER NOT NULL)",
    "INSERT OR IGNORE INTO languages(code,name,native_name,region,featured,sort_order) VALUES('und','Other languages','Other','international',0,999)",
    # v3: YouTube full-song catalogue
    """CREATE TABLE IF NOT EXISTS youtube_imports (id TEXT PRIMARY KEY, label TEXT NOT NULL, source TEXT NOT NULL, lang TEXT NOT NULL,
       mood TEXT, max_videos INTEGER NOT NULL, status TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0, added INTEGER NOT NULL DEFAULT 0,
       error TEXT, created_at INTEGER NOT NULL, finished_at INTEGER)""",
    "DROP INDEX IF EXISTS idx_songs_status_plays",  # made SQLite scan by status instead of looking songs up by id
    "CREATE INDEX IF NOT EXISTS idx_songs_plays ON songs(play_count DESC)",
    "CREATE INDEX IF NOT EXISTS idx_songs_released ON songs(release_year DESC, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_songs_youtube ON songs(youtube_id)",
    # v4: resumable imports + case-insensitive genre names on PostgreSQL too
    "ALTER TABLE youtube_imports ADD COLUMN checkpoint TEXT",
    "ALTER TABLE youtube_imports ADD COLUMN updated_at INTEGER",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_genres_lower ON genres(lower(name))",
]


def migrate():
    for sql in MIGRATIONS:
        if BACKEND == "postgres":
            run(_pg_ddl(sql))
            continue
        try:
            run(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e):
                raise


def init():
    if BACKEND == "postgres":
        run(_pg_ddl(SCHEMA))  # idempotent: CREATE ... IF NOT EXISTS, ADD COLUMN IF NOT EXISTS
        migrate()
        _pg_secret()
    else:
        conn().executescript(SCHEMA)
        migrate()
    for k, v in DEFAULT_SETTINGS.items():
        run("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    seed_plans()
    seed_admin()
    if config.SEED_DEMO and not one("SELECT 1 FROM songs LIMIT 1"):
        seed_catalog()


def _pg_secret():
    """With no SECRET_KEY set, keep the generated signing key in the database rather than on the
    (temporary) disk, so logins survive restarts."""
    if config.SECRET_KEY:
        return
    run("CREATE TABLE IF NOT EXISTS app_secrets (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
    run("INSERT INTO app_secrets(name,value) VALUES('signing_key',?) ON CONFLICT DO NOTHING", (secrets.token_hex(32),))
    security._SECRET = one("SELECT value FROM app_secrets WHERE name='signing_key'")["value"].encode()


def seed_plans():
    plans = [
        ("free", "Free", "free", 0, "none", ["Ad-supported listening", "Up to 6 skips per hour", "Song highlights and previews", "Basic playlists"], 0),
        ("premium-monthly", "Premium Monthly", "premium", 11900, "month", ["Full licensed streaming", "No ads", "Unlimited skips", "High quality audio where available", "Offline downloads where licensing permits", "Advanced playlists"], 1),
        ("premium-yearly", "Premium Yearly", "premium", 119900, "year", ["Everything in Premium Monthly", "2 months free"], 2),
    ]
    for pid, name, tier, price, interval, feats, order in plans:
        run("INSERT OR IGNORE INTO plans(id,name,tier,price_minor,currency,interval,features_json,sort_order) VALUES(?,?,?,?,?,?,?,?)",
            (pid, name, tier, price, config.CURRENCY, interval, json.dumps(feats), order))


def seed_admin():
    if one("SELECT 1 FROM admins WHERE role='super_admin'"):
        return
    u = one("SELECT id FROM users WHERE email=?", (config.BOOTSTRAP_ADMIN_EMAIL,))
    uid = u["id"] if u else new_id("usr")
    if not u:
        run("INSERT INTO users(id,email,password_hash,display_name,created_at) VALUES(?,?,?,?,?)",
            (uid, config.BOOTSTRAP_ADMIN_EMAIL, security.hash_password(config.BOOTSTRAP_ADMIN_PASSWORD), "PENDEL Admin", now()))
    run("INSERT INTO admins(user_id, role, created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET role=excluded.role",
        (uid, "super_admin", now()))
    print(f"[pendel] Bootstrap super admin: {config.BOOTSTRAP_ADMIN_EMAIL} (change the password after first login)")


def genre_id(name):
    if not name:
        return None
    run("INSERT OR IGNORE INTO genres(name) VALUES(?)", (name,))
    return one("SELECT id FROM genres WHERE lower(name)=lower(?)", (name,))["id"]


def seed_catalog():
    path = os.path.join(config.ROOT, "server", "seed", "catalog.json")
    if not os.path.exists(path):
        print("[pendel] No demo catalogue found; run tools/generate_demo_music.py")
        return
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    t = now()
    with tx():
        for i, (code, name, native, region, featured) in enumerate(data["languages"]):
            run("INSERT OR IGNORE INTO languages(code,name,native_name,region,featured,sort_order) VALUES(?,?,?,?,?,?)",
                (code, name, native, region, featured, i))
        for i, (slug, name, color, feed) in enumerate(data["categories"]):
            run("INSERT OR IGNORE INTO categories(slug,name,color,has_highlight_feed,sort_order) VALUES(?,?,?,?,?)", (slug, name, color, feed, i))
        cats = {r["slug"]: r["id"] for r in q("SELECT id, slug FROM categories")}
        artist_ids = {}
        for slug, name, country, bio, indep in data["artists"]:
            aid = new_id("art")
            artist_ids[slug] = aid
            run("INSERT INTO artists(id,slug,name,country,bio,image_key,is_independent,is_verified,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (aid, slug, name, country, bio, f"artists/{slug}.svg", int(indep), 1, t))
        albums = {}
        for s in data["songs"]:
            if s["album_slug"] not in albums:
                alid = new_id("alb")
                albums[s["album_slug"]] = alid
                run("INSERT INTO albums(id,slug,title,artist_id,cover_key,release_year,label,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (alid, s["album_slug"], s["album"], artist_ids[s["artist"]], s["cover"], s["year"], "PENDEL Demo Originals (CC0)", t))
        for i, s in enumerate(data["songs"]):
            sid = new_id("sng")
            run("""INSERT INTO songs(id,slug,title,artist_id,album_id,language_code,genre_id,release_year,duration_sec,cover_key,
                   is_featured,play_count,is_demo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, s["slug"], s["title"], artist_ids[s["artist"]], albums[s["album_slug"]], s["lang"], genre_id(s["genre"]),
                 s["year"], s["duration"], s["cover"], int(s.get("featured", False)), s.get("plays", 0), 1, t - i * 3600 * 20, t))
            for c in s["cats"]:
                run("INSERT OR IGNORE INTO song_categories(song_id,category_id) VALUES(?,?)", (sid, cats[c]))
            full_id, prev_id = new_id("aud"), new_id("aud")
            run("INSERT INTO audio_assets(id,song_id,kind,storage_key,bitrate_kbps,duration_sec,size_bytes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (full_id, sid, "full", s["audio_full"], 80, s["duration"], _size(s["audio_full"]), t))
            run("INSERT INTO audio_assets(id,song_id,kind,storage_key,bitrate_kbps,duration_sec,size_bytes,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (prev_id, sid, "preview", s["audio_preview"], 80, s["highlight_end"] - s["highlight_start"], _size(s["audio_preview"]), t))
            run("INSERT INTO song_highlights(id,song_id,start_sec,end_sec,source,preview_asset_id,updated_at) VALUES(?,?,?,?,?,?,?)",
                (new_id("hl"), sid, s["highlight_start"], s["highlight_end"], "editorial", prev_id, t))
            run("""INSERT INTO licensing(id,song_id,licensor,license_type,allow_free_full_stream,allow_offline,allow_lyrics,reference,notes)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (new_id("lic"), sid, "PENDEL Demo Originals", "cc0", 0 if s.get("premium_only") else 1, 1, 1, "PENDEL-DEMO-CC0",
                 "Original composition and lyrics created for the PENDEL demo; dedicated to the public domain (CC0)."))
            if s["lyrics"]:
                lines = [{"t": l["t"], "text": l["text"]} for l in s["lyrics"]]
                save_lyrics(sid, s["lang"], lines, False, "PENDEL Demo Originals (CC0)")
                if any(l.get("tr") for l in s["lyrics"]) and s["lang"] != "en":
                    tr = [{"t": l["t"], "text": l.get("tr") or ""} for l in s["lyrics"]]
                    save_lyrics(sid, "en", tr, True, "PENDEL Demo Originals (CC0)")
        # demo listener
        if not one("SELECT 1 FROM users WHERE email='demo@pendel.local'"):
            run("INSERT INTO users(id,email,password_hash,display_name,created_at) VALUES(?,?,?,?,?)",
                (new_id("usr"), "demo@pendel.local", security.hash_password("Demo@12345"), "Demo Listener", t))
    print(f"[pendel] Seeded {len(data['songs'])} demo songs")


def save_lyrics(song_id, lang, lines, is_translation, source):
    synced = int(all(isinstance(l.get("t"), (int, float)) for l in lines) and len(lines) > 0)
    run("""INSERT INTO lyrics(id,song_id,language_code,is_translation,is_synced,lines_json,plain_text,source,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(song_id,language_code,is_translation) DO UPDATE SET is_synced=excluded.is_synced,
             lines_json=excluded.lines_json, plain_text=excluded.plain_text, source=excluded.source""",
        (new_id("lyr"), song_id, lang, int(is_translation), synced, json.dumps(lines, ensure_ascii=False),
         "\n".join(l["text"] for l in lines), source, now()))


def _size(key):
    p = os.path.join(config.MEDIA_DIR, key)
    return os.path.getsize(p) if os.path.exists(p) else None

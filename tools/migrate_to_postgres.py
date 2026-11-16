"""Copy your local PENDEL database (data/pendel.db, SQLite) into PostgreSQL.

Use it once, after creating a free PostgreSQL database (Neon, Supabase ...), so the
songs you already imported on your computer, your users and playlists appear on the
live site without spending YouTube quota again.

    python tools/migrate_to_postgres.py      (it asks you to paste the connection string)

    or set it first:
    # PowerShell (Windows)
    $env:DATABASE_URL = "postgresql://user:password@host/dbname?sslmode=require"
    python tools/migrate_to_postgres.py

    # macOS / Linux
    DATABASE_URL="postgresql://..." python tools/migrate_to_postgres.py

Options:
    --sqlite PATH     SQLite file to read (default: data/pendel.db)
    --force           replace data that is already in the PostgreSQL database
    --with-cache      also copy the Apple Music lookup cache (big, and refilled automatically)

The target must be empty (or use --force, which deletes what is there first).
Nothing is changed in the SQLite file.
"""
import argparse, os, sqlite3, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# parents before children, so foreign keys are satisfied while copying
TABLES = ["languages", "categories", "genres", "users", "admins", "artists", "albums", "songs", "song_categories", "audio_assets",
          "song_highlights", "licensing", "lyrics", "playlists", "playlist_songs", "playlist_follows", "listening_history", "favourites",
          "user_languages", "user_categories", "plans", "subscriptions", "payments", "skip_events", "takedowns", "audit_log", "settings",
          "youtube_imports", "external_cache"]
SERIAL = ["categories", "genres", "listening_history", "audit_log"]
BATCH = 400


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sqlite", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--with-cache", action="store_true")
    a = ap.parse_args()

    os.environ["SEED_DEMO"] = "0"  # the copy brings its own catalogue
    from server import config  # also reads DATABASE_URL from the .env file
    if "postgres" not in (config.DATABASE_URL or ""):
        print("No DATABASE_URL found in .env. Paste your PostgreSQL connection string from Neon")
        print("(Connect button, Connection pooling off), then press Enter. It starts with postgresql://")
        config.DATABASE_URL = input("> ").strip()
    if not config.DATABASE_URL:
        sys.exit("Set DATABASE_URL to your PostgreSQL connection string first (see the top of this file).")
    src_path = a.sqlite or config.DATABASE_PATH
    if not os.path.exists(src_path):
        sys.exit(f"SQLite database not found: {src_path}")
    from server import db

    print(f"Reading  {src_path}")
    print(f"Writing  PostgreSQL at {db.describe()['host']} / {db.describe()['database']}")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    db.init()  # creates the tables (no demo seed)
    existing = db.one("SELECT COUNT(*) n FROM songs")["n"] + db.one("SELECT COUNT(*) n FROM users WHERE email<>?", (config.BOOTSTRAP_ADMIN_EMAIL,))["n"]
    if existing and not a.force:
        sys.exit("The PostgreSQL database already has songs or users. Run again with --force to replace them.")

    tables = [t for t in TABLES if a.with_cache or t != "external_cache"]
    db.run("TRUNCATE " + ", ".join(tables) + " CASCADE")

    t0 = time.time()
    for table in tables:
        if not src.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
            continue
        src_cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
        dst_cols = {r["column_name"] for r in db.q("SELECT column_name FROM information_schema.columns WHERE table_name=?", (table,))}
        cols = [c for c in src_cols if c in dst_cols]
        total = src.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        copied = skipped = 0
        cur = src.execute(f"SELECT {', '.join(cols)} FROM {table}")
        one_row = "(" + ",".join("?" * len(cols)) + ")"
        while True:
            rows = cur.fetchmany(BATCH)
            if not rows:
                break
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES " + ",".join([one_row] * len(rows)) + " ON CONFLICT DO NOTHING"
            try:
                db.run(sql, [v for r in rows for v in r])
                copied += len(rows)
            except db.IntegrityError:  # a row pointing at something that no longer exists: copy the rest one by one
                for r in rows:
                    try:
                        db.run(f"INSERT INTO {table} ({', '.join(cols)}) VALUES {one_row} ON CONFLICT DO NOTHING", list(r))
                        copied += 1
                    except db.IntegrityError:
                        skipped += 1
            print(f"\r  {table:<20} {copied:>8,} / {total:,}", end="", flush=True)
        print(f"\r  {table:<20} {copied:>8,} / {total:,}" + (f"   ({skipped} broken rows skipped)" if skipped else ""))

    for table in SERIAL:  # continue numbering after the copied ids
        db.run(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)")
    # imports that were running locally continue on the server from their checkpoints
    db.run("UPDATE youtube_imports SET status='queued' WHERE status='running'")
    print(f"Done in {time.time() - t0:.0f}s. Songs in PostgreSQL: {db.one('SELECT COUNT(*) n FROM songs')['n']:,}")
    print("Next: add the same DATABASE_URL to Render (Environment tab) and redeploy.")


if __name__ == "__main__":
    main()

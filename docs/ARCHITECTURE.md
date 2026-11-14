# PENDEL architecture

## Today (single node)

```
Browser SPA ──HTTP/JSON──▶ server/app.py (ThreadingHTTPServer)
   │  <audio> + Range            ├── SQLite (WAL)          data/pendel.db
   └──── signed URL ───────────▶ ├── Storage driver        media/  or S3-compatible bucket (+ CDN)
                                 ├── highlights.py         ffmpeg + numpy analysis, preview cutting
                                 ├── recommend.py          content-based scoring
                                 └── payments.py           sandbox / Razorpay (pluggable)
```

- **Auth:** PBKDF2-SHA256 password hashes (200k iterations); HMAC-signed bearer tokens with a per-user `token_version` (used for "log out everywhere" and password changes); login and sign-up rate limits.
- **Media security:** every audio URL carries `exp` + `sig` (HMAC on local storage, SigV4 presign on S3). Uploaded images are restricted to PNG/JPEG/WebP with a magic-byte check, and SVG uploads are refused. `/media` responses are sandboxed with their own CSP.
- **Web security:** strict CSP (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, and HTML escaping by default in the frontend template helper.
- **Validation:** every write endpoint validates types, lengths and ranges and returns friendly 4xx messages. The database adds foreign keys, `CHECK` constraints (highlight length, enum values) and unique keys.

## Data model

`users`, `admins` (role), `artists`, `albums`, `songs` (unique `sng_…` id + slug), `song_categories` (many-to-many), `languages`, `categories`, `genres`, `audio_assets` (full/preview × standard/high), `song_highlights` (one per song, start/end stored separately, `CHECK` 20–30 s), `lyrics` (original + translations, synced lines as JSON, plain text for search), `licensing` (one per song), `playlists`, `playlist_songs` (ordered), `playlist_follows`, `listening_history`, `favourites`, `user_languages`, `user_categories`, `plans`, `subscriptions`, `payments`, `skip_events`, `takedowns`, `audit_log`, `settings`.

## Scaling to millions of listeners

| Layer | Change |
|---|---|
| Database | Move the schema to PostgreSQL (the SQL is portable; swap `db.py`'s connection for psycopg). Add read replicas, and partition `listening_history` by month. |
| API | Run several stateless app instances behind a load balancer. Tokens are self-contained, so no sticky sessions are needed. Port the handlers to FastAPI or Go if you want async I/O. |
| Audio | Store files in S3/R2 behind a CDN with signed URLs (already supported). Add HLS/DASH packaging with multiple bitrates for adaptive streaming. |
| Search | Mirror songs and lyrics into OpenSearch/Elasticsearch with ICU analyzers for Indic and CJK scripts. |
| Caching | Put Redis in front of `/api/home`, language pages and highlight feeds (short TTLs, per-language keys). |
| Background jobs | Move highlight detection, preview cutting and transcoding to a queue (Celery/RQ/SQS workers). |
| Recommendations | Keep the current content-based scorer as the cold-start layer. Add collaborative filtering or embeddings trained on `listening_history` behind the same `recommend()` interface. |
| Analytics | Stream play events to a warehouse (BigQuery/ClickHouse) and keep only recent rows in the OLTP database. |

# PENDEL

### Feel Every Beat.

**🔗 Live demo: [pendel.onrender.com](https://pendel.onrender.com)**
*(free hosting — the first visit can take up to a minute to wake the server)*

Demo login: `demo@pendel.local` / `Demo@12345`

PENDEL is a full-stack, multilingual music streaming platform built around one idea: every song has a 20–30 second moment that makes people fall in love with it. Listeners swipe through those **Song Highlights** by mood and language, then tap **Play full song** to hear the complete licensed track.

![PENDEL home](docs/screenshots/home.png)

## New in v4: data that survives restarts, background imports, a compact player

- **Persistent database.** Set `DATABASE_URL` to a PostgreSQL database and PENDEL stores everything there: users, playlists, history and every imported song. Render's free plan wipes its disk on each restart; with this nothing is lost. Without `DATABASE_URL`, PENDEL keeps using SQLite in `data/pendel.db` (local development). PostgreSQL support uses PENDEL's own small driver (`server/pgwire.py`, standard library only), so there is still nothing to `pip install`.
- **Imports run in the background.** Starting an import returns at once; a worker thread does the work and saves a checkpoint after every page of 50 videos. If Render restarts or sleeps, the import continues from its checkpoint on the next start (the 100-unit search is never repeated). Jobs paused by the daily quota continue by themselves the next day. The dashboard shows a live progress bar.
- **Admin routes stay locked.** Every import, export, cancel and delete route needs an admin login with the right role (tests check that listeners get 403 and visitors 401).
- **Compact mini-player and reliable auto-next.** The bar shows a 64×64 thumbnail; the YouTube video sits in a 200×200 dock (YouTube's minimum size), which listeners can move or enlarge. When a song ends the next one starts; unplayable videos are skipped; with *Autoplay* on (queue panel) similar songs keep playing after the queue ends.

- **Phone-friendly.** Lock-screen and notification controls (title, artist, cover, play/pause, next/previous, seek) through the Media Session API, and an installable app: on Android Chrome use *Install app*, on iPhone Safari use *Share → Add to Home Screen*. PENDEL's own songs and Apple previews keep playing with the screen locked. YouTube songs pause when the screen locks or you switch apps (YouTube's rule for embedded players) and continue when you come back.

- **Direct audio files (native player).** A song can have an `audio_url`: a licensed `.mp3`, `.m4a`, `.aac`, `.opus`, `.ogg`, `.webm`, `.flac` or `.wav` file on https. Such songs play in PENDEL's own `<audio id="audio-player">` player instead of YouTube, so they keep playing with the screen off and when switching apps, with full lock-screen controls. Set it in **Admin → Songs → edit → Direct audio file**, or with the `audio_url`, `audio_format`, `cover_url` and `duration_sec` columns of the CSV import. Links to YouTube audio are refused. Songs with no direct file keep using the visible YouTube player.

- **Upload tracks to Cloudflare R2.** *Admin → Songs → Upload track*: title, artist, language, mood, audio (.mp3, .m4a, .opus, .wav), optional cover, and a required rights confirmation. The file goes to your R2 bucket (signed S3 API calls in `server/storage.py`, standard library only) and the song streams from R2's public URL in the native player. Endpoint: `POST /api/songs/upload` (multipart form, admins only). Without the R2 settings, uploads are kept on the server's own disk, which Render wipes on restart.

- **“Hey Pendel” voice control.** Tap the microphone in the top bar, then say *“Hey Pendel, pause”*, *“next song”*, *“previous”*, *“forward 10 seconds”*, *“go back 5 seconds”*, *“repeat this song”* / *“repeat off”*, or *“play Kesariya”*. Uses the browser's own speech recognition (Chrome, Edge, Safari; not Firefox) through `web/js/voice.js`; the music dips while you speak, a small pill shows what was heard, and short spoken replies can be switched off with *“Hey Pendel, stop talking”*. The browser listens only while the tab is open and in front: phones stop the microphone when the screen locks.

### Set up Cloudflare R2 (free tier, about 10 minutes)

1. In the Cloudflare dashboard open **R2 Object Storage** and **Create bucket** (for example `pendel-songs`).
2. In the bucket's **Settings**, turn on the **Public Development URL** (`https://pub-….r2.dev`), or connect your own domain.
3. Back in R2, open **Manage API tokens → Create API token** with **Object Read & Write** on that bucket. Copy the **Access Key ID**, **Secret Access Key** and the **S3 endpoint** (`https://<account-id>.r2.cloudflarestorage.com`).
4. Add `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` and `R2_PUBLIC_URL` to Render's **Environment** tab (and to `.env` for local use). Never commit them.

### Keep your data on Render (free PostgreSQL, about 10 minutes)

1. Create a free account at [neon.com](https://neon.com), create a project (pick the region closest to your Render service, e.g. Singapore), and copy the **connection string**. It looks like `postgresql://user:password@ep-xxxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`. Use the normal (direct) string, not the "pooled" one.
2. **Copy what you already have** (your imported songs, users and playlists) from your computer, once:
   ```powershell
   $env:DATABASE_URL = "postgresql://...paste yours..."
   python tools/migrate_to_postgres.py
   Remove-Item Env:DATABASE_URL
   ```
3. On Render open your service → **Environment** → add `DATABASE_URL` with the same string, and also `SECRET_KEY` (any long random text) so logins survive redeploys. Save; Render redeploys.
4. Open `https://pendel.onrender.com/admin` → YouTube catalogue. The Database card shows **PostgreSQL · saved**. From now on, imports you start there are permanent; there is no snapshot to export or commit.

Keep the connection string secret like a password: put it only in Render's Environment tab or your local `.env` (which is git-ignored), never in code or GitHub. Supabase, Render Postgres and any other PostgreSQL host work the same way. MongoDB isn't supported: PENDEL's catalogue is relational (joins, unique keys and transactions everywhere).

## v3: 50,000+ full songs in every language (YouTube)

PENDEL can import **complete official songs** for all 26 languages from YouTube, which plays them in a visible YouTube player inside the app.

**One-time setup (about 1 hour, mostly waiting):**
1. Get a free YouTube API key (see *Turn on full songs* below) and add it to `.env` as `YOUTUBE_API_KEY=...`. Restart PENDEL.
2. Log in as admin, open **Admin → YouTube catalogue** and click **Run round 1**. PENDEL finds playlists such as "kannada sad songs" or "telugu love songs" for every language and mood and imports every song in them. Language and mood come from the playlist, so labels are accurate. Trailers, teasers, jukeboxes, live streams and non-embeddable videos are skipped. Leave PENDEL running; the page shows progress.
3. Round 1 typically adds tens of thousands of songs using about a third of the free daily quota. YouTube allows about 100 searches a day, so run **round 2** and **round 3** on the next days for more.
4. *Only if you don't use `DATABASE_URL`:* click **Export snapshot**, then publish it:
   ```powershell
   git add catalog/youtube_snapshot.json.gz
   git commit -m "Add YouTube catalogue"
   git push
   ```
   Render reloads the snapshot on every start, without using any quota. Also add `YOUTUBE_API_KEY` in Render's Environment settings so it can refresh old snapshots automatically.

**Rules PENDEL follows:** songs play only in the visible YouTube player (no audio-only playback, no downloads); titles and thumbnails are shown unchanged; stored YouTube data is refreshed or removed within 30 days (export a new snapshot monthly).

**Limits:**
- The exact count depends on how big the playlists YouTube returns are. Add more with rounds 2–3 or by importing a label's channel (paste its link in *Import a channel or playlist*).
- A YouTube song's highlight is an estimated 25-second window from about a third of the way in, because PENDEL can't analyse YouTube audio. Admins can set exact windows.
- YouTube songs stop when the phone screen locks or the browser tab is in the background; that is how YouTube's player works. PENDEL's own songs and Apple previews keep playing.
- YouTube requires its embedded player to be visible and at least 200×200 pixels, so PENDEL can't shrink the video to a 64×64 thumbnail or hide it for audio-only playback. The bar's 64×64 image is the song's thumbnail; the 200×200 dock is the player.
- YouTube may show its own ads inside its player.

## New in v2: real songs in every language + 1-hour Mood Sessions

- **World catalogue:** PENDEL is connected to Apple's public **iTunes Search API**, giving millions of real releases in 26 languages, from Telugu, Tamil, Hindi and Kannada to K-pop and Latin. Every song plays its **official 30-second preview** as its highlight in the Highlights feed, on language and mood pages, and in search. Nothing is copied: audio streams from Apple's servers.
- **Full songs:** add a free YouTube API key and "Play full song" plays the official video in a visible YouTube player inside PENDEL. Admins can also paste a video link and set their **own 20–30 s highlight** on it.
- **Mood Session:** pick a mood, one or more languages and a length (30 min, 1 h, 90 min or 2 h). PENDEL builds a non-stop queue: either a **Highlights marathon** (about 120 back-to-back best moments per hour) or **Full songs**.
- **Trending on Apple Music:** the home page shows the live most-played chart for India.
- PENDEL's own 39 songs keep everything they had: full audio, waveform highlight editor and synced lyrics.

### Turn on full songs (free YouTube API key, about 5 minutes)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and create a project, for example "PENDEL".
2. Open **APIs & Services → Library**, search for **YouTube Data API v3** and click **Enable**.
3. Open **APIs & Services → Credentials → Create credentials → API key**. Restrict the key to *YouTube Data API v3*.
4. Add it as `YOUTUBE_API_KEY` in your `.env` file (local) or in Render's **Environment** settings (live site).

Each new song costs one YouTube search, and the result is remembered. The free daily quota covers roughly 100 new songs a day. Songs without a found video still play their 30-second preview.

### Limits of the world catalogue (be honest in interviews)

- Previews are chosen by Apple, so they may not be the chorus. Use the admin YouTube editor for hand-picked highlights.
- Apple has no "language" field. PENDEL labels songs by the language it searched for and Apple's genre names (Telugu, Tamil, Bollywood, K-Pop…), so a few songs may be mislabelled. Admins can fix the language in the song editor.
- Lyrics are only shown for PENDEL's own songs, because real song lyrics need a lyrics licence.
- This setup is for a portfolio or demo. A commercial streaming business needs licensing deals with music labels.

## Screenshots

| Song Highlights (swipe feed) | Full player with synced lyrics |
|---|---|
| ![Highlights](docs/screenshots/highlights.png) | ![Player and lyrics](docs/screenshots/player-lyrics.png) |
| **Language-wise library** | **Mobile** |
| ![Language page](docs/screenshots/language.png) | <img src="docs/screenshots/mobile-highlights.png" width="260" alt="Mobile highlights"> |

**Admin waveform highlight editor:** drag a 20–30 s window over the chorus, auto-detect it from the audio, preview and save.

![Admin highlight editor](docs/screenshots/admin-highlight-editor.png)

## Highlights of the project

- **20–30 s Song Highlights:** a vertical swipe feed with 10 mood feeds, synced lyric lines and translations, auto-advance, and one tap to the full song.
- **Automatic chorus detection:** scores audio windows by loudness and repetition (NumPy + FFmpeg). It finds the chorus in 35 of 39 demo songs.
- **24 languages** in the demo catalogue (Telugu, Tamil, Hindi, Kannada, Malayalam, Bengali, Punjabi, English, Spanish, Korean, Japanese, Arabic and more), with native-script lyrics.
- **Complete player:** queue, shuffle, repeat, seek, mini and full-screen modes, lock-screen controls, synced lyrics with click-to-seek.
- **Accounts and library:** email/Google login, favourites, playlists (drag to reorder, share, follow), auto playlists and listening history.
- **Recommendations:** content-based "Made For You" mixes and a mood picker.
- **Free and Premium plans:** skip limits, house ads and Premium-only tracks, with a UPI/card payment architecture (Razorpay-ready, test mode by default).
- **Admin dashboard:** role-based access, analytics, song and licence management, bulk CSV import, lyrics editor, copyright takedowns and an audit log.
- **Security:** PBKDF2 password hashing, signed bearer tokens, expiring HMAC-signed audio URLs, a strict Content Security Policy and rate limiting.
- **Tested:** 31 automated API tests (including simulated Apple and YouTube responses and a 68,000-song import) and a Playwright browser walkthrough.

**Tech stack:** Python 3.10+ (standard library HTTP server, no frameworks) · PostgreSQL (own wire-protocol driver) or SQLite · vanilla JavaScript ES modules · HTML/CSS · NumPy · FFmpeg · deployed on Render.

PENDEL's own 39 demo songs, covers and lyrics are original works generated by code and released as CC0. World-catalogue songs are streamed from Apple (30-second previews) and YouTube (official videos); PENDEL never stores their audio.

---

## Run it (Windows PowerShell)

```powershell
cd PENDEL
python --version          # needs 3.10 or newer
python -m server.app
```

Open **http://localhost:8000**.

| Account | Email | Password |
|---|---|---|
| Listener | `demo@pendel.local` | `Demo@12345` |
| Super admin | `admin@pendel.local` | `Admin@12345` (change it after first login) |

> Upgrading from the earlier "Refrain" build? Your existing `data/refrain.db` is picked up automatically, and its accounts keep their old `@refrain.local` emails. Delete `data/` to reseed with the PENDEL demo accounts.

The database is created in `data/` on first start and seeded with the demo catalogue. To start fresh, stop the server and delete the folder with `Remove-Item -Recurse -Force data`.

To change settings, run `Copy-Item .env.example .env` and edit `.env` (port, admin account, storage, Google sign-in, payments).

**Optional:** install [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) and run `pip install numpy`. With both installed, the server automatically detects the best highlight in uploaded songs and cuts a separate preview file for each one. Without them, everything still works: admins set highlights by hand, and the player plays that time window from the full file.

On macOS or Linux the commands are the same; use `python3`.

## Tests

```powershell
python -m unittest tests.test_api -v      # 34 API tests on a throwaway SQLite database (Apple/YouTube responses simulated)
python tests/e2e_autonext.py              # browser test: mini-player size, auto-next, autoplay, skipping broken videos
```

To run the API tests on PostgreSQL too, point `TEST_DATABASE_URL` at an **empty, throwaway** database (the tests delete everything in it): `$env:TEST_DATABASE_URL = "postgresql://..."`.

`tests/e2e_browser.py` is a Playwright browser walkthrough. It needs `pip install playwright` and the server running; set `BASE` to the server's URL.

---

## Feature map

| Area | Where |
|---|---|
| Home: Trending, Most Popular, Recently Released, Top 20, Recommended (with a mood picker), Made For You mixes, and 19 mood/category rails | `/` |
| **Song Highlights**: vertical swipe feed (scroll-snap, arrow keys, ▲▼ buttons), 10 feeds (Trending, Sad, Love, Heartbreak, Devotional, DJ, Melody, Motivation, Romantic, Feelings), language filter, synced lyric lines with translation, progress bar, auto-advance, favourite, add to playlist, share, **Play full song** (opens the full-screen player) | `/highlights/<feed>` |
| Languages: 12 Indian and 14 international languages plus "discover more" (14 extra); each language page has trending, new releases, sad, love, devotional, DJ, artists, albums and complete songs | `/languages`, `/language/te` |
| Full Songs: search, language, mood, genre, year range, has-lyrics and free-to-stream filters; six sort orders; pagination | `/songs` |
| Player: play/pause, next/previous, ±10 s, seek, volume and mute, shuffle, repeat (all/one), queue drawer (reorder, remove, play next), mini-player, full-screen player, lock-screen and headset controls (Media Session), keyboard shortcuts, keeps playing across pages | everywhere |
| Lyrics: licensed lyrics only, synced highlighting, auto-scroll (pauses when the listener scrolls), tap a line to seek, translations, expandable drawer, any Unicode script with `dir="auto"` | 🎤 button |
| Search: songs, artists, albums, languages (by English or native name), moods, genres, and licensed lyric lines; live autocomplete with keyboard navigation | top bar, `/search` |
| Accounts: email sign-up and login, Google Sign-In (once configured), avatar upload, preferred languages and moods, theme, change password, log out of all devices | `/profile` |
| Library: playlists, auto playlists (My Sad Songs, My Love Songs, My Favourite Devotional Songs, My DJ Collection, My Trending Songs), favourites, recently played, listening history | `/library` |
| Playlists: create, edit, delete, add/remove songs, drag-and-drop or ▲▼ reordering, public/private, share link, follow | `/playlist/<id>` |
| Subscriptions: Free (house ads every N songs, skip limit, previews of Premium-only songs) and Premium (full streams, no ads, unlimited skips, high-quality assets when uploaded) | `/premium` |
| Admin: role-based (super admin, content manager, moderator, analyst); analytics including highlight→full-song conversion; song CRUD with mandatory licensing; audio and cover upload; **waveform highlight editor** (drag a 20–30 s window, auto-detect, preview, save); LRC lyrics and translations; bulk upload with CSV import and dry run; artists, albums, categories, languages, users, roles, Premium grants, playlists, plans and free-tier rules; copyright takedown/restore log; audit log | `/admin` |
| SEO: server-rendered `<title>`, meta description, Open Graph tags and JSON-LD (`MusicRecording`/`MusicGroup`) on song, artist, language and category URLs; `sitemap.xml`, `robots.txt` | |

## How highlights work

1. Every song has one row in `song_highlights` (`start_sec`, `end_sec`), connected to the song by `song_id`. A database `CHECK` constraint enforces a length of 20–30 seconds.
2. When an admin uploads audio, `server/highlights.py` decodes it and scores every 25-second window on **loudness/density** and **repetition** (how closely it matches another part of the song, which is the signature of a chorus). The first 15 seconds are penalised, so a highlight never defaults to the intro. On the demo catalogue it lands on a chorus in 35 of 39 songs.
3. The suggestion is saved as `source=auto`. Admins can drag the window in the waveform editor, and their choice is saved as `manual`, which a later re-upload never overwrites.
4. ffmpeg cuts a separate **preview asset** with short fades. The highlight feed streams only that file, so free listeners never receive the full track for Premium-only songs.

## Licensing safeguards built in

- A song cannot be created without a licensing record (licensor, type, territories, dates, reference) and cannot be published without audio.
- Licence flags control **free full streaming**, **offline downloads** and **lyrics display**. Lyrics can only be added to a song when the licence allows them.
- Audio is only served through signed URLs that expire.
- **Takedown** removes a song from every listing, feed and search result and returns HTTP 451 for streams. It keeps a legal record of the claimant and reason, and can be reversed.
- Every admin action is written to the audit log.

## Going to production

| Concern | What to do |
|---|---|
| Real catalogue | Upload licensed audio in the admin dashboard, or bulk-upload files and import a CSV. Set `SEED_DEMO=false` (and delete `data/`) to start without the demo songs. |
| Cloud audio + CDN | Set `STORAGE_DRIVER=s3` and the `S3_*` variables, then run `python tools/sync_media_to_s3.py` once. Put CloudFront or Cloudflare in front of the bucket and set `CDN_BASE_URL`. If the bucket is on another domain, enable CORS for GET from your site so the admin waveform can load. |
| Payments | Set `PAYMENT_PROVIDERS=razorpay` with `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` (supports UPI, cards, netbanking and wallets). Point the Razorpay webhook to `/api/webhooks/razorpay` and set `RAZORPAY_WEBHOOK_SECRET`. **Remove `sandbox`.** Other gateways (Stripe, PayPal, Paytm) plug in through `server/payments.py`. |
| Google login | Set `GOOGLE_CLIENT_ID`. The button appears automatically, and ID tokens are verified on the server. |
| Database | Set `DATABASE_URL` (PostgreSQL). Copy an existing SQLite database over with `python tools/migrate_to_postgres.py`. Set `SECRET_KEY` as well. |
| HTTPS & scale | Run behind Nginx or Caddy with TLS, and set `PUBLIC_URL`. For millions of users, see `docs/ARCHITECTURE.md`: read replicas, Redis, workers, OpenSearch and an audio CDN. |

## Project layout

```
server/      app.py (HTTP server, router, 100+ API endpoints), db.py (schema, SQLite/PostgreSQL, seed), pgwire.py (PostgreSQL driver), security.py,
             storage.py (local/S3), highlights.py (detection + preview cutting), recommend.py, payments.py, config.py
web/         index.html, css/app.css, js/{app,api,ui,player,lyrics,views,highlights,admin}.js, img/ (logo, icons)
media/       demo audio (full + preview), album covers, artist images
tools/       generate_demo_music.py (re-creates the CC0 demo catalogue; needs numpy + ffmpeg), catalog_data.py, sync_media_to_s3.py,
             migrate_to_postgres.py (copy data/pendel.db into PostgreSQL)
tests/       test_api.py, e2e_browser.py, e2e_autonext.py
docs/        ARCHITECTURE.md
```

import { session, setAuth, get, post, put, patch, del, upload } from "./api.js";
import { setVoiceEnabled, voiceWanted, voiceSupported, onVoiceStatus, voiceStatusText } from "./voice.js";
import { player } from "./player.js";
import {
  html, raw, render, icon, $, $$, fmtTime, fmtNum, fmtMoney, fmtDate, toast, toastError, modal, closeModal, confirmDialog,
  rail, songCard, songList, registerQueue, emptyState, errorState, skeletonRails, skeletonRows, mosaic, debounce, songs,
} from "./ui.js";

const nav = (h) => window.rfNavigate(h);
const MOODS = [["sad", "Sad"], ["love", "Love"], ["romantic", "Romantic"], ["motivational", "Motivation"], ["devotional", "Devotional"], ["dj", "DJ"], ["melody", "Melody"], ["party", "Party"]];

// ------------------------------------------------------------------ Home
export async function homeView(view, query, alive) {
  render(view, skeletonRails());
  const d = await get("/api/home");
  if (!alive()) return;
  const feat = d.featured;
  let slide = 0;
  const heroQ = registerQueue(feat, "Featured");
  const hero = () => {
    const s = feat[slide];
    return html`<section class="hero" aria-roledescription="carousel" aria-label="Featured songs">
      <div class="hero-bg" style="background-image:url('${s.cover_url}')"></div>
      <div class="hero-inner">
        <img class="hero-art" src="${s.cover_url}" alt="" width="210" height="210">
        <div>
          <div class="hero-kicker">Featured · ${s.language.name}</div>
          <h1>${s.title}</h1>
          <div class="hero-meta"><a href="/artist/${s.artist.slug}" data-link>${s.artist.name}</a><span>${s.genre}</span><span>${s.year}</span><span>${fmtNum(s.plays)} plays</span></div>
          <div class="hero-actions">
            <button class="btn primary lg" data-action="play" data-id="${s.id}" data-queue="${heroQ}" data-index="${slide}">${icon("play")}Play full song</button>
            ${s.highlight ? html`<button class="btn lg" data-action="play-highlight" data-id="${s.id}">${icon("spark")}Hear the highlight</button>` : ""}
            <button class="icon-btn ${s.is_favourite ? "fav-on" : ""}" data-action="fav" data-id="${s.id}" aria-label="Favourite">${icon(s.is_favourite ? "heart-fill" : "heart")}</button>
          </div>
        </div>
      </div>
      <div class="hero-dots">${feat.map((_, i) => html`<button class="${i === slide ? "on" : ""}" data-slide="${i}" aria-label="Show featured song ${i + 1}"></button>`)}</div>
    </section>`;
  };
  const greet = (() => { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; })();
  const byKey = Object.fromEntries(d.rails.map((r) => [r.key, r]));
  const top = ["trending", "popular", "recent", "top20"].map((k) => byKey[k]);
  const rest = d.rails.filter((r) => !["trending", "popular", "recent", "top20", "recommended"].includes(r.key));
  const rec = byKey.recommended;
  render(view, html`
    <h2 style="margin-bottom:16px">${greet}${session.user ? `, ${session.user.display_name.split(" ")[0]}` : ""}</h2>
    <div id="hero-slot">${feat.length ? hero() : ""}</div>
    <section class="section"><div class="section-head"><div><h2>Mood Session</h2><p>One tap, one hour of music for how you feel, in your languages</p></div><a class="chip" href="/mood" data-link>${icon("timer")}Customise</a></div>
      <div class="mood-grid">${[["love", "Love", "#ff4f8b"], ["sad", "Sad", "#5b7cfa"], ["devotional", "Devotional", "#ff9f43"], ["dj", "DJ & Party", "#00e0c6"], ["motivational", "Motivation", "#ffb020"], ["melody", "Melody", "#3ad29f"]]
        .map(([m, n, c]) => html`<a class="mood-pick" style="--c1:${c};display:flex;flex-direction:column;justify-content:space-between" href="/mood?mood=${m}&minutes=60" data-link>${n}<span style="font-size:12px;font-weight:500;opacity:.85">1-hour session</span></a>`)}</div></section>
    ${d.made_for_you.length ? html`<section class="section"><div class="section-head"><div><h2>Made For You</h2><p>Mixes built from your listening, favourites and languages</p></div></div>
      <div class="rail">${d.made_for_you.map((m) => { const q = registerQueue(m.songs, m.title); return html`<article class="card" data-action="play-all" data-queue="${q}" tabindex="0"><div class="card-art">${mosaic(m.songs.map((s) => s.cover_url))}<button class="play-btn grad card-play" data-action="play-all" data-queue="${q}" aria-label="Play ${m.title}">${icon("play")}</button></div><div class="card-title">${m.title}</div><div class="card-sub">${m.subtitle}</div></article>`; })}</div></section>` : ""}
    ${top.map((r) => rail(r.title, r.songs, { subtitle: r.subtitle, ranked: r.ranked, hlFeed: r.highlight_feed || "", moreHref: r.key === "recent" ? "/songs?sort=newest" : r.key === "popular" ? "/songs?sort=popular" : "" }))}
    <section class="section" id="rec-section">${rail(rec.title, rec.songs, { subtitle: rec.subtitle })}</section>
    ${session.user ? "" : ""}
    ${rest.map((r) => rail(r.title, r.songs, { hlFeed: r.highlight_feed || "", moreHref: `/category/${r.category}` }))}
  `);
  // mood picker for "Recommended For You"
  const recSec = $("#rec-section");
  if (session.user) {
    const head = $(".section-head", recSec);
    head.insertAdjacentHTML("afterend", html`<div class="chips scroll mood-bar" role="group" aria-label="Pick a mood">${[["", "For you"], ...MOODS].map(([k, l]) => html`<button class="chip ${k ? "" : "active"}" data-mood="${k}">${l}</button>`)}</div>`.s);
    recSec.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-mood]");
      if (!b) return;
      $$("[data-mood]", recSec).forEach((x) => x.classList.toggle("active", x === b));
      try {
        const r = await get(`/api/me/recommendations?mood=${b.dataset.mood}`);
        const q = registerQueue(r.songs, "Recommended");
        render($(".rail", recSec), html`${r.songs.slice(0, 16).map((s, i) => songCard(s, { queue: q, index: i }))}`);
      } catch (err) { toastError(err); }
    });
  }
  const slot = $("#hero-slot");
  const setSlide = (i) => { slide = (i + feat.length) % feat.length; render(slot, hero()); };
  slot.addEventListener("click", (e) => { const b = e.target.closest("[data-slide]"); if (b) setSlide(+b.dataset.slide); });
  const timer = feat.length > 1 ? setInterval(() => { if (!slot.matches(":hover") && !slot.contains(document.activeElement)) setSlide(slide + 1); }, 7000) : null;
  return () => clearInterval(timer);
}

// ------------------------------------------------------------------ Explore / languages / categories
const LANG_COLORS = [["#8b5cf6", "#ec4899"], ["#06b6d4", "#3b82f6"], ["#f59e0b", "#ef4444"], ["#10b981", "#0ea5e9"], ["#ec4899", "#f97316"], ["#6366f1", "#14b8a6"], ["#e11d48", "#7c3aed"], ["#0891b2", "#22c55e"]];
const langTile = (l, i) => { const [a, b] = LANG_COLORS[i % LANG_COLORS.length]; return html`<a class="tile" href="/language/${l.code}" data-link style="--c1:${a};--c2:${b}"><div class="tile-native" lang="${l.code}">${l.native_name}</div><div><div class="tile-name">${l.name}</div><div class="tile-count">${l.song_count} songs</div></div></a>`; };
const catTile = (c) => html`<a class="tile" href="/category/${c.slug}" data-link style="--c1:${c.color};--c2:#1a1530"><div class="tile-name" style="font-size:20px">${c.name}</div><div class="tile-count">${c.song_count} songs${c.has_highlight_feed ? " · highlights" : ""}</div></a>`;

export async function exploreView(view, query, alive) {
  render(view, skeletonRails(2));
  const [langs, cats, pub] = await Promise.all([get("/api/languages"), get("/api/categories"), get("/api/playlists/public")]);
  if (!alive()) return;
  const featured = langs.languages.filter((l) => l.featured);
  render(view, html`<h1>Explore</h1><p class="muted" style="margin-top:-4px">Every language, every mood.</p>
    <section class="section"><div class="section-head"><h2>Moods & categories</h2></div><div class="tiles">${cats.categories.map(catTile)}</div></section>
    <section class="section"><div class="section-head"><div><h2>Indian languages</h2></div><a class="chip" href="/languages" data-link>All languages</a></div><div class="tiles">${featured.filter((l) => l.region === "indian").map(langTile)}</div></section>
    <section class="section"><div class="section-head"><h2>International</h2></div><div class="tiles">${featured.filter((l) => l.region === "international").map((l, i) => langTile(l, i + 3))}</div></section>
    <section class="section"><div class="section-head"><div><h2>Community playlists</h2><p>Public playlists from listeners</p></div></div>
      ${pub.playlists.length ? html`<div class="rail">${pub.playlists.map((p) => html`<a class="card" href="/playlist/${p.id}" data-link><div class="card-art">${mosaic(p.cover_urls)}</div><div class="card-title">${p.title}</div><div class="card-sub">by ${p.owner_name} · ${p.song_count} songs</div></a>`)}</div>`
        : emptyState("queue", "No public playlists yet", "Make one of your playlists public to share it here.")}
    </section>`);
}

export async function languagesView(view, query, alive) {
  render(view, skeletonRows(4));
  const { languages } = await get("/api/languages");
  if (!alive()) return;
  const feat = languages.filter((l) => l.featured), more = languages.filter((l) => !l.featured);
  render(view, html`<h1>Languages</h1><p class="muted">Pick a language to explore trending songs, new releases, artists and highlights.</p>
    <section class="section"><div class="section-head"><h2>Indian languages</h2></div><div class="tiles">${feat.filter((l) => l.region === "indian").map(langTile)}</div></section>
    <section class="section"><div class="section-head"><h2>International languages</h2></div><div class="tiles">${feat.filter((l) => l.region === "international").map((l, i) => langTile(l, i + 2))}</div></section>
    <section class="section"><div class="section-head"><div><h2>Discover more languages</h2><p>Growing catalogues from around the world</p></div><button class="chip" id="more-btn" aria-expanded="false">Show ${more.length} more</button></div>
      <div class="tiles" id="more-langs" hidden>${more.map((l, i) => langTile(l, i + 5))}</div></section>`);
  $("#more-btn").addEventListener("click", (e) => { const m = $("#more-langs"); m.hidden = !m.hidden; e.target.setAttribute("aria-expanded", String(!m.hidden)); e.target.textContent = m.hidden ? `Show ${more.length} more` : "Show fewer"; });
}

export async function languageView(view, code, query, alive) {
  render(view, skeletonRails());
  const d = await get(`/api/languages/${code}`);
  if (!alive()) return;
  const l = d.language;
  const all = d.sections.find((s) => s.key === "all");
  const qAll = registerQueue(all.songs, l.name);
  render(view, html`
    <section class="hero cat"><div class="hero-bg" style="background:linear-gradient(135deg,#8b5cf6,#ec4899,#f59e0b)"></div>
      <div class="hero-inner"><div><div class="hero-kicker">Language</div><h1 lang="${l.code}">${l.native_name}</h1>
        <div class="hero-meta"><span>${l.name}</span><span>${all.songs.length} songs</span></div>
        <div class="hero-actions">
          <button class="btn primary lg" data-action="play-all" data-queue="${qAll}" ${raw(all.songs.length ? "" : "disabled")}>${icon("play")}Play all</button>
          <a class="btn lg" href="/highlights/trending?lang=${l.code}" data-link>${icon("spark")}${l.name} highlights</a>
        </div></div></div></section>
    ${all.songs.length ? "" : emptyState("globe", `No ${l.name} songs yet`, "We're adding licensed music in this language. Check back soon.")}
    ${d.sections.filter((s) => s.key !== "all" && s.songs.length).map((s) => rail(s.title, s.songs, { hlFeed: s.category && ["sad", "love", "devotional", "dj"].includes(s.category) ? s.category : "" }))}
    ${d.artists.length ? html`<section class="section"><div class="section-head"><h2>Popular artists</h2></div><div class="rail">${d.artists.map((a) => html`<a class="card round" href="/artist/${a.slug}" data-link><div class="card-art"><img src="${a.image_url}" alt="" loading="lazy"></div><div class="card-title">${a.name}</div><div class="card-sub">Artist</div></a>`)}</div></section>` : ""}
    ${d.albums.length ? html`<section class="section"><div class="section-head"><h2>Albums</h2></div><div class="rail">${d.albums.map((a) => html`<a class="card" href="/album/${a.slug}" data-link><div class="card-art"><img src="${a.cover_url}" alt="" loading="lazy"></div><div class="card-title">${a.title}</div><div class="card-sub">${a.artist_name} · ${a.release_year}</div></a>`)}</div></section>` : ""}
    ${all.songs.length ? html`<section class="section"><div class="section-head"><h2>Complete songs</h2></div>${songList(all.songs, { label: l.name })}</section>` : ""}`);
}

export async function categoryView(view, slug, query, alive) {
  render(view, skeletonRows());
  const d = await get(`/api/categories/${slug}`);
  if (!alive()) return;
  const c = d.category, q = registerQueue(d.songs, c.name);
  render(view, html`
    <section class="hero cat"><div class="hero-bg" style="background:linear-gradient(135deg,${c.color},#14112a)"></div>
      <div class="hero-inner"><div><div class="hero-kicker">Mood & category</div><h1>${c.name}</h1><div class="hero-meta"><span>${d.songs.length} songs from every language</span></div>
      <div class="hero-actions"><button class="btn primary lg" data-action="play-all" data-queue="${q}" ${raw(d.songs.length ? "" : "disabled")}>${icon("play")}Play all</button>
      <button class="btn lg" data-action="play-all" data-queue="${q}" data-shuffle="1" ${raw(d.songs.length ? "" : "disabled")}>${icon("shuffle")}Shuffle</button>
      ${c.has_highlight_feed ? html`<a class="btn lg" href="/highlights/${c.slug}" data-link>${icon("spark")}Highlights</a>` : ""}</div></div></div></section>
    ${songList(d.songs, { label: c.name, empty: `No ${c.name.toLowerCase()} yet` })}`);
}

// ------------------------------------------------------------------ Full songs library
export async function fullSongsView(view, query, alive) {
  const [langs, cats] = await Promise.all([get("/api/languages"), get("/api/categories")]);
  if (!alive()) return;
  const state = { q: query.q || "", lang: query.lang || "", category: query.category || "", genre: query.genre || "", sort: query.sort || "popular", year_from: query.year_from || "", year_to: query.year_to || "", has_lyrics: query.has_lyrics || "", access: query.access || "", page: +(query.page || 1) };
  render(view, html`<h1>Full Songs</h1><p class="muted">Every complete, licensed song in the catalogue. Filter, sort and play.</p>
    <form class="toolbar" id="filters" role="search" aria-label="Filter songs">
      <input class="input" name="q" type="search" placeholder="Filter by title, artist or album" value="${state.q}" aria-label="Filter text">
      <select class="input" name="lang" aria-label="Language"><option value="">All languages</option>${langs.languages.filter((l) => l.song_count).map((l) => html`<option value="${l.code}" ${raw(l.code === state.lang ? "selected" : "")}>${l.name}</option>`)}</select>
      <select class="input" name="category" aria-label="Category"><option value="">All moods</option>${cats.categories.map((c) => html`<option value="${c.slug}" ${raw(c.slug === state.category ? "selected" : "")}>${c.name}</option>`)}</select>
      <select class="input" name="genre" aria-label="Genre" id="genre-sel"><option value="">All genres</option></select>
      <input class="input" name="year_from" type="number" min="1900" max="2100" placeholder="From year" value="${state.year_from}" style="min-width:110px;flex:0 0 120px" aria-label="From year">
      <input class="input" name="year_to" type="number" min="1900" max="2100" placeholder="To year" value="${state.year_to}" style="min-width:110px;flex:0 0 120px" aria-label="To year">
      <select class="input" name="sort" aria-label="Sort by">${[["popular", "Most popular"], ["trending", "Trending"], ["newest", "Newest"], ["oldest", "Oldest"], ["title", "Title A–Z"], ["duration", "Shortest"]].map(([v, l]) => html`<option value="${v}" ${raw(v === state.sort ? "selected" : "")}>${l}</option>`)}</select>
      <label class="check"><input type="checkbox" name="has_lyrics" value="1" ${raw(state.has_lyrics ? "checked" : "")}>Has lyrics</label>
      <label class="check"><input type="checkbox" name="access" value="free" ${raw(state.access ? "checked" : "")}>Free to stream</label>
    </form>
    <div id="results">${skeletonRows()}</div>`);
  const form = $("#filters"), out = $("#results");
  let genresFilled = false;
  async function load() {
    const p = new URLSearchParams(Object.entries(state).filter(([, v]) => v !== "" && v !== null && !(v === 1 && false)));
    history.replaceState({}, "", "/songs?" + p.toString());
    try {
      const d = await get("/api/songs?limit=30&" + p.toString());
      if (!alive()) return;
      if (!genresFilled) { genresFilled = true; $("#genre-sel").insertAdjacentHTML("beforeend", d.facets.genres.map((g) => html`<option ${raw(g === state.genre ? "selected" : "")}>${g}</option>`.s).join("")); }
      render(out, html`<p class="dim small">${d.total} songs</p>${songList(d.songs, { label: "Full songs", empty: "No songs match these filters", emptyText: "Try removing a filter." })}
        ${d.pages > 1 ? html`<div class="toolbar" style="justify-content:center;margin-top:18px"><button class="btn sm" data-page="${d.page - 1}" ${raw(d.page <= 1 ? "disabled" : "")}>Previous</button><span class="dim small">Page ${d.page} of ${d.pages}</span><button class="btn sm" data-page="${d.page + 1}" ${raw(d.page >= d.pages ? "disabled" : "")}>Next</button></div>` : ""}`);
    } catch (e) { render(out, errorState(e)); }
  }
  const sync = () => { const f = new FormData(form); Object.keys(state).forEach((k) => { if (k !== "page") state[k] = f.get(k) || ""; }); state.page = 1; load(); };
  form.addEventListener("input", debounce(sync, 250));
  form.addEventListener("submit", (e) => { e.preventDefault(); sync(); });
  out.addEventListener("click", (e) => { const b = e.target.closest("[data-page]"); if (b) { state.page = +b.dataset.page; load(); view.scrollTop = 0; } });
  load();
}

// ------------------------------------------------------------------ Song / artist / album
export async function songView(view, slug, query, alive) {
  render(view, html`<div class="skel skel-hero"></div>${skeletonRows(3)}`);
  const { song: s } = await get(`/api/songs/${slug}`);
  if (!alive()) return;
  songs.set(s.id, s);
  const lyr = s.has_lyrics ? await get(`/api/songs/${s.id}/lyrics`).catch(() => null) : null;
  if (!alive()) return;
  const hlLines = lyr?.available && s.highlight ? lyr.lines.filter((l) => l.t !== null && l.t >= s.highlight.start - 0.5 && l.t < s.highlight.end) : [];
  render(view, html`
    <section class="hero"><div class="hero-bg" style="background-image:url('${s.cover_url}')"></div>
      <div class="hero-inner"><img class="hero-art" src="${s.cover_url}" alt="Cover of ${s.title}" width="210" height="210">
        <div><div class="hero-kicker">Song${s.premium_only ? " · Premium" : ""}</div><h1>${s.title}</h1>
          <div class="hero-meta"><a href="/artist/${s.artist.slug}" data-link><strong>${s.artist.name}</strong></a>${s.album ? html`<a href="/album/${s.album.slug}" data-link>${s.album.title}</a>` : ""}<span>${s.year}</span><span>${fmtTime(s.duration)}</span><span>${fmtNum(s.plays)} plays</span></div>
          <div class="hero-actions">
            <button class="btn primary lg" data-action="play" data-id="${s.id}">${icon("play")}${s.premium_only && !session.premium ? "Play (Premium)" : "Play full song"}</button>
            ${s.highlight ? html`<button class="btn lg" data-action="play-highlight" data-id="${s.id}">${icon("spark")}${s.highlight.source === "provider" ? "30s preview" : `Highlight ${fmtTime(s.highlight.start)}–${fmtTime(s.highlight.end)}`}</button>` : ""}
            <button class="icon-btn ${s.is_favourite ? "fav-on" : ""}" data-action="fav" data-id="${s.id}" aria-label="Favourite" aria-pressed="${s.is_favourite}">${icon(s.is_favourite ? "heart-fill" : "heart")}</button>
            <button class="icon-btn" data-action="add-to-playlist" data-id="${s.id}" aria-label="Add to playlist">${icon("plus")}</button>
            <button class="icon-btn" data-action="share-song" data-id="${s.id}" aria-label="Share">${icon("share")}</button>
            <button class="icon-btn" data-action="song-menu" data-id="${s.id}" aria-label="More">${icon("more")}</button>
          </div>
          ${s.is_external ? html`<div class="hero-actions" style="margin-top:10px">
            ${s.source === "youtube" ? html`<a class="btn sm" href="${s.external_url}" target="_blank" rel="noopener">${icon("external")}Watch on YouTube</a>`
              : html`${s.external_url ? html`<a class="btn sm" href="${s.external_url}" target="_blank" rel="noopener">${icon("external")}Open in Apple Music</a>` : ""}
            <a class="btn sm" href="${s.youtube_url}" target="_blank" rel="noopener">${icon("external")}Find on YouTube</a>`}</div>` : ""}
          </div></div></section>
    <div class="split">
      <div class="panel"><h3>Details</h3><dl class="kv">
        <dt>Language</dt><dd><a href="/language/${s.language.code}" data-link>${s.language.name}</a> <span class="dim" lang="${s.language.code}">${s.language.native}</span></dd>
        <dt>Genre</dt><dd>${s.genre || "—"}</dd>
        <dt>Categories</dt><dd class="chips">${s.categories.map((c) => html`<a class="tag" href="/category/${c.slug}" data-link>${c.name}</a>`)}</dd>
        <dt>Released</dt><dd>${s.year || "—"}</dd><dt>Duration</dt><dd>${fmtTime(s.duration)}</dd>
        <dt>Highlight</dt><dd>${!s.highlight ? "Not set" : s.highlight.source === "provider" ? "Official 30-second preview chosen by Apple Music" : s.source === "youtube" && s.highlight.source === "auto" ? `${fmtTime(s.highlight.start)} – ${fmtTime(s.highlight.end)} (estimated; an admin can fine-tune it)` : `${fmtTime(s.highlight.start)} – ${fmtTime(s.highlight.end)} (${Math.round(s.highlight.end - s.highlight.start)}s, ${s.highlight.source === "auto" ? "auto-detected" : "hand-picked"})`}</dd>
        <dt>${s.is_external ? "Source" : "Licence"}</dt><dd class="small">${s.source === "youtube" ? "Official upload on YouTube · plays in full in the YouTube player" : s.is_external ? "Apple Music catalogue · official 30-second preview · full song plays via YouTube when connected" : s.license ? `${s.license.licensor} · ${s.license.license_type.toUpperCase()}${s.license.allow_offline ? " · offline allowed" : ""}` : "—"}</dd>
      </dl></div>
      <div class="panel"><h3>The highlight</h3>
        ${hlLines.length ? html`<div class="lyrics-panel" style="margin-top:8px">${hlLines.map((l) => html`<p class="ly-line on" style="font-size:20px;margin-bottom:10px" lang="${s.language.code}" dir="auto">${l.text}</p>`)}</div>`
          : html`<p class="muted">${s.source === "youtube" ? "This full song plays in the YouTube player. Its highlight is an estimated 25-second window. Lyrics for catalogue songs need a lyrics licence, so they aren't shown." : s.is_external ? "This song's highlight is its official 30-second preview from Apple Music. Lyrics for catalogue songs need a lyrics licence, so they aren't shown." : s.highlight ? (s.has_lyrics ? "This highlight is instrumental." : "Lyrics aren't licensed for this song, so we show the music only.") : "No highlight has been set yet."}</p>`}
        ${lyr?.available ? html`<button class="btn sm" data-action="play-with-lyrics" data-id="${s.id}" style="margin-top:10px">${icon("lyrics")}Play with full lyrics</button>` : ""}
      </div>
    </div>
    ${s.more_by_artist?.length ? rail(`More by ${s.artist.name}`, s.more_by_artist) : ""}
    ${s.similar?.length ? rail("You might also like", s.similar) : ""}`);
}

export async function artistView(view, slug, query, alive) {
  render(view, html`<div class="skel skel-hero"></div>${skeletonRows()}`);
  const d = await get(`/api/artists/${slug}`);
  if (!alive()) return;
  const a = d.artist, q = registerQueue(d.songs, a.name);
  render(view, html`<section class="hero"><div class="hero-bg" style="background-image:url('${a.image_url}')"></div>
      <div class="hero-inner"><img class="hero-art" style="border-radius:50%" src="${a.image_url}" alt="" width="210" height="210">
      <div><div class="hero-kicker">${a.is_verified ? "Verified artist" : "Artist"}${a.is_independent ? " · Independent" : ""}</div><h1>${a.name}</h1>
      <div class="hero-meta"><span>${a.country || ""}</span><span>${fmtNum(a.monthly_plays)} total plays</span></div>
      ${a.bio ? html`<p style="max-width:560px;opacity:.85">${a.bio}</p>` : ""}
      <div class="hero-actions"><button class="btn primary lg" data-action="play-all" data-queue="${q}" ${raw(d.songs.length ? "" : "disabled")}>${icon("play")}Play</button><button class="btn lg" data-action="play-all" data-queue="${q}" data-shuffle="1">${icon("shuffle")}Shuffle</button></div></div></div></section>
    <section class="section"><div class="section-head"><h2>Songs</h2></div>${songList(d.songs, { label: a.name })}</section>
    ${d.albums.length ? html`<section class="section"><div class="section-head"><h2>Albums</h2></div><div class="rail">${d.albums.map((al) => html`<a class="card" href="/album/${al.slug}" data-link><div class="card-art"><img src="${al.cover_url}" alt="" loading="lazy"></div><div class="card-title">${al.title}</div><div class="card-sub">${al.release_year || ""}</div></a>`)}</div></section>` : ""}`);
}

export async function albumView(view, slug, query, alive) {
  render(view, html`<div class="skel skel-hero"></div>${skeletonRows()}`);
  const d = await get(`/api/albums/${slug}`);
  if (!alive()) return;
  const al = d.album, q = registerQueue(d.songs, al.title);
  const total = d.songs.reduce((t, s) => t + s.duration, 0);
  render(view, html`<section class="hero"><div class="hero-bg" style="background-image:url('${al.cover_url}')"></div>
      <div class="hero-inner"><img class="hero-art" src="${al.cover_url}" alt="" width="210" height="210">
      <div><div class="hero-kicker">Album</div><h1>${al.title}</h1>
      <div class="hero-meta"><a href="/artist/${al.artist_slug}" data-link><strong>${al.artist_name}</strong></a><span>${al.release_year || ""}</span><span>${d.songs.length} songs, ${Math.round(total / 60)} min</span>${al.label ? html`<span>${al.label}</span>` : ""}</div>
      <div class="hero-actions"><button class="btn primary lg" data-action="play-all" data-queue="${q}" ${raw(d.songs.length ? "" : "disabled")}>${icon("play")}Play album</button></div></div></div></section>
    ${songList(d.songs, { label: al.title })}`);
}

// ------------------------------------------------------------------ Search
// Instant search box: results appear while typing (250 ms after the last key), with play / play all /
// shuffle / add all to queue. Used on the Search page and at the top of Your Library.
export function instantSearch(box, { initial = "", placeholder = "Search songs, artists, languages, moods…", syncUrl = false,
  autofocus = false, onQuery = null, alive = () => true } = {}) {
  render(box, html`<div class="isearch">
    <div class="searchbox isearch-box" role="search">${icon("search")}
      <input type="search" value="${initial}" placeholder="${placeholder}" aria-label="Search songs" aria-controls="isearch-results" autocomplete="off" enterkeyhint="search" maxlength="100" ${raw(autofocus ? "autofocus" : "")}>
      <button type="button" class="icon-btn sm isearch-clear" aria-label="Clear search" ${raw(initial ? "" : "hidden")}>${icon("x")}</button>
    </div>
    <div class="isearch-results" id="isearch-results" aria-live="polite"></div></div>`);
  const input = $("input", box), clear = $(".isearch-clear", box), out = $(".isearch-results", box);
  let seq = 0, last = null;

  const show = (q, d) => {
    if (!q) { render(out, ""); return; }
    if (!d.songs.length) { render(out, emptyState("search", `No songs match “${q}”`, "Check the spelling, or try an artist, a language or a mood.")); return; }
    const qid = registerQueue(d.songs, `Search: ${q}`);
    render(out, html`<div class="isearch-head"><span class="dim small">${d.songs.length === 50 ? "Top 50 songs" : `${d.songs.length} song${d.songs.length === 1 ? "" : "s"}`} for “${q}”</span>
        <div class="isearch-actions"><button class="btn primary sm" data-action="play-all" data-queue="${qid}">${icon("play")}Play all</button>
        <button class="btn sm" data-action="play-all" data-queue="${qid}" data-shuffle="1">${icon("shuffle")}Shuffle</button>
        <button class="btn sm" data-action="queue-all" data-queue="${qid}">${icon("list")}Add all to queue</button></div></div>
      ${songList(d.songs, { label: `Search: ${q}` })}`);
  };
  const run = async (raw) => {
    const q = raw.trim().replace(/\s+/g, " ");
    clear.hidden = !raw;
    if (syncUrl) history.replaceState(history.state, "", q ? `/search?q=${encodeURIComponent(q)}` : "/search");
    onQuery?.(q);
    if (q === last) return;
    last = q;
    const mine = ++seq;
    if (!q) { show("", null); return; }
    out.classList.add("loading");
    try {
      const d = await get(`/api/songs/search?q=${encodeURIComponent(q)}`, { quiet: true });
      if (mine !== seq || !alive()) return; // a newer search has started: drop this answer
      show(q, d);
    } catch (e) {
      if (mine === seq && alive()) render(out, errorState(e, false));
    } finally {
      if (mine === seq) out.classList.remove("loading");
    }
  };
  const debounced = debounce(run, 250);
  input.addEventListener("input", () => { clear.hidden = !input.value; debounced(input.value); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); run(input.value); }
    else if (e.key === "Escape" && input.value) { e.preventDefault(); input.value = ""; run(""); }
  });
  clear.addEventListener("click", () => { input.value = ""; run(""); input.focus(); });
  if (initial) run(initial);
  if (autofocus) setTimeout(() => input.focus(), 0);
  return { input, run };
}

export async function searchView(view, query, alive) {
  const q = query.q || "";
  render(view, html`<div id="isearch-slot"></div><div id="sres-more"></div><div id="sres-browse"></div>`);
  const more = $("#sres-more"), browse = $("#sres-browse");
  // artists, albums, lyrics and language/mood chips: the full search, a little after typing settles
  let moreSeq = 0;
  const loadMore = debounce(async (term) => {
    const mine = ++moreSeq;
    if (!term) { render(more, ""); return; }
    try {
      const d = await get(`/api/search?q=${encodeURIComponent(term)}`, { quiet: true });
      if (mine !== moreSeq || !alive()) return;
      render(more, html`
        ${d.languages.length || d.categories.length ? html`<div class="chips" style="margin:18px 0 6px">${d.languages.map((l) => html`<a class="chip" href="/language/${l.code}" data-link>${icon("globe")}${l.name} · ${l.native_name}</a>`)}${d.categories.map((c) => html`<a class="chip" href="/category/${c.slug}" data-link>${c.name}</a>`)}</div>` : ""}
        ${d.lyrics_matches.length ? html`<section class="section"><div class="section-head"><div><h2>Lyrics matches</h2><p>From licensed lyrics</p></div></div>${d.lyrics_matches.map((m) => html`<div class="q-item" style="padding:10px"><img src="${m.song.cover_url}" alt=""><div class="meta"><a class="t" href="/song/${m.song.slug}" data-link>${m.song.title}</a><div class="s" dir="auto">“${m.line}” · ${m.song.artist.name}</div></div><button class="icon-btn" data-action="play" data-id="${m.song.id}" aria-label="Play">${icon("play")}</button></div>`)}</section>` : ""}
        ${d.artists.length ? html`<section class="section"><div class="section-head"><h2>Artists</h2></div><div class="rail">${d.artists.map((a) => html`<a class="card round" href="/artist/${a.slug}" data-link><div class="card-art"><img src="${a.image_url}" alt=""></div><div class="card-title">${a.name}</div><div class="card-sub">${a.country || "Artist"}</div></a>`)}</div></section>` : ""}
        ${d.albums.length ? html`<section class="section"><div class="section-head"><h2>Albums</h2></div><div class="rail">${d.albums.map((a) => html`<a class="card" href="/album/${a.slug}" data-link><div class="card-art"><img src="${a.cover_url}" alt=""></div><div class="card-title">${a.title}</div><div class="card-sub">${a.artist_name}</div></a>`)}</div></section>` : ""}`);
    } catch { if (mine === moreSeq) render(more, ""); }
  }, 700);
  let browseShown = false;
  const showBrowse = async (term) => {
    browse.hidden = !!term;
    if (term || browseShown) return;
    browseShown = true;
    const cats = await get("/api/categories");
    if (alive()) render(browse, html`<h2 style="margin:22px 0 14px">Browse moods</h2><div class="tiles">${cats.categories.map(catTile)}</div>`);
  };
  instantSearch($("#isearch-slot"), { initial: q, syncUrl: true, autofocus: true, alive, placeholder: "What do you want to listen to?",
    onQuery: (term) => { loadMore(term); showBrowse(term); } });
  showBrowse(q);
}

// ------------------------------------------------------------------ Library & playlists
function needLogin(view, what) {
  render(view, emptyState("lock", `Log in to see ${what}`, "Your favourites, playlists and history sync across all your devices.", { href: `/login?next=${encodeURIComponent(location.pathname)}`, label: "Log in" }));
}

export async function libraryView(view, tab, query, alive) {
  if (!session.user) return needLogin(view, "your library");
  tab = tab || "playlists";
  const tabs = [["playlists", "Playlists"], ["favourites", "Favourites"], ["recent", "Recently played"], ["history", "Listening history"]];
  render(view, html`<div class="section-head"><h1 style="margin:0">Your Library</h1><button class="btn primary" id="new-pl">${icon("plus")}New playlist</button></div>
    <div id="lib-search" class="lib-search"></div>
    <div id="lib-body"><div class="tabs" role="tablist">${tabs.map(([k, l]) => html`<a class="tab ${k === tab ? "active" : ""}" role="tab" aria-selected="${k === tab}" href="/library/${k}" data-link>${l}</a>`)}</div><div id="lib">${skeletonRows()}</div></div>`);
  $("#new-pl").addEventListener("click", () => playlistDialog());
  // search the whole catalogue from the library; the tabs come back when the box is cleared
  instantSearch($("#lib-search"), { placeholder: "Search all songs to play or add to the queue…", alive,
    onQuery: (term) => { $("#lib-body").hidden = !!term; } });
  const out = $("#lib");
  if (tab === "playlists") {
    const [mine, auto] = await Promise.all([get("/api/me/playlists"), get("/api/me/auto-playlists")]);
    if (!alive()) return;
    const card = (p, href, sub) => html`<a class="card" href="${href}" data-link><div class="card-art">${mosaic(p.cover_urls)}</div><div class="card-title">${p.title}</div><div class="card-sub">${sub}</div></a>`;
    render(out, html`
      <section class="section"><div class="section-head"><div><h2>Your playlists</h2></div></div>
        ${mine.playlists.length ? html`<div class="grid">${mine.playlists.map((p) => card(p, `/playlist/${p.id}`, `${p.song_count} songs · ${p.is_public ? "Public" : "Private"}`))}</div>` : emptyState("queue", "Create your first playlist", "Collect songs for every mood, then share them.")}</section>
      <section class="section"><div class="section-head"><div><h2>Made automatically</h2><p>Updated from your favourites and what you listen to</p></div></div>
        <div class="grid">${auto.playlists.map((p) => card(p, `/playlist/${p.id}`, `${p.count} songs · auto`))}</div></section>
      ${mine.followed.length ? html`<section class="section"><div class="section-head"><h2>Following</h2></div><div class="grid">${mine.followed.map((p) => card(p, `/playlist/${p.id}`, `by ${p.owner_name}`))}</div></section>` : ""}`);
  } else if (tab === "favourites") {
    const d = await get("/api/me/favourites");
    if (!alive()) return;
    const q = registerQueue(d.songs, "Favourites");
    render(out, html`${d.songs.length ? html`<div class="hero-actions" style="margin:0 0 16px"><button class="btn primary" data-action="play-all" data-queue="${q}">${icon("play")}Play favourites</button><button class="btn" data-action="play-all" data-queue="${q}" data-shuffle="1">${icon("shuffle")}Shuffle</button></div>` : ""}
      ${songList(d.songs, { label: "Favourites", empty: "No favourites yet", emptyText: "Tap the heart on any song or highlight to save it here.", emptyAction: { href: "/highlights", label: "Discover highlights" } })}`);
    const onFav = (e) => { if (!e.detail.value) $(`[data-song-row="${e.detail.id}"]`, out)?.remove(); };
    document.addEventListener("rf:fav", onFav);
    return () => document.removeEventListener("rf:fav", onFav);
  } else if (tab === "recent") {
    const d = await get("/api/me/recently-played");
    if (!alive()) return;
    render(out, songList(d.songs, { label: "Recently played", empty: "Nothing played yet", emptyText: "Songs you play will show up here.", emptyAction: { href: "/", label: "Start listening" } }));
  } else {
    const d = await get("/api/me/history");
    if (!alive()) return;
    render(out, d.items.length ? html`<div class="toolbar"><span class="dim small">${d.items.length} songs · ${Math.round(d.items.reduce((t, i) => t + i.seconds, 0) / 60)} minutes listened</span><button class="btn sm danger" id="clear-h" style="margin-left:auto">Clear history</button></div>
      <div class="table-wrap"><table class="tbl"><thead><tr><th></th><th>Song</th><th>Plays</th><th>Listened</th><th>Last played</th></tr></thead><tbody>
      ${d.items.map((it) => html`<tr><td><img src="${it.song.cover_url}" alt=""></td><td><a href="/song/${it.song.slug}" data-link><strong>${it.song.title}</strong></a><div class="dim small">${it.song.artist.name}</div></td><td>${it.plays}</td><td>${fmtTime(it.seconds)}</td><td>${fmtDate(it.last_played)}</td></tr>`)}
      </tbody></table></div>` : emptyState("list", "No listening history", "Your history powers recommendations and auto playlists."));
    $("#clear-h")?.addEventListener("click", async () => {
      if (!(await confirmDialog("Clear listening history?", "This also resets recommendations based on your history.", { ok: "Clear", danger: true }))) return;
      try { await del("/api/me/history"); toast("History cleared"); libraryView(view, "history", query, alive); } catch (e) { toastError(e); }
    });
  }
}

function playlistDialog(p = null, onSaved = null) {
  const m = modal(html`<h3>${p ? "Edit playlist" : "New playlist"}</h3><form id="pl-form">
    <div class="field"><label for="plt">Name</label><input class="input" id="plt" name="title" maxlength="80" required value="${p?.title || ""}"></div>
    <div class="field"><label for="pld">Description</label><input class="input" id="pld" name="description" maxlength="300" value="${p?.description || ""}"></div>
    <label class="check"><input type="checkbox" name="is_public" ${raw(p?.is_public ? "checked" : "")}>Public — anyone with the link can listen and follow</label>
    <div class="modal-actions"><button type="button" class="btn ghost" data-cancel>Cancel</button><button class="btn primary">${p ? "Save" : "Create"}</button></div></form>`);
  $("[data-cancel]", m).onclick = closeModal;
  $("#pl-form", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, body = { title: f.title.value, description: f.description.value, is_public: f.is_public.checked };
    try {
      const r = p ? await patch(`/api/playlists/${p.id}`, body) : await post("/api/playlists", body);
      closeModal(); document.dispatchEvent(new Event("rf:playlists"));
      if (onSaved) onSaved(r); else nav(`/playlist/${r.playlist.id}`);
    } catch (err) { toastError(err); }
  });
}

export async function playlistView(view, id, query, alive) {
  if (id.startsWith("auto-")) {
    if (!session.user) return needLogin(view, "your auto playlists");
    const d = await get(`/api/me/auto-playlists/${id.slice(5)}`);
    if (!alive()) return;
    return renderPlaylist(view, d, true);
  }
  const d = await get(`/api/playlists/${id}`);
  if (!alive()) return;
  renderPlaylist(view, d);
}
export async function sharedPlaylistView(view, token, query, alive) {
  const d = await get(`/api/playlists/shared/${token}`);
  if (!alive()) return;
  renderPlaylist(view, d);
}

function renderPlaylist(view, d, auto = false) {
  const p = d.playlist, list = d.songs;
  const q = registerQueue(list, p.title);
  const owner = p.is_owner && !auto;
  const total = list.reduce((t, s) => t + s.duration, 0);
  render(view, html`<section class="hero"><div class="hero-bg" style="background-image:url('${list[0]?.cover_url || ""}');background-color:#3b1d63"></div>
    <div class="hero-inner"><div class="hero-art" style="overflow:hidden">${mosaic(list.map((s) => s.cover_url))}</div>
      <div><div class="hero-kicker">${auto ? "Auto playlist" : p.is_public ? "Public playlist" : "Private playlist"}</div><h1>${p.title}</h1>
      ${p.description ? html`<p style="opacity:.85;max-width:560px">${p.description}</p>` : ""}
      <div class="hero-meta">${p.owner_name ? html`<span>by <strong>${p.owner_name}</strong></span>` : ""}<span>${list.length} songs, ${Math.round(total / 60)} min</span>${p.followers ? html`<span>${p.followers} followers</span>` : ""}${p.unavailable_count ? html`<span>${p.unavailable_count} unavailable</span>` : ""}</div>
      <div class="hero-actions">
        <button class="btn primary lg" data-action="play-all" data-queue="${q}" ${raw(list.length ? "" : "disabled")}>${icon("play")}Play</button>
        <button class="btn lg" data-action="play-all" data-queue="${q}" data-shuffle="1" ${raw(list.length ? "" : "disabled")}>${icon("shuffle")}Shuffle</button>
        ${owner ? html`<button class="icon-btn" id="pl-edit" aria-label="Edit playlist">${icon("edit")}</button><button class="icon-btn" id="pl-share" aria-label="Share playlist">${icon("share")}</button><button class="icon-btn" id="pl-del" aria-label="Delete playlist">${icon("trash")}</button>` : ""}
        ${!owner && !auto && session.user && p.is_public ? html`<button class="btn lg" id="pl-follow">${p.is_following ? "Following" : "Follow"}</button>` : ""}
      </div></div></div></section>
    <div id="pl-list">${songList(list, { label: p.title, removable: owner, draggable: owner, empty: auto ? "Nothing here yet" : "This playlist is empty", emptyText: auto ? "Favourite or listen to songs in this mood and they'll appear here automatically." : "Find songs and use “Add to playlist”.", emptyAction: { href: "/songs", label: "Browse songs" } })}</div>`);
  if (owner) {
    $("#pl-edit").onclick = () => playlistDialog(p, (r) => renderPlaylist(view, r));
    $("#pl-share").onclick = async () => {
      if (!p.is_public && !(await confirmDialog("Share this playlist?", "Anyone with the link will be able to listen. The playlist stays out of public listings unless you make it public."))) return;
      import("./app.js").then((m) => m.share(p.share_url, p.title));
    };
    $("#pl-del").onclick = async () => {
      if (!(await confirmDialog("Delete playlist?", `“${p.title}” will be deleted for good.`, { ok: "Delete", danger: true }))) return;
      try { await del(`/api/playlists/${p.id}`); toast("Playlist deleted"); document.dispatchEvent(new Event("rf:playlists")); nav("/library"); } catch (e) { toastError(e); }
    };
    const box = $("#pl-list");
    const order = () => $$("[data-song-row]", box).map((r) => r.dataset.songRow);
    const save = async () => { try { await put(`/api/playlists/${p.id}/order`, { song_ids: order() }); } catch (e) { toastError(e); } };
    box.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-action=pl-remove], [data-action=move-up], [data-action=move-down]");
      if (!b) return;
      e.stopPropagation();
      const row = b.closest("[data-song-row]");
      if (b.dataset.action === "pl-remove") {
        try { await del(`/api/playlists/${p.id}/songs/${b.dataset.id}`); row.remove(); toast("Removed from playlist"); } catch (err) { toastError(err); }
      } else {
        const sib = b.dataset.action === "move-up" ? row.previousElementSibling : row.nextElementSibling;
        if (!sib || !sib.dataset.songRow) return;
        b.dataset.action === "move-up" ? sib.before(row) : sib.after(row);
        save();
      }
    }, true);
    let dragging = null;
    box.addEventListener("dragstart", (e) => { dragging = e.target.closest("[data-song-row]"); dragging?.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    box.addEventListener("dragover", (e) => { const r = e.target.closest("[data-song-row]"); if (!dragging || !r || r === dragging) return; e.preventDefault(); $$(".drop-target", box).forEach((x) => x.classList.remove("drop-target")); r.classList.add("drop-target"); });
    box.addEventListener("drop", (e) => { const r = e.target.closest("[data-song-row]"); if (!dragging || !r) return; e.preventDefault(); r.before(dragging); save(); });
    box.addEventListener("dragend", () => { dragging?.classList.remove("dragging"); $$(".drop-target", box).forEach((x) => x.classList.remove("drop-target")); dragging = null; });
  }
  $("#pl-follow")?.addEventListener("click", async (e) => {
    try {
      const r = p.is_following ? await del(`/api/playlists/${p.id}/follow`) : await post(`/api/playlists/${p.id}/follow`, {});
      p.is_following = r.is_following; e.target.textContent = r.is_following ? "Following" : "Follow";
      toast(r.is_following ? "Added to your library" : "Unfollowed"); document.dispatchEvent(new Event("rf:playlists"));
    } catch (err) { toastError(err); }
  });
}

// ------------------------------------------------------------------ Auth
function googleButton(slotId) {
  const cid = session.config?.google_client_id;
  if (!cid) return;
  const s = document.createElement("script");
  s.src = "https://accounts.google.com/gsi/client"; s.async = true;
  s.onload = () => {
    window.google.accounts.id.initialize({ client_id: cid, callback: async (r) => {
      try { const d = await post("/api/auth/google", { credential: r.credential }); setAuth(d.token, d.user); toast(`Welcome, ${d.user.display_name}`); nav(new URLSearchParams(location.search).get("next") || "/"); }
      catch (e) { toastError(e); }
    } });
    const el = document.getElementById(slotId);
    if (el) window.google.accounts.id.renderButton(el, { theme: "filled_black", size: "large", shape: "pill", width: 320 });
  };
  document.head.append(s);
}
const safeNext = () => { const n = new URLSearchParams(location.search).get("next") || "/"; return n.startsWith("/") && !n.startsWith("//") ? n : "/"; };

export function loginView(view) {
  if (session.user) return nav("/profile");
  const google = !!session.config?.google_client_id;
  render(view, html`<div class="auth"><div class="brand-lockup"><img src="/img/logo.svg" alt="PENDEL logo" width="64" height="64"><span class="brand-name">PENDEL</span><span class="brand-tag">Feel Every Beat</span></div><h1>Welcome back</h1><p class="lead">Log in to PENDEL and pick up where the beat left off.</p>
    ${google ? html`<div id="g-btn" style="display:flex;justify-content:center"></div><div class="divider">or</div>` : ""}
    <form id="login-form" novalidate><div id="err"></div>
      <div class="field"><label for="em">Email</label><input class="input" id="em" name="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="pw">Password</label><input class="input" id="pw" name="password" type="password" autocomplete="current-password" required></div>
      <button class="btn primary lg" style="width:100%">Log in</button></form>
    <p class="muted small" style="text-align:center;margin-top:18px">New to PENDEL? <a href="/register${location.search}" data-link style="color:var(--accent)">Create an account</a></p>
    ${google ? "" : html`<p class="dim small" style="text-align:center">Google sign-in appears here once a Google Client ID is configured.</p>`}
    <div class="demo-creds">Demo listener: demo@pendel.local / Demo@12345${["localhost", "127.0.0.1"].includes(location.hostname) ? html`<br>Demo admin: admin@pendel.local / Admin@12345` : ""}</div></div>`);
  googleButton("g-btn");
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = e.target, btn = $("button", f);
    btn.disabled = true;
    try { const d = await post("/api/auth/login", { email: f.email.value, password: f.password.value }); setAuth(d.token, d.user); toast(`Welcome back, ${d.user.display_name}`); nav(safeNext()); }
    catch (err) { render($("#err"), html`<div class="form-error" role="alert">${err.message}</div>`); btn.disabled = false; }
  });
}

export async function registerView(view) {
  if (session.user) return nav("/profile");
  const { languages } = await get("/api/languages");
  const google = !!session.config?.google_client_id;
  render(view, html`<div class="auth" style="max-width:520px"><div class="brand-lockup"><img src="/img/logo.svg" alt="PENDEL logo" width="64" height="64"><span class="brand-name">PENDEL</span><span class="brand-tag">Feel Every Beat</span></div><h1>Join PENDEL</h1><p class="lead">Feel every beat, free forever. Highlights, playlists and recommendations in every language.</p>
    ${google ? html`<div id="g-btn" style="display:flex;justify-content:center"></div><div class="divider">or sign up with email</div>` : ""}
    <form id="reg-form" novalidate><div id="err"></div>
      <div class="field"><label for="dn">Name</label><input class="input" id="dn" name="display_name" autocomplete="name" maxlength="60" required></div>
      <div class="field"><label for="em">Email</label><input class="input" id="em" name="email" type="email" autocomplete="email" required></div>
      <div class="field"><label for="pw">Password</label><input class="input" id="pw" name="password" type="password" autocomplete="new-password" minlength="8" required><span class="hint">At least 8 characters with a letter and a number.</span></div>
      <div class="field"><span class="lbl">Languages you love (optional)</span><div class="chips">${languages.filter((l) => l.featured).map((l) => html`<button type="button" class="chip" data-lang="${l.code}" aria-pressed="false">${l.name}</button>`)}</div></div>
      <p class="muted small consent-line">By creating an account you confirm you are 13 or older (under 18 in India: with a parent's or guardian's consent) and agree to the <a href="/legal/terms" data-link>Terms of Service</a> and <a href="/legal/privacy" data-link>Privacy Policy</a>.</p>
      <button class="btn primary lg" style="width:100%">Create account</button></form>
    <p class="muted small" style="text-align:center;margin-top:18px">Already have an account? <a href="/login${location.search}" data-link style="color:var(--accent)">Log in</a></p></div>`);
  googleButton("g-btn");
  const form = $("#reg-form");
  form.addEventListener("click", (e) => { const b = e.target.closest("[data-lang]"); if (b) b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button.primary"); btn.disabled = true;
    try {
      const d = await post("/api/auth/register", { display_name: form.display_name.value, email: form.email.value, password: form.password.value, preferred_languages: $$("[data-lang][aria-pressed=true]", form).map((b) => b.dataset.lang) });
      setAuth(d.token, d.user); toast("Welcome to PENDEL. Feel every beat!"); nav(safeNext());
    } catch (err) { render($("#err"), html`<div class="form-error" role="alert">${err.message}</div>`); btn.disabled = false; }
  });
}

// ------------------------------------------------------------------ Profile
export async function profileView(view, query, alive) {
  if (!session.user) return needLogin(view, "your profile");
  const [{ languages }, { categories }, pays] = await Promise.all([get("/api/languages"), get("/api/categories"), get("/api/me/payments")]);
  if (!alive()) return;
  const u = session.user;
  render(view, html`<div class="hero cat" style="min-height:0"><div class="hero-bg" style="background:var(--grad)"></div><div class="hero-inner" style="align-items:center">
      <label class="avatar lg" title="Change photo" style="cursor:pointer">${u.avatar_url ? html`<img src="${u.avatar_url}" alt="" style="width:100%;height:100%;object-fit:cover">` : u.display_name[0].toUpperCase()}<input type="file" id="av" accept="image/png,image/jpeg,image/webp" class="sr-only" aria-label="Upload profile picture"></label>
      <div><div class="hero-kicker">Profile</div><h1>${u.display_name}</h1><div class="hero-meta"><span>${u.email}</span><span>${u.plan === "premium" ? "Premium" : "Free plan"}</span><span>Joined ${fmtDate(u.created_at)}</span></div></div></div></div>
    <div class="split">
      <div class="panel"><h3>Account</h3><form id="acct">
        <div class="field"><label for="nm">Display name</label><input class="input" id="nm" name="display_name" value="${u.display_name}" maxlength="60" required></div>
        <div class="field"><span class="lbl">Theme</span><div class="chips">${["dark", "light"].map((t) => html`<button type="button" class="chip ${u.theme === t ? "active" : ""}" data-theme-pick="${t}">${t === "dark" ? "Dark" : "Light"}</button>`)}</div></div>
        <button class="btn primary">Save</button></form>
        ${u.auth_provider === "email" ? html`<div class="ed-section"><h4>Change password</h4><form id="pwf"><div class="field"><input class="input" type="password" name="current_password" placeholder="Current password" autocomplete="current-password" required></div><div class="field"><input class="input" type="password" name="new_password" placeholder="New password" autocomplete="new-password" minlength="8" required></div><button class="btn">Update password</button></form></div>` : ""}
        <div class="ed-section voice-switch"><div><h4 style="margin:0">“Hey Pendel” voice control</h4><p class="muted small" id="voice-status" style="margin:4px 0 0"></p></div><button type="button" class="btn sm" id="voice-toggle" aria-pressed="false"></button></div>
        <div class="ed-section" style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="logout">${icon("logout")}Log out</button><button class="btn ghost" id="logout-all">Log out of all devices</button></div>
      </div>
      <div>
        <div class="panel"><h3>Your plan</h3>
          ${u.plan === "premium" ? html`<p><span class="badge premium">Premium</span> ${u.plan_cancel_at_period_end ? `Ends on ${fmtDate(u.plan_renews_at)}` : `Renews on ${fmtDate(u.plan_renews_at)}`}</p>${u.plan_cancel_at_period_end ? "" : html`<button class="btn sm danger" id="cancel-sub">Cancel subscription</button>`}`
            : html`<p class="muted">You're on Free: highlights, previews, ad-supported listening and ${session.config?.free_skips_per_hour} skips an hour.</p><a class="btn primary" href="/premium" data-link>${icon("crown")}Upgrade to Premium</a>`}
          ${pays.payments.length ? html`<div class="ed-section"><h4>Payments</h4>${pays.payments.map((p) => html`<div class="small" style="display:flex;justify-content:space-between;padding:4px 0"><span>${fmtDate(p.created_at)} · ${p.plan_name || ""} · ${p.provider}${p.provider === "sandbox" ? " (test)" : ""}</span><span>${fmtMoney(p.amount_minor, p.currency)} · ${p.status}</span></div>`)}</div>` : ""}
        </div>
        <div class="panel"><h3>Preferred languages</h3><p class="muted small">Shapes recommendations and the order of highlights.</p>
          <div class="chips" id="pref-lang">${languages.map((l) => html`<button class="chip" data-v="${l.code}" aria-pressed="${u.preferred_languages.includes(l.code)}">${l.name}</button>`)}</div>
          <h3 style="margin-top:20px">Favourite moods</h3>
          <div class="chips" id="pref-cat">${categories.map((c) => html`<button class="chip" data-v="${c.slug}" aria-pressed="${u.preferred_categories.includes(c.slug)}">${c.name}</button>`)}</div>
          <button class="btn primary" id="save-prefs" style="margin-top:16px">Save preferences</button></div>
        <section class="panel" aria-labelledby="your-data-h"><h3 id="your-data-h">Your data &amp; legal</h3>
          <p class="muted small">Your rights under India's DPDP Act, 2023 and the GDPR. See the <a href="/legal/privacy" data-link>Privacy Policy</a>.</p>
          <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn" id="export-data">${icon("down")}Download my data</button><button class="btn danger" id="delete-account">${icon("trash")}Delete my account</button></div>
          <nav class="legal-links" aria-label="Legal documents"><a href="/legal/terms" data-link>Terms of Service</a><a href="/legal/privacy" data-link>Privacy Policy</a><a href="/legal/copyright" data-link>Copyright / DMCA</a><a href="/legal/refunds" data-link>Refunds &amp; Cancellation</a><a href="/legal/contact" data-link>Contact &amp; Grievance Officer</a></nav>
        </section>
      </div></div>`);
  const refreshUser = (user) => { session.user = user; import("./api.js").then((m) => m.emitSession()); };
  $("#av").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (f.size > 8e6) return toast("Pick an image under 8 MB", { error: true });
    try { await upload("/api/me/avatar", f); const me = await get("/api/auth/me"); refreshUser(me.user); toast("Profile picture updated"); profileView(view, query, alive); } catch (err) { toastError(err); }
  });
  $("#acct").addEventListener("click", (e) => { const b = e.target.closest("[data-theme-pick]"); if (b) { $$("[data-theme-pick]").forEach((x) => x.classList.toggle("active", x === b)); document.documentElement.dataset.theme = b.dataset.themePick; } });
  $("#acct").addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await patch("/api/me", { display_name: e.target.display_name.value, theme: $(".chip.active[data-theme-pick]").dataset.themePick }); refreshUser(r.user); toast("Saved"); } catch (err) { toastError(err); }
  });
  $("#pwf")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    try { const r = await post("/api/auth/change-password", { current_password: e.target.current_password.value, new_password: e.target.new_password.value }); setAuth(r.token, r.user); e.target.reset(); toast("Password updated. Other devices were signed out."); } catch (err) { toastError(err); }
  });
  $$("#pref-lang, #pref-cat").forEach((box) => box.addEventListener("click", (e) => { const b = e.target.closest("[data-v]"); if (b) b.setAttribute("aria-pressed", String(b.getAttribute("aria-pressed") !== "true")); }));
  $("#save-prefs").addEventListener("click", async () => {
    try {
      const r = await patch("/api/me", { preferred_languages: $$("#pref-lang [aria-pressed=true]").map((b) => b.dataset.v), preferred_categories: $$("#pref-cat [aria-pressed=true]").map((b) => b.dataset.v) });
      refreshUser(r.user); toast("Preferences saved. Your recommendations will update.");
    } catch (err) { toastError(err); }
  });
  $("#logout").onclick = () => { player.flushHistory(); setAuth(null, null); toast("You're logged out"); nav("/"); };
  $("#logout-all").onclick = async () => {
    if (!(await confirmDialog("Log out everywhere?", "You'll be signed out on every device, including this one."))) return;
    try { await post("/api/auth/logout-all", {}); setAuth(null, null); nav("/login"); } catch (err) { toastError(err); }
  };
  const syncVoice = () => {
    const b = $("#voice-toggle"); if (!b) return;
    $("#voice-status").textContent = voiceStatusText();
    b.hidden = !voiceSupported();
    b.textContent = voiceWanted() ? "Turn off" : "Turn on";
    b.setAttribute("aria-pressed", String(voiceWanted()));
  };
  syncVoice();
  const offVoice = onVoiceStatus(() => { if (!$("#voice-toggle")) return offVoice(); syncVoice(); });
  $("#voice-toggle").onclick = () => { setVoiceEnabled(!voiceWanted()); syncVoice(); };
  $("#export-data").onclick = async (e) => {
    const b = e.currentTarget; b.disabled = true;
    try {
      const data = await get("/api/me/export");
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = Object.assign(document.createElement("a"), { href: url, download: `pendel-data-${new Date().toISOString().slice(0, 10)}.json` });
      document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast("Your data download has started");
    } catch (err) { toastError(err); } finally { b.disabled = false; }
  };
  $("#delete-account").onclick = () => {
    if (u.admin_role) return toast("Admin accounts can't be deleted here. Ask a super admin to remove your admin role first.", { error: true });
    const pw = u.auth_provider === "email";
    const m = modal(html`<h3 id="del-h">Delete your account?</h3>
      <p class="muted">This permanently erases your profile, playlists, favourites, listening history and preferences, and signs you out everywhere. ${u.plan === "premium" ? "Your Premium subscription will be cancelled without a refund for the current period. " : ""}Payment records are kept without your name or email, as tax law requires. This can't be undone.</p>
      <form id="del-form"><div class="field">${pw
        ? html`<label for="del-pw">Enter your password to confirm</label><input class="input" id="del-pw" type="password" autocomplete="current-password" required>`
        : html`<label for="del-pw">Type DELETE to confirm</label><input class="input" id="del-pw" autocomplete="off" required>`}</div>
        <div id="del-err"></div>
        <div class="modal-actions"><button type="button" class="btn ghost" data-x>Cancel</button><button class="btn danger">Delete account</button></div></form>`);
    m.setAttribute("aria-labelledby", "del-h");
    m.querySelector("[data-x]").onclick = () => closeModal();
    m.querySelector("#del-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const v = m.querySelector("#del-pw").value, btn = e.target.querySelector(".btn.danger"); btn.disabled = true;
      try {
        await del("/api/me", { body: pw ? { password: v } : { confirm: v } });
        closeModal(); setAuth(null, null); toast("Your account has been deleted"); nav("/");
      } catch (err) { render(m.querySelector("#del-err"), html`<div class="form-error" role="alert">${err.message}</div>`); btn.disabled = false; }
    });
  };
  $("#cancel-sub")?.addEventListener("click", async () => {
    if (!(await confirmDialog("Cancel Premium?", `You'll keep Premium until ${fmtDate(u.plan_renews_at)}. After that you'll move to the Free plan.`, { ok: "Cancel subscription", danger: true }))) return;
    try { const r = await post("/api/subscriptions/cancel", {}); refreshUser(r.user); toast("Subscription cancelled"); profileView(view, query, alive); } catch (err) { toastError(err); }
  });
}

// ------------------------------------------------------------------ Premium & checkout
export async function premiumView(view, query, alive) {
  const d = await get("/api/plans");
  if (!alive()) return;
  const per = { month: "/month", year: "/year", none: "" };
  render(view, html`<div id="premium-page"><div style="text-align:center;max-width:640px;margin:0 auto 30px"><h1>PENDEL Premium</h1><p style="font-weight:600;margin:-6px 0 8px;letter-spacing:.12em;text-transform:uppercase;font-size:12px" class="dim">Feel Every Beat</p><p class="muted">Full licensed songs, no ads, unlimited skips and high-quality audio where available.</p></div>
    ${!d.providers.length ? html`<div class="notice">Payments are not active on this server yet. An administrator needs to add payment-provider credentials before subscriptions can be purchased.</div>` : ""}
    <div class="plans">${d.plans.map((p) => html`<div class="plan ${p.id === "premium-yearly" ? "featured" : ""}">
      ${p.id === "premium-yearly" ? html`<span class="badge" style="position:absolute;top:-10px;right:20px">Best value</span>` : ""}
      <h3>${p.name}</h3><div class="price">${p.price_minor ? fmtMoney(p.price_minor, p.currency) : "Free"}<small>${per[p.interval]}</small></div>
      <ul>${p.features.map((f) => html`<li>${icon("check")}${f}</li>`)}</ul>
      ${p.tier === "free" ? html`<button class="btn" disabled>${session.premium ? "Included" : "Your current plan"}</button>`
        : html`<button class="btn primary" data-plan="${p.id}" ${raw(!d.providers.length ? "disabled" : "")}>${session.premium ? "Extend Premium" : "Get Premium"}</button>`}
    </div>`)}</div>
    <p class="dim small" style="text-align:center;margin-top:24px">Accepted: ${d.providers.length ? d.providers.map((p) => p.label).join(" · ") : "none configured"}. Offline downloads are available only for songs whose licence permits it.</p>
    <p class="dim small" style="text-align:center">Cancel anytime; Premium stays active until the end of the paid period. Fees are non-refundable once activated, except as described in our <a href="/legal/refunds" data-link style="color:var(--accent)">Refund &amp; Cancellation Policy</a>. By subscribing you agree to the <a href="/legal/terms" data-link style="color:var(--accent)">Terms of Service</a>.</p></div>`);
  $("#premium-page").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-plan]");
    if (!b) return;
    if (!session.user) return nav(`/login?next=/premium`);
    const prov = d.providers.length === 1 ? d.providers[0] : await pickProvider(d.providers);
    if (!prov) return;
    try {
      const co = await post("/api/subscriptions/checkout", { plan_id: b.dataset.plan, provider: prov.key });
      if (prov.key === "sandbox") sandboxCheckout(co); else razorpayCheckout(co);
    } catch (err) { toastError(err); }
  });
}
function pickProvider(list) {
  return new Promise((resolve) => {
    const m = modal(html`<h3>Choose payment method</h3>${list.map((p, i) => html`<button class="btn" style="width:100%;margin-bottom:8px" data-i="${i}">${p.label}</button>`)}`, { onClose: () => resolve(null) });
    m.addEventListener("click", (e) => { const b = e.target.closest("[data-i]"); if (b) { m.parentElement._onClose = null; closeModal(); resolve(list[+b.dataset.i]); } });
  });
}
function sandboxCheckout(co) {
  const m = modal(html`<h3>Checkout · ${co.plan.name}</h3>
    <div class="notice"><strong>Test mode.</strong> This is the sandbox payment provider. No money is charged. Configure Razorpay keys to accept real UPI, card and netbanking payments.</div>
    <p style="font-size:26px;font-weight:800;font-family:var(--font);margin:6px 0">${fmtMoney(co.order.amount, co.order.currency)}</p>
    <div class="pay-methods" role="radiogroup" aria-label="Payment method">${["upi", "card", "netbanking"].map((k, i) => html`<label><input type="radio" name="pm" value="${k}" ${raw(i === 0 ? "checked" : "")}><span>${{ upi: "UPI", card: "Card", netbanking: "Netbanking" }[k]}</span></label>`)}</div>
    <div class="modal-actions"><button class="btn ghost" data-out="failure">Simulate failure</button><button class="btn primary" data-out="success">Pay ${fmtMoney(co.order.amount, co.order.currency)}</button></div>`);
  m.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-out]");
    if (!b) return;
    b.disabled = true;
    try {
      const r = await post("/api/subscriptions/verify", { payment_id: co.payment_id, outcome: b.dataset.out, method: $("input[name=pm]:checked", m).value });
      closeModal(); session.user = r.user; (await import("./api.js")).emitSession();
      toast("Welcome to PENDEL Premium! Full songs and unlimited skips are unlocked."); nav("/profile");
    } catch (err) { b.disabled = false; toastError(err); }
  });
}
function razorpayCheckout(co) {
  const go = () => {
    const rz = new window.Razorpay({
      key: co.provider.key_id, order_id: co.order.order_id, amount: co.order.amount, currency: co.order.currency,
      name: "PENDEL", description: co.plan.name, prefill: co.prefill, theme: { color: "#8b5cf6" },
      handler: async (resp) => {
        try { const r = await post("/api/subscriptions/verify", { payment_id: co.payment_id, ...resp }); session.user = r.user; (await import("./api.js")).emitSession(); toast("Payment successful. Welcome to Premium!"); nav("/profile"); }
        catch (err) { toastError(err); }
      },
    });
    rz.open();
  };
  if (window.Razorpay) return go();
  const s = document.createElement("script"); s.src = "https://checkout.razorpay.com/v1/checkout.js"; s.onload = go;
  s.onerror = () => toast("Couldn't load the payment window. Check your connection.", { error: true });
  document.head.append(s);
}


// ------------------------------------------------------------------ Mood Session (non-stop, 30 min to 2 hours)
const sessionState = { data: null, startedAt: null };
export async function moodView(view, query, alive) {
  const [{ categories }, { languages }, world] = await Promise.all([get("/api/categories"), get("/api/languages"), get("/api/world/status").catch(() => ({ enabled: false }))]);
  if (!alive()) return;
  const prefs = session.user?.preferred_languages?.length ? session.user.preferred_languages.slice(0, 4) : ["hi", "te", "ta", "en"];
  const st = { mood: query.mood || "love", langs: new Set(query.langs ? query.langs.split(",") : prefs), minutes: +(query.minutes || 60), mode: query.mode || "highlights" };
  const moods = [{ slug: "trending", name: "Trending", color: "#7c3aed" }, ...categories.filter((c) => !["independent", "international"].includes(c.slug))];
  const langList = languages.filter((l) => l.featured);
  render(view, html`<h1>Mood Session</h1><p class="muted" style="margin-top:-4px">Pick a mood and your languages. PENDEL builds a non-stop session that lasts as long as you want.</p>
    <section class="section"><div class="section-head"><h2>1 · Mood</h2></div>
      <div class="mood-grid" id="m-mood">${moods.map((m) => html`<button class="mood-pick" style="--c1:${m.color}" data-mood="${m.slug}" aria-pressed="${m.slug === st.mood}">${m.name.replace(/ Songs$/, "")}</button>`)}</div></section>
    <section class="section"><div class="section-head"><h2>2 · Languages</h2><p>Pick one or more</p></div>
      <div class="chips" id="m-langs">${langList.map((l) => html`<button class="chip" data-lang="${l.code}" aria-pressed="${st.langs.has(l.code)}">${l.name}</button>`)}</div></section>
    <section class="section"><div class="section-head"><h2>3 · Length and style</h2></div>
      <div class="toolbar">
        <div class="seg" id="m-min" role="group" aria-label="Session length">${[30, 60, 90, 120].map((n) => html`<button data-min="${n}" aria-pressed="${n === st.minutes}">${n === 60 ? "1 hour" : n === 120 ? "2 hours" : `${n} min`}</button>`)}</div>
        <div class="seg" id="m-mode" role="group" aria-label="Session style"><button data-mode="highlights" aria-pressed="${st.mode === "highlights"}">Highlights marathon</button><button data-mode="full" aria-pressed="${st.mode === "full"}">Full songs</button></div>
        <button class="btn primary lg" id="m-go">${icon("play")}Start session</button>
      </div>
      <p class="dim small">Highlights marathon: back-to-back 20–30 second best moments. Full songs: complete tracks${world.enabled && !world.full_songs ? " (world catalogue songs play in full once a YouTube API key is added; until then PENDEL songs are used)" : ""}.</p>
    </section>
    <div id="m-out">${sessionState.data ? "" : ""}</div>`);
  const out = $("#m-out");
  const pick = (box, attr, val) => $$(`[${attr}]`, box).forEach((b) => b.setAttribute("aria-pressed", String(b.getAttribute(attr) === String(val))));
  $("#m-mood").onclick = (e) => { const b = e.target.closest("[data-mood]"); if (b) { st.mood = b.dataset.mood; pick($("#m-mood"), "data-mood", st.mood); } };
  $("#m-langs").onclick = (e) => { const b = e.target.closest("[data-lang]"); if (!b) return; const c = b.dataset.lang; st.langs.has(c) ? st.langs.delete(c) : st.langs.add(c); b.setAttribute("aria-pressed", String(st.langs.has(c))); };
  $("#m-min").onclick = (e) => { const b = e.target.closest("[data-min]"); if (b) { st.minutes = +b.dataset.min; pick($("#m-min"), "data-min", st.minutes); } };
  $("#m-mode").onclick = (e) => { const b = e.target.closest("[data-mode]"); if (b) { st.mode = b.dataset.mode; pick($("#m-mode"), "data-mode", st.mode); } };

  const fmtMin = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)} h ${Math.round((sec % 3600) / 60)} min` : `${Math.round(sec / 60)} min`;
  function drawSession() {
    const d = sessionState.data;
    if (!d) return;
    const q = registerQueue(d.songs, "Mood session");
    render(out, html`<section class="panel"><div class="section-head"><div><h2>Your ${d.mood === "trending" ? "Trending" : d.mood} session</h2>
        <p>${d.songs.length} ${d.mode === "highlights" ? "highlights" : "songs"} · ${fmtMin(d.total_seconds)} of ${fmtMin(d.target_seconds)}</p></div>
        <button class="btn" id="m-again">${icon("shuffle")}New mix</button></div>
      ${d.note ? html`<div class="notice">${d.note}</div>` : ""}
      <div class="session-bar"><span id="m-bar"></span></div>
      <div class="session-stats"><span id="m-played">0 min played</span><span id="m-left"></span><span id="m-now"></span></div></section>
      ${songList(d.songs, { label: "Mood session" })}`);
    $("#m-again").onclick = start;
    tick();
  }
  function tick() {
    const d = sessionState.data, bar = $("#m-bar");
    if (!d || !bar) return;
    const lens = d.songs.map((s) => d.mode === "highlights" ? (s.highlight ? s.highlight.end - s.highlight.start : 30) : s.duration);
    const i = player.queue.findIndex((s) => s.id === player.song?.id);
    const inSession = player.sessionMode && i >= 0 && player.queue.length === d.songs.length;
    const done = inSession ? lens.slice(0, i).reduce((a, b) => a + b, 0) + player.pos : 0;
    bar.style.width = `${Math.min(100, (100 * done) / d.total_seconds)}%`;
    $("#m-played").textContent = `${fmtMin(done)} played`;
    $("#m-left").textContent = `${fmtMin(Math.max(0, d.total_seconds - done))} left`;
    $("#m-now").textContent = inSession ? `Now: ${player.song.title} (${i + 1}/${d.songs.length})` : "";
  }
  async function start() {
    if (!st.langs.size) return toast("Pick at least one language", { error: true });
    const go = $("#m-go"); go.disabled = true;
    render(out, skeletonRows(6));
    try {
      const p = new URLSearchParams({ mood: st.mood, langs: [...st.langs].join(","), minutes: st.minutes, mode: st.mode, seed: Date.now() });
      const d = await get(`/api/sessions/mood?${p}`);
      if (!alive()) return;
      if (!d.songs.length) { render(out, emptyState("timer", "Nothing to play for that mix yet", d.note || "Try another mood or add languages.")); return; }
      sessionState.data = d;
      drawSession();
      out.scrollIntoView({ behavior: "smooth", block: "start" });
      player.hlQueue = false;
      player.play(d.songs[0], { queue: d.songs, index: 0, mode: d.mode === "highlights" ? "highlight" : "full", session: d.mode });
    } catch (e) { render(out, errorState(e)); } finally { go.disabled = false; }
  }
  $("#m-go").onclick = start;
  if (sessionState.data) drawSession();
  const off1 = player.on("time", tick), off2 = player.on("change", tick);
  return () => { off1(); off2(); };
}

// Song Highlights: a vertical, swipeable feed that plays only each song's
// curated 20-30 s highlight, with synced lyric lines, then auto-advances.
import { session, get } from "./api.js";
import { player } from "./player.js";
import { html, render, icon, $, $$, fmtTime, songs, emptyState } from "./ui.js";

let unlocked = false; // browsers need one user gesture before audio can autoplay

export async function highlightsView(view, feed = "trending", query = {}, alive) {
  feed = feed || "trending";
  const lang = query.lang || "";
  view.classList.add("flush");
  render(view, html`<div class="hl-wrap"><div class="hl-feed"><div class="hl-slide"><div class="skel" style="width:300px;height:300px;border-radius:22px"></div></div></div></div>`);
  const [d, langs] = await Promise.all([get(`/api/highlights?feed=${feed}&limit=10${lang ? `&lang=${lang}` : ""}`), get("/api/languages", { quiet: true })]);
  if (!alive()) return;
  const items = d.items.slice();
  let next = d.next_offset, loadingMore = false, active = -1;

  const tabs = html`<div class="hl-tabs"><div class="chips scroll" role="tablist" aria-label="Highlight feeds">
      ${d.feeds.map((f) => html`<a class="chip ${f.slug === feed ? "active" : ""}" role="tab" aria-selected="${f.slug === feed}" href="/highlights/${f.slug}${lang ? `?lang=${lang}` : ""}" data-link>${f.slug === "trending" ? "Trending" : f.name.replace(/ Songs$/, "")}</a>`)}
      <select class="chip" id="hl-lang" aria-label="Filter highlights by language" style="padding-right:10px"><option value="">All languages</option>${langs.languages.filter((l) => l.song_count).map((l) => html`<option value="${l.code}" ${l.code === lang ? "selected" : ""}>${l.name}</option>`)}</select>
    </div></div>`;
  render(view, html`<div class="hl-wrap">${tabs}
    <div class="hl-nav"><button class="icon-btn" data-hl="up" aria-label="Previous highlight">${icon("up")}</button><button class="icon-btn" data-hl="down" aria-label="Next highlight">${icon("down")}</button></div>
    <div class="hl-feed" id="hl-feed" tabindex="0" aria-label="Highlights. Use up and down arrow keys to move between songs.">
      ${items.length ? items.map(slide) : html`<div class="hl-slide"><div class="hl-card">${emptyState("spark", "No highlights here yet", "Try another mood or language.")}</div></div>`}
    </div>
    ${unlocked || !items.length ? "" : html`<div class="hl-start" id="hl-start"><div class="hl-start-card"><img src="/img/logo.svg" alt="" width="64" height="64"><h2>Song Highlights</h2><p>The best 20–30 seconds of every song. Swipe up for the next one.</p><button class="btn primary lg" id="hl-go">${icon("play")}Start listening</button></div></div>`}
  </div>`);
  const feedEl = $("#hl-feed");
  $("#hl-lang").addEventListener("change", (e) => window.rfNavigate(`/highlights/${feed}${e.target.value ? `?lang=${e.target.value}` : ""}`));
  $("#hl-go")?.addEventListener("click", () => { unlocked = true; $("#hl-start").remove(); activate(0, true); });

  function slide(s, i) {
    songs.set(s.id, s);
    const len = s.highlight.end - s.highlight.start;
    return html`<section class="hl-slide" data-i="${i}" data-id="${s.id}" aria-label="${s.title} by ${s.artist.name}">
      <div class="hl-bg" style="background-image:url('${s.cover_url}')"></div>
      <div class="hl-card">
        <div class="hl-art-wrap"><img class="hl-art" src="${s.cover_url}" alt="" loading="${i < 2 ? "eager" : "lazy"}">
          <button class="tap" data-hl="toggle" aria-label="Play or pause highlight"><span class="play-btn xl grad">${icon("play")}</span></button></div>
        <div><h2 class="hl-title" dir="auto">${s.title}</h2><div class="hl-artist"><a href="/artist/${s.artist.slug}" data-link>${s.artist.name}</a>${s.album ? ` · ${s.album.title}` : ""}</div></div>
        <div class="hl-tags"><span class="tag">${s.language.name}</span>${s.categories.slice(0, 3).map((c) => html`<span class="tag">${c.name}</span>`)}${s.premium_only ? html`<span class="tag">Premium full song</span>` : ""}</div>
        <div class="hl-lyrics" lang="${s.language.code}" dir="auto">
          ${s.lyrics_excerpt ? s.lyrics_excerpt.map((l) => html`<div class="ln" data-t="${l.t}">${l.text}${l.tr ? html`<span class="tr" lang="en">${l.tr}</span>` : ""}</div>`)
            : html`<div class="nolyr">${s.has_lyrics ? "♪ Instrumental moment" : "Lyrics aren't licensed for this song."}</div>`}
        </div>
        <div><div class="hl-progress"><span></span></div><div class="hl-time"><span class="hl-cur">0:00</span><span>Highlight ${fmtTime(s.highlight.start)}–${fmtTime(s.highlight.end)} · ${Math.round(len)}s</span></div></div>
        <div class="hl-actions">
          <button class="icon-btn ${s.is_favourite ? "fav-on" : ""}" data-action="fav" data-id="${s.id}" aria-label="${s.is_favourite ? "Remove from" : "Add to"} favourites" aria-pressed="${s.is_favourite}">${icon(s.is_favourite ? "heart-fill" : "heart")}</button>
          <button class="btn primary" data-hl="full" data-i="${i}">${icon("play")}Play full song</button>
          <button class="icon-btn" data-action="add-to-playlist" data-id="${s.id}" aria-label="Add to playlist">${icon("plus")}</button>
          <button class="icon-btn" data-action="share-song" data-id="${s.id}" aria-label="Share">${icon("share")}</button>
        </div>
      </div>
      ${i === 0 ? html`<div class="hl-hint">${icon("up")}Swipe up for the next highlight</div>` : ""}
    </section>`;
  }

  const slides = () => $$(".hl-slide[data-i]", feedEl);
  function activate(i, force = false) {
    const s = items[i];
    if (!s || (i === active && !force)) return;
    active = i;
    slides().forEach((el) => el.classList.toggle("is-playing", false));
    if (!unlocked) return;
    player.hlQueue = true;
    player.play(s, { queue: [s], mode: "highlight" });
    if (i >= items.length - 3) loadMore();
  }
  async function loadMore() {
    if (next === null || loadingMore) return;
    loadingMore = true;
    try {
      const m = await get(`/api/highlights?feed=${feed}&limit=10&offset=${next}${lang ? `&lang=${lang}` : ""}`, { quiet: true });
      const start = items.length;
      m.items.forEach((s) => items.push(s));
      next = m.next_offset;
      feedEl.insertAdjacentHTML("beforeend", m.items.map((s, k) => slide(s, start + k).s).join(""));
      m.items.forEach((_, k) => io.observe(feedEl.querySelector(`[data-i="${start + k}"]`)));
      if (next === null) appendEnd();
    } catch { /* keep current feed */ } finally { loadingMore = false; }
  }
  function appendEnd() {
    if ($(".hl-end", feedEl)) return;
    feedEl.insertAdjacentHTML("beforeend", html`<section class="hl-slide hl-end"><div class="hl-card" style="text-align:center;align-items:center">
      <h2 class="hl-title">You're all caught up</h2><p style="opacity:.8">Try another mood:</p>
      <div class="chips" style="justify-content:center">${d.feeds.filter((f) => f.slug !== feed).map((f) => html`<a class="chip" style="background:rgba(255,255,255,.12);color:#fff" href="/highlights/${f.slug}" data-link>${f.slug === "trending" ? "Trending" : f.name}</a>`)}</div>
      <button class="btn" data-hl="top" style="margin-top:12px">Back to the top</button></div></section>`.s);
  }
  if (next === null && items.length) appendEnd();

  const io = new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting && en.intersectionRatio >= 0.6) activate(+en.target.dataset.i); });
  }, { root: feedEl, threshold: [0.6] });
  slides().forEach((el) => io.observe(el));
  if (unlocked && items.length) activate(0, true);

  const scrollTo = (i) => { const el = feedEl.querySelector(`[data-i="${i}"]`) || $(".hl-end", feedEl); if (el) feedEl.scrollTo({ top: el.offsetTop, behavior: "smooth" }); };
  const curSlide = () => feedEl.querySelector(`[data-i="${active}"]`);

  const offTime = player.on("time", () => {
    const c = player.current, el = curSlide();
    if (!c || c.mode !== "highlight" || !el || c.song.id !== el.dataset.id) return;
    const pos = player.pos;
    $(".hl-progress span", el).style.width = `${(100 * pos) / player.length}%`;
    $(".hl-cur", el).textContent = fmtTime(pos);
    const lines = $$(".ln", el);
    let on = -1;
    lines.forEach((l, k) => { if (parseFloat(l.dataset.t) <= pos + 0.15) on = k; });
    lines.forEach((l, k) => l.classList.toggle("on", k === on));
  });
  const offState = player.on("state", () => {
    const el = curSlide();
    slides().forEach((x) => x.classList.toggle("is-playing", x === el && player.playing && player.current?.mode === "highlight"));
  });
  const offEnded = player.on("highlight-ended", () => { if (active + 1 < items.length) scrollTo(active + 1); else scrollTo(active + 1); });
  const offBlocked = player.on("blocked", () => { unlocked = false; });

  feedEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-hl]");
    if (!b) return;
    const act = b.dataset.hl;
    if (act === "toggle") {
      unlocked = true;
      const i = +b.closest("[data-i]").dataset.i;
      if (i !== active || player.current?.song.id !== items[i].id || player.current?.mode !== "highlight") activate(i, true);
      else player.toggle();
    } else if (act === "full") {
      const i = +b.dataset.i;
      player.hlQueue = false;
      player.play(items[i], { queue: items.slice(), index: i, mode: "full" }).then(() => document.dispatchEvent(new Event("rf:open-fullplayer")));
    } else if (act === "top") { scrollTo(0); }
  });
  view.addEventListener("click", onNav);
  function onNav(e) {
    const b = e.target.closest(".hl-nav [data-hl]");
    if (b) { unlocked = true; scrollTo(Math.max(0, active + (b.dataset.hl === "up" ? -1 : 1))); }
  }
  const onKey = (e) => {
    if (e.target.closest("input, select, textarea") || !$("#fullplayer").hidden) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); unlocked = true; scrollTo(active + 1); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); scrollTo(Math.max(0, active - 1)); }
    else if (e.code === "Space" && player.current?.mode === "highlight") { /* handled globally */ }
  };
  document.addEventListener("keydown", onKey);

  return () => {
    io.disconnect(); offTime(); offState(); offEnded(); offBlocked();
    document.removeEventListener("keydown", onKey);
    view.removeEventListener("click", onNav);
    if (player.current?.mode === "highlight" && player.hlQueue) player.pause();
    player.hlQueue = false;
  };
}

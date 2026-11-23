import { session, onSession, loadSession, get, post, put, patch, del, setAuth } from "./api.js";
import { setVoiceEnabled, voiceWanted, voiceSupported, onVoiceStatus, voiceStatusText } from "./voice.js";
import { player, syncUI, updateMuteIcon, setMuted } from "./player.js";
import { refreshLyrics } from "./lyrics.js";
import { html, raw, render, icon, $, $$, toast, toastError, modal, closeModal, openMenu, closeMenu, songs, queues, debounce, esc, errorState } from "./ui.js";
import * as V from "./views.js";
import { highlightsView } from "./highlights.js";

// ------------------------------------------------------------------ router
const routes = [
  [/^\/$/, V.homeView, "home"],
  [/^\/explore$/, V.exploreView, "explore"],
  [/^\/highlights(?:\/([\w-]+))?$/, highlightsView, "highlights"],
  [/^\/mood$/, V.moodView, "mood"],
  [/^\/languages$/, V.languagesView, "languages"],
  [/^\/language\/([\w-]+)$/, V.languageView, "languages"],
  [/^\/category\/([\w-]+)$/, V.categoryView, "explore"],
  [/^\/songs$/, V.fullSongsView, "songs"],
  [/^\/song\/([^/]+)$/, V.songView, ""],
  [/^\/artist\/([^/]+)$/, V.artistView, ""],
  [/^\/album\/([^/]+)$/, V.albumView, ""],
  [/^\/search$/, V.searchView, "search"],
  [/^\/library(?:\/([\w-]+))?$/, V.libraryView, "library"],
  [/^\/playlist\/shared\/([\w-]+)$/, V.sharedPlaylistView, "library"],
  [/^\/playlist\/(auto-[\w-]+|pl_\w+)$/, V.playlistView, "library"],
  [/^\/profile$/, V.profileView, "profile"],
  [/^\/login$/, V.loginView, "profile"],
  [/^\/register$/, V.registerView, "profile"],
  [/^\/premium$/, V.premiumView, ""],
  [/^\/admin(?:\/([\w-]+))?$/, async (...a) => (await import("./admin.js")).adminView(...a), "admin"],
  [/^\/legal(?:\/([\w-]+))?$/, async (...a) => { const L = await import("./legal.js"); L.setPendingHash(location.hash.slice(1)); return L.legalView(...a); }, "legal"],
];
let cleanup = null;
let renderSeq = 0;

export function navigate(href, { replace = false } = {}) {
  const u = new URL(href, location.href);
  if (u.origin !== location.origin) { location.href = href; return; }
  if (u.pathname + u.search === location.pathname + location.search && !replace) {
    if (u.hash !== location.hash) history.replaceState({}, "", u.pathname + u.search + u.hash);
    route(); return;
  }
  history[replace ? "replaceState" : "pushState"]({}, "", u.pathname + u.search + u.hash);
  route();
}
window.rfNavigate = navigate;

async function route() {
  closeMenu(); closeModal();
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const query = Object.fromEntries(new URLSearchParams(location.search));
  const view = $("#view");
  if (typeof cleanup === "function") { try { cleanup(); } catch { /* ignore */ } }
  cleanup = null;
  const match = routes.map(([rx, fn, nav]) => [path.match(rx), fn, nav]).find(([m]) => m);
  document.body.className = document.body.className.replace(/\broute-\S+/g, "").trim();
  view.classList.remove("flush");
  const seq = ++renderSeq;
  if (!match) { render(view, html`<div class="empty">${icon("compass")}<h3>Page not found</h3><p>That page doesn't exist.</p><a class="btn primary" href="/" data-link>Go home</a></div>`); return; }
  let [m, fn, nav] = match;
  if (path === "/library/favourites") nav = "favourites";
  document.body.classList.add("route-" + (nav || "other"));
  $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
  view.scrollTop = 0;
  try {
    const res = await fn(view, ...m.slice(1).map((x) => x && decodeURIComponent(x)), query, () => seq === renderSeq);
    if (seq === renderSeq) cleanup = res;
    else if (typeof res === "function") res();
  } catch (e) {
    console.error(e);
    if (seq === renderSeq) render(view, errorState(e));
  }
  if (seq === renderSeq) { syncUI(); view.focus({ preventScroll: true }); }
}
window.addEventListener("popstate", route);

// ------------------------------------------------------------------ legal footer
// One footer element kept at the end of #view whatever a view renders (views replace
// #view's children, sometimes more than once). Hidden on full-bleed routes via CSS.
const FOOTER_LINKS = [["terms", "Terms of Service"], ["privacy", "Privacy Policy"], ["copyright", "Copyright / DMCA"], ["refunds", "Refunds & Cancellation"], ["contact", "Contact"]];
const siteFooter = document.createElement("footer");
siteFooter.className = "site-footer";
render(siteFooter, html`<nav aria-label="Legal and policies"><ul>${FOOTER_LINKS.map(([k, t]) => html`<li><a href="/legal/${k}" data-link>${t}</a></li>`)}</ul></nav>
  <p>© ${new Date().getFullYear()} PENDEL · Feel Every Beat · Music belongs to its respective rights holders.</p>
  <p class="voice-foot" hidden>Hey Pendel voice control: <span class="voice-foot-status"></span> <button type="button" class="linklike" data-action="voice-toggle"></button></p>`);
// "Hey Pendel" has no header button: its on/off switch lives here and in Profile
onVoiceStatus(syncVoiceFoot);
function syncVoiceFoot() {
  const p = $(".voice-foot", siteFooter);
  if (!voiceSupported()) { p.hidden = true; return; }
  p.hidden = false;
  $(".voice-foot-status", siteFooter).textContent = voiceStatusText();
  $("[data-action=voice-toggle]", siteFooter).textContent = voiceWanted() ? "Turn off" : "Turn on";
}
syncVoiceFoot();
function keepFooter() {
  const view = $("#view");
  if (view && view.lastElementChild !== siteFooter) view.appendChild(siteFooter);
}
new MutationObserver(keepFooter).observe($("#view"), { childList: true });
keepFooter();

// ------------------------------------------------------------------ shell: account + sidebar
function renderAccount() {
  const u = session.user;
  const slot = $("#account-slot");
  if (!u) render(slot, html`<a class="btn sm ghost" href="/register" data-link>Sign up</a> <a class="btn sm primary" href="/login" data-link>Log in</a>`);
  else render(slot, html`<button class="avatar" data-action="account-menu" aria-label="Account menu for ${u.display_name}">${u.avatar_url ? html`<img src="${u.avatar_url}" alt="" width="36" height="36" style="width:100%;height:100%;object-fit:cover">` : u.display_name[0].toUpperCase()}</button>`);
  $$(".admin-link").forEach((a) => (a.hidden = !session.isAdmin));
  $("#premium-cta").hidden = session.premium;
  document.documentElement.dataset.theme = u?.theme || localStorage.getItem("rf_theme") || "dark";
  loadSidePlaylists();
}
async function loadSidePlaylists() {
  const el = $("#side-playlists");
  if (!session.user) { el.innerHTML = ""; return; }
  try {
    const d = await get("/api/me/playlists", { quiet: true });
    const list = d.playlists.concat(d.followed).slice(0, 12);
    render(el, list.length ? html`<div class="side-title">Playlists</div>${list.map((p) => html`<a href="/playlist/${p.id}" data-link>${p.title}</a>`)}` : "");
  } catch { el.innerHTML = ""; }
}
onSession(renderAccount);
document.addEventListener("rf:playlists", loadSidePlaylists);

// ------------------------------------------------------------------ search autocomplete
const sform = $("#searchbox"), sinput = $("input", sform), slist = $("#suggest");
let sugg = [], sel = -1;
const fetchSuggest = debounce(async (q) => {
  if (!q.trim()) { hideSuggest(); return; }
  try {
    const d = await get(`/api/search/suggest?q=${encodeURIComponent(q)}`, { quiet: true });
    if (sinput.value !== q) return;
    sugg = d.suggestions; sel = -1;
    if (!sugg.length) { hideSuggest(); return; }
    render(slist, html`${sugg.map((s, i) => html`<li role="option" id="sg-${i}" data-i="${i}" aria-selected="false"><span class="s-type">${s.type}</span><span>${s.label}</span><span class="s-sub">${s.sub}</span></li>`)}
      <li role="option" data-i="all" aria-selected="false"><span class="s-type">search</span><span>See all results for “${q}”</span></li>`);
    slist.hidden = false; sinput.setAttribute("aria-expanded", "true");
  } catch { hideSuggest(); }
}, 140);
function hideSuggest() { slist.hidden = true; sinput.setAttribute("aria-expanded", "false"); sel = -1; }
sinput.addEventListener("input", () => fetchSuggest(sinput.value));
sinput.addEventListener("focus", () => { if (sinput.value) fetchSuggest(sinput.value); });
sinput.addEventListener("keydown", (e) => {
  const items = $$("li", slist);
  if (slist.hidden || !items.length) return;
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    sel = (sel + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items.forEach((li, i) => li.setAttribute("aria-selected", String(i === sel)));
    sinput.setAttribute("aria-activedescendant", items[sel].id || "");
  } else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); pickSuggest(items[sel].dataset.i); }
  else if (e.key === "Escape") hideSuggest();
});
slist.addEventListener("mousedown", (e) => { const li = e.target.closest("li"); if (li) { e.preventDefault(); pickSuggest(li.dataset.i); } });
sinput.addEventListener("blur", () => setTimeout(hideSuggest, 120));
function pickSuggest(i) {
  hideSuggest();
  if (i === "all") navigate(`/search?q=${encodeURIComponent(sinput.value)}`);
  else navigate(sugg[+i].href);
}
sform.addEventListener("submit", (e) => { e.preventDefault(); hideSuggest(); if (sinput.value.trim()) navigate(`/search?q=${encodeURIComponent(sinput.value.trim())}`); });

// ------------------------------------------------------------------ song actions
function songFrom(el) { return songs.get(el.dataset.id) || (player.song?.id === el.dataset.id ? player.song : null); }
function queueFrom(el, song) {
  const q = queues.get(el.dataset.queue);
  if (!q) return { list: [song], index: 0 };
  const list = q.ids.map((id) => songs.get(id)).filter(Boolean);
  const idx = el.dataset.index !== undefined && el.dataset.index !== "" ? +el.dataset.index : list.findIndex((s) => s.id === song.id);
  return { list, index: Math.max(0, idx) };
}
export function requireLogin(msg = "Log in to do that") {
  if (session.user) return true;
  toast(msg, { action: { href: `/login?next=${encodeURIComponent(location.pathname)}`, label: "Log in" } });
  return false;
}
export async function toggleFavourite(song) {
  if (!requireLogin("Log in to save favourites")) return;
  const want = !song.is_favourite;
  try {
    if (want) await put(`/api/me/favourites/${song.id}`); else await del(`/api/me/favourites/${song.id}`);
    song.is_favourite = want;
    const s = songs.get(song.id); if (s) s.is_favourite = want;
    player.setFavourite(song.id, want);
    $$(`[data-action=fav][data-id="${song.id}"]`).forEach((b) => {
      b.classList.toggle("fav-on", want); b.setAttribute("aria-pressed", String(want));
      b.querySelector("use")?.setAttribute("href", `/img/icons.svg#${want ? "heart-fill" : "heart"}`);
    });
    document.dispatchEvent(new CustomEvent("rf:fav", { detail: { id: song.id, value: want } }));
    toast(want ? "Added to your favourites" : "Removed from favourites", { ms: 1800 });
  } catch (e) { toastError(e); }
}
export async function addToPlaylistDialog(song) {
  if (!requireLogin("Log in to build playlists")) return;
  let d;
  try { d = await get("/api/me/playlists"); } catch (e) { return toastError(e); }
  const m = modal(html`<h3>Add to playlist</h3><p class="muted small">“${song.title}” · ${song.artist.name}</p>
    <div class="songlist" style="max-height:300px;overflow:auto;margin:10px 0">
      ${d.playlists.length ? d.playlists.map((p) => html`<button class="q-item" style="width:100%;border:0;background:none;text-align:left;cursor:pointer" data-pl="${p.id}"><div class="meta"><div class="t">${p.title}</div><div class="s">${p.song_count} songs · ${p.is_public ? "Public" : "Private"}</div></div>${icon("plus")}</button>`) : html`<p class="dim small">You don't have any playlists yet.</p>`}
    </div>
    <form class="field" data-new><label for="np">New playlist</label><div style="display:flex;gap:8px"><input class="input" id="np" name="title" placeholder="Playlist name" maxlength="80" required><button class="btn primary">Create</button></div></form>
    <div class="modal-actions"><button class="btn ghost" data-close>Done</button></div>`);
  m.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close]")) return closeModal();
    const b = e.target.closest("[data-pl]");
    if (!b) return;
    try { const r = await post(`/api/playlists/${b.dataset.pl}/songs`, { song_id: song.id }); toast(r.message); closeModal(); document.dispatchEvent(new Event("rf:playlists")); }
    catch (err) { toastError(err); }
  });
  $("form[data-new]", m).addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const r = await post("/api/playlists", { title: e.target.title.value, song_ids: [song.id] });
      toast(`Created “${r.playlist.title}”`); closeModal(); document.dispatchEvent(new Event("rf:playlists"));
    } catch (err) { toastError(err); }
  });
}
export async function share(url, title) {
  const full = new URL(url, location.origin).href;
  if (navigator.share) { try { await navigator.share({ title, url: full }); return; } catch { /* cancelled */ } }
  try { await navigator.clipboard.writeText(full); toast("Link copied to clipboard"); }
  catch {
    const m = modal(html`<h3>Share</h3><p class="muted small">Copy this link:</p><input class="input" value="${full}" readonly><div class="modal-actions"><button class="btn" data-close>Close</button></div>`);
    const inp = $("input", m); inp.focus(); inp.select();
    $("[data-close]", m).onclick = closeModal;
  }
}
function songMenu(anchor, song) {
  openMenu(anchor, [
    { label: "Play full song", icon: "play", run: () => player.play(song, { queue: [song] }) },
    ...(song.highlight ? [{ label: "Play 20–30s highlight", icon: "spark", run: () => player.play(song, { queue: [song], mode: "highlight" }) }] : []),
    { label: "Play next", icon: "queue", run: () => player.playNext(song) },
    { label: "Add to queue", icon: "list", run: () => player.addToQueue(song) },
    "-",
    { label: "Add to playlist", icon: "plus", run: () => addToPlaylistDialog(song) },
    { label: song.is_favourite ? "Remove from favourites" : "Add to favourites", icon: "heart", run: () => toggleFavourite(song) },
    "-",
    { label: "Go to song", icon: "disc", run: () => navigate(`/song/${song.slug}`) },
    { label: "Go to artist", icon: "user", run: () => navigate(`/artist/${song.artist.slug}`) },
    ...(song.album ? [{ label: "Go to album", icon: "library", run: () => navigate(`/album/${song.album.slug}`) }] : []),
    { label: "Share", icon: "share", run: () => share(`/song/${song.slug}`, `${song.title} — ${song.artist.name}`) },
  ]);
}

// ------------------------------------------------------------------ drawers
function renderQueue() {
  const body = $("#queue-body");
  if ($("#queue-drawer").hidden) return;
  const q = player.queue, i = player.index;
  if (!q.length) { render(body, html`<div class="empty">${icon("queue")}<h3>Your queue is empty</h3><p>Play something to get started.</p></div>`); return; }
  const item = (s, idx, cur = false) => html`<div class="q-item ${cur ? "current" : ""}">
      <img src="${s.cover_url}" alt="" width="42" height="42">
      <div class="meta" data-action="queue-jump" data-i="${idx}"><div class="t">${s.title}</div><div class="s">${s.artist.name} · ${s.language.name}</div></div>
      ${cur ? "" : html`<button class="icon-btn sm" data-action="queue-move" data-i="${idx}" data-d="-1" aria-label="Move up">${icon("up")}</button><button class="icon-btn sm" data-action="queue-move" data-i="${idx}" data-d="1" aria-label="Move down">${icon("down")}</button><button class="icon-btn sm" data-action="queue-remove" data-i="${idx}" aria-label="Remove from queue">${icon("x")}</button>`}
    </div>`;
  render(body, html`<div class="q-label">Now playing</div>${q[i] ? item(q[i], i, true) : ""}
    <div class="q-label">Next up ${player.shuffle ? "· shuffled" : ""}</div>
    ${q.length > i + 1 ? q.slice(i + 1).map((s, k) => item(s, i + 1 + k)) : html`<p class="dim small">${player.autoplay ? "Similar songs will keep playing when this one ends." : "Nothing queued. Use “Add to queue” from any song's menu."}</p>`}
    <label class="q-autoplay"><input type="checkbox" data-action="toggle-autoplay" ${raw(player.autoplay ? "checked" : "")}> Autoplay similar songs when the queue ends</label>`);
}
player.on("queue", renderQueue);
player.on("change", renderQueue);
function openDrawer(id) {
  ["#queue-drawer", "#lyrics-drawer"].forEach((d) => { if (d !== id) $(d).hidden = true; });
  $(id).hidden = false;
}

// ------------------------------------------------------------------ global click delegation
const actions = {
  "back": () => history.back(),
  "forward": () => history.forward(),
  "reload": () => route(),
  "toggle-theme": async () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; localStorage.setItem("rf_theme", next);
    if (session.user) { session.user.theme = next; patch("/api/me", { theme: next }, { quiet: true }).catch(() => {}); }
  },
  "account-menu": (el) => openMenu(el, [
    { label: "Profile", icon: "user", run: () => navigate("/profile") },
    { label: "Library", icon: "library", run: () => navigate("/library") },
    ...(session.isAdmin ? [{ label: "Admin dashboard", icon: "shield", run: () => navigate("/admin") }] : []),
    ...(!session.premium ? [{ label: "Go Premium", icon: "crown", run: () => navigate("/premium") }] : []),
    { label: "Legal & privacy", icon: "shield", run: () => navigate("/legal") },
    "-",
    { label: "Log out", icon: "logout", run: () => { player.flushHistory(); setAuth(null, null); toast("You're logged out"); navigate("/"); } },
  ]),
  "open-song": (el, e) => { if (e.target.closest("a, button")) return; navigate(`/song/${el.dataset.slug}`); },
  "play": (el) => { const s = songFrom(el); if (!s) return; const { list, index } = queueFrom(el, s); player.hlQueue = false; player.play(s, { queue: list, index }); },
  "play-highlight": (el) => { const s = songFrom(el); if (s) { player.hlQueue = false; player.play(s, { queue: [s], mode: "highlight" }); } },
  "queue-all": (el) => { const q = queues.get(el.dataset.queue); if (!q?.ids.length) return; player.addAllToQueue(q.ids.map((id) => songs.get(id)).filter(Boolean)); },
  "play-all": (el) => { const q = queues.get(el.dataset.queue); if (!q?.ids.length) return; const list = q.ids.map((id) => songs.get(id)); const i = el.dataset.shuffle ? Math.floor(Math.random() * list.length) : 0; if (el.dataset.shuffle && !player.shuffle) player.shuffle = true; player.hlQueue = false; player.play(list[i], { queue: list, index: i }); },
  "play-with-lyrics": (el) => { const s = songFrom(el); if (!s) return; player.hlQueue = false; player.play(s, { queue: [s] }).then(() => actions["open-lyrics"]()); },
  "fav": (el) => { const s = songFrom(el); if (s) toggleFavourite(s); },
  "player-fav": () => player.song && toggleFavourite(player.song),
  "song-menu": (el) => { const s = songFrom(el); if (s) songMenu(el, s); },
  "song-menu-current": (el) => player.song && songMenu(el, player.song),
  "add-to-playlist": (el) => { const s = songFrom(el); if (s) addToPlaylistDialog(s); },
  "share-song": (el) => { const s = songFrom(el); if (s) share(`/song/${s.slug}`, s.title); },
  "toggle-play": () => player.toggle(),
  "next": () => player.next(),
  "prev": () => player.prev(),
  "shuffle": () => player.toggleShuffle(),
  "repeat": () => player.cycleRepeat(),
  "seek-back": () => player.seekBy(-10),
  "seek-fwd": () => player.seekBy(10),
  "mute": () => setMuted(!player.audio.muted),
  "open-fullplayer": (el, e) => { if (e.target.closest("button")) return; $("#fullplayer").hidden = false; refreshLyrics(); },
  "close-fullplayer": () => { $("#fullplayer").hidden = true; },
  "toggle-fp-lyrics": (el) => { const fp = $("#fullplayer"); fp.classList.toggle("show-lyrics"); el.classList.toggle("active"); refreshLyrics(); },
  "open-queue": () => { openDrawer("#queue-drawer"); renderQueue(); },
  "close-queue": () => { $("#queue-drawer").hidden = true; },
  "clear-queue": () => player.clearUpcoming(),
  "voice-toggle": () => { setVoiceEnabled(!voiceWanted()); syncVoiceFoot(); },
  "toggle-autoplay": (el) => { player.setAutoplay(el.checked); renderQueue(); },
  "queue-jump": (el) => player.jump(+el.dataset.i),
  "queue-remove": (el) => player.removeAt(+el.dataset.i),
  "queue-move": (el) => player.move(+el.dataset.i, +el.dataset.d),
  "open-lyrics": () => { if (window.innerWidth < 900) { $("#fullplayer").hidden = false; $("#fullplayer").classList.add("show-lyrics"); } else { openDrawer("#lyrics-drawer"); } refreshLyrics(); },
  "close-lyrics": () => { $("#lyrics-drawer").hidden = true; },
  "expand-lyrics": () => $("#lyrics-drawer").classList.toggle("expanded"),
  "rail-scroll": (el) => { const r = document.getElementById(el.dataset.target); r?.scrollBy({ left: +el.dataset.dir * r.clientWidth * 0.8, behavior: "smooth" }); },
};
document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const a = e.target.closest("a[data-link]");
  const act = e.target.closest("[data-action]");
  // a link nested inside an actionable element (e.g. artist name inside a card) wins
  const linkWins = a && (!act || (act.contains(a) && act !== a));
  if (act && !linkWins && actions[act.dataset.action]) {
    if (act.tagName !== "A") { actions[act.dataset.action](act, e); return; }
    actions[act.dataset.action](act, e);
  }
  if (a) {
    e.preventDefault();
    if (a.closest("#fullplayer")) $("#fullplayer").hidden = true;
    navigate(a.getAttribute("href"));
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    if ($("#ctx-menu")) return closeMenu();
    if ($("#modal-root").firstElementChild) return closeModal();
    if (!$("#fullplayer").hidden) { $("#fullplayer").hidden = true; return; }
    ["#queue-drawer", "#lyrics-drawer"].forEach((d) => ($(d).hidden = true));
  }
  if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-action=open-song], .pl-track")) {
    e.preventDefault(); e.target.click();
  }
});

document.addEventListener("rf:open-fullplayer", () => { $("#fullplayer").hidden = false; refreshLyrics(); });
// lyrics load lazily: refresh whenever the full player or lyrics drawer becomes visible
new MutationObserver(() => refreshLyrics()).observe($("#fullplayer"), { attributes: true, attributeFilter: ["hidden", "class"] });

// ------------------------------------------------------------------ boot
(async function boot() {
  document.documentElement.dataset.theme = localStorage.getItem("rf_theme") || "dark";
  try { await loadSession(); } catch (e) { toastError(e); }
  renderAccount();
  route();
})();

// Installable app (Add to Home Screen) + offline app shell. Only on https or localhost.
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => { /* not critical */ }));
}

export { route };

// Rendering helpers. Every interpolated value is HTML-escaped unless wrapped in raw().
export class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(String(s ?? ""));
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);
function fmt(v) {
  if (v instanceof Raw) return v.s;
  if (Array.isArray(v)) return v.map(fmt).join("");
  if (v === null || v === undefined || v === false) return "";
  return esc(v);
}
export function html(strings, ...vals) {
  let out = "";
  strings.forEach((s, i) => { out += s + (i < vals.length ? fmt(vals[i]) : ""); });
  return new Raw(out);
}
export function render(el, content) { el.innerHTML = content instanceof Raw ? content.s : esc(content); }
export const icon = (name, cls = "i") => raw(`<svg class="${cls}" aria-hidden="true"><use href="/img/icons.svg#${name}"/></svg>`);

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function fmtTime(sec) {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
export function fmtNum(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "K";
  return String(n ?? 0);
}
export function fmtMoney(minor, cur = "INR") {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, maximumFractionDigits: 0 }).format(minor / 100); }
  catch { return `${cur} ${(minor / 100).toFixed(0)}`; }
}
export const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "—";
export const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

// ------------------------------------------------------------------ registries
// Songs rendered on screen are kept here so click handlers can find full objects.
export const songs = new Map();
export const queues = new Map();
let qid = 0;
export function registerSongs(list) { list.forEach((s) => songs.set(s.id, s)); return list; }
export function registerQueue(list, label = "") { const id = "q" + (++qid); queues.set(id, { ids: list.map((s) => s.id), label }); registerSongs(list); return id; }

// ------------------------------------------------------------------ toasts / modals / menus
export function toast(msg, { error = false, action = null, ms = 3200 } = {}) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.setAttribute("role", error ? "alert" : "status");
  el.append(document.createTextNode(msg));
  if (action) {
    const a = document.createElement("a");
    a.href = action.href; a.textContent = action.label; a.dataset.link = "";
    el.append(a);
  }
  document.getElementById("toasts").append(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; setTimeout(() => el.remove(), 300); }, ms);
}
export const toastError = (e) => toast(e?.message || String(e), { error: true });

let lastFocus = null;
export function modal(content, { wide = false, onClose } = {}) {
  closeModal();
  lastFocus = document.activeElement;
  const root = document.getElementById("modal-root");
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal${wide ? " wide" : ""}" role="dialog" aria-modal="true">${content instanceof Raw ? content.s : esc(content)}</div>`;
  back.addEventListener("mousedown", (e) => { if (e.target === back) closeModal(); });
  back._onClose = onClose;
  root.append(back);
  const first = back.querySelector("input, select, textarea, button");
  first?.focus();
  return back.querySelector(".modal");
}
export function closeModal() {
  const root = document.getElementById("modal-root");
  const b = root.firstElementChild;
  if (b) { b._onClose?.(); b.remove(); lastFocus?.focus?.(); }
}
export function confirmDialog(title, text, { ok = "Confirm", danger = false } = {}) {
  return new Promise((resolve) => {
    const m = modal(html`<h3>${title}</h3><p class="muted">${text}</p>
      <div class="modal-actions"><button class="btn ghost" data-x="no">Cancel</button><button class="btn ${danger ? "danger" : "primary"}" data-x="yes">${ok}</button></div>`,
    { onClose: () => resolve(false) });
    m.addEventListener("click", (e) => {
      const b = e.target.closest("[data-x]");
      if (!b) return;
      const v = b.dataset.x === "yes";
      m.parentElement._onClose = null; closeModal(); resolve(v);
    });
  });
}

export function openMenu(anchor, items) {
  closeMenu();
  const m = document.createElement("div");
  m.className = "menu"; m.setAttribute("role", "menu"); m.id = "ctx-menu";
  m.innerHTML = items.map((it, i) => it === "-" ? "<hr>" : `<button role="menuitem" data-i="${i}">${it.icon ? `<svg class="i"><use href="/img/icons.svg#${it.icon}"/></svg>` : ""}${esc(it.label)}</button>`).join("");
  document.body.append(m);
  const r = anchor.getBoundingClientRect();
  const w = m.offsetWidth, h = m.offsetHeight;
  m.style.left = Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w)) + "px";
  m.style.top = (r.bottom + h + 8 > window.innerHeight ? Math.max(8, r.top - h - 4) : r.bottom + 4) + "px";
  m.addEventListener("click", (e) => { const b = e.target.closest("[data-i]"); if (b) { closeMenu(); items[+b.dataset.i].run(); } });
  m.querySelector("button")?.focus();
  setTimeout(() => document.addEventListener("mousedown", outside), 0);
  function outside(e) { if (!m.contains(e.target)) { closeMenu(); } }
  m._cleanup = () => document.removeEventListener("mousedown", outside);
}
export function closeMenu() { const m = document.getElementById("ctx-menu"); if (m) { m._cleanup?.(); m.remove(); } }

// ------------------------------------------------------------------ components
export function songCard(s, { queue, index = 0, rank = null } = {}) {
  songs.set(s.id, s);
  return html`<article class="card" data-action="open-song" data-slug="${s.slug}" tabindex="0" aria-label="${s.title} by ${s.artist.name}">
    <div class="card-art">
      <img src="${s.cover_url}" alt="" loading="lazy" width="172" height="172">
      ${s.highlight ? html`<button class="card-hl" data-action="play-highlight" data-id="${s.id}" data-queue="${queue || ""}" aria-label="Play 20-30 second highlight of ${s.title}">${icon("spark")}Highlight</button>` : ""}
      ${s.premium_only ? html`<span class="lock" title="Full song for Premium">${icon("lock")}</span>` : ""}
      ${rank ? html`<span class="card-rank">${rank}</span>` : ""}
      <button class="play-btn grad card-play" data-action="play" data-id="${s.id}" data-queue="${queue || ""}" data-index="${index}" aria-label="Play ${s.title}">${icon("play")}</button>
    </div>
    <div class="card-title">${s.title}</div>
    <div class="card-sub"><a href="/artist/${s.artist.slug}" data-link>${s.artist.name}</a> · ${s.language.name}</div>
  </article>`;
}

export function rail(title, list, { subtitle = "", moreHref = "", hlFeed = "", ranked = false, empty = "Nothing here yet." } = {}) {
  const q = registerQueue(list, title);
  const id = "rail-" + q;
  return html`<section class="section">
    <div class="section-head">
      <div><h2>${title}</h2>${subtitle ? html`<p>${subtitle}</p>` : ""}</div>
      <div class="actions">
        ${hlFeed ? html`<a class="chip" href="/highlights/${hlFeed}" data-link>${icon("spark")}Highlights</a>` : ""}
        ${moreHref ? html`<a class="chip" href="${moreHref}" data-link>See all</a>` : ""}
        <div class="rail-arrows"><button class="icon-btn sm" data-action="rail-scroll" data-target="${id}" data-dir="-1" aria-label="Scroll left">${icon("chev-left")}</button><button class="icon-btn sm" data-action="rail-scroll" data-target="${id}" data-dir="1" aria-label="Scroll right">${icon("chev-right")}</button></div>
      </div>
    </div>
    ${list.length ? html`<div class="rail" id="${id}">${list.map((s, i) => songCard(s, { queue: q, index: i, rank: ranked ? i + 1 : null }))}</div>` : emptyState("music", empty)}
  </section>`;
}

export function songRow(s, i, q, { removable = false, draggable = false, showIdx = true } = {}) {
  songs.set(s.id, s);
  return html`<div class="row" data-song-row="${s.id}" ${raw(draggable ? 'draggable="true"' : "")}>
    <div class="row-idx">${showIdx ? html`<span class="num">${i + 1}</span>` : ""}<span class="play-ico"><button class="icon-btn" data-action="play" data-id="${s.id}" data-queue="${q}" data-index="${i}" aria-label="Play ${s.title}">${icon("play")}</button></span></div>
    <img src="${s.cover_url}" alt="" loading="lazy" width="48" height="48" data-action="play" data-id="${s.id}" data-queue="${q}" data-index="${i}">
    <div class="row-main">
      <div class="row-title"><a href="/song/${s.slug}" data-link>${s.title}</a>${s.premium_only ? html`<span class="badge premium" title="Full song for Premium listeners">Premium</span>` : ""}${s.is_explicit ? html`<span class="badge outline">E</span>` : ""}</div>
      <div class="row-sub"><a href="/artist/${s.artist.slug}" data-link>${s.artist.name}</a>${s.genre ? ` · ${s.genre}` : ""}</div>
    </div>
    <div class="row-album">${s.album ? html`<a href="/album/${s.album.slug}" data-link>${s.album.title}</a>` : ""}</div>
    <div class="row-lang">${s.language.name}</div>
    <div class="row-dur">${fmtTime(s.duration)}</div>
    <div class="row-actions">
      ${draggable ? html`<button class="icon-btn sm" data-action="move-up" data-id="${s.id}" aria-label="Move up">${icon("up")}</button><button class="icon-btn sm" data-action="move-down" data-id="${s.id}" aria-label="Move down">${icon("down")}</button>` : ""}
      ${s.highlight ? html`<button class="icon-btn sm" data-action="play-highlight" data-id="${s.id}" data-queue="${q}" aria-label="Play highlight">${icon("spark")}</button>` : ""}
      <button class="icon-btn sm ${s.is_favourite ? "fav-on" : ""}" data-action="fav" data-id="${s.id}" aria-label="${s.is_favourite ? "Remove from" : "Add to"} favourites" aria-pressed="${s.is_favourite}">${icon(s.is_favourite ? "heart-fill" : "heart")}</button>
      ${removable ? html`<button class="icon-btn sm" data-action="pl-remove" data-id="${s.id}" aria-label="Remove from playlist">${icon("x")}</button>` : ""}
      <button class="icon-btn sm row-more" data-action="song-menu" data-id="${s.id}" aria-label="More options for ${s.title}">${icon("more")}</button>
    </div>
  </div>`;
}

export function songList(list, opts = {}) {
  const q = registerQueue(list, opts.label || "");
  if (!list.length) return emptyState("music", opts.empty || "No songs here yet.", opts.emptyText || "", opts.emptyAction);
  return html`<div class="songlist" data-queue-id="${q}">
    <div class="row list-head"><div>#</div><div></div><div>Title</div><div class="row-album">Album</div><div class="row-lang">Language</div><div class="row-dur">Time</div><div></div></div>
    ${list.map((s, i) => songRow(s, i, q, opts))}
  </div>`;
}

export function emptyState(ic, title, text = "", action = null) {
  return html`<div class="empty">${icon(ic)}<h3>${title}</h3>${text ? html`<p>${text}</p>` : ""}${action ? html`<a class="btn primary" href="${action.href}" data-link>${action.label}</a>` : ""}</div>`;
}
export function errorState(e, retry = true) {
  return html`<div class="empty">${icon("x")}<h3>${e.status === 404 ? "Not found" : e.status === 451 ? "Unavailable" : "Something went wrong"}</h3><p>${e.message}</p>${retry ? html`<button class="btn" data-action="reload">Try again</button>` : ""}</div>`;
}
export const skeletonRails = (n = 3) => html`<div class="skel skel-hero"></div>${Array.from({ length: n }, () => html`<div class="section"><div class="skel" style="width:200px;height:22px;margin-bottom:14px"></div><div class="rail">${Array.from({ length: 7 }, () => html`<div class="skel skel-card"></div>`)}</div></div>`)}`;
export const skeletonRows = (n = 8) => html`${Array.from({ length: n }, () => html`<div class="skel skel-row"></div>`)}`;

export function mosaic(urls) {
  if (!urls?.length) return html`<div class="pl-cover-fallback">${icon("queue")}</div>`;
  if (urls.length < 4) return html`<div class="mosaic one"><img src="${urls[0]}" alt=""></div>`;
  return html`<div class="mosaic">${urls.slice(0, 4).map((u) => html`<img src="${u}" alt="">`)}</div>`;
}

export function setRangeFill(input) {
  const max = +input.max || 100;
  input.style.setProperty("--p", `${(100 * (+input.value)) / max}%`);
}

// Licensed, time-synced lyrics: loads for the current song, highlights the active
// line, auto-scrolls (pausing auto-scroll briefly when the listener scrolls),
// supports translations and click-to-seek.
import { get } from "./api.js";
import { player } from "./player.js";
import { html, render, $, $$ } from "./ui.js";

const cache = new Map();
const panels = () => [$("#lyrics-body"), $("#fp-lyrics")].filter((p) => p && p.offsetParent !== null);
let showTr = localStorage.getItem("rf_lyr_tr") === "1";
let userScrollUntil = 0;
let loadedFor = null;

async function fetchLyrics(id) {
  if (!cache.has(id)) cache.set(id, get(`/api/songs/${id}/lyrics`, { quiet: true }).catch((e) => ({ available: false, reason: e.message })));
  return cache.get(id);
}

export async function refreshLyrics(force = false) {
  const song = player.song;
  const targets = panels();
  if (!targets.length) return;
  if (!song) { targets.forEach((p) => render(p, html`<div class="lyrics-empty">Play a song to see its lyrics.</div>`)); return; }
  if (!force && loadedFor === song.id && targets.every((p) => p.dataset.song === song.id)) return;
  targets.forEach((p) => render(p, html`<div class="skel skel-row"></div><div class="skel skel-row"></div><div class="skel skel-row"></div>`));
  const d = await fetchLyrics(song.id);
  if (player.song?.id !== song.id) return;
  loadedFor = song.id;
  const tr = d.translations?.[0];
  const content = d.available ? html`
      <div class="lyrics-tools">
        <span class="tag">${d.language_name}${d.synced ? " · synced" : ""}</span>
        ${tr ? html`<button class="chip ${showTr ? "active" : ""}" data-action="lyrics-tr" aria-pressed="${showTr}">Translation (${tr.language.toUpperCase()})</button>` : ""}
      </div>
      ${d.lines.map((l, i) => html`<p class="ly-line" data-t="${l.t ?? ""}" lang="${d.language}" dir="auto">${l.text}${showTr && tr?.lines[i]?.text ? html`<span class="tr" lang="${tr.language}">${tr.lines[i].text}</span>` : ""}</p>`)}
      <div class="lyrics-src">Lyrics licensed from ${d.source || "the rights holder"}</div>`
    : html`<div class="lyrics-empty">${d.reason || "Lyrics aren't available for this song."}</div>`;
  targets.forEach((p) => { render(p, content); p.dataset.song = song.id; p.classList.toggle("unsynced", d.available && !d.synced); });
  tick(true);
}

function tick(jump = false) {
  const t = player.songTime;
  panels().forEach((p) => {
    const lines = $$(".ly-line", p);
    if (!lines.length || p.classList.contains("unsynced")) return;
    let active = -1;
    lines.forEach((l, i) => { const lt = parseFloat(l.dataset.t); if (!isNaN(lt) && lt <= t + 0.15) active = i; });
    lines.forEach((l, i) => { l.classList.toggle("on", i === active); l.classList.toggle("past", i < active); });
    const el = lines[active];
    if (el && (jump || (el.dataset.was !== "1" && Date.now() > userScrollUntil))) {
      lines.forEach((l) => (l.dataset.was = ""));
      el.dataset.was = "1";
      const top = el.offsetTop - p.clientHeight * 0.35;
      p.scrollTo({ top, behavior: jump ? "auto" : "smooth" });
    }
  });
}

player.on("time", () => tick());
player.on("change", () => { loadedFor = null; refreshLyrics(); });

document.addEventListener("click", (e) => {
  const line = e.target.closest(".ly-line");
  if (line && line.dataset.t !== "") { player.seekSong(parseFloat(line.dataset.t)); return; }
  if (e.target.closest("[data-action=lyrics-tr]")) {
    showTr = !showTr; localStorage.setItem("rf_lyr_tr", showTr ? "1" : "0"); refreshLyrics(true);
  }
});
["wheel", "touchmove"].forEach((ev) => document.addEventListener(ev, (e) => { if (e.target.closest?.(".lyrics-panel")) userScrollUntil = Date.now() + 4000; }, { passive: true }));

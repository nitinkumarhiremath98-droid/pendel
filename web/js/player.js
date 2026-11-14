import { session, get, post } from "./api.js";
import { $, $$, esc, fmtTime, toast, setRangeFill, songs as songRegistry } from "./ui.js";

const audio = document.getElementById("audio");

// ------------------------------------------------------------------ playback engines
// PENDEL songs and Apple previews play in <audio>. Full world-catalogue songs play in a visible
// YouTube embed (YouTube's terms require the player to be shown). `media` hides the difference.
const yt = { player: null, ready: null, active: false, state: -1, timer: null };
export const media = {
  get engine() { return yt.active ? "youtube" : "audio"; },
  get currentTime() { return yt.active ? (yt.player?.getCurrentTime?.() || 0) : audio.currentTime; },
  set currentTime(v) { if (yt.active) yt.player?.seekTo?.(v, true); else audio.currentTime = v; },
  get paused() { return yt.active ? !(yt.state === 1 || yt.state === 3) : audio.paused; },
  get ended() { return yt.active ? yt.state === 0 : audio.ended; },
  play() { if (yt.active) { yt.player?.playVideo?.(); return Promise.resolve(); } return audio.play(); },
  pause() { if (yt.active) yt.player?.pauseVideo?.(); else audio.pause(); },
};
function ensureYT() {
  if (window.YT?.Player) return Promise.resolve(true);
  if (!yt.ready) {
    yt.ready = new Promise((res) => {
      const prev = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { prev?.(); res(true); };
      const s = document.createElement("script");
      s.src = "https://www.youtube.com/iframe_api";
      s.onerror = () => { yt.ready = null; res(false); };
      document.head.append(s);
    });
  }
  return yt.ready;
}
function showDock(on) { const d = document.getElementById("yt-dock"); if (d) d.hidden = !on; document.body.classList.toggle("has-video", on); }
function stopYT() {
  if (!yt.active) return;
  yt.active = false; clearInterval(yt.timer);
  try { yt.player?.stopVideo?.(); } catch { /* ignore */ }
  showDock(false);
}
async function ytLoad(stream, autoplay) {
  audio.pause(); audio.removeAttribute("src");
  yt.active = true; yt.state = -1; showDock(true);
  if (!(await ensureYT())) { toast("YouTube couldn't load. Check your connection.", { error: true }); stopYT(); return; }
  const opts = { videoId: stream.video_id, startSeconds: Math.floor(stream.start || 0) };
  if (stream.end && stream.end > (stream.start || 0) && player.current?.mode === "highlight") opts.endSeconds = Math.ceil(stream.end);
  const vol = +(localStorage.getItem("rf_vol") ?? 80);
  if (!yt.player) {
    await new Promise((res) => {
      yt.player = new window.YT.Player("yt-frame", {
        width: "100%", height: "100%", videoId: opts.videoId,
        playerVars: { autoplay: autoplay ? 1 : 0, playsinline: 1, rel: 0, start: opts.startSeconds, ...(opts.endSeconds ? { end: opts.endSeconds } : {}), origin: location.origin },
        events: {
          onReady: () => { yt.player.setVolume(vol); res(); },
          onStateChange: (e) => { yt.state = e.data; syncUI(); emit("state"); if (e.data === 0) onEnded(); },
          onError: () => { toast("This video can't be played here. Try “Open on YouTube”.", { error: true }); },
        },
      });
    });
  } else if (autoplay) yt.player.loadVideoById(opts); else yt.player.cueVideoById(opts);
  clearInterval(yt.timer);
  yt.timer = setInterval(() => { if (yt.active && yt.state === 1) onTime(media.currentTime); }, 250);
}
const listeners = {};
const on = (ev, fn) => ((listeners[ev] ||= new Set()).add(fn), () => listeners[ev].delete(fn));
const emit = (ev, d) => listeners[ev]?.forEach((fn) => fn(d));

export const player = {
  on, audio,
  queue: [], index: -1, original: null, shuffle: false, repeat: "off",
  current: null, // { song, mode: 'full'|'highlight', stream, start, end }
  listened: 0, lastT: null, loadToken: 0, retried: false,

  get song() { return this.current?.song || null; },
  get playing() { return !media.paused && !media.ended; },
  get pos() { return this.current ? Math.max(0, media.currentTime - this.current.start) : 0; },
  get length() { return this.current ? Math.max(0.01, this.current.end - this.current.start) : 0; },
  /** position within the original song (used for lyric sync, even when playing a preview cut) */
  get songTime() { return this.current ? media.currentTime + (this.current.stream?.song_offset || 0) : 0; },
  sessionMode: null, // 'full' | 'highlights' while a mood session is playing

  async play(song, { queue = null, index = 0, mode = "full", autoplay = true, session = null } = {}) {
    this.sessionMode = session;
    if (queue) { this.queue = queue.slice(); this.index = index; this.original = null; if (this.shuffle) this.applyShuffle(); }
    else if (!this.queue.length || this.queue[this.index]?.id !== song.id) { this.queue = [song]; this.index = 0; }
    await this.load(song, mode, autoplay);
  },

  async load(song, mode, autoplay = true) {
    this.flushHistory();
    const token = ++this.loadToken;
    if (mode === "full" && !session.premium && await maybeShowAd()) { if (token !== this.loadToken) return; }
    let stream;
    try {
      stream = await get(`/api/stream/${song.id}?kind=${mode === "highlight" ? "highlight" : "full"}`, { quiet: true });
    } catch (e) {
      toast(e.message, { error: true });
      if (e.status === 451 && this.queue.length > 1) return this.next(true);
      return;
    }
    if (token !== this.loadToken) return;
    if (stream.mode === "restricted") {
      if (!stream.preview) { toast(stream.message, { error: true }); return; }
      if (stream.reason === "external_full") toast(stream.message, { action: { href: stream.links.youtube, label: "Open on YouTube", external: true }, ms: 6000 });
      else toast("This full song is for Premium listeners. Playing its highlight.", { action: { href: "/premium", label: "Go Premium" }, ms: 5000 });
      stream = stream.preview; mode = "highlight";
    }
    this.current = { song, mode, stream, start: stream.start, end: stream.end, ended: false };
    this.listened = 0; this.lastT = null; this.retried = false;
    if (stream.mode === "youtube") { ytLoad(stream, autoplay); }
    else {
      stopYT();
      audio.src = stream.url;
      audio.dataset.seekTo = String(stream.start);
    }
    if (mode === "full") countTrackForAds();
    updateMediaSession(song);
    emit("change", this.current);
    if (autoplay) this.resume(); else syncUI();
  },

  resume() {
    if (!this.current) return;
    const p = media.play();
    if (p) p.catch((e) => { if (e.name === "NotAllowedError") { emit("blocked"); syncUI(); } });
  },
  pause() { media.pause(); },
  toggle() { if (!this.current) return; this.playing ? this.pause() : this.resume(); },
  seekFrac(f) { if (this.current) media.currentTime = this.current.start + Math.max(0, Math.min(0.999, f)) * this.length; },
  seekBy(d) { if (this.current) media.currentTime = Math.max(this.current.start, Math.min(this.current.end - 0.2, media.currentTime + d)); },
  seekSong(t) { // seek using original-song time (lyrics click)
    if (!this.current) return;
    const local = t - (this.current.stream?.song_offset || 0);
    if (local >= this.current.start && local < this.current.end) media.currentTime = local;
  },

  async next(auto = false) {
    if (!this.queue.length) return;
    if (!auto && this.current?.mode === "full" && !(await skipAllowed())) return;
    let i = this.index + 1;
    if (i >= this.queue.length) {
      if (this.repeat === "all") i = 0;
      else { this.pause(); if (auto) { media.currentTime = this.current.start; emit("ended-queue"); } return; }
    }
    this.index = i;
    await this.load(this.queue[i], this.sessionMode === "highlights" || (this.current?.mode === "highlight" && this.hlQueue) ? "highlight" : "full");
  },
  async prev() {
    if (this.current && this.pos > 3) { media.currentTime = this.current.start; return; }
    if (this.index > 0) { this.index -= 1; await this.load(this.queue[this.index], "full"); }
    else if (this.current) media.currentTime = this.current.start;
  },
  jump(i) { if (i >= 0 && i < this.queue.length) { this.index = i; this.load(this.queue[i], "full"); } },

  playNext(song) {
    if (!this.current) return this.play(song, { queue: [song] });
    this.queue.splice(this.index + 1, 0, song); emit("queue"); toast(`“${song.title}” will play next`);
  },
  addToQueue(song) {
    if (!this.current) return this.play(song, { queue: [song] });
    this.queue.push(song); emit("queue"); toast(`Added “${song.title}” to the queue`);
  },
  removeAt(i) { if (i === this.index) return; this.queue.splice(i, 1); if (i < this.index) this.index--; emit("queue"); },
  move(i, d) {
    const j = i + d;
    if (i <= this.index || j <= this.index || j >= this.queue.length) return;
    [this.queue[i], this.queue[j]] = [this.queue[j], this.queue[i]]; emit("queue");
  },
  clearUpcoming() { this.queue.splice(this.index + 1); emit("queue"); },

  toggleShuffle() {
    this.shuffle = !this.shuffle;
    if (this.shuffle) this.applyShuffle();
    else if (this.original) { const cur = this.queue[this.index]; this.queue = this.original; this.index = Math.max(0, this.queue.findIndex((s) => s.id === cur?.id)); this.original = null; }
    emit("queue"); syncUI();
    toast(this.shuffle ? "Shuffle on" : "Shuffle off", { ms: 1500 });
  },
  applyShuffle() {
    this.original = this.queue.slice();
    const head = this.queue.slice(0, this.index + 1), rest = this.queue.slice(this.index + 1);
    for (let i = rest.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [rest[i], rest[j]] = [rest[j], rest[i]]; }
    this.queue = head.concat(rest);
  },
  cycleRepeat() {
    this.repeat = { off: "all", all: "one", one: "off" }[this.repeat];
    syncUI(); toast({ off: "Repeat off", all: "Repeating queue", one: "Repeating this song" }[this.repeat], { ms: 1500 });
  },

  flushHistory() {
    const c = this.current;
    if (!c || !session.user || this.listened < 5) return;
    const secs = Math.round(this.listened);
    this.listened = 0;
    post("/api/me/history", { song_id: c.song.id, seconds: secs, context: c.mode, completed: this.pos >= this.length - 2 }, { quiet: true }).catch(() => {});
  },

  setFavourite(id, value) {
    if (this.current?.song.id === id) this.current.song.is_favourite = value;
    this.queue.forEach((s) => { if (s.id === id) s.is_favourite = value; });
    syncUI();
  },
};

// ------------------------------------------------------------------ audio events
audio.addEventListener("loadedmetadata", () => {
  const t = parseFloat(audio.dataset.seekTo || "0");
  if (t > 0) { try { audio.currentTime = t; } catch { /* ignore */ } }
  audio.dataset.seekTo = "";
  syncUI();
});
audio.addEventListener("timeupdate", () => { if (!yt.active) onTime(audio.currentTime); });
function onTime(t) {
  const c = player.current;
  if (!c) return;
  if (player.lastT !== null && t > player.lastT && t - player.lastT < 2) player.listened += t - player.lastT;
  player.lastT = t;
  if (t >= c.end - 0.08 && c.mode === "highlight") { onEnded(); return; }
  syncTime();
  emit("time", t);
}
audio.addEventListener("ended", () => { if (!yt.active) onEnded(); });
audio.addEventListener("play", () => { syncUI(); emit("state"); });
audio.addEventListener("pause", () => { syncUI(); emit("state"); });
audio.addEventListener("waiting", () => document.body.classList.add("buffering"));
audio.addEventListener("playing", () => document.body.classList.remove("buffering"));
audio.addEventListener("error", async () => {
  if (yt.active || !audio.getAttribute("src")) return;
  const c = player.current;
  if (!c || player.retried) { if (c) toast("This track couldn't be played. Skipping…", { error: true }); if (c && player.queue.length > 1) player.next(true); return; }
  // signed URLs expire: fetch a fresh one and resume where we were
  player.retried = true;
  const at = audio.currentTime;
  try {
    const s = await get(`/api/stream/${c.song.id}?kind=${c.mode === "highlight" ? "highlight" : "full"}`, { quiet: true });
    const fresh = s.mode === "restricted" ? s.preview : s;
    c.stream = fresh; audio.src = fresh.url; audio.dataset.seekTo = String(Math.max(at, fresh.start)); player.resume();
  } catch (e) { toast(e.message, { error: true }); }
});

function onEnded() {
  const c = player.current;
  if (!c || c.ended) return;
  c.ended = true;
  player.flushHistory();
  if (c.mode === "highlight") {
    media.pause();
    media.currentTime = c.start;
    if (player.sessionMode) { player.next(true); return; }
    if (player.hlQueue) emit("highlight-ended", c.song);
    else emit("ended", c.song);
    syncUI();
    return;
  }
  if (player.repeat === "one") { c.ended = false; media.currentTime = c.start; player.resume(); return; }
  player.next(true);
}

// ------------------------------------------------------------------ free tier: skips + ads
async function skipAllowed() {
  if (session.premium) return true;
  const limit = session.config?.free_skips_per_hour ?? 6;
  if (session.user) {
    try {
      const r = await post("/api/me/skip", {}, { quiet: true });
      if (!r.allowed) { toast(`You've used your ${limit} skips this hour. Unlimited skips come with Premium.`, { action: { href: "/premium", label: "Go Premium" }, ms: 5000 }); return false; }
      return true;
    } catch { return true; }
  }
  const now = Date.now();
  const list = JSON.parse(localStorage.getItem("rf_skips") || "[]").filter((t) => now - t < 3600e3);
  if (list.length >= limit) { toast(`Free listening includes ${limit} skips per hour.`, { action: { href: "/premium", label: "Go Premium" }, ms: 5000 }); return false; }
  list.push(now); localStorage.setItem("rf_skips", JSON.stringify(list));
  return true;
}
function countTrackForAds() {
  if (session.premium) return;
  sessionStorage.setItem("rf_tracks", String(+(sessionStorage.getItem("rf_tracks") || 0) + 1));
}
function maybeShowAd() {
  const every = session.config?.ad_every_n_tracks || 0;
  const n = +(sessionStorage.getItem("rf_tracks") || 0);
  if (!every || n === 0 || n % every !== 0 || sessionStorage.getItem("rf_ad_at") === String(n)) return Promise.resolve(false);
  sessionStorage.setItem("rf_ad_at", String(n));
  media.pause();
  const ov = $("#ad-overlay"), cnt = $(".ad-count b", ov);
  let left = session.config?.ad_duration_seconds || 5;
  cnt.textContent = left; ov.hidden = false;
  return new Promise((resolve) => {
    const t = setInterval(() => {
      left -= 1; cnt.textContent = left;
      if (left <= 0) { clearInterval(t); ov.hidden = true; resolve(true); }
    }, 1000);
    ov.querySelector("[data-action=ad-cta]").onclick = () => { clearInterval(t); ov.hidden = true; resolve(true); };
  });
}

// ------------------------------------------------------------------ media session (lock screen / headset controls)
function updateMediaSession(song) {
  if (!("mediaSession" in navigator)) return;
  navigator.mediaSession.metadata = new MediaMetadata({
    title: song.title, artist: song.artist.name, album: song.album?.title || "",
    artwork: [{ src: new URL(song.cover_url, location.href).href, sizes: "600x600", type: song.cover_url.endsWith(".svg") ? "image/svg+xml" : "image/jpeg" }],
  });
}
if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  ms.setActionHandler("play", () => player.resume());
  ms.setActionHandler("pause", () => player.pause());
  ms.setActionHandler("nexttrack", () => player.next());
  ms.setActionHandler("previoustrack", () => player.prev());
  ms.setActionHandler("seekbackward", () => player.seekBy(-10));
  ms.setActionHandler("seekforward", () => player.seekBy(10));
  try { ms.setActionHandler("seekto", (d) => { if (player.current) media.currentTime = player.current.start + d.seekTime; }); } catch { /* older browsers */ }
}

// ------------------------------------------------------------------ UI binding
const bar = $("#player"), fp = $("#fullplayer");
let seeking = false;

function syncTime() {
  const c = player.current;
  if (!c) return;
  const pos = player.pos, len = player.length;
  if (!seeking) $$(".seek").forEach((r) => { r.value = Math.round((1000 * pos) / len); setRangeFill(r); });
  $$(".t-cur").forEach((e) => (e.textContent = fmtTime(pos)));
  $$(".t-dur").forEach((e) => (e.textContent = fmtTime(len)));
  $(".pl-progress-mini span").style.width = `${(100 * pos) / len}%`;
  if ("mediaSession" in navigator && navigator.mediaSession.setPositionState && isFinite(len)) {
    try { navigator.mediaSession.setPositionState({ duration: len, position: Math.min(pos, len), playbackRate: 1 }); } catch { /* ignore */ }
  }
}

export function syncUI() {
  const c = player.current;
  bar.hidden = !c;
  document.body.classList.toggle("has-player", !!c);
  if (!c) return;
  const s = c.song, playing = player.playing;
  $(".pl-art", bar).src = s.cover_url;
  $(".pl-title", bar).textContent = s.title;
  $(".pl-artist", bar).textContent = s.artist.name;
  $(".badge-hl", bar).hidden = c.mode !== "highlight";
  $(".fp-art", fp).src = s.cover_url;
  $(".fp-bg", fp).style.backgroundImage = `url("${s.cover_url}")`;
  $(".fp-title", fp).textContent = s.title;
  const fa = $(".fp-artist", fp); fa.textContent = s.artist.name; fa.href = `/artist/${s.artist.slug}`;
  $(".fp-context", fp).textContent = c.mode === "highlight" ? "Song highlight" : "Now playing";
  $(".fp-chips", fp).innerHTML = [s.language.name, ...s.categories.slice(0, 3).map((x) => x.name), c.mode === "highlight" ? "20–30s highlight" : null]
    .filter(Boolean).map((t) => `<span class="tag">${esc(t)}</span>`).join("");
  $$("[data-action=toggle-play]").forEach((b) => {
    b.querySelector("use").setAttribute("href", `/img/icons.svg#${playing ? "pause" : "play"}`);
    b.setAttribute("aria-label", playing ? "Pause" : "Play");
  });
  $$(".pl-fav").forEach((b) => {
    b.classList.toggle("fav-on", !!s.is_favourite);
    b.querySelector("use").setAttribute("href", `/img/icons.svg#${s.is_favourite ? "heart-fill" : "heart"}`);
    b.setAttribute("aria-label", s.is_favourite ? "Remove from favourites" : "Add to favourites");
  });
  $$("[data-action=shuffle]").forEach((b) => b.setAttribute("aria-pressed", String(player.shuffle)));
  $$("[data-action=repeat]").forEach((b) => {
    b.setAttribute("aria-pressed", String(player.repeat !== "off"));
    b.setAttribute("aria-label", `Repeat: ${player.repeat}`);
    b.querySelector("use").setAttribute("href", `/img/icons.svg#${player.repeat === "one" ? "repeat-one" : "repeat"}`);
  });
  // mark playing rows / cards
  $$(".row.playing").forEach((r) => r.classList.remove("playing"));
  $$(`[data-song-row="${s.id}"]`).forEach((r) => r.classList.add("playing"));
  syncTime();
}
player.on("change", syncUI);

$$(".seek").forEach((r) => {
  r.addEventListener("input", () => { seeking = true; setRangeFill(r); $$(".t-cur").forEach((e) => (e.textContent = fmtTime((r.value / 1000) * player.length))); });
  r.addEventListener("change", () => { player.seekFrac(r.value / 1000); seeking = false; });
});
const savedVol = +(localStorage.getItem("rf_vol") ?? 80);
audio.volume = savedVol / 100;
$$(".volume").forEach((r) => {
  r.value = savedVol; setRangeFill(r);
  r.addEventListener("input", () => {
    audio.volume = r.value / 100; audio.muted = false; localStorage.setItem("rf_vol", r.value);
    try { yt.player?.setVolume?.(+r.value); yt.player?.unMute?.(); } catch { /* not ready */ }
    $$(".volume").forEach((o) => { o.value = r.value; setRangeFill(o); });
    updateMuteIcon();
  });
});
export function setMuted(m) { audio.muted = m; try { m ? yt.player?.mute?.() : yt.player?.unMute?.(); } catch { /* not ready */ } updateMuteIcon(); }
export function updateMuteIcon() {
  $$("[data-action=mute] use").forEach((u) => u.setAttribute("href", `/img/icons.svg#${audio.muted || audio.volume === 0 ? "mute" : "volume"}`));
}

// keyboard shortcuts: space = play/pause, shift+arrows = prev/next, arrows = seek
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (document.body.classList.contains("route-highlights") && ["ArrowUp", "ArrowDown"].includes(e.key)) return;
  if (e.code === "Space") { e.preventDefault(); player.toggle(); }
  else if (e.key === "ArrowRight") e.shiftKey ? player.next() : player.seekBy(5);
  else if (e.key === "ArrowLeft") e.shiftKey ? player.prev() : player.seekBy(-5);
});

window.addEventListener("pagehide", () => player.flushHistory());
export { songRegistry };

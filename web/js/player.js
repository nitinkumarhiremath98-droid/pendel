import { session, get, post } from "./api.js";
import { $, $$, esc, fmtTime, toast, setRangeFill, songs as songRegistry } from "./ui.js";

// PENDEL's own audio player: songs with a direct audio file, PENDEL uploads and previews play here, so they keep
// playing with the screen off and show lock-screen controls. Only songs whose sole source is YouTube use the YouTube player.
const audio = document.getElementById("audio-player");

// ------------------------------------------------------------------ playback engines
// PENDEL songs and Apple previews play in <audio>. Full world-catalogue songs play in a visible
// YouTube embed (YouTube's terms require the player to be shown). `media` hides the difference.
const yt = { player: null, ready: null, active: false, state: -1, timer: null, videoId: null, errors: 0 };
// YT.PlayerState values, usable before the API script has loaded
const YTS = { UNSTARTED: -1, ENDED: 0, PLAYING: 1, PAUSED: 2, BUFFERING: 3, CUED: 5 };
const ytState = (name) => window.YT?.PlayerState?.[name] ?? YTS[name];
export const media = {
  get engine() { return yt.active ? "youtube" : "audio"; },
  get currentTime() { return yt.active ? (yt.player?.getCurrentTime?.() || 0) : audio.currentTime; },
  set currentTime(v) { if (yt.active) yt.player?.seekTo?.(v, true); else audio.currentTime = v; },
  get paused() { return yt.active ? !(yt.state === ytState("PLAYING") || yt.state === ytState("BUFFERING")) : audio.paused; },
  get ended() { return yt.active ? yt.state === ytState("ENDED") : audio.ended; },
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
// The YouTube player is docked as a compact square above the player bar. YouTube requires an embedded
// player to stay visible and at least 200x200 px, so "compact" is exactly that minimum; the bar keeps a
// 64x64 thumbnail like an audio app. The listener can switch sides or make it bigger.
const dock = document.getElementById("yt-dock");
const dockPrefs = () => { try { return JSON.parse(localStorage.getItem("rf_dock") || "{}"); } catch { return {}; } };
function applyDock() {
  if (!dock) return;
  const p = dockPrefs();
  dock.classList.toggle("left", p.side === "left");
  dock.classList.toggle("wide", p.size === "wide");
}
dock?.addEventListener("click", (e) => {
  const b = e.target.closest("[data-dock]");
  if (!b) return;
  const p = dockPrefs();
  if (b.dataset.dock === "side") p.side = p.side === "left" ? "right" : "left";
  if (b.dataset.dock === "size") p.size = p.size === "wide" ? "compact" : "wide";
  try { localStorage.setItem("rf_dock", JSON.stringify(p)); } catch { /* private mode */ }
  applyDock();
});
applyDock();
function showDock(on) { if (dock) dock.hidden = !on; document.body.classList.toggle("has-video", on); }
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
  yt.videoId = stream.video_id;
  if (stream.end && stream.end > (stream.start || 0) && player.current?.mode === "highlight") opts.endSeconds = Math.ceil(stream.end);
  const vol = +(localStorage.getItem("rf_vol") ?? 80);
  if (!yt.player) {
    await new Promise((res) => {
      yt.player = new window.YT.Player("yt-frame", {
        width: "100%", height: "100%", videoId: opts.videoId,
        // Allowed, documented parameters only. Controls, the YouTube logo/link and fullscreen stay: hiding the
        // player's standard features, covering it, or blocking taps on it is against YouTube's policies.
        // Quality: YouTube picks it from the player size, so the 200x200 dock already streams a low resolution
        // (setPlaybackQuality is deprecated and ignored).
        playerVars: { autoplay: autoplay ? 1 : 0, playsinline: 1, rel: 0, iv_load_policy: 3, disablekb: 1, start: opts.startSeconds,
                      ...(opts.endSeconds ? { end: opts.endSeconds } : {}), origin: location.origin },
        events: {
          onReady: () => { yt.player.setVolume(vol); res(); },
          onStateChange: onYTState,
          onError: onYTError,
        },
      });
    });
  } else if (autoplay) yt.player.loadVideoById(opts); else yt.player.cueVideoById(opts);
  clearInterval(yt.timer);
  yt.timer = setInterval(ytTick, 250);
}
function currentVideoId() { try { return yt.player?.getVideoData?.()?.video_id || yt.videoId; } catch { return yt.videoId; } }
function onYTState(e) {
  yt.state = e.data;
  if (e.data === ytState("PAUSED") && document.visibilityState === "hidden" && !player.userPaused) pausedByBrowser = true;
  if (e.data === ytState("PLAYING")) yt.errors = 0;
  syncUI(); emit("state");
  // auto-advance: the video finished. Ignore a late ENDED from a video we already moved away from.
  if (e.data === ytState("ENDED") && yt.active && currentVideoId() === player.current?.stream?.video_id) onEnded();
}
function onYTError(e) {
  // 100 = removed, 101/150 = owner disabled embedding, 2/5 = bad id / HTML5 error: skip instead of stopping the music
  yt.errors += 1;
  const c = player.current;
  toast("This video can't be played here. Skipping to the next song…", c?.stream?.links?.youtube
    ? { error: true, action: { href: c.stream.links.youtube, label: "Open on YouTube", external: true } } : { error: true });
  if (yt.errors > 5) { yt.errors = 0; return; } // many in a row: probably offline, don't spin through the queue
  if (c) { c.ended = true; setTimeout(() => { if (player.current === c) player.next(true); }, 1200); }
  void e;
}
function ytTick() {
  if (!yt.active) return;
  if (yt.state === ytState("PLAYING")) onTime(media.currentTime);
  // safety net: some browsers (background tabs, battery saver) drop the ENDED event. If the video sits at its end, advance.
  const c = player.current;
  if (c && !c.ended && c.mode === "full" && yt.state !== ytState("PLAYING") && yt.state !== ytState("BUFFERING")) {
    let dur = 0, t = 0;
    try { dur = yt.player?.getDuration?.() || 0; t = yt.player?.getCurrentTime?.() || 0; } catch { /* not ready */ }
    if (dur > 30 && t >= dur - 1.2 && yt.state !== ytState("CUED") && yt.state !== ytState("UNSTARTED")) onEnded();
  }
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
  autoplay: localStorage.getItem("rf_autoplay") !== "0", // keep playing similar songs when the queue ends

  setAutoplay(on) { this.autoplay = on; try { localStorage.setItem("rf_autoplay", on ? "1" : "0"); } catch { /* ignore */ } syncUI(); },
  async extendQueue() {
    const seed = this.current?.song;
    if (!seed) return false;
    try {
      const exclude = this.queue.slice(-150).map((s) => s.id).join(",");
      const d = await get(`/api/songs/${seed.id}/up-next?exclude=${encodeURIComponent(exclude)}`, { quiet: true });
      const fresh = (d.songs || []).filter((s) => !this.queue.some((q) => q.id === s.id));
      if (!fresh.length) return false;
      fresh.forEach((s) => songRegistry?.set?.(s.id, s));
      this.queue.push(...fresh);
      if (this.original) this.original.push(...fresh);
      emit("queue");
      return true;
    } catch { return false; }
  },

  async play(song, { queue = null, index = 0, mode = "full", autoplay = true, session = null } = {}) {
    this.sessionMode = session;
    if (queue) { this.queue = queue.slice(); this.index = index; this.original = null; if (this.shuffle) this.applyShuffle(); }
    else if (!this.queue.length || this.queue[this.index]?.id !== song.id) { this.queue = [song]; this.index = 0; }
    await this.load(song, mode, autoplay);
  },

  async load(song, mode, autoplay = true) {
    this.flushHistory();
    const requested = mode;
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
    // `requested` remembers that the listener asked for the full song even if only a preview could play,
    // so the queue keeps moving when that preview ends
    this.current = { song, mode, requested, stream, start: stream.start, end: stream.end, ended: false };
    this.listened = 0; this.lastT = null; this.retried = false;
    if (stream.mode === "youtube") {
      // loadVideoById starts playback by itself; calling playVideo() first would briefly restart the previous video
      await ytLoad(stream, autoplay);
      if (token !== this.loadToken) return;
    } else {
      stopYT();
      if (!canPlay(stream.format)) {
        toast(`This browser can't play ${stream.format} audio. Skipping…`, { error: true });
        this.current.ended = true;
        setTimeout(() => this.next(true), 800);
        return;
      }
      audio.src = stream.url;
      audio.dataset.seekTo = String(stream.start);
    }
    if (mode === "full") countTrackForAds();
    updateMediaSession(song);
    emit("change", this.current);
    if (autoplay && stream.mode !== "youtube") this.resume(); else syncUI();
  },

  resume() {
    if (!this.current) return;
    this.userPaused = false;
    const p = media.play();
    if (p) p.catch((e) => { if (e.name === "NotAllowedError") { emit("blocked"); syncUI(); } });
  },
  userPaused: false,
  pause() { this.userPaused = true; media.pause(); },
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
      else if (this.autoplay && !this.sessionMode && this.current?.requested === "full" && await this.extendQueue()) i = this.index + 1;
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
  addAllToQueue(list) {
    const fresh = list.filter((s) => s && !this.queue.slice(this.index + 1).some((q) => q.id === s.id));
    if (!fresh.length) return;
    if (!this.current) return this.play(fresh[0], { queue: fresh, index: 0 });
    this.queue.push(...fresh); if (this.original) this.original.push(...fresh);
    emit("queue"); toast(`Added ${fresh.length} song${fresh.length === 1 ? "" : "s"} to the queue`);
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
const MIME = { mp3: "audio/mpeg", m4a: "audio/mp4", aac: "audio/aac", opus: 'audio/ogg; codecs="opus"', ogg: "audio/ogg", webm: "audio/webm", flac: "audio/flac", wav: "audio/wav" };
function canPlay(fmt) { return !fmt || !MIME[fmt] || audio.canPlayType(MIME[fmt]) !== ""; }

audio.addEventListener("loadedmetadata", () => {
  // a direct audio file may arrive without a stored duration: take it from the file so the seek bar works
  const c = player.current;
  if (c && c.mode === "full" && !(c.end > c.start) && isFinite(audio.duration)) c.end = audio.duration;
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
  if (!c || player.retried) { if (c) { toast("This track couldn't be played. Skipping…", { error: true }); c.ended = true; player.next(true); } return; }
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
  if (c.mode === "highlight" && c.requested !== "full") {
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

// ------------------------------------------------------------------ media session (lock screen, notification shade, headset keys)
const APP_ICONS = [96, 128, 192, 256, 512].map((n) => ({ src: `/img/icons/icon-${n}.png`, sizes: `${n}x${n}`, type: "image/png" }));
function artworkFor(song) {
  const abs = (u) => new URL(u, location.href).href;
  const yt = song.youtube_id || (/i\.ytimg\.com\/vi\/([\w-]{11})\//.exec(song.cover_url || "") || [])[1];
  if (yt) { // YouTube thumbnails come in fixed sizes; the OS picks the closest one
    return [["default", "120x90"], ["mqdefault", "320x180"], ["hqdefault", "480x360"], ["sddefault", "640x480"]]
      .map(([f, sz]) => ({ src: `https://i.ytimg.com/vi/${yt}/${f}.jpg`, sizes: sz, type: "image/jpeg" })).concat(APP_ICONS.map((i) => ({ ...i, src: abs(i.src) })));
  }
  const cover = song.cover_url || "";
  if (!cover || cover.endsWith(".svg")) return APP_ICONS.map((i) => ({ ...i, src: abs(i.src) })); // Android can't draw SVG artwork
  const type = /\.png(\?|$)/.test(cover) ? "image/png" : "image/jpeg";
  // Apple artwork URLs carry their size (…/600x600bb.jpg): ask for each size the OS may want
  if (/\/\d+x\d+bb\.(jpg|png)/.test(cover)) {
    return [96, 128, 256, 512].map((n) => ({ src: cover.replace(/\/\d+x\d+bb\./, `/${n}x${n}bb.`), sizes: `${n}x${n}`, type }));
  }
  return ["96x96", "128x128", "256x256", "512x512"].map((sizes) => ({ src: abs(cover), sizes, type }));
}
function updateMediaSession(song) {
  if (!("mediaSession" in navigator) || !window.MediaMetadata) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: song.title, artist: song.artist?.name || "", album: song.album?.title || "PENDEL", artwork: artworkFor(song),
    });
  } catch { /* very old browsers */ }
}
function syncMediaSessionState() {
  if (!("mediaSession" in navigator)) return;
  try { navigator.mediaSession.playbackState = !player.current ? "none" : player.playing ? "playing" : "paused"; } catch { /* ignore */ }
}
if ("mediaSession" in navigator) {
  const ms = navigator.mediaSession;
  const handlers = {
    play: () => { player.resume(); },
    pause: () => { player.pause(); },
    stop: () => { player.pause(); },
    nexttrack: () => { player.next(); },
    previoustrack: () => { player.prev(); },
    seekbackward: (d) => { player.seekBy(-(d?.seekOffset || 10)); },
    seekforward: (d) => { player.seekBy(d?.seekOffset || 10); },
    seekto: (d) => { if (player.current && d && isFinite(d.seekTime)) media.currentTime = player.current.start + d.seekTime; syncTime(); },
  };
  for (const [action, fn] of Object.entries(handlers)) {
    try { ms.setActionHandler(action, fn); } catch { /* action not supported by this browser */ }
  }
}

// ------------------------------------------------------------------ backgrounding (screen lock, app switch)
// PENDEL's own songs and Apple previews play in <audio>, which mobile browsers keep playing in the
// background with the lock-screen controls above. YouTube songs follow YouTube's rules: the browser
// pauses its player when the page is hidden, and PENDEL does not work around that. When the listener
// comes back, a song that the browser (not the listener) paused continues where it stopped.
let pausedByBrowser = false;
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    pausedByBrowser = false;
    syncMediaSessionState();
    return;
  }
  if (pausedByBrowser && yt.active && player.current && !player.userPaused && yt.state === ytState("PAUSED")) {
    player.resume();
  }
  pausedByBrowser = false;
  syncUI();
});

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
  syncMediaSessionState();
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

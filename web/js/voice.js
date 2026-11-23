// "Hey Pendel": hands-free voice control with the browser's own Web Speech API.
// Nothing is recorded or sent to PENDEL's server: the browser turns speech into text, and only the song
// name you ask for goes to /api/songs/search. Chrome, Edge and Safari support it; Firefox does not.
//
// Two stages:
//   1. Passive standby: recognition runs continuously and everything is ignored until the wake phrase
//      ("Hey Pendel", "Hi Pendel", "OK Pendel", or what speech-to-text makes of it: pendle, pedal, panel …).
//   2. Active window (7 s): the music dips to 15 %, a short chime plays and the pill shows what is heard.
//      "Hey Pendel next song" in one breath runs at once; "Hey Pendel" … pause … "play Kesariya" is caught in
//      the following phrases. After the command (or 7 s of nothing) it goes back to standby and the volume returns.
// Quick commands (pause, next, forward 10 …) run from the live (interim) text; "play <song name>" waits for the
// final text so the whole name is heard.
import { get } from "./api.js";
import { player, media, duck, unduck, syncUI, getVolume, setVolume, setMuted } from "./player.js";
import { toast } from "./ui.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const PREF = "rf_voice", SPEAK = "rf_voice_speak";
const COOLDOWN_MS = 1200, RESTART_MS = 250, ACTIVE_MS = 7000, DUCK_TO = 0.15;
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const log = (...a) => console.debug("[HeyPendel]", ...a);

// ------------------------------------------------------------------ wake phrase
// What speech-to-text writes for "Pendel" most often. With "hey / heyy / hi / ok / okay / yo" in front it is accepted
// anywhere in the phrase; on its own it must open the phrase, so song lyrics that happen to contain
// "panel" or "handle" don't trigger anything.
const NAMES = "pendel|pendle|pendal|pandel|pandal|pedal|pencil|panel|panda|bundle|handle|candle|pendul|pendhel|pindle|pebble|paddle";
const WITH_GREETING = new RegExp(`\\b(?:hey+|hi+|hai|hay|ok|okay|yo)\\s*,?\\s*(?:${NAMES})\\b[\\s,.!?:;-]*`, "i");
const AT_START = new RegExp(`^\\s*(?:(?:uh|um|so|and|oh)\\s+)?(?:${NAMES})\\b[\\s,.!?:;-]*`, "i");

/** { cmd, strong } for the text after the wake phrase, or null. strong = said with "hey / hi / ok …". */
export function wakeInfo(text) {
  const t = text || "";
  const g = WITH_GREETING.exec(t);
  if (g) return { cmd: t.slice(g.index + g[0].length).trim(), strong: true };
  const b = AT_START.exec(t);
  return b ? { cmd: t.slice(b.index + b[0].length).trim(), strong: false } : null;
}
/** The command after the wake phrase ("" if only the wake phrase), or null when there is no wake phrase. */
export function afterWake(text) { return wakeInfo(text)?.cmd ?? null; }

// ------------------------------------------------------------------ command parsing
const WORD_NUM = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, fifteen: 15,
  twenty: 20, "twenty five": 25, thirty: 30, forty: 40, "forty five": 45, fifty: 50, sixty: 60, ninety: 90 };
const WORDS_LONGEST_FIRST = Object.keys(WORD_NUM).sort((a, b) => b.length - a.length);
const NUMBER = new RegExp(`\\d|\\b(${WORDS_LONGEST_FIRST.join("|")}|a minute|half a minute)\\b`);

/** Seconds named in a phrase ("10 sec", "fifteen seconds", "2 minutes", "a minute"), or the default. */
export function secondsIn(text, fallback = 10) {
  const t = ` ${text.toLowerCase()} `;
  if (/\bhalf a minute\b/.test(t)) return 30;
  let n = null;
  const d = /(\d+(?:\.\d+)?)/.exec(t);
  if (d) n = parseFloat(d[1]);
  else for (const w of WORDS_LONGEST_FIRST) if (new RegExp(`\\s${w}\\s`).test(t)) { n = WORD_NUM[w]; break; }
  if (n === null && /\ba minute\b/.test(t)) n = 1;
  if (n === null) return fallback;
  if (/\b(minutes?|mins?)\b/.test(t)) n *= 60;
  return Math.max(1, Math.min(600, n));
}

const clean = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim()
  .replace(/^(please|can you|could you|will you|would you|just|now)\s+/, "").replace(/\s+(please|now|for me)$/, "");

const GENERIC = /^(music|song|songs|it|again|the song|this|something|anything|now|this song|some music|some songs|a song|any song|something nice|my music|music please)$/;

/** Turns a command into an intent: { action, seconds?, query?, instant? }. `instant` = safe to run from the live
 *  (interim) transcript, before the listener stops speaking. Exported for tests. */
export function parseCommand(raw) {
  const c = clean(raw || "");
  if (!c) return { action: "none" };
  const hasNumber = NUMBER.test(c);
  const hasUnit = /\b(sec|secs|second|seconds|s|minute|minutes|min|mins)\b/.test(c);
  if (/\b(repeat off|stop repeat(ing)?|unloop|disable repeat|turn off repeat|repeat (is )?off|no repeat|don t repeat)\b/.test(c)) return { action: "repeat-off", instant: true };
  if (/\b(repeat|loop)\b/.test(c)) return { action: "repeat-on", instant: /\b(repeat (the|this) song|repeat on|loop (it|this|the song))\b/.test(c) };
  if (/\b(stop talking|be quiet|mute (your )?voice|voice off)\b/.test(c)) return { action: "speak-off" };
  if (/\b(talk to me|voice on|speak again)\b/.test(c)) return { action: "speak-on" };
  // volume: "volume up", "louder", "volume down", "quieter", "mute", "unmute", "volume 50 percent"
  if (/\b(unmute|un mute|sound on)\b/.test(c)) return { action: "unmute", instant: true };
  if (/^(mute|mute (it|the (song|music)|music|sound))$|\bsound off\b/.test(c)) return { action: "mute", instant: true };
  const pct = /\b(?:volume|set (?:the )?volume)\b(?: to)? (\d{1,3})(?: ?(?:percent|%))?\b/.exec(c);
  if (pct) return { action: "volume-set", value: Math.min(100, +pct[1]) / 100, instant: true };
  if (/\b(volume up|louder|increase (the )?volume|turn (it |the volume |the music )?up|raise (the )?volume|more volume)\b/.test(c)) return { action: "volume", delta: 0.2, instant: true };
  if (/\b(volume down|quieter|softer|lower (the )?volume|decrease (the )?volume|turn (it |the volume |the music )?down|less volume|too loud)\b/.test(c)) return { action: "volume", delta: -0.2, instant: true };
  // seeking runs instantly once a number is heard ("forward 10"), otherwise on the final transcript (default 10 s)
  if (/\b(rewind|backwards?)\b/.test(c) || (/\bback\b/.test(c) && (hasNumber || hasUnit)) || /^back$/.test(c)) return { action: "seek", seconds: -secondsIn(c), instant: hasNumber };
  if (/\b(forward|ahead|fast forward)\b/.test(c) || (/\bskip\b/.test(c) && hasUnit)) return { action: "seek", seconds: secondsIn(c), instant: hasNumber };
  if (/\b(next|change (the |this )?(song|track|music)|skip|another song|different song)\b/.test(c)) return { action: "next", instant: true };
  if (/\b(previous|last song|go back|play (the )?last)\b/.test(c)) return { action: "prev", instant: true };
  if (/^(pause|stop)\b|\b(pause|stop) (the )?(song|music|it|playing|this)\b|\bpause\b/.test(c)) return { action: "pause", instant: true };
  const m = /^(?:play|put on|start|i want to (?:hear|listen to)|listen to)\s+(?:the )?(?:song )?(.+)$/.exec(c);
  if (m && !GENERIC.test(m[1])) return { action: "play-song", query: m[1].replace(/\s+song$/, "") };
  // bare "play" / "play the song" / "play something" waits for the final transcript: it may still become "play <song name>".
  // It resumes the paused song, or starts popular music when nothing is loaded yet.
  if (/^(play|resume|continue|start|unpause)$/.test(c) || (m && GENERIC.test(m[1])) || /\b(resume|continue|unpause)\b/.test(c)) return { action: "resume", instant: /\b(resume|continue|unpause)\b/.test(c) };
  return { action: "unknown", text: c };
}

// ------------------------------------------------------------------ feedback: spoken replies + chime
function say(text) {
  if (store.get(SPEAK) === "0" || !window.speechSynthesis || !text) return;
  try {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = recLang || "en-IN";
    u.rate = 1.05;
    duck(0.25, 1800);
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch { /* speech output is optional */ }
}

// A short two-tone chime made with the Web Audio API (no audio file). Browsers only let an AudioContext make
// sound after a tap, so it is created / resumed on the mic-button click or the first tap on the page.
let actx = null;
function unlockAudio() {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    actx ||= new AC();
    if (actx.state === "suspended") actx.resume().catch(() => {});
  } catch { /* no Web Audio */ }
}
export function chime() {
  try {
    if (!actx || actx.state !== "running") return false;
    const t0 = actx.currentTime;
    [[784, 0], [1175, 0.11]].forEach(([f, d]) => {
      const o = actx.createOscillator(), g = actx.createGain();
      o.type = "sine"; o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t0 + d);
      g.gain.exponentialRampToValueAtTime(0.12, t0 + d + 0.015);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 0.16);
      o.connect(g).connect(actx.destination);
      o.start(t0 + d); o.stop(t0 + d + 0.18);
    });
    return true;
  } catch { return false; }
}

// ------------------------------------------------------------------ actions
/** If the browser blocks autoplay, offer a one-tap start instead of failing silently. */
function watchBlocked(title) {
  const off = player.on("blocked", () => {
    off();
    show(`🎙️ Tap to start ${title}`, "error", 8000);
    toast(`Tap to start “${title}”`, { action: { label: "Play", onClick: () => player.resume() }, ms: 8000 });
  });
  setTimeout(off, 5000);
}

async function startSong(song, queue, index) {
  watchBlocked(song.title);
  await player.play(song, { queue, index });
  syncUI();
  show(`🎙️ Playing: ${song.title}`);
  say(`Playing ${song.title}`);
  log("playing", song.title, song.audio_url ? "(audio file)" : "(YouTube / stream)");
}

async function playByName(query) {
  show(`🎙️ Searching “${query}”…`, "processing", 0);
  let songs = [];
  try { songs = (await get(`/api/songs/search?q=${encodeURIComponent(query.slice(0, 100))}&limit=10`, { quiet: true })).songs || []; }
  catch (e) { log("search failed", e); }
  log("search", query, "->", songs.length, "songs");
  if (!songs.length) {
    show(`🎙️ Couldn't find “${query}”`, "error");
    toast(`No song found for “${query}”`, { error: true });
    say("Sorry, I couldn't find that song.");
    return;
  }
  // prefer a song that streams from its own audio file (plays with the screen off), among the best matches
  const i = Math.max(0, songs.slice(0, 5).findIndex((s) => s.audio_url));
  await startSong(songs[i], songs, i);
}

/** "Play something" with nothing loaded: start the popular songs. */
async function playPopular() {
  show("🎙️ Finding something to play…", "processing", 0);
  let songs = [];
  try { songs = (await get("/api/songs?sort=popular&limit=30", { quiet: true })).songs || []; }
  catch (e) { log("popular songs failed", e); }
  if (!songs.length) { show("🎙️ Couldn't load songs", "error"); say("Sorry, I couldn't load any songs."); return; }
  const i = Math.max(0, songs.slice(0, 5).findIndex((s) => s.audio_url));
  await startSong(songs[i], songs, i);
}

/** Runs one parsed command against the player. Exported for tests. */
export async function runCommand(rawOrCmd) {
  const cmd = typeof rawOrCmd === "string" ? parseCommand(rawOrCmd) : rawOrCmd;
  const has = !!player.current;
  const needSong = () => { show("🎙️ Nothing is playing yet. Try “Hey Pendel, play …”", "error"); say("Nothing is playing yet."); };
  log("command", cmd);
  switch (cmd.action) {
    case "pause": if (!has) return needSong(); player.pause(); syncUI(); show("🎙️ Paused"); break;
    case "resume":
      if (!has) return playPopular();
      if (!player.playing) { watchBlocked(player.current.song.title); player.resume(); }
      syncUI(); show(`🎙️ Playing: ${player.current.song.title}`);
      break;
    case "next": {
      if (!has) return playPopular();
      show("🎙️ Next song…", "processing", 0);
      const moved = await player.skipToNext();
      syncUI();
      show(moved ? `🎙️ Next: ${player.current?.song?.title || "song"}` : "🎙️ No next song", moved ? "ok" : "error");
      break;
    }
    case "prev": if (!has) return needSong(); await player.prev(); syncUI(); show(`🎙️ Previous: ${player.current?.song?.title || "song"}`); break;
    case "seek": {
      if (!has) return needSong();
      const c = player.current, to = media.currentTime + cmd.seconds;
      media.currentTime = Math.max(c.start, Math.min((c.end > c.start ? c.end : Infinity) - 0.2, to));
      syncUI();
      show(`🎙️ ${cmd.seconds > 0 ? "Forward +" : "Back −"}${Math.abs(cmd.seconds)}s`);
      break;
    }
    case "repeat-on": player.repeat = "one"; syncUI(); show("🎙️ Repeat on"); say("Repeat on"); break;
    case "repeat-off": player.repeat = "off"; syncUI(); show("🎙️ Repeat off"); say("Repeat off"); break;
    case "volume": case "volume-set": {
      const v = setVolume(cmd.action === "volume" ? Math.round((getVolume() + cmd.delta) * 10) / 10 : cmd.value);
      show(`🎙️ Volume ${Math.round(v * 100)}%`);
      break;
    }
    case "mute": setMuted(true); show("🎙️ Muted"); break;
    case "unmute": setMuted(false); show(`🎙️ Volume ${Math.round(getVolume() * 100)}%`); break;
    case "speak-off": store.set(SPEAK, "0"); show("🎙️ Voice replies off"); break;
    case "speak-on": store.set(SPEAK, "1"); show("🎙️ Voice replies on"); say("Okay"); break;
    case "play-song": await playByName(cmd.query); break;
    case "none": show("🎙️ Listening for command...", "triggered", 0); break;
    default: show(`🎙️ Didn't catch that: “${cmd.text}”`, "error"); say("Sorry, I didn't catch that.");
  }
}

// ------------------------------------------------------------------ UI: mic button + status pill
let btn = null, pill = null, pillTimer = null;
function setState(state) { // off | listening | triggered | processing
  document.documentElement.dataset.voice = state;
  if (!btn) return;
  btn.dataset.state = state;
  btn.setAttribute("aria-pressed", String(state !== "off"));
  btn.setAttribute("aria-label", state === "off" ? "Turn on Hey Pendel voice control" : "Turn off Hey Pendel voice control (listening)");
  btn.title = { off: "Hey Pendel: off", listening: "Listening for “Hey Pendel”…", triggered: "Listening for a command…", processing: "Working on it…" }[state];
}
/** Shows the status pill. ms = 0 keeps it up until the next message. */
function show(text, kind = "ok", ms = 2600) {
  if (!pill) return;
  pill.textContent = text;
  pill.dataset.kind = kind;
  pill.hidden = false;
  clearTimeout(pillTimer);
  if (ms) pillTimer = setTimeout(() => { pill.hidden = true; if (enabled && !active) setState("listening"); }, ms);
}

// ------------------------------------------------------------------ the two-stage state machine
// `phrase` identifies one spoken phrase: the recognition session plus its result index.
let active = false, activeTimer = null, wakePhrase = "", donePhrase = "", lastFired = { key: "", at: 0 }, session = 0;

function activate(phrase) {
  if (!active) {
    active = true;
    duck(DUCK_TO, ACTIVE_MS);
    const rang = chime();
    setState("triggered");
    show("🎙️ Listening for command...", "triggered", 0);
    log("wake word heard", { phrase, chime: rang });
  }
  wakePhrase = phrase;
  clearTimeout(activeTimer);
  activeTimer = setTimeout(() => { if (active) { log("no command within 7 s"); deactivate(); show("🎙️ No command heard", "ok", 1500); } }, ACTIVE_MS);
}
function deactivate() {
  active = false;
  clearTimeout(activeTimer);
  unduck();
  if (enabled) setState("listening");
}
export const voiceState = () => ({ active, enabled, running });

function fire(cmd, phrase) {
  donePhrase = phrase; // later updates of this phrase (live text -> final text) are ignored
  deactivate();
  const key = cmd.action + (cmd.seconds ?? "") + (cmd.query ?? "") + (cmd.delta ?? "") + (cmd.value ?? "");
  const now = Date.now();
  if (key === lastFired.key && now - lastFired.at < COOLDOWN_MS) return false; // the same command again within 1.2 s
  lastFired = { key, at: now };
  setState("processing");
  runCommand(cmd).catch((e) => { log("command failed", e); show("🎙️ Something went wrong", "error"); })
    .finally(() => { if (enabled && !active) setState("listening"); });
  return true;
}

/** Called for every interim and final piece of speech. Exported for tests. */
export function handleTranscript(text, isFinal, resultIndex = 0) {
  const phrase = `${session}:${resultIndex}`;
  if (phrase === donePhrase) return; // this phrase already ran its command
  const w = wakeInfo(text);
  let cmd;
  if (active) {
    // in the command window: the rest of the wake phrase, or the whole of a later phrase
    cmd = w ? w.cmd : phrase !== wakePhrase ? text.trim() : "";
  } else {
    if (!w) return; // standby: music, lyrics and talk are ignored
    // a bare "panel …" / "handle …" without "hey" counts only if a real command follows, or it is said on its own
    if (!w.strong && (w.cmd ? parseCommand(w.cmd).action === "unknown" : !isFinal)) return;
    cmd = w.cmd;
    activate(phrase);
  }
  if (w && (w.strong || !w.cmd) && phrase !== wakePhrase) activate(phrase); // "Hey Pendel" said again: restart the window
  if (!cmd) return;
  show(`🎙️ Heard: ${cmd}`, "triggered", 0);
  const parsed = parseCommand(cmd);
  if (!isFinal) { if (parsed.instant) { log("live command", cmd); fire(parsed, phrase); } return; }
  if (parsed.action === "none") return;
  log("final command", cmd);
  fire(parsed, phrase);
}

// ------------------------------------------------------------------ microphone + recognition loop
let rec = null, enabled = false, running = false, recLang = "en-IN", restarts = [], autoStarting = false;
let lastEvent = 0, watchdog = null;

async function micPermission() {
  try { return (await navigator.permissions?.query({ name: "microphone" }))?.state || "prompt"; }
  catch { return "prompt"; }
}

/** Makes sure the browser has microphone permission, then lets go of the microphone at once (on computers and
 *  phones alike), so SpeechRecognition can own it. Keeping a second stream open locks the device on some
 *  Windows and Android set-ups. */
async function ensureMic() {
  if (!navigator.mediaDevices?.getUserMedia) return true;
  if (await micPermission() === "granted") return true;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    s.getTracks().forEach((t) => t.stop());
    return true;
  } catch (e) {
    log("microphone request failed", e?.name);
    if (e?.name === "NotAllowedError" || e?.name === "SecurityError") return "denied";
    if (e?.name === "NotFoundError") return "missing";
    return true; // other problems: let speech recognition try on its own
  }
}

function build() {
  const r = new Recognition();
  r.continuous = true;
  r.interimResults = true;
  r.lang = recLang;
  r.maxAlternatives = 1;
  r.onstart = () => { running = true; autoStarting = false; session += 1; lastEvent = Date.now(); log("recognition started", recLang); };
  r.onresult = (e) => {
    lastEvent = Date.now();
    for (let i = e.resultIndex; i < e.results.length; i++) handleTranscript(e.results[i][0].transcript, e.results[i].isFinal, i);
  };
  r.onerror = (e) => {
    lastEvent = Date.now();
    if (e.error !== "no-speech" && e.error !== "aborted") log("recognition error", e.error);
    if (e.error === "no-speech" || e.error === "aborted" || e.error === "network") return; // onend restarts
    if (e.error === "language-not-supported" && recLang !== "en-US") { recLang = "en-US"; rec = null; return; }
    if (e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture") {
      const wasOn = enabled;
      stop(false);
      if (wasOn && !autoStarting) toast(e.error === "audio-capture" ? "No microphone found for Hey Pendel." : "Microphone blocked. Allow it in the browser's site settings to use Hey Pendel.", { error: true });
    }
  };
  r.onend = () => {
    running = false;
    lastEvent = Date.now();
    if (enabled) scheduleRestart();
  };
  return r;
}

function scheduleRestart() {
  // keep listening: restart after 250 ms; if the browser keeps ending it straight away, wait 2 s instead
  const now = Date.now();
  restarts = restarts.filter((t) => now - t < 10000).concat(now);
  setTimeout(() => { if (enabled && !running) listen(); }, restarts.length > 8 ? 2000 : RESTART_MS);
}

function listen() {
  if (!rec) rec = build();
  try { rec.start(); } catch { /* already running */ }
}

async function start(silent = false) {
  if (!Recognition) return false;
  enabled = true;
  setState("listening");
  const mic = await ensureMic();
  if (mic === "denied" || mic === "missing") {
    const quiet = autoStarting;
    stop(false);
    if (!quiet) toast(mic === "missing" ? "No microphone found for Hey Pendel." : "Microphone blocked. Allow it in the browser's site settings to use Hey Pendel.", { error: true });
    return false;
  }
  if (!enabled) return false; // turned off while the permission prompt was open
  listen();
  // safety net for mobile Chrome, which sometimes stops listening without telling us
  clearInterval(watchdog);
  watchdog = setInterval(() => {
    if (!enabled) return;
    if (!running && Date.now() - lastEvent > 3000) listen();
    else if (running && Date.now() - lastEvent > 30000) { try { rec?.abort(); } catch { /* restart via onend */ } }
  }, 2000);
  if (!silent) show("🎙️ Listening… say “Hey Pendel, …”", "ok", 3000);
  return true;
}

function stop(savePref = true) {
  enabled = false;
  running = false;
  deactivate();
  clearInterval(watchdog);
  try { rec?.abort(); } catch { /* not started */ }
  rec = null;
  setState("off");
  if (savePref) store.set(PREF, "0");
}

export function toggleVoice() {
  if (!Recognition) {
    toast("Voice control needs Chrome, Edge or Safari. This browser doesn't support speech recognition.", { error: true });
    return;
  }
  unlockAudio();
  if (enabled) { stop(); show("🎙️ Hey Pendel off", "ok", 1500); }
  else { store.set(PREF, "1"); start(); }
}

// ------------------------------------------------------------------ set-up
async function mount() {
  btn = document.getElementById("voice-btn");
  pill = document.getElementById("voice-pill");
  if (!btn) return;
  if (!Recognition) { btn.hidden = true; return; } // Firefox etc.
  btn.hidden = false;
  btn.addEventListener("click", toggleVoice);
  document.addEventListener("pointerdown", unlockAudio, { once: true, capture: true });
  setState("off");
  // Start quietly when it was on last time, or when the microphone is already allowed and it was never turned off.
  const pref = store.get(PREF);
  if (pref === "1" || (pref === null && await micPermission() === "granted")) { autoStarting = true; start(true); }
  // most phones stop the microphone while the page is hidden: pick up again when the listener comes back
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && enabled && !running) listen();
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();

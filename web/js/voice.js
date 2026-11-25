// Voice control: tap the mic button, say one command, done.
// Mic Off (default): the microphone is not used at all. Tap the mic: it turns on (Mic On, pulsing), a live pill shows
// what you are saying, and the first command you speak ("play Kesariya", "pause", "next song", "volume up",
// "search arijit" …) runs straight away. Then the mic switches itself off. Tap again while it's on to stop.
// No wake word is needed (a leading "Hey Pendel" is ignored if you say it anyway).
// Nothing is recorded or sent to PENDEL's server: the browser's speech service turns speech into text, and only a song
// name or search goes to the API. Chrome, Edge and Safari support it; Firefox does not (the button is hidden there).
import { get } from "./api.js";
import { player, media, duck, unduck, syncUI, getVolume, setVolume, setMuted } from "./player.js";
import { toast } from "./ui.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const SPEAK = "rf_voice_speak";
const LISTEN_MS = 8000, DUCK_TO = 0.15;  // the mic stays on for at most 8 s; music dips to 15 % meanwhile
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};
const log = (...a) => console.debug("[HeyPendel]", ...a);

// ------------------------------------------------------------------ optional "Hey Pendel" prefix
// Not needed any more, but people say it out of habit: it is stripped from the command. These are the spellings
// speech-to-text produces for "Pendel".
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
  if (/\b(stop listening|turn off (hey pendel|voice control|the (mic|microphone)|listening)|mic off|go to sleep|never ?mind|cancel)\b/.test(c)) return { action: "voice-off", instant: true };
  // "search arijit singh", "search for love songs", "find kesariya", "look up ..."
  const sq = /^(?:search(?: for)?|find|look up|look for|show me)\s+(.+)$/.exec(c);
  if (sq) return { action: "search", query: sq[1] };
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
      g.gain.exponentialRampToValueAtTime(0.09, t0 + d + 0.02);
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
    show(`Tap to start ${title}`, "error", 8000);
    toast(`Tap to start “${title}”`, { action: { label: "Play", onClick: () => player.resume() }, ms: 8000 });
  });
  setTimeout(off, 5000);
}

async function startSong(song, queue, index) {
  watchBlocked(song.title);
  await player.play(song, { queue, index });
  syncUI();
  show(`Playing ${song.title}`);
  say(`Playing ${song.title}`);
  log("playing", song.title, song.audio_url ? "(audio file)" : "(YouTube / stream)");
}

async function playByName(query) {
  show(`Searching “${query}”…`, "processing", 0);
  let songs = [];
  try { songs = (await get(`/api/songs/search?q=${encodeURIComponent(query.slice(0, 100))}&limit=10`, { quiet: true })).songs || []; }
  catch (e) { log("search failed", e); }
  log("search", query, "->", songs.length, "songs");
  if (!songs.length) {
    show(`Couldn't find “${query}”`, "error");
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
  show("Finding something to play…", "processing", 0);
  let songs = [];
  try { songs = (await get("/api/songs?sort=popular&limit=30", { quiet: true })).songs || []; }
  catch (e) { log("popular songs failed", e); }
  if (!songs.length) { show("Couldn't load songs", "error"); say("Sorry, I couldn't load any songs."); return; }
  const i = Math.max(0, songs.slice(0, 5).findIndex((s) => s.audio_url));
  await startSong(songs[i], songs, i);
}

/** Runs one parsed command against the player. Exported for tests. */
export async function runCommand(rawOrCmd) {
  const cmd = typeof rawOrCmd === "string" ? parseCommand(rawOrCmd) : rawOrCmd;
  const has = !!player.current;
  const needSong = () => { show("Nothing is playing yet. Try “Hey Pendel, play …”", "error"); say("Nothing is playing yet."); };
  log("command", cmd);
  switch (cmd.action) {
    case "pause": if (!has) return needSong(); player.pause(); syncUI(); show("Paused"); break;
    case "resume":
      if (!has) return playPopular();
      if (!player.playing) { watchBlocked(player.current.song.title); player.resume(); }
      syncUI(); show(`Playing ${player.current.song.title}`);
      break;
    case "next": {
      if (!has) return playPopular();
      show("Next song…", "processing", 0);
      const moved = await player.skipToNext();
      syncUI();
      show(moved ? `Next song · ${player.current?.song?.title || ""}`.replace(/ · $/, "") : "No next song", moved ? "ok" : "error");
      break;
    }
    case "prev": if (!has) return needSong(); await player.prev(); syncUI(); show(`Previous song · ${player.current?.song?.title || ""}`.replace(/ · $/, "")); break;
    case "seek": {
      if (!has) return needSong();
      const c = player.current, to = media.currentTime + cmd.seconds;
      media.currentTime = Math.max(c.start, Math.min((c.end > c.start ? c.end : Infinity) - 0.2, to));
      syncUI();
      show(`${cmd.seconds > 0 ? "Forward +" : "Back −"}${Math.abs(cmd.seconds)}s`);
      break;
    }
    case "repeat-on": player.repeat = "one"; syncUI(); show("Repeat on"); say("Repeat on"); break;
    case "repeat-off": player.repeat = "off"; syncUI(); show("Repeat off"); say("Repeat off"); break;
    case "volume": case "volume-set": {
      const v = setVolume(cmd.action === "volume" ? Math.round((getVolume() + cmd.delta) * 10) / 10 : cmd.value);
      show(`Volume ${Math.round(v * 100)}%`);
      break;
    }
    case "mute": setMuted(true); show("Muted"); break;
    case "unmute": setMuted(false); show(`Volume ${Math.round(getVolume() * 100)}%`); break;
    case "voice-off": show("Mic off"); break;
    case "search": window.rfNavigate(`/search?q=${encodeURIComponent(cmd.query)}`); show(`Searching “${cmd.query}”`); break;
    case "speak-off": store.set(SPEAK, "0"); show("Voice replies off"); break;
    case "speak-on": store.set(SPEAK, "1"); show("Voice replies on"); say("Okay"); break;
    case "play-song": await playByName(cmd.query); break;
    case "none": show("Listening...", "triggered", 0); break;
    default: show(`Didn't catch that: “${cmd.text}”`, "error"); say("Sorry, I didn't catch that.");
  }
}

// ------------------------------------------------------------------ UI: mic button + live transcript pill
let btn = null, btnLabel = null, pill = null, pillTimer = null, pillText = null;

function setState(state) { // off | listening | processing
  document.documentElement.dataset.voice = state;
  if (!btn) return;
  const on = state === "listening";
  btn.dataset.state = state;
  btn.setAttribute("aria-pressed", String(on));
  const label = on ? "Mic On — Tap to stop" : "Mic Off — tap to speak a command";
  btn.setAttribute("aria-label", label);
  btn.title = label;
  if (btnLabel) btnLabel.textContent = on ? "Mic On" : "Mic Off";
  const use = btn.querySelector("use");
  if (use) use.setAttribute("href", `/img/icons.svg#${on ? "mic" : "mic-off"}`);
}

/** Shows the pill: the live transcript while listening, then the result. It slides away after `ms`
 *  (1.5 s for a confirmation, 2.5 s for a problem); ms = 0 keeps it up until the next message. */
function show(text, kind = "ok", ms) {
  if (!pill) return;
  if (!pillText) {
    pill.innerHTML = '<span class="vp-wave" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span><span class="vp-text"></span>';
    pillText = pill.querySelector(".vp-text");
  }
  pillText.textContent = text;
  pill.dataset.kind = kind;
  pill.classList.remove("leaving");
  pill.hidden = false;
  clearTimeout(pillTimer);
  ms ??= kind === "error" ? 2500 : 1500;
  if (ms) pillTimer = setTimeout(hidePill, ms);
}
function hidePill() {
  clearTimeout(pillTimer);
  if (!pill || pill.hidden) return;
  pill.classList.add("leaving");
  pillTimer = setTimeout(() => { pill.hidden = true; pill.classList.remove("leaving"); }, 220);
}

// ------------------------------------------------------------------ one command per tap
let rec = null, isMicEnabled = false, handled = false, heard = "", listenTimer = null, recLang = "en-IN";
export const voiceState = () => ({ isMicEnabled, handled, heard, state: document.documentElement.dataset.voice || "off" });

function build() {
  const r = new Recognition();
  r.continuous = false;      // one phrase per tap
  r.interimResults = true;   // live transcript in the pill
  r.lang = recLang;
  r.maxAlternatives = 1;
  r.onstart = () => log("mic on", recLang);
  r.onresult = (e) => {
    if (r !== rec) return;
    let text = "", final = false;
    for (let i = 0; i < e.results.length; i++) {
      if (!e.results[i]) continue;
      text += e.results[i][0].transcript;
      final = e.results[i].isFinal;
    }
    onSpeech(text, final);
  };
  r.onerror = (e) => {
    if (r !== rec || !isMicEnabled) return;
    log("recognition error", e.error);
    if (e.error === "aborted") return;
    if (e.error === "language-not-supported" && recLang !== "en-US") { recLang = "en-US"; micOff("Try again", "error"); return; }
    const msg = {
      "no-speech": "Didn't hear anything. Tap the mic and try again.",
      "not-allowed": "Microphone blocked. Allow it in the browser's site settings.",
      "service-not-allowed": "Microphone blocked. Allow it in the browser's site settings.",
      "audio-capture": "No microphone found.",
      network: "Voice needs an internet connection.",
    }[e.error] || "Voice didn't work. Tap the mic to try again.";
    micOff(msg, "error");
  };
  r.onend = () => {
    if (r !== rec) return;
    log("recognition ended", { handled, heard });
    // the browser ends the phrase by itself: use what was heard, or switch off quietly
    if (isMicEnabled && !handled) {
      if (heard) execute(parseCommand(heard));
      else micOff("Didn't hear anything. Tap the mic and try again.", "error");
    }
  };
  return r;
}

/** Live and final text of the phrase. Exported for tests. */
export function onSpeech(text, isFinal) {
  if (!isMicEnabled || handled) return;
  heard = (afterWake(text) ?? text).trim(); // "Hey Pendel, pause" still works
  if (!heard) return;
  show(`“${heard}”`, "listening", 0);
  const cmd = parseCommand(heard);
  if (cmd.action === "none") return;
  if (isFinal || cmd.instant) execute(cmd); // quick commands run from the live text; "play <name>" waits for all of it
}

function execute(cmd) {
  if (handled) return;
  handled = true;
  log("command", heard, "->", cmd);
  micOff(null);
  setState("processing");
  runCommand(cmd).catch((e) => { log("command failed", e); show("Something went wrong", "error"); })
    .finally(() => { if (!isMicEnabled) setState("off"); });
}

/** Mic ON: called from the button's click handler, so recognition starts inside the tap. */
export function micOn() {
  if (!Recognition || isMicEnabled) return false;
  unlockAudio();
  isMicEnabled = true; handled = false; heard = "";
  rec = build();
  try {
    rec.start();
  } catch (e) {
    log("could not start", e);
    micOff("Voice didn't start. Tap the mic to try again.", "error");
    return false;
  }
  setState("listening");
  show("Mic On — Tap to stop", "listening", 0);
  duck(DUCK_TO, LISTEN_MS);
  chime();
  clearTimeout(listenTimer);
  listenTimer = setTimeout(() => {
    if (!isMicEnabled) return;
    if (heard) execute(parseCommand(heard)); else micOff("Didn't hear anything. Tap the mic and try again.", "error");
  }, LISTEN_MS);
  return true;
}

/** Mic OFF: stops recognition at once and resets the UI. `message` (if any) is shown briefly. */
export function micOff(message = null, kind = "ok") {
  const was = isMicEnabled;
  isMicEnabled = false;
  clearTimeout(listenTimer);
  const r = rec;
  rec = null;            // late events from this recognition are ignored
  try { r?.abort(); } catch { /* not started */ }
  unduck();
  setState("off");
  if (message) show(message, kind);
  else if (was && !handled) hidePill();
}

export function toggleVoice() {
  if (!Recognition) {
    toast("Voice control needs Chrome, Edge or Safari. This browser doesn't support speech recognition.", { error: true });
    return;
  }
  if (isMicEnabled) micOff("Mic off");
  else micOn();
}

// ------------------------------------------------------------------ set-up
function mount() {
  btn = document.getElementById("voice-btn");
  pill = document.getElementById("voice-pill");
  try { localStorage.removeItem("rf_voice"); } catch { /* old always-on setting */ }
  if (!btn) return;
  btnLabel = btn.querySelector(".voice-label");
  if (!Recognition) { btn.hidden = true; return; } // Firefox etc.
  btn.hidden = false;
  btn.addEventListener("click", toggleVoice);
  pill?.addEventListener("click", () => { if (isMicEnabled) micOff("Mic off"); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && isMicEnabled) micOff("Mic off"); });
  // never keep the microphone on in a hidden tab
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden" && isMicEnabled) micOff(); });
  setState("off");
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();

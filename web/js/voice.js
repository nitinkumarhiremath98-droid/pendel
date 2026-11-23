// "Hey Pendel": hands-free voice control with the browser's own Web Speech API.
// Nothing is recorded or sent to PENDEL's server: the browser turns speech into text, and only the song
// name you ask for goes to /api/songs/search. Chrome, Edge and Safari support it; Firefox does not.
import { get } from "./api.js";
import { player, media, duck, syncUI } from "./player.js";
import { toast } from "./ui.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const PREF = "rf_voice", SPEAK = "rf_voice_speak";
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------ wake phrase
// "hey/hi pendel", or "pendel" alone, plus what speech-to-text often hears instead after "hey"/"hi"
const NAME = "pendel|pendle|pendal|pandel|pandal|pendul|pendhel";
const SOUNDS_LIKE = "bundle|candle|handle|pedal|pendant|spindle|kendall|vandal|pebble|panel|penal|kindle|paddle";
const WAKE = new RegExp(`\\b(?:(?:hey|hi|hay|hai|ok|okay)\\s+(?:${NAME}|${SOUNDS_LIKE})|${NAME})\\b[\\s,.!?:;-]*`, "i");

/** Returns the command after the wake phrase ("" if only the wake phrase), or null when there's no wake phrase. */
export function afterWake(text) {
  const m = WAKE.exec(text || "");
  return m ? text.slice(m.index + m[0].length).trim() : null;
}

// ------------------------------------------------------------------ command parsing
const WORD_NUM = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10, fifteen: 15,
  twenty: 20, "twenty five": 25, thirty: 30, forty: 40, "forty five": 45, fifty: 50, sixty: 60, ninety: 90 };
const WORDS_LONGEST_FIRST = Object.keys(WORD_NUM).sort((a, b) => b.length - a.length);

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

/** Turns a command into an intent: { action, seconds?, query? }. Exported for tests. */
export function parseCommand(raw) {
  const c = clean(raw || "");
  if (!c) return { action: "none" };
  const hasSeconds = /\b(sec|secs|second|seconds|s|minute|minutes|min|mins)\b/.test(c);
  if (/\b(repeat off|stop repeat(ing)?|unloop|disable repeat|turn off repeat|repeat (is )?off|no repeat|don t repeat)\b/.test(c)) return { action: "repeat-off" };
  if (/\b(repeat|loop)\b/.test(c)) return { action: "repeat-on" };
  if (/\b(stop talking|be quiet|mute (your )?voice|voice off)\b/.test(c)) return { action: "speak-off" };
  if (/\b(talk to me|voice on|speak again)\b/.test(c)) return { action: "speak-on" };
  if (/\b(rewind|backwards?)\b/.test(c) || (/\bback\b/.test(c) && hasSeconds) || /^back$/.test(c)) return { action: "seek", seconds: -secondsIn(c) };
  if (/\b(forward|ahead|fast forward)\b/.test(c) || (/\bskip\b/.test(c) && hasSeconds)) return { action: "seek", seconds: secondsIn(c) };
  if (/\b(next|change (the )?(song|track)|skip)\b/.test(c)) return { action: "next" };
  if (/\b(previous|last song|go back|play (the )?last)\b/.test(c)) return { action: "prev" };
  if (/^(pause|stop)\b|\b(pause|stop) (the )?(song|music|it|playing|this)\b|\bpause\b/.test(c)) return { action: "pause" };
  const m = /^(?:play|put on|start|i want to (?:hear|listen to)|listen to)\s+(?:the )?(?:song )?(.+)$/.exec(c);
  if (m && !/^(music|song|it|again|the song|this|something|now|this song)$/.test(m[1])) return { action: "play-song", query: m[1].replace(/\s+song$/, "") };
  if (/^(play|resume|continue|start|unpause|play (music|it|again|the song))$/.test(c) || /\b(resume|continue|unpause)\b/.test(c)) return { action: "resume" };
  return { action: "unknown", text: c };
}

// ------------------------------------------------------------------ actions
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

async function playByName(query) {
  show(`🎙️ Searching “${query}”…`, "processing");
  let songs = [];
  try { songs = (await get(`/api/songs/search?q=${encodeURIComponent(query.slice(0, 100))}&limit=10`, { quiet: true })).songs || []; }
  catch { /* offline: handled below */ }
  if (!songs.length) {
    show(`🎙️ Couldn't find “${query}”`, "error");
    toast(`No song found for “${query}”`, { error: true });
    say("Sorry, I couldn't find that song.");
    return;
  }
  // prefer a song that streams from its own audio file (plays with the screen off), among the best matches
  const i = Math.max(0, songs.slice(0, 5).findIndex((s) => s.audio_url));
  await player.play(songs[i], { queue: songs, index: i });
  show(`🎙️ Playing: ${songs[i].title}`);
  say(`Playing ${songs[i].title}`);
}

export async function runCommand(raw) {
  const cmd = parseCommand(raw);
  const has = !!player.current;
  const needSong = () => { show("🎙️ Nothing is playing yet", "error"); say("Nothing is playing yet."); };
  switch (cmd.action) {
    case "pause": if (!has) return needSong(); player.pause(); show("🎙️ Hey Pendel: Paused"); break;
    case "resume": if (!has) return needSong(); player.resume(); show("🎙️ Hey Pendel: Playing"); break;
    case "next": if (!has) return needSong(); await player.next(); show("🎙️ Hey Pendel: Next song"); break;
    case "prev": if (!has) return needSong(); player.prev(); show("🎙️ Hey Pendel: Previous song"); break;
    case "seek": {
      if (!has) return needSong();
      const c = player.current, to = media.currentTime + cmd.seconds;
      media.currentTime = Math.max(c.start, Math.min((c.end > c.start ? c.end : Infinity) - 0.2, to));
      show(`🎙️ Hey Pendel: ${cmd.seconds > 0 ? "Forward +" : "Back −"}${Math.abs(cmd.seconds)}s`);
      break;
    }
    case "repeat-on": player.repeat = "one"; syncUI(); show("🎙️ Hey Pendel: Repeat on"); say("Repeat on"); break;
    case "repeat-off": player.repeat = "off"; syncUI(); show("🎙️ Hey Pendel: Repeat off"); say("Repeat off"); break;
    case "speak-off": store.set(SPEAK, "0"); show("🎙️ Voice replies off"); break;
    case "speak-on": store.set(SPEAK, "1"); show("🎙️ Voice replies on"); say("Okay"); break;
    case "play-song": await playByName(cmd.query); break;
    case "none": show("🎙️ Yes? Say a command", "triggered"); armed = Date.now(); break;
    default: show(`🎙️ Didn't catch that: “${cmd.text}”`, "error"); say("Sorry, I didn't catch that.");
  }
}

// ------------------------------------------------------------------ UI: header button + status pill
let btn = null, pill = null, pillTimer = null;
function setState(state) { // off | listening | triggered | processing
  document.documentElement.dataset.voice = state;
  if (!btn) return;
  btn.dataset.state = state;
  btn.setAttribute("aria-pressed", String(state !== "off"));
  btn.setAttribute("aria-label", state === "off" ? "Turn on Hey Pendel voice control" : "Turn off Hey Pendel voice control (listening)");
  btn.title = state === "off" ? "Hey Pendel: off" : state === "listening" ? "Hey Pendel: listening for “Hey Pendel”" : "Hey Pendel: heard you";
}
function show(text, kind = "ok", ms = 2600) {
  if (!pill) return;
  pill.textContent = text;
  pill.dataset.kind = kind;
  pill.hidden = false;
  clearTimeout(pillTimer);
  pillTimer = setTimeout(() => { pill.hidden = true; if (enabled) setState("listening"); }, ms);
}

// ------------------------------------------------------------------ recognition loop
let rec = null, enabled = false, recLang = "en-IN", armed = 0, triggeredInUtterance = false, restarts = [], autoStarting = false;

function build() {
  const r = new Recognition();
  r.continuous = true;
  r.interimResults = true;
  r.lang = recLang;
  r.maxAlternatives = 1;
  r.onstart = () => { autoStarting = false; };
  r.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const text = e.results[i][0].transcript;
      handleTranscript(text, e.results[i].isFinal);
    }
  };
  r.onerror = (e) => {
    if (e.error === "no-speech" || e.error === "aborted" || e.error === "network") return; // onend restarts
    if (e.error === "language-not-supported" && recLang !== "en-US") { recLang = "en-US"; rec = null; return; }
    if (e.error === "not-allowed" || e.error === "service-not-allowed" || e.error === "audio-capture") {
      const wasOn = enabled;
      stop(false);
      if (wasOn && !autoStarting) toast(e.error === "audio-capture" ? "No microphone found for Hey Pendel." : "Microphone blocked. Allow it in the browser's site settings to use Hey Pendel.", { error: true });
    }
  };
  r.onend = () => {
    if (!enabled) return;
    // keep listening: restart, but back off if the browser keeps ending it straight away
    const now = Date.now();
    restarts = restarts.filter((t) => now - t < 10000).concat(now);
    setTimeout(() => { if (enabled) start(true); }, restarts.length > 8 ? 2000 : 250);
  };
  return r;
}

/** Called for every interim and final piece of speech. Exported for tests. */
export function handleTranscript(text, isFinal) {
  let cmd = afterWake(text);
  if (cmd === null && armed && Date.now() - armed < 6000 && isFinal) cmd = text; // "Hey Pendel" ... (pause) ... "next song"
  if (cmd === null) { if (isFinal) triggeredInUtterance = false; return; }
  if (!triggeredInUtterance) { // first time we hear the wake phrase in this utterance: duck the music so the mic hears the command
    triggeredInUtterance = true;
    duck(0.2, 1500);
    setState("triggered");
    show("🎙️ Hey Pendel…", "triggered", 4000);
  }
  if (!isFinal) return;
  triggeredInUtterance = false;
  armed = cmd ? 0 : Date.now();
  setState("processing");
  runCommand(cmd).catch(() => show("🎙️ Something went wrong", "error"));
}

function start(silent = false) {
  if (!Recognition) return false;
  if (!rec) rec = build();
  try { rec.start(); } catch { /* already started */ }
  enabled = true;
  setState("listening");
  if (!silent) { show("🎙️ Listening. Say “Hey Pendel, …”", "triggered", 3000); }
  return true;
}

function stop(savePref = true) {
  enabled = false;
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
  if (enabled) { stop(); show("🎙️ Hey Pendel off", "ok", 1500); }
  else { store.set(PREF, "1"); start(); }
}

// ------------------------------------------------------------------ set-up
function mount() {
  btn = document.getElementById("voice-btn");
  pill = document.getElementById("voice-pill");
  if (!btn) return;
  if (!Recognition) { btn.hidden = true; return; } // Firefox etc.
  btn.hidden = false;
  btn.addEventListener("click", toggleVoice);
  setState("off");
  // turned on last time: try again (works when the microphone permission was already granted; otherwise it stays off quietly)
  if (store.get(PREF) === "1") { autoStarting = true; start(true); }
  // a hidden page can't use the microphone in most mobile browsers: resume when the listener comes back
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && enabled) { try { rec?.start(); } catch { /* running */ } }
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();

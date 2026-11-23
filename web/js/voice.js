// "Hey Pendel": hands-free voice control with the browser's own Web Speech API.
// Nothing is recorded or sent to PENDEL's server: the browser turns speech into text, and only the song
// name you ask for goes to /api/songs/search. Chrome, Edge and Safari support it; Firefox does not.
//
// Real-time: quick commands (pause, next, forward 10 …) run as soon as they appear in the live (interim)
// transcript; "play <song name>" waits for the final transcript so the whole name is heard. A 1.2 s
// de-duplicator stops one spoken command from firing twice (interim + final).
import { get } from "./api.js";
import { player, media, duck, syncUI } from "./player.js";
import { toast } from "./ui.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const PREF = "rf_voice", SPEAK = "rf_voice_speak";
const COOLDOWN_MS = 1200, RESTART_MS = 250;
const store = {
  get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------ wake phrase
// What speech-to-text writes for "Pendel" most often. With "hey / hi / ok / okay / yo" in front it is accepted
// anywhere in the phrase; on its own it must open the phrase, so song lyrics that happen to contain
// "panel" or "handle" don't trigger anything.
const NAMES = "pendel|pendle|pendal|pandel|pandal|pedal|pencil|panel|bundle|handle|candle|pendul|pendhel|pindle|pebble|paddle";
const WITH_GREETING = new RegExp(`\\b(?:hey|hi|hai|hay|ok|okay|yo)\\s*,?\\s*(?:${NAMES})\\b[\\s,.!?:;-]*`, "i");
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
  // seeking runs instantly once a number is heard ("forward 10"), otherwise on the final transcript (default 10 s)
  if (/\b(rewind|backwards?)\b/.test(c) || (/\bback\b/.test(c) && (hasNumber || hasUnit)) || /^back$/.test(c)) return { action: "seek", seconds: -secondsIn(c), instant: hasNumber };
  if (/\b(forward|ahead|fast forward)\b/.test(c) || (/\bskip\b/.test(c) && hasUnit)) return { action: "seek", seconds: secondsIn(c), instant: hasNumber };
  if (/\b(next|change (the |this )?(song|track|music)|skip|another song|different song)\b/.test(c)) return { action: "next", instant: true };
  if (/\b(previous|last song|go back|play (the )?last)\b/.test(c)) return { action: "prev", instant: true };
  if (/^(pause|stop)\b|\b(pause|stop) (the )?(song|music|it|playing|this)\b|\bpause\b/.test(c)) return { action: "pause", instant: true };
  const m = /^(?:play|put on|start|i want to (?:hear|listen to)|listen to)\s+(?:the )?(?:song )?(.+)$/.exec(c);
  if (m && !/^(music|song|it|again|the song|this|something|now|this song)$/.test(m[1])) return { action: "play-song", query: m[1].replace(/\s+song$/, "") };
  // bare "play" waits for the final transcript: it may still become "play <song name>"
  if (/^(play|resume|continue|start|unpause|play (music|it|again|the song))$/.test(c) || /\b(resume|continue|unpause)\b/.test(c)) return { action: "resume", instant: /\b(resume|continue|unpause)\b/.test(c) };
  return { action: "unknown", text: c };
}

// ------------------------------------------------------------------ feedback: spoken replies
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

// ------------------------------------------------------------------ actions
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

/** Runs one parsed command against the player. Exported for tests. */
export async function runCommand(rawOrCmd) {
  const cmd = typeof rawOrCmd === "string" ? parseCommand(rawOrCmd) : rawOrCmd;
  const has = !!player.current;
  const needSong = () => { show("🎙️ Nothing is playing yet. Try “Hey Pendel, play …”", "error"); say("Nothing is playing yet."); };
  switch (cmd.action) {
    case "pause": if (!has) return needSong(); player.pause(); show("🎙️ Paused"); break;
    case "resume": if (!has) return needSong(); player.resume(); show("🎙️ Playing"); break;
    case "next": {
      if (!has) return needSong();
      show("🎙️ Next song…", "processing");
      const moved = await player.skipToNext();
      show(moved ? `🎙️ Next: ${player.current?.song?.title || "song"}` : "🎙️ No next song", moved ? "ok" : "error");
      break;
    }
    case "prev": if (!has) return needSong(); await player.prev(); show(`🎙️ Previous: ${player.current?.song?.title || "song"}`); break;
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
    case "speak-off": store.set(SPEAK, "0"); show("🎙️ Voice replies off"); break;
    case "speak-on": store.set(SPEAK, "1"); show("🎙️ Voice replies on"); say("Okay"); break;
    case "play-song": await playByName(cmd.query); break;
    case "none": show("🎙️ Yes? Say a command", "triggered", 6000); break;
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
  btn.title = { off: "Hey Pendel: off", listening: "Listening for “Hey Pendel”…", triggered: "Heard “Hey Pendel”", processing: "Working on it…" }[state];
}
function show(text, kind = "ok", ms = 2600) {
  if (!pill) return;
  pill.textContent = text;
  pill.dataset.kind = kind;
  pill.hidden = false;
  clearTimeout(pillTimer);
  pillTimer = setTimeout(() => { pill.hidden = true; if (enabled) setState("listening"); }, ms);
}

// ------------------------------------------------------------------ transcript handling (interim + final)
let armed = 0, lastFired = { key: "", at: 0 }, utter = { idx: -1, woke: false, done: false };

function fire(cmd) {
  const key = cmd.action + (cmd.seconds ?? "") + (cmd.query ?? "");
  const now = Date.now();
  if (key === lastFired.key && now - lastFired.at < COOLDOWN_MS) return false; // same command again within 1.2 s
  lastFired = { key, at: now };
  setState("processing");
  runCommand(cmd).catch(() => show("🎙️ Something went wrong", "error"));
  return true;
}

/** Called for every interim and final piece of speech. Exported for tests. */
export function handleTranscript(text, isFinal, resultIndex = 0) {
  if (resultIndex !== utter.idx) utter = { idx: resultIndex, woke: false, done: false }; // a new phrase
  const w = wakeInfo(text);
  let cmd = w ? w.cmd : null, strong = !!w?.strong;
  if (cmd === null && armed && Date.now() - armed < 6000) { cmd = text.trim(); strong = true; } // "Hey Pendel" … pause … "next song"
  if (cmd === null) { if (isFinal) utter = { idx: -1, woke: false, done: false }; return; } // lyrics / talk: ignored
  // a bare "panel …" / "handle …" without "hey" counts only if what follows is a real command
  if (!strong && cmd && parseCommand(cmd).action === "unknown") { if (isFinal) utter = { idx: -1, woke: false, done: false }; return; }
  if (!strong && !cmd) return;
  if (!utter.woke) { // first time in this phrase: dip the music so the microphone hears the command
    utter.woke = true;
    duck(0.2, 1500);
    setState("triggered");
  }
  if (cmd) show(`🎙️ Heard: ${cmd}`, "triggered", 4000);
  else show("🎙️ Hey Pendel…", "triggered", 4000);
  if (utter.done) { if (isFinal) utter = { idx: -1, woke: false, done: false }; return; } // already ran from interim
  const parsed = parseCommand(cmd);
  if (!isFinal) {
    if (parsed.instant) { utter.done = true; armed = 0; fire(parsed); }
    return;
  }
  utter = { idx: -1, woke: false, done: false };
  armed = parsed.action === "none" ? Date.now() : 0;
  fire(parsed);
}

// ------------------------------------------------------------------ microphone + recognition loop
let rec = null, enabled = false, running = false, recLang = "en-IN", restarts = [], autoStarting = false;
let micStream = null, lastEvent = 0, watchdog = null;
const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

/** Ask for the microphone with echo cancellation, noise suppression and auto gain, so the browser turns on its
 *  acoustic echo canceller and the song from the speakers is filtered out. On phones the stream is released
 *  right away (the speech recogniser needs the microphone to itself there); on computers it is kept open. */
async function openMic() {
  if (micStream || !navigator.mediaDevices?.getUserMedia) return true;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (isMobile) closeMic();
    return true;
  } catch (e) {
    if (e?.name === "NotAllowedError" || e?.name === "SecurityError") return "denied";
    if (e?.name === "NotFoundError") return "missing";
    return true; // other problems: let speech recognition try on its own
  }
}
function closeMic() { micStream?.getTracks().forEach((t) => t.stop()); micStream = null; }

function build() {
  const r = new Recognition();
  r.continuous = true;
  r.interimResults = true;
  r.lang = recLang;
  r.maxAlternatives = 1;
  r.onstart = () => { running = true; autoStarting = false; lastEvent = Date.now(); };
  r.onresult = (e) => {
    lastEvent = Date.now();
    for (let i = e.resultIndex; i < e.results.length; i++) handleTranscript(e.results[i][0].transcript, e.results[i].isFinal, i);
  };
  r.onerror = (e) => {
    lastEvent = Date.now();
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
  const mic = await openMic();
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
  if (!silent) show("🎙️ Listening… say “Hey Pendel, …”", "triggered", 3000);
  return true;
}

function stop(savePref = true) {
  enabled = false;
  running = false;
  clearInterval(watchdog);
  try { rec?.abort(); } catch { /* not started */ }
  rec = null;
  closeMic();
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
  // turned on last time: try again quietly (works when the microphone permission was already granted)
  if (store.get(PREF) === "1") { autoStarting = true; start(true); }
  // most phones stop the microphone while the page is hidden: pick up again when the listener comes back
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && enabled && !running) listen();
  });
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount);
else mount();

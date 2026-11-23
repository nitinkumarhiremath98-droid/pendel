"""Song highlight detection and preview rendering.

Every highlight is a 40-45 s window (42 s by default, HIGHLIGHT_DURATION_SEC) on the main vocal hook / chorus,
never the intro. smart_window() picks the start, best source first:

  1. Synced lyrics: the first full occurrence of the most repeated lyric block (the chorus), or, when nothing
     repeats, the moment the vocals come in.
  2. A "most replayed" peak or a chorus chapter, when one is known: the window starts 5 s before it.
  3. Audio analysis (songs whose audio file is on this server): suggest_highlight() decodes the track with ffmpeg
     and scores windows that are loud and repeated elsewhere -- the acoustic signature of a chorus.
  4. Structure: ~30 % into a 2.5-6 min song (where the first chorus usually lands after the intro and verse),
     25 % into longer tracks, 15 % into very short ones.

The window always ends at least 5 s before the song does when the song is long enough. Admins have the final
say: a window set by hand ("manual") or supplied in an import ("editorial") is never recomputed.
"""
import os, re, shutil, subprocess, tempfile, unicodedata
from collections import Counter

from . import config

DEFAULT_LEN = float(config.HIGHLIGHT_DURATION_SEC)
MIN_LEN, MAX_LEN = 40.0, 45.0
TAIL = 5.0          # keep the window this far from the end of the song
LEAD_IN = 0.6       # start just before the first sung word of the phrase


def ffmpeg_available():
    return shutil.which(config.FFMPEG) is not None or os.path.exists(config.FFMPEG)


def validate_window(start, end, duration):
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return "Start and end must be numbers (seconds)."
    if start < 0:
        return "Start must be 0 or later."
    if duration and end > duration + 0.05:
        return f"End ({end:.1f}s) is past the end of the song ({duration:.1f}s)."
    length = end - start
    # a song shorter than a highlight plays as a whole
    shortest = min(MIN_LEN, float(duration)) if duration else MIN_LEN
    if length < shortest - 0.05 or length > MAX_LEN + 1e-6:
        return f"A highlight must be {MIN_LEN:.0f}-{MAX_LEN:.0f} seconds long (got {length:.1f}s)."
    return None


def fit(start, duration, length=None):
    """A window of `length` (default 42 s) starting as close to `start` as the song allows, ending >= 5 s before
    the end of the song when it is long enough. Returns (start, end)."""
    length = float(length or DEFAULT_LEN)
    if not duration:
        return round(max(0.0, start), 2), round(max(0.0, start) + length, 2)
    duration = float(duration)
    if duration <= length:                      # shorter than a highlight: the whole song
        return 0.0, round(duration, 2)
    latest = duration - length - TAIL if duration - length - TAIL >= 0 else duration - length
    start = max(0.0, min(float(start), latest))
    return round(start, 2), round(start + length, 2)


def structural_start(duration):
    """Where the first main hook usually lands when nothing else is known."""
    if not duration:
        return 45.0
    if duration < 90:
        return duration * 0.15
    if duration > 360:
        return duration * 0.25
    return duration * 0.30                      # 2.5-6 min songs: ~45-110 s in, after the intro and first verse


def _norm(text):
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def lyrics_start(lines, duration, length=None):
    """Start of the chorus from synced lyric lines [{t, text}], or of the first vocals when nothing repeats.
    Returns (start, how) or None."""
    length = float(length or DEFAULT_LEN)
    pts = sorted(((float(l["t"]), _norm(l.get("text"))) for l in (lines or []) if l.get("t") is not None and _norm(l.get("text"))),
                 key=lambda x: x[0])
    if not pts:
        return None
    room = lambda t: not duration or t - LEAD_IN + min(length, duration) <= duration - (TAIL if duration - length - TAIL >= 0 else 0) + 0.01
    counts = Counter(text for _, text in pts)
    top = max(counts.values())
    if top >= 2:
        # the hook = the most repeated line (verses repeat too, but the chorus line repeats most);
        # start at the first time it is sung that still leaves a full window
        hooks = [text for text, n in counts.items() if n == top]
        firsts = sorted((next(t for t, x in pts if x == h), h) for h in hooks)
        for _, hook in firsts:
            for t, text in pts:
                if text == hook and room(t):
                    return max(0.0, t - LEAD_IN), "lyrics-chorus"
    first = pts[0][0]
    return max(0.0, first - LEAD_IN), "lyrics-vocals"


def smart_window(duration, lyrics=None, peak=None, audio_path=None, length=None):
    """The highlight window for a song: {start, end, method, confidence}. See the module docstring."""
    length = float(length or DEFAULT_LEN)
    found = lyrics_start(lyrics, duration, length) if lyrics else None
    if found:
        start, end = fit(found[0], duration, length)
        return {"start": start, "end": end, "method": found[1], "confidence": 0.8 if found[1] == "lyrics-chorus" else 0.5}
    if peak is not None:
        try:
            start, end = fit(float(peak) - 5.0, duration, length)
            return {"start": start, "end": end, "method": "peak", "confidence": 0.7}
        except (TypeError, ValueError):
            pass
    if audio_path:
        return suggest_highlight(audio_path, duration, length)
    return _fallback(duration, length)


def _fallback(duration, length=None):
    start, end = fit(structural_start(duration), duration, length)
    return {"start": start, "end": end, "method": "heuristic", "confidence": 0.2}


def suggest_highlight(audio_path, duration, length=None):
    length = float(length or DEFAULT_LEN)
    try:
        import numpy as np
    except ImportError:
        return _fallback(duration, length)
    if not ffmpeg_available() or not duration or duration < length + TAIL + 5:
        return _fallback(duration, length)
    sr = 8000
    try:
        raw = subprocess.run([config.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", audio_path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                             capture_output=True, check=True, timeout=120).stdout
    except Exception:
        return _fallback(duration, length)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    hop = sr // 2  # 0.5 s frames
    n = len(x) // hop
    if n < 2 * (length + TAIL):
        return _fallback(duration, length)
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    spec = np.abs(np.fft.rfft(frames * np.hanning(hop), axis=1))
    edges = np.unique(np.geomspace(3, spec.shape[1] - 1, 25).astype(int))
    bands = np.stack([spec[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:]) if b > a], axis=1)
    feat = np.log1p(bands)
    feat = (feat - feat.mean(axis=0)) / (feat.std(axis=0) + 1e-6)
    feat /= np.linalg.norm(feat, axis=1, keepdims=True) + 1e-9

    win = int(length * 2)
    energy = np.convolve(rms, np.ones(win) / win, mode="valid")
    energy = (energy - energy.min()) / (np.ptp(energy) + 1e-9)
    # repetition: how similar each frame is to its best match at least 10 s away
    sim = feat @ feat.T
    idx = np.arange(n)
    sim[np.abs(idx[:, None] - idx[None, :]) < 20] = -1
    rep = sim.max(axis=1)
    repw = np.convolve(rep, np.ones(win) / win, mode="valid")
    repw = (repw - repw.min()) / (np.ptp(repw) + 1e-9)
    starts = np.arange(len(energy))
    pos = starts / max(1, n)
    # never the intro (first 20 s or 12 % of the song), and keep 5 s clear of the end
    penalty = np.where((starts * 0.5 < 20) | (pos < 0.12), 0.8, 0.0) + np.where(starts * 0.5 + length > duration - TAIL, 1.0, 0.0)
    score = 0.55 * energy + 0.45 * repw - penalty
    score = np.nan_to_num(score, nan=-1.0)
    best = int(np.argmax(score))
    # snap to the strongest energy rise within +-3 s (section boundary)
    lo, hi = max(1, best - 6), min(n - win - 1, best + 6)
    if hi > lo:
        rise = rms[lo:hi] - rms[lo - 1:hi - 1]
        best = lo + int(np.argmax(rise))
    start, end = fit(best * 0.5, duration, length)
    conf = float(np.clip(score[min(best, len(score) - 1)], 0, 1))
    return {"start": round(start, 2), "end": round(end, 2), "method": "audio-analysis", "confidence": round(conf, 2)}


def render_preview(src_path, start, end, out_path):
    """Cut the licensed preview asset (with short fades). Returns True if rendered."""
    if not ffmpeg_available():
        return False
    length = end - start
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    subprocess.run([config.FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src_path,
                    "-af", f"afade=t=in:d=0.4,afade=t=out:st={max(0, length - 1.2):.2f}:d=1.2", "-c:a", "libmp3lame", "-b:a", "128k", out_path],
                   check=True, timeout=120)
    return True


def probe_duration(path):
    """Duration in seconds via ffprobe/ffmpeg, or None."""
    if not ffmpeg_available():
        return None
    try:
        r = subprocess.run([config.FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60)
        import re
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return None


def temp_path(suffix):
    fd, p = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return p

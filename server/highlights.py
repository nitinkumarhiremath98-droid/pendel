"""Song highlight detection and preview rendering.

suggest_highlight() decodes the track with ffmpeg and looks for the 20-30 s
window that is (a) loud and dense relative to the rest of the song and (b)
repeated elsewhere in the track -- the acoustic signature of a chorus or hook.
The opening of the song is penalised so we never default to "first 30 seconds".
Admins always have the final say: the suggestion only pre-fills the editor.

If numpy or ffmpeg is unavailable the function falls back to a structural
heuristic (a window starting around 35% into the song).
"""
import os, shutil, subprocess, tempfile

from . import config

MIN_LEN, MAX_LEN, DEFAULT_LEN = 20.0, 30.0, 25.0


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
    if length < MIN_LEN - 1e-6 or length > MAX_LEN + 1e-6:
        return f"A highlight must be 20-30 seconds long (got {length:.1f}s)."
    return None


def _fallback(duration):
    length = min(DEFAULT_LEN, max(MIN_LEN, duration)) if duration else DEFAULT_LEN
    start = max(0.0, min(duration * 0.35, duration - length)) if duration else 30.0
    return {"start": round(start, 2), "end": round(start + length, 2), "method": "heuristic", "confidence": 0.2}


def suggest_highlight(audio_path, duration):
    try:
        import numpy as np
    except ImportError:
        return _fallback(duration)
    if not ffmpeg_available() or duration < MIN_LEN + 5:
        return _fallback(duration)
    sr = 8000
    try:
        raw = subprocess.run([config.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", audio_path, "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                             capture_output=True, check=True, timeout=120).stdout
    except Exception:
        return _fallback(duration)
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    hop = sr // 2  # 0.5 s frames
    n = len(x) // hop
    if n < 60:
        return _fallback(duration)
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    spec = np.abs(np.fft.rfft(frames * np.hanning(hop), axis=1))
    edges = np.unique(np.geomspace(3, spec.shape[1] - 1, 25).astype(int))
    bands = np.stack([spec[:, a:b].mean(axis=1) for a, b in zip(edges[:-1], edges[1:]) if b > a], axis=1)
    feat = np.log1p(bands)
    feat = (feat - feat.mean(axis=0)) / (feat.std(axis=0) + 1e-6)
    feat /= np.linalg.norm(feat, axis=1, keepdims=True) + 1e-9

    win = int(DEFAULT_LEN * 2)
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
    penalty = np.where(starts * 0.5 < 15, 0.6, 0.0) + np.where(pos > 0.85, 0.5, 0.0)
    score = 0.55 * energy + 0.45 * repw - penalty
    score = np.nan_to_num(score, nan=-1.0)
    best = int(np.argmax(score))
    # snap to the strongest energy rise within +-3 s (section boundary)
    lo, hi = max(1, best - 6), min(n - win - 1, best + 6)
    if hi > lo:
        rise = rms[lo:hi] - rms[lo - 1:hi - 1]
        best = lo + int(np.argmax(rise))
    start = best * 0.5
    end = min(duration, start + DEFAULT_LEN)
    if end - start < MIN_LEN:
        start = max(0.0, end - MIN_LEN)
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

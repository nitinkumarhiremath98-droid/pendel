"""Generate PENDEL's original, royalty-free (CC0) demo catalogue.

  python tools/generate_demo_music.py

Requires numpy + ffmpeg. Writes:
  media/audio/full/<slug>.mp3      complete song
  media/audio/preview/<slug>.mp3   the 20-30 s highlight cut (licensed preview asset)
  media/covers/<album>.svg         album artwork
  media/artists/<artist>.svg       artist artwork
  server/seed/catalog.json         metadata, synced lyrics and highlight times

Each song is composed as intro / verse / chorus / verse / chorus / outro. The
highlight is set on the second (fullest) chorus, never on the first 30 seconds.
"""
import json, math, os, re, subprocess, sys, hashlib
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from catalog_data import SONGS, ARTISTS, LANGUAGES, CATEGORIES  # noqa: E402

SR = 32000
MAJOR = [0, 2, 4, 5, 7, 9, 11]
MINOR = [0, 2, 3, 5, 7, 8, 10]

STYLES = {
    #            scale  progression(degrees)  drums     lead      pad   bass  swing
    "love":       (MAJOR, [0, 4, 5, 3], "pop", "flute", 0.5, 0.5, 0),
    "romantic":   (MAJOR, [0, 5, 3, 4], "soft", "bell", 0.6, 0.45, 0),
    "sad":        (MINOR, [0, 5, 2, 6], "soft", "flute", 0.65, 0.4, 0),
    "heartbreak": (MINOR, [0, 3, 5, 4], "soft", "pluck", 0.6, 0.45, 0),
    "motivational": (MAJOR, [5, 3, 0, 4], "rock", "saw", 0.5, 0.6, 0),
    "dj":         (MINOR, [0, 0, 5, 6], "four", "saw", 0.45, 0.7, 0),
    "party":      (MAJOR, [0, 4, 3, 4], "dhol", "pluck", 0.4, 0.65, 0.12),
    "devotional": (MAJOR, [0, 3, 0, 4], "tabla", "bell", 0.55, 0.3, 0),
    "friendship": (MAJOR, [0, 3, 4, 3], "pop", "pluck", 0.45, 0.55, 0),
    "melody":     (MAJOR, [0, 2, 3, 4], "soft", "flute", 0.6, 0.4, 0),
    "classic":    (MAJOR, [0, 3, 4, 0], "tabla", "flute", 0.6, 0.35, 0.1),
    "ambient":    (MAJOR, [0, 5, 3, 4], "lofi", "bell", 0.75, 0.35, 0.08),
}


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def env(n, a=0.01, r=0.2, sustain=1.0):
    t = np.arange(n) / SR
    e = np.ones(n) * sustain
    an = max(1, int(a * SR))
    e[:an] = np.linspace(0, 1, an)
    rn = min(n, max(1, int(r * SR)))
    e[-rn:] *= np.linspace(1, 0, rn)
    return e


def tone(freq, dur, timbre):
    n = max(1, int(dur * SR))
    t = np.arange(n) / SR
    if timbre == "flute":
        vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t)
        ph = 2 * np.pi * freq * t * vib
        w = np.sin(ph) + 0.25 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)
        w += 0.02 * np.random.default_rng(int(freq)).standard_normal(n)
        return w * env(n, 0.06, min(0.25, dur * 0.4), 0.9)
    if timbre == "bell":
        w = np.sin(2 * np.pi * freq * t) + 0.5 * np.sin(2 * np.pi * freq * 2.76 * t) * np.exp(-t * 6) + 0.3 * np.sin(2 * np.pi * freq * 5.4 * t) * np.exp(-t * 9)
        return w * np.exp(-t * 2.2) * env(n, 0.003, 0.05)
    if timbre == "pluck":
        w = sum((0.6 ** k) * np.sin(2 * np.pi * freq * (k + 1) * t) * np.exp(-t * (3 + 2 * k)) for k in range(6))
        return w * env(n, 0.002, 0.05)
    if timbre == "saw":
        w = sum(((-1) ** k) * np.sin(2 * np.pi * freq * (k + 1) * t * (1 + 0.003 * (k % 2))) / (k + 1) for k in range(8))
        return 0.8 * w * env(n, 0.01, min(0.15, dur * 0.3), 0.85)
    if timbre == "pad":
        w = sum(np.sin(2 * np.pi * freq * d * t) for d in (0.997, 1.0, 1.004)) / 3
        w += 0.3 * np.sin(2 * np.pi * freq * 2 * t)
        return w * env(n, min(0.6, dur * 0.3), min(0.8, dur * 0.4))
    if timbre == "bass":
        w = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(4 * np.pi * freq * t)
        return w * env(n, 0.005, 0.08) * np.exp(-t * 1.5)
    raise ValueError(timbre)


def drum(kind, rng):
    if kind == "kick":
        n = int(0.35 * SR); t = np.arange(n) / SR
        f = 45 + 90 * np.exp(-t * 30)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
    if kind == "snare":
        n = int(0.22 * SR); t = np.arange(n) / SR
        return 0.6 * rng.standard_normal(n) * np.exp(-t * 18) + 0.4 * np.sin(2 * np.pi * 190 * t) * np.exp(-t * 25)
    if kind == "hat":
        n = int(0.06 * SR); t = np.arange(n) / SR
        x = np.diff(rng.standard_normal(n + 1))
        return 0.25 * x * np.exp(-t * 70)
    if kind == "dhol":
        n = int(0.4 * SR); t = np.arange(n) / SR
        return np.sin(2 * np.pi * (70 + 40 * np.exp(-t * 20)) * t) * np.exp(-t * 7)
    if kind == "slap":
        n = int(0.12 * SR); t = np.arange(n) / SR
        return 0.5 * np.sin(2 * np.pi * 420 * t) * np.exp(-t * 30) + 0.3 * rng.standard_normal(n) * np.exp(-t * 40)
    if kind == "tabla":
        n = int(0.5 * SR); t = np.arange(n) / SR
        f = 260 * (1 + 0.15 * np.exp(-t * 15))
        return 0.6 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 6)
    if kind == "ge":
        n = int(0.4 * SR); t = np.arange(n) / SR
        f = 90 + 60 * np.exp(-t * 8)
        return 0.7 * np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 5)
    raise ValueError(kind)


PATTERNS = {  # 16 steps per bar
    "pop":   {"kick": [0, 8, 10], "snare": [4, 12], "hat": list(range(0, 16, 2))},
    "rock":  {"kick": [0, 6, 8, 10], "snare": [4, 12], "hat": list(range(0, 16, 2))},
    "four":  {"kick": [0, 4, 8, 12], "snare": [4, 12], "hat": [2, 6, 10, 14, 3, 7, 11, 15]},
    "dhol":  {"dhol": [0, 3, 6, 8, 11, 14], "slap": [4, 12, 7, 15], "hat": list(range(0, 16, 2))},
    "soft":  {"kick": [0, 10], "snare": [8], "hat": [0, 4, 8, 12]},
    "tabla": {"ge": [0, 6, 8], "tabla": [2, 4, 10, 12, 14]},
    "lofi":  {"kick": [0, 7, 10], "snare": [4, 12], "hat": [0, 2, 4, 6, 8, 10, 12, 14]},
}


def compose(song):
    style = STYLES[song["style"]]
    scale, prog, kit, lead_t, pad_lvl, bass_lvl, swing = style
    bpm, root = song["bpm"], song["root"]
    seed = int(hashlib.md5(song["slug"].encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    beat = 60.0 / bpm
    bar = 4 * beat

    # chorus length: an even number of bars that lands between 20 and 30 s
    cb = max(4, int(round(25 / bar)))
    if cb % 2:
        cb += 1 if (cb + 1) * bar <= 30 else -1
    while cb * bar > 30:
        cb -= 2
    while cb * bar < 20:
        cb += 2
    vb = cb
    intro_b = 2 if bar > 3 else 4
    sections = [("intro", intro_b), ("verse", vb), ("chorus", cb), ("verse2", max(2, vb // 2)), ("chorus2", cb), ("outro", intro_b)]
    total_bars = sum(b for _, b in sections)
    dur = total_bars * bar + 2.5
    out = np.zeros(int(dur * SR) + SR)

    def put(sig, t, gain=1.0):
        i = int(t * SR)
        j = min(len(out), i + len(sig))
        out[i:j] += gain * sig[: j - i]

    def chord_notes(deg, octave=0):
        return [root + 12 * octave + scale[(deg + k) % 7] + 12 * ((deg + k) // 7) for k in (0, 2, 4)]

    # hook motif: 2 bars of 8th-note rhythm, chosen once per song
    rhythms = [[0, 2, 3, 4, 6, 8, 10, 11, 12], [0, 1, 2, 4, 6, 8, 9, 10, 12, 14], [0, 3, 4, 6, 8, 11, 12], [0, 2, 4, 5, 6, 8, 12, 13, 14]]
    hook_r = rhythms[rng.integers(len(rhythms))]
    hook_steps = [int(x) for x in rng.integers(-2, 3, size=len(hook_r))]
    verse_r = rhythms[rng.integers(len(rhythms))]

    timeline = []  # (name, start_time, bars)
    t = 0.0
    for name, nb in sections:
        timeline.append((name, t, nb))
        for b in range(nb):
            bt = t + b * bar
            deg = prog[b % len(prog)]
            full = name.startswith("chorus")
            level = {"intro": 0.6, "verse": 0.8, "verse2": 0.8, "chorus": 1.0, "chorus2": 1.1, "outro": 0.5}[name]
            # pad
            for m in chord_notes(deg):
                put(tone(mtof(m), bar + 0.3, "pad"), bt, 0.11 * pad_lvl * level)
            if song["style"] in ("devotional", "classic"):  # tanpura-like drone
                put(tone(mtof(root - 12), bar + 0.3, "pad"), bt, 0.08)
                put(tone(mtof(root - 5), bar + 0.3, "pad"), bt, 0.05)
            # bass
            if name not in ("intro", "outro") or kit in ("four",):
                bm = chord_notes(deg, -2)[0]
                for s in ([0, 8] if kit != "four" else [0, 3, 6, 8, 11, 14]):
                    put(tone(mtof(bm), beat * 1.6 if kit != "four" else beat * 0.6, "bass"), bt + s * beat / 4, 0.3 * bass_lvl * level)
            # drums
            if name not in ("intro", "outro") or (name == "intro" and kit == "four"):
                pat = PATTERNS[kit]
                for kind, steps in pat.items():
                    if not full and kind == "hat" and name == "verse" and kit in ("soft",):
                        continue
                    for s in steps:
                        sw = swing * beat / 4 if s % 2 else 0
                        put(drum(kind, rng), bt + s * beat / 4 + sw, (0.45 if kind in ("kick", "dhol", "ge") else 0.3) * level)
            # lead melody
            if name in ("verse", "verse2", "chorus", "chorus2"):
                phrase_bar = b % 2
                ct = chord_notes(deg, 1 if full else 0)
                rhy = hook_r if full else verse_r
                if full and (b // 2) % 2 == 1 and phrase_bar == 1:
                    rhy = rhy[: max(3, len(rhy) // 2)]  # answer phrase breathes
                step_scale_idx = 0
                for k, s in enumerate(rhy):
                    if s < 8 * phrase_bar or s >= 8 * (phrase_bar + 1):
                        continue
                    pos = (s - 8 * phrase_bar) * 2  # 16th steps
                    if full:
                        base = ct[0]
                        idx = (hook_steps[k] + step_scale_idx) % 3
                        m = ct[idx] + (12 if hook_steps[k] > 1 else 0)
                    else:
                        m = ct[int(rng.integers(0, 3))] + int(rng.choice([0, 0, 2, -1]))
                        rel = (m - root) % 12
                        m = m - rel + min(scale, key=lambda d: abs(rel - d))  # snap passing notes to the scale
                    nd = beat * (0.9 if full else 0.75)
                    put(tone(mtof(m), nd, lead_t), bt + pos * beat / 4, 0.22 * level)
                    if full and lead_t in ("flute", "bell"):
                        put(tone(mtof(m - 12), nd, "pluck"), bt + pos * beat / 4, 0.06)
        t += nb * bar

    # outro fade + normalise
    fade = int(3 * SR)
    end = int((total_bars * bar + 2.0) * SR)
    out = out[:end]
    out[-fade:] *= np.linspace(1, 0, fade)
    out /= max(1e-9, np.max(np.abs(out))) / 0.89
    return out, timeline, bar, round(len(out) / SR, 2)


def lyric_timeline(song, timeline, bar):
    """Attach original lyric lines to melodic phrases (every 2 bars)."""
    verse, chorus = song.get("verse") or [], song.get("chorus") or []
    tr = song.get("translation") or []
    tr_v, tr_c = tr[: len(verse)], tr[len(verse):]
    lines = []
    for name, start, nb in timeline:
        src, src_tr = (chorus, tr_c) if name.startswith("chorus") else (verse, tr_v) if name.startswith("verse") else (None, None)
        if not src:
            continue
        n_phr = nb // 2
        if name == "verse2":
            src, src_tr = src[-min(len(src), n_phr):], src_tr[-min(len(src_tr), n_phr):] if src_tr else []
        for i in range(n_phr):
            if not src:
                break
            j = i % len(src)
            lines.append({"t": round(start + i * 2 * bar, 2), "text": src[j], "tr": src_tr[j] if src_tr and j < len(src_tr) else None})
    return lines


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


PALETTES = {
    "love": ("#ff4f8b", "#7b2cbf"), "romantic": ("#ff7a7a", "#5a189a"), "sad": ("#3a86ff", "#0b132b"), "heartbreak": ("#c2415d", "#1b0a2a"),
    "motivational": ("#ffb020", "#d62828"), "dj": ("#00e0c6", "#3a0ca3"), "party": ("#ff5ad1", "#ff9e00"), "devotional": ("#ff9f43", "#9d0208"),
    "friendship": ("#44c2ff", "#06d6a0"), "melody": ("#8be38b", "#1d3557"), "classic": ("#d4a373", "#432818"), "ambient": ("#6c8ea0", "#14213d"),
}


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def cover_svg(title, artist, style, seed):
    a, b = PALETTES[style]
    rng = np.random.default_rng(seed)
    shapes = []
    for i in range(7):
        cx, cy, r = rng.integers(0, 600), rng.integers(0, 600), rng.integers(40, 260)
        op = round(float(rng.uniform(0.08, 0.35)), 2)
        shapes.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#fff" opacity="{op}"/>')
    waves = "".join(
        f'<path d="M0 {380 + i * 26} Q150 {340 + i * 26 + rng.integers(-40, 40)} 300 {380 + i * 26} T600 {380 + i * 26}" stroke="#fff" stroke-opacity="{0.15 + i * 0.05:.2f}" stroke-width="3" fill="none"/>'
        for i in range(5))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" role="img" aria-label="{esc(title)} cover">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></linearGradient>
<filter id="blur"><feGaussianBlur stdDeviation="18"/></filter></defs>
<rect width="600" height="600" fill="url(#g)"/><g filter="url(#blur)">{"".join(shapes)}</g>{waves}
<rect x="0" y="470" width="600" height="130" fill="#000" opacity="0.28"/>
<text x="36" y="530" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="40" font-weight="800" fill="#fff">{esc(title)}</text>
<text x="36" y="570" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="22" fill="#fff" opacity="0.85">{esc(artist)}</text>
</svg>'''


def artist_svg(name, seed):
    keys = list(PALETTES)
    a, b = PALETTES[keys[seed % len(keys)]]
    initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", name)[:2]).upper()
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400" role="img" aria-label="{esc(name)}">
<defs><radialGradient id="g" cx="30%" cy="25%" r="90%"><stop offset="0" stop-color="{a}"/><stop offset="1" stop-color="{b}"/></radialGradient></defs>
<rect width="400" height="400" fill="url(#g)"/><circle cx="200" cy="160" r="70" fill="#fff" opacity="0.18"/>
<path d="M70 400 Q200 230 330 400Z" fill="#fff" opacity="0.18"/>
<text x="200" y="228" text-anchor="middle" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="120" font-weight="800" fill="#fff">{initials}</text>
</svg>'''


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or hashlib.md5(s.encode()).hexdigest()[:8]


def main():
    for d in ("media/audio/full", "media/audio/preview", "media/covers", "media/artists", "server/seed"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
    artists = {a[0]: a for a in ARTISTS}
    album_art = {}
    catalog = []
    only = set(sys.argv[1:])
    for i, s in enumerate(SONGS):
        if only and s["slug"] not in only:
            continue
        audio, timeline, bar, dur = compose(s)
        pcm = (audio * 32767).astype("<i2").tobytes()
        full = os.path.join(ROOT, "media/audio/full", s["slug"] + ".mp3")
        prev = os.path.join(ROOT, "media/audio/preview", s["slug"] + ".mp3")
        p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "-",
                            "-af", "aecho=0.8:0.6:60|120:0.25|0.15,loudnorm=I=-15:TP=-1.5:LRA=11", "-ar", "32000", "-c:a", "libmp3lame", "-b:a", "80k",
                            "-metadata", f"title={s['title']}", "-metadata", f"artist={artists[s['artist']][1]}",
                            "-metadata", "copyright=CC0 - PENDEL demo original", full], input=pcm)
        p.check_returncode()
        ch2 = [x for x in timeline if x[0] == "chorus2"][0]
        hs, hl = round(ch2[1], 2), round(ch2[2] * bar, 2)
        hl = max(20.0, min(30.0, hl))
        ffmpeg("-ss", str(hs), "-t", str(hl), "-i", full, "-af", f"afade=t=in:d=0.4,afade=t=out:st={hl - 1.2}:d=1.2", "-c:a", "libmp3lame", "-b:a", "80k", prev)
        album_slug = slugify(s["album"] + "-" + s["artist"])
        if album_slug not in album_art:
            album_art[album_slug] = True
            with open(os.path.join(ROOT, "media/covers", album_slug + ".svg"), "w", encoding="utf-8") as f:
                f.write(cover_svg(s["album"], artists[s["artist"]][1], s["style"], i * 7 + 3))
        lyr = lyric_timeline(s, timeline, bar)
        sections = [{"name": n, "start": round(st, 2), "end": round(st + nb * bar, 2)} for n, st, nb in timeline]
        catalog.append({**{k: v for k, v in s.items() if k not in ("verse", "chorus", "translation")},
                        "album_slug": album_slug, "duration": dur, "highlight_start": hs, "highlight_end": round(hs + hl, 2),
                        "sections": sections, "lyrics": lyr,
                        "audio_full": f"audio/full/{s['slug']}.mp3", "audio_preview": f"audio/preview/{s['slug']}.mp3",
                        "cover": f"covers/{album_slug}.svg"})
        print(f"{s['slug']:24s} {dur:6.1f}s  highlight {hs:6.2f}-{hs + hl:6.2f}  lyrics {len(lyr)}")
    for slug, name, *_ in ARTISTS:
        with open(os.path.join(ROOT, "media/artists", slug + ".svg"), "w", encoding="utf-8") as f:
            f.write(artist_svg(name, sum(map(ord, slug))))
    if not only:
        with open(os.path.join(ROOT, "server/seed/catalog.json"), "w", encoding="utf-8") as f:
            json.dump({"artists": ARTISTS, "languages": LANGUAGES, "categories": CATEGORIES, "songs": catalog}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

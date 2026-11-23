"""Re-cut the demo songs' highlight previews to PENDEL's highlight length (42 s by default) on the chorus.

  python tools/render_demo_previews.py

Requires ffmpeg. For every song in server/seed/catalog.json it finds the window with server.highlights.smart_window
(the first chorus, from the synced lyrics), writes media/audio/preview/<slug>.mp3 and stores the new
highlight_start / highlight_end in the catalogue. Run it again after changing HIGHLIGHT_DURATION_SEC.
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from server import highlights  # noqa: E402

CATALOG = os.path.join(ROOT, "server/seed/catalog.json")


def main():
    with open(CATALOG, encoding="utf-8") as f:
        data = json.load(f)
    for s in data["songs"]:
        w = highlights.smart_window(s["duration"], lyrics=s.get("lyrics"))
        start, length = w["start"], round(w["end"] - w["start"], 2)
        src = os.path.join(ROOT, "media", s["audio_full"])
        out = os.path.join(ROOT, "media", s["audio_preview"])
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src,
                        "-af", f"afade=t=in:d=0.4,afade=t=out:st={max(0, length - 1.2):.2f}:d=1.2", "-ar", "32000", "-ac", "1",
                        "-c:a", "libmp3lame", "-b:a", "80k", "-metadata", f"title={s['title']} (highlight)",
                        "-metadata", "copyright=CC0 - PENDEL demo original", out], check=True)
        s["highlight_start"], s["highlight_end"] = start, w["end"]
        print(f"{s['slug']:24s} {s['duration']:6.1f}s  highlight {start:6.2f}-{w['end']:6.2f}  ({w['method']})")
    with open(CATALOG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()

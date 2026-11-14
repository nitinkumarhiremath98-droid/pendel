"""Simulated iTunes / Apple RSS / YouTube responses for offline tests.

Enable with PENDEL_FAKE_WORLD=1. Responses follow the documented JSON shapes of
the iTunes Search API (results[] with trackId, trackName, previewUrl, ...), the
Apple Music RSS feed (feed.results[] with id) and YouTube Data API search
(items[].id.videoId). Preview URLs point at PENDEL_FAKE_PREVIEW_URL.
"""
import hashlib, os, urllib.parse

GENRE = {"telugu": "Telugu", "tamil": "Tamil", "kannada": "Kannada", "malayalam": "Malayalam", "hindi": "Bollywood", "bengali": "Bengali",
         "marathi": "Marathi", "punjabi": "Punjabi", "gujarati": "Gujarati", "odia": "Odia", "assamese": "Assamese", "urdu": "Ghazal",
         "latino": "Pop Latino", "k-pop": "K-Pop", "j-pop": "J-Pop", "mandopop": "Mandopop", "arabic": "Arabic"}
PREVIEW = os.environ.get("PENDEL_FAKE_PREVIEW_URL", "https://audio-ssl.itunes.apple.com/fake/preview.m4a")


def _track(term, i, country):
    h = int(hashlib.md5(f"{term}|{i}".encode()).hexdigest()[:8], 16)
    word = term.split()[0] if term else ""
    genre = GENRE.get(word, "Pop")
    if word in GENRE and i % 3 == 2:  # like real search results: some songs in other languages
        genre = "Bollywood" if word != "hindi" else "Tamil"
    return {"wrapperType": "track", "kind": "song", "trackId": 1_000_000 + h % 9_000_000, "artistId": 500 + h % 40, "collectionId": 9000 + h % 90,
            "artistName": f"{genre} Artist {h % 40}", "collectionName": f"{genre} Collection Vol. {h % 5}", "trackName": f"{genre} Track {h % 997}",
            "previewUrl": PREVIEW, "artworkUrl100": "https://is1-ssl.mzstatic.com/image/thumb/fake/100x100bb.jpg",
            "trackTimeMillis": 180_000 + h % 120_000, "releaseDate": f"20{10 + h % 16}-05-01T07:00:00Z", "primaryGenreName": genre,
            "trackViewUrl": f"https://music.apple.com/{country.lower()}/album/x/{9000 + h % 90}?i={1_000_000 + h % 9_000_000}",
            "trackExplicitness": "notExplicit", "country": country}


def fetch(url):
    u = urllib.parse.urlparse(url)
    q = dict(urllib.parse.parse_qsl(u.query))
    if u.netloc == "itunes.apple.com" and u.path == "/search":
        n = min(int(q.get("limit", 50)), 50)
        return {"resultCount": n, "results": [_track(q["term"], i, q.get("country", "IN")) for i in range(n)]}
    if u.netloc == "itunes.apple.com" and u.path == "/lookup":
        ids = q["id"].split(",")
        res = []
        for i, tid in enumerate(ids):
            t = _track("hindi chart", i, q.get("country", "IN"))
            t["trackId"] = int(tid)
            res.append(t)
        return {"resultCount": len(res), "results": res}
    if u.netloc == "rss.applemarketingtools.com":
        return {"feed": {"title": "Top Songs", "results": [{"id": str(2_000_000 + i), "name": f"Chart Song {i + 1}", "kind": "songs"} for i in range(30)]}}
    if u.netloc == "www.googleapis.com":
        return _youtube(u.path.rsplit("/", 1)[-1], q)
    raise RuntimeError(f"unexpected URL {url}")


# ------------------------------------------------------------------ YouTube Data API v3 (documented response shapes)
PLAYLIST_SIZE = int(os.environ.get("PENDEL_FAKE_PLAYLIST_SIZE", "120"))


def _vid(seed):
    return hashlib.md5(seed.encode()).hexdigest()[:11].replace("a", "A").replace("b", "_").replace("c", "-")


def _youtube(method, q):
    if method == "search" and q.get("type") == "playlist":
        return {"items": [{"id": {"kind": "youtube#playlist", "playlistId": "PL" + hashlib.md5(f"{q['q']}{i}".encode()).hexdigest()[:16]},
                           "snippet": {"title": f"{q['q'].title()} Playlist {i + 1}"}} for i in range(5)]}
    if method == "search":
        return {"items": [{"id": {"kind": "youtube#video", "videoId": "abcDEF12345"}}]}
    if method == "channels":
        return {"items": [{"id": "UCfakechannel0000000001", "snippet": {"title": "Fake Label Music"},
                           "contentDetails": {"relatedPlaylists": {"uploads": "UUfakeuploads000001"}}}]}
    if method == "playlistItems":
        page = int(q.get("pageToken", "0"))
        start, end = page * 50, min(PLAYLIST_SIZE, page * 50 + 50)
        items = [{"contentDetails": {"videoId": _vid(f"{q['playlistId']}|{i}")}} for i in range(start, end)]
        return {"items": items, **({"nextPageToken": str(page + 1)} if end < PLAYLIST_SIZE else {})}
    if method == "videos":
        out = []
        for n, vid in enumerate(q["id"].split(",")):
            h = int(hashlib.md5(vid.encode()).hexdigest()[:6], 16)
            kind = h % 20
            title = (f"Official Trailer {h}" if kind == 0 else f"Jukebox Non Stop {h}" if kind == 1 else f"Song {h} (Lyrical Video)")
            dur = "PT1H2M" if kind == 2 else f"PT{3 + h % 3}M{h % 60}S"
            out.append({"id": vid, "snippet": {"title": title, "channelTitle": f"Label {h % 7}", "channelId": f"UC{h % 7:022d}",
                                               "publishedAt": f"20{10 + h % 16}-03-01T00:00:00Z", "liveBroadcastContent": "none"},
                        "contentDetails": {"duration": dur}, "status": {"embeddable": kind != 3, "privacyStatus": "public"}})
        return {"items": out}
    raise RuntimeError(f"unexpected YouTube method {method}")

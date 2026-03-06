import os
import random
import requests
from flask import (
    Flask, redirect, request, session,
    jsonify, render_template, url_for
)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

# ── Spotify config ────────────────────────────────────────────────────────────
SPOTIFY_CLIENT_ID     = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI          = os.environ["REDIRECT_URI"]
SPOTIFY_AUTH_URL      = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL     = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE      = "https://api.spotify.com/v1"

# ── Last.fm config ────────────────────────────────────────────────────────────
LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"

# ── Rock bias config ──────────────────────────────────────────────────────────
# Set ROCK_BIAS_PERCENT in env (0–100). 0 = no bias, 80 = 80% chance rock song
ROCK_BIAS_PERCENT = int(os.environ.get("ROCK_BIAS_PERCENT", "0"))

YEAR_RANGE = (1955, 2023)

# Rock tags used on Last.fm
ROCK_TAGS = ["rock", "classic rock", "hard rock", "indie rock", "alternative rock", "punk rock"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_token():
    return session.get("access_token")


def refresh_access_token():
    rf = session.get("refresh_token")
    if not rf:
        return False
    resp = requests.post(SPOTIFY_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": rf,
        "client_id":     SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    })
    if resp.ok:
        d = resp.json()
        session["access_token"] = d["access_token"]
        if "refresh_token" in d:
            session["refresh_token"] = d["refresh_token"]
        return True
    return False


def spotify_get(path, **kwargs):
    """GET with automatic token refresh."""
    token = get_token()
    if not token:
        return None, 401
    r = requests.get(f"{SPOTIFY_API_BASE}{path}",
                     headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code == 401:
        if refresh_access_token():
            token = get_token()
            r = requests.get(f"{SPOTIFY_API_BASE}{path}",
                             headers={"Authorization": f"Bearer {token}"}, **kwargs)
        else:
            return None, 401
    return r, r.status_code


def spotify_post(path, **kwargs):
    """POST with automatic token refresh."""
    token = get_token()
    if not token:
        return None, 401
    r = requests.post(f"{SPOTIFY_API_BASE}{path}",
                      headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code == 401:
        if refresh_access_token():
            token = get_token()
            r = requests.post(f"{SPOTIFY_API_BASE}{path}",
                              headers={"Authorization": f"Bearer {token}"}, **kwargs)
        else:
            return None, 401
    return r, r.status_code


def spotify_put(path, **kwargs):
    """PUT with automatic token refresh."""
    token = get_token()
    if not token:
        return None, 401
    r = requests.put(f"{SPOTIFY_API_BASE}{path}",
                     headers={"Authorization": f"Bearer {token}"}, **kwargs)
    if r.status_code == 401:
        if refresh_access_token():
            token = get_token()
            r = requests.put(f"{SPOTIFY_API_BASE}{path}",
                             headers={"Authorization": f"Bearer {token}"}, **kwargs)
        else:
            return None, 401
    return r, r.status_code


# ── Last.fm helpers ───────────────────────────────────────────────────────────

def lastfm_get_top_tracks_for_year(year: int, rock: bool = False) -> list:
    """
    Return a list of (artist, title) tuples that were popular in `year`.
    Uses Last.fm chart.getTopTracks with a tag filter for rock if needed.
    Falls back to a curated list on failure.
    """
    if not LASTFM_API_KEY:
        return _fallback_tracks(year)

    try:
        if rock:
            tag = random.choice(ROCK_TAGS)
            params = {
                "method": "tag.gettoptracks",
                "tag": tag,
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": 50,
            }
        else:
            params = {
                "method": "chart.gettoptracks",
                "api_key": LASTFM_API_KEY,
                "format": "json",
                "limit": 100,
                "page": random.randint(1, 5),
            }
        resp = requests.get(LASTFM_API_URL, params=params, timeout=8)
        if not resp.ok:
            return _fallback_tracks(year)

        data = resp.json()
        tracks_raw = (
            data.get("tracks", {}).get("track", []) or
            data.get("toptracks", {}).get("track", [])
        )
        tracks = [(t["artist"]["name"], t["name"]) for t in tracks_raw if "artist" in t]
        return tracks if tracks else _fallback_tracks(year)
    except Exception:
        return _fallback_tracks(year)


def lastfm_get_track_info(artist: str, title: str) -> dict | None:
    """
    Fetch detailed track info (including real release year) from Last.fm.
    Returns dict with keys: title, artist, year, album_art  — or None.
    """
    if not LASTFM_API_KEY:
        return None
    try:
        params = {
            "method":      "track.getInfo",
            "artist":      artist,
            "track":       title,
            "api_key":     LASTFM_API_KEY,
            "format":      "json",
            "autocorrect": 1,
        }
        resp = requests.get(LASTFM_API_URL, params=params, timeout=8)
        if not resp.ok:
            return None
        data  = resp.json().get("track", {})
        album = data.get("album", {})

        # Best-effort year extraction
        year = None
        wiki = data.get("wiki", {})
        if wiki:
            published = wiki.get("published", "")
            # format: "05 Jan 1973, 00:00"
            import re
            m = re.search(r'\b(19[4-9]\d|20[0-2]\d)\b', published)
            if m:
                year = int(m.group(1))

        # Also try album release date if wiki didn't work
        if not year:
            release_date = album.get("releasedate", "") or ""
            import re
            m = re.search(r'\b(19[4-9]\d|20[0-2]\d)\b', release_date)
            if m:
                year = int(m.group(1))

        # Album art — pick largest
        images = album.get("image", [])
        album_art = ""
        for img in reversed(images):
            url = img.get("#text", "")
            if url and "2a96cbd8b46e442fc41c2b86b821562f" not in url:
                album_art = url
                break

        return {
            "title":     data.get("name", title),
            "artist":    data.get("artist", {}).get("name", artist) if isinstance(data.get("artist"), dict) else artist,
            "year":      year,
            "album_art": album_art,
            "album":     album.get("title", ""),
        }
    except Exception:
        return None


def _fallback_tracks(year: int) -> list:
    """Curated decade-based fallback when Last.fm is unavailable."""
    decade_tracks = {
        1950: [("Elvis Presley", "Hound Dog"), ("Chuck Berry", "Johnny B. Goode"), ("Buddy Holly", "That'll Be the Day")],
        1960: [("The Beatles", "Hey Jude"), ("The Rolling Stones", "Paint It Black"), ("The Doors", "Light My Fire")],
        1970: [("Led Zeppelin", "Stairway to Heaven"), ("Queen", "Bohemian Rhapsody"), ("Pink Floyd", "Wish You Were Here")],
        1980: [("Michael Jackson", "Billie Jean"), ("Madonna", "Like a Virgin"), ("Prince", "Purple Rain")],
        1990: [("Nirvana", "Smells Like Teen Spirit"), ("Oasis", "Wonderwall"), ("Radiohead", "Creep")],
        2000: [("Eminem", "Lose Yourself"), ("Linkin Park", "In the End"), ("Coldplay", "The Scientist")],
        2010: [("Adele", "Rolling in the Deep"), ("Ed Sheeran", "Shape of You"), ("Billie Eilish", "Bad Guy")],
        2020: [("Olivia Rodrigo", "drivers license"), ("The Weeknd", "Blinding Lights"), ("Dua Lipa", "Levitating")],
    }
    decade = (year // 10) * 10
    return decade_tracks.get(decade, decade_tracks[1980])


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    logged_in = bool(session.get("access_token"))
    return render_template("index.html", logged_in=logged_in)


@app.route("/login")
def login():
    scope = "user-read-playback-state user-modify-playback-state streaming"
    params = {
        "client_id":     SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         scope,
        "show_dialog":   "true",
    }
    url = requests.Request("GET", SPOTIFY_AUTH_URL, params=params).prepare().url
    return redirect(url)


@app.route("/callback")
def callback():
    code  = request.args.get("code")
    error = request.args.get("error")
    if error or not code:
        return redirect(url_for("index"))

    resp = requests.post(SPOTIFY_TOKEN_URL, data={
        "grant_type":    "authorization_code",
        "code":          code,
        "redirect_uri":  REDIRECT_URI,
        "client_id":     SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    })
    if resp.ok:
        d = resp.json()
        session["access_token"]  = d["access_token"]
        session["refresh_token"] = d.get("refresh_token", "")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/api/random-song")
def random_song():
    if not session.get("access_token"):
        return jsonify({"error": "not_logged_in"})

    # Pick year
    year = random.randint(*YEAR_RANGE)

    # Rock bias
    use_rock = ROCK_BIAS_PERCENT > 0 and random.randint(1, 100) <= ROCK_BIAS_PERCENT

    # Get candidates from Last.fm
    candidates = lastfm_get_top_tracks_for_year(year, rock=use_rock)
    if not candidates:
        return jsonify({"error": "no_tracks"})

    # Try a few candidates until one plays on Spotify
    random.shuffle(candidates)
    for artist, title in candidates[:10]:
        # Search Spotify for the track URI
        r, status = spotify_get("/search", params={
            "q":     f"track:{title} artist:{artist}",
            "type":  "track",
            "limit": 5,
        })
        if status != 200 or not r:
            continue

        items = r.json().get("tracks", {}).get("items", [])
        if not items:
            # Fallback: broader search
            r2, s2 = spotify_get("/search", params={
                "q":     f"{title} {artist}",
                "type":  "track",
                "limit": 5,
            })
            if s2 == 200 and r2:
                items = r2.json().get("tracks", {}).get("items", [])

        if not items:
            continue

        track_uri = items[0]["uri"]
        spotify_album_art = ""
        for img in items[0].get("album", {}).get("images", []):
            if img.get("url"):
                spotify_album_art = img["url"]
                break

        # Get accurate info from Last.fm
        lfm_info = lastfm_get_track_info(artist, title)
        canonical_year   = (lfm_info or {}).get("year") or year
        canonical_title  = (lfm_info or {}).get("title") or title
        canonical_artist = (lfm_info or {}).get("artist") or artist
        album_art        = (lfm_info or {}).get("album_art") or spotify_album_art

        # Play on Spotify
        play_r, play_s = spotify_put("/me/player/play", json={"uris": [track_uri]})

        played = play_s in (200, 204)

        return jsonify({
            "title":     canonical_title,
            "artist":    canonical_artist,
            "year":      canonical_year,
            "album_art": album_art,
            "uri":       track_uri,
            "played":    played,
        })

    return jsonify({"error": "no_playable_track"})


@app.route("/api/pause", methods=["POST"])
def pause():
    r, s = spotify_put("/me/player/pause")
    return jsonify({"status": s})


@app.route("/api/resume", methods=["POST"])
def resume():
    r, s = spotify_put("/me/player/play")
    return jsonify({"status": s})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

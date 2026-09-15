"""Spotify: two ways in, and a clear account of what each one costs.

Spotify has no anonymous, supported way to read a playlist. It has two
unsupported-ish ones, and siphon uses both in order:

**The embed page.** `open.spotify.com/embed/album/…` serves a widget whose
HTML carries the whole track list as JSON. No key, no sign-in, no developer
account — which means siphon works the moment it is installed. What it does
not carry is ISRC, release dates, or more than the first fifty tracks of a
long playlist, and because it is a page rather than an API it will break
without warning one day.

**The Web API.** A free developer app gives a client id and secret, and the
client-credentials flow needs no user login at all. Complete data, proper
pagination, ISRCs, and a contract that does not change under you.

So: the API when the keys are there, the embed when they are not, and a
sentence saying which happened when it matters. What must never happen is a
silent truncation — somebody asking for a 200-track playlist and getting 50
without being told has lost 150 tracks and does not know it.

As with Deezer, nothing here fetches audio. These are catalogue items with no
url; `resolve` finds them something fetchable.
"""

import base64
import json
import re
import time
import urllib.parse

import credentials
import net
from model import Item
from sources import SourceError

ACCOUNTS = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
EMBED = "https://open.spotify.com/embed"

# The embed hands back a page at a time and will not say so.
EMBED_PAGE = 50

_PATH = re.compile(r"/(?:intl-[a-z]{2}/)?(playlist|album|track)/([A-Za-z0-9]+)")
_URI = re.compile(r"^spotify:(playlist|album|track):([A-Za-z0-9]+)$")
_HOSTS = {"open.spotify.com", "play.spotify.com", "spotify.com",
          "www.spotify.com", "link.tospotify.com"}

_BROWSER = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"),
    "Accept-Language": "en",
}

_token = {"value": None, "expires": 0}


def handles(target):
    if _URI.match(target.strip()):
        return True
    try:
        host = (urllib.parse.urlparse(target).hostname or "").lower()
    except ValueError:
        return False
    return host in _HOSTS


def expand(url, limit=None, **_options):
    kind, identifier = _identify(url)
    if _have_keys():
        return _from_api(kind, identifier, limit=limit)
    return _from_embed(kind, identifier, limit=limit)


def _identify(target):
    target = target.strip()
    match = _URI.match(target)
    if match:
        return match.group(1), match.group(2)
    parsed = urllib.parse.urlparse(target)
    if (parsed.hostname or "").lower() == "link.tospotify.com":
        parsed = urllib.parse.urlparse(net.follow(target))
    match = _PATH.search(parsed.path)
    if not match:
        raise SourceError(
            f"siphon can read Spotify playlists, albums and tracks. That link "
            f"is none of those: {target}"
        )
    return match.group(1), match.group(2)


# ---------------------------------------------------------------------------
# The embed, which needs nothing
# ---------------------------------------------------------------------------

def _from_embed(kind, identifier, limit=None):
    html = net.get_bytes(f"{EMBED}/{kind}/{identifier}",
                         headers=_BROWSER).decode("utf-8", "replace")
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html, re.S,
    )
    if not match:
        raise SourceError(
            "Spotify's embed page has changed shape and siphon could not read "
            "it. Adding a Spotify key in Settings switches to the proper API, "
            "which does not move around like this."
        )
    try:
        entity = (json.loads(match.group(1))["props"]["pageProps"]["state"]
                  ["data"]["entity"])
    except (KeyError, ValueError, TypeError):
        raise SourceError("Spotify's embed page did not contain a track list.")

    tracks = entity.get("trackList") or []
    if not tracks:
        # A single track's embed is the entity itself — the fields sit at the
        # top level and there is no list to walk. An album's and a playlist's
        # are lists. Same page, two shapes.
        if kind == "track" and (entity.get("name") or entity.get("title")):
            return [_embed_single(entity, identifier)]
        raise SourceError(f"That Spotify {kind} has nothing in it siphon can fetch.")

    name = entity.get("name") or entity.get("title")
    cover = _embed_cover(entity)
    collection = None if kind == "track" else name

    items = []
    for index, track in enumerate(tracks, start=1):
        duration = track.get("duration")
        item = Item(
            origin="spotify",
            url=None,
            kind="audio",
            title=track.get("title"),
            artist=track.get("subtitle"),
            album=name if kind == "album" else None,
            album_artist=entity.get("subtitle") if kind == "album" else None,
            track_number=index if kind == "album" else None,
            track_total=len(tracks) if kind == "album" else None,
            duration=(duration / 1000.0) if isinstance(duration, (int, float)) else None,
            artwork_url=cover,
            webpage_url=f"https://open.spotify.com/{kind}/{identifier}",
            extra={"spotify_uri": track.get("uri"), "via": "embed"},
        )
        if collection:
            item.collection = collection
            item.collection_index = index
        items.append(item)

    if limit:
        items = items[: int(limit)]
    for item in items:
        item.extra["collection_size"] = len(items)

    # A page-sized answer is the shape of truncation. Saying so is the whole
    # difference between a limitation and a silent loss.
    if len(tracks) % EMBED_PAGE == 0 and kind == "playlist":
        items[0].extra["truncated"] = (
            f"Spotify's embed returns {EMBED_PAGE} tracks at a time and this "
            f"playlist filled the page exactly, so there may be more. Adding a "
            f"Spotify key in Settings reads the whole thing."
        )
    return items


def _embed_single(entity, identifier):
    """One track, from the shape the embed uses when there is only one."""
    duration = entity.get("duration")
    return Item(
        origin="spotify",
        url=None,
        kind="audio",
        title=entity.get("name") or entity.get("title"),
        artist=_embed_artists(entity),
        year=_year(entity.get("releaseDate", {}).get("isoString")
                   if isinstance(entity.get("releaseDate"), dict)
                   else entity.get("releaseDate")),
        duration=(duration / 1000.0) if isinstance(duration, (int, float)) else None,
        artwork_url=_embed_cover(entity),
        webpage_url=f"https://open.spotify.com/track/{identifier}",
        extra={"spotify_uri": entity.get("uri"), "via": "embed",
               "collection_size": 1},
    )


def _embed_artists(entity):
    names = [a.get("name") for a in (entity.get("artists") or []) if a.get("name")]
    return ", ".join(names) if names else entity.get("subtitle")


def _embed_cover(entity):
    for block in (entity.get("visualIdentity") or {}), (entity.get("coverArt") or {}):
        images = block.get("image") or block.get("sources") or []
        if images:
            best = max(images, key=lambda i: i.get("maxWidth") or i.get("width") or 0)
            if best.get("url"):
                return best["url"]
    return None


# ---------------------------------------------------------------------------
# The Web API, when there are keys for it
# ---------------------------------------------------------------------------

def _have_keys():
    return bool(credentials.get("spotify", "client_id")
                and credentials.get("spotify", "client_secret"))


def _bearer():
    """A client-credentials token. No user login; this reads public data only."""
    if _token["value"] and _token["expires"] > time.time() + 30:
        return _token["value"]

    identifier = credentials.get("spotify", "client_id")
    secret = credentials.get("spotify", "client_secret")
    basic = base64.b64encode(f"{identifier}:{secret}".encode()).decode()
    try:
        payload = net.get_json(
            ACCOUNTS,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=b"grant_type=client_credentials",
        )
    except net.HttpError as error:
        raise SourceError(
            f"Spotify would not accept those keys ({error}). Check them in "
            f"Settings — the secret is the one behind “View client secret”."
        ) from error
    _token["value"] = payload.get("access_token")
    _token["expires"] = time.time() + float(payload.get("expires_in") or 3600)
    if not _token["value"]:
        raise SourceError("Spotify returned no token for those keys.")
    return _token["value"]


def _api(path, **params):
    query = urllib.parse.urlencode(params) if params else ""
    url = f"{API}/{path}" + (f"?{query}" if query else "")
    try:
        return net.get_json(url,
                            headers={"Authorization": f"Bearer {_bearer()}"})
    except net.HttpError as error:
        text = str(error)
        if "refused" in text or "401" in text or "403" in text:
            _token["value"], _token["expires"] = None, 0
            raise SourceError(
                "Spotify rejected the key siphon has. If you rotated the "
                "secret, paste the new one into Settings — or run `siphon "
                "keys --set spotify.client_secret`, which asks for it without "
                "echoing it. siphon will fall back to the keyless embed in "
                "the meantime, which sees no ISRCs and stops at fifty tracks."
            ) from error
        raise SourceError(text) from error


def _from_api(kind, identifier, limit=None):
    if kind == "track":
        return [_api_track(_api(f"tracks/{identifier}"))]
    if kind == "album":
        album = _api(f"albums/{identifier}")
        entries = _pages(album.get("tracks") or {}, limit)
        cover = _api_cover(album)
        total = len(entries)
        items = []
        for index, data in enumerate(entries, start=1):
            item = _api_track(data, cover=cover)
            item.album = album.get("name")
            item.album_artist = _names(album.get("artists"))
            item.year = _year(album.get("release_date"))
            item.track_number = item.track_number or index
            item.track_total = total
            item.collection = f"{item.album_artist} — {album.get('name')}"
            item.collection_index = index
            items.append(item)
        return _sized(items)

    playlist = _api(f"playlists/{identifier}")
    entries = _pages(playlist.get("tracks") or {}, limit)
    items = []
    for index, row in enumerate(entries, start=1):
        track = (row or {}).get("track") or row
        if not track or track.get("type") == "episode":
            continue
        item = _api_track(track)
        item.collection = playlist.get("name")
        item.collection_index = index
        items.append(item)
    if not items:
        raise SourceError(
            f"“{playlist.get('name')}” has nothing in it siphon can fetch — "
            f"podcast episodes and local files do not count."
        )
    return _sized(items)


def _pages(block, limit=None):
    entries = list(block.get("items") or [])
    next_url = block.get("next")
    while next_url and (limit is None or len(entries) < limit):
        page = net.get_json(next_url,
                            headers={"Authorization": f"Bearer {_bearer()}"})
        entries.extend(page.get("items") or [])
        next_url = page.get("next")
    return entries[: int(limit)] if limit else entries


def _api_track(data, cover=None):
    album = data.get("album") or {}
    return Item(
        origin="spotify",
        url=None,
        kind="audio",
        title=data.get("name"),
        artist=_names(data.get("artists")),
        album=album.get("name"),
        album_artist=_names(album.get("artists")),
        track_number=data.get("track_number"),
        disc_number=data.get("disc_number"),
        year=_year(album.get("release_date")),
        isrc=(data.get("external_ids") or {}).get("isrc"),
        duration=(data.get("duration_ms") or 0) / 1000.0 or None,
        artwork_url=_api_cover(album) or cover,
        webpage_url=(data.get("external_urls") or {}).get("spotify"),
        extra={"spotify_id": data.get("id"), "via": "api"},
    )


def _api_cover(block):
    images = (block or {}).get("images") or []
    if not images:
        return None
    return max(images, key=lambda i: i.get("width") or 0).get("url")


def _names(artists):
    names = [a.get("name") for a in (artists or []) if a.get("name")]
    return ", ".join(names) if names else None


def _sized(items):
    for item in items:
        item.extra["collection_size"] = len(items)
    return items


def _year(date):
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None

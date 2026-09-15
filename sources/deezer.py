"""Deezer: a catalogue, not a source of bytes.

This is the first of the music sources, and it works differently from yt-dlp
in a way worth stating plainly. Deezer tells siphon *what the tracks are* —
title, artist, album, running time, ISRC, cover art. It does not hand over any
audio, and siphon does not ask it to. The items this module produces have no
`url` at all: they are catalogue entries, and `resolve` is what finds each one
something that can actually be fetched.

Deezer is first because its public API answers anyone. No key, no token, no
developer account, no sign-in — which makes it the one music service where the
whole path can be tested by anybody who checks out this repository.

One shape to know: a **playlist**'s nested tracks carry `isrc` and nothing
carries it on an **album**'s. So a playlist arrives fully described, and an
album's tracks are filled in one at a time, but only for the tracks somebody
actually fetches. Enriching all of them up front would be a request per track
for metadata most of them will never need.
"""

import re
import urllib.parse

import net
from model import Item
from sources import SourceError

API = "https://api.deezer.com"

# www.deezer.com/en/playlist/123, deezer.com/album/123, /track/123 — the
# language segment is optional and means nothing to the API.
_PATH = re.compile(r"/(?:[a-z]{2}/)?(playlist|album|track|artist)/(\d+)")
_HOSTS = {"deezer.com", "www.deezer.com", "api.deezer.com"}
_SHORT = {"deezer.page.link", "link.deezer.com", "dzr.page.link"}


def handles(target):
    try:
        host = (urllib.parse.urlparse(target).hostname or "").lower()
    except ValueError:
        return False
    return host in _HOSTS or host in _SHORT


def expand(url, limit=None, **_options):
    kind, identifier = _identify(url)
    if kind == "track":
        return [_track(net.get_json(f"{API}/track/{identifier}"))]
    if kind == "playlist":
        return _playlist(identifier, limit=limit)
    if kind == "album":
        return _album(identifier, limit=limit)
    raise SourceError(
        "siphon can read Deezer playlists, albums and tracks. An artist page "
        "is not one of those — open one of their albums instead."
    )


def _identify(url):
    if (urllib.parse.urlparse(url).hostname or "").lower() in _SHORT:
        url = net.follow(url)
    match = _PATH.search(urllib.parse.urlparse(url).path)
    if not match:
        raise SourceError(f"That does not look like a Deezer link: {url}")
    return match.group(1), match.group(2)


def _check(payload, what):
    if isinstance(payload, dict) and "error" in payload:
        message = (payload["error"] or {}).get("message", "")
        if "no data" in message.lower():
            raise SourceError(f"Deezer has no {what} with that id.")
        raise SourceError(f"Deezer refused: {message or 'no reason given'}")
    return payload


def _playlist(identifier, limit=None):
    payload = _check(net.get_json(f"{API}/playlist/{identifier}"), "playlist")
    title = payload.get("title")
    entries = _collect(payload.get("tracks") or {}, limit)
    if not entries:
        raise SourceError(f"“{title}” has nothing in it that siphon can fetch.")

    items = []
    for index, data in enumerate(entries, start=1):
        item = _track(data, cover=payload.get("picture_xl"))
        item.collection = title
        item.collection_index = index
        item.extra["collection_size"] = len(entries)
        items.append(item)
    return items


def _album(identifier, limit=None):
    payload = _check(net.get_json(f"{API}/album/{identifier}"), "album")
    title = payload.get("title")
    artist = (payload.get("artist") or {}).get("name")
    cover = payload.get("cover_xl") or payload.get("cover_big")
    entries = _collect(payload.get("tracks") or {}, limit)
    if not entries:
        raise SourceError(f"“{title}” has no tracks siphon can fetch.")

    year = _year(payload.get("release_date"))
    items = []
    for index, data in enumerate(entries, start=1):
        item = _track(data, cover=cover)
        item.album = item.album or title
        item.album_artist = artist
        item.year = item.year or year
        # An album's nested tracks carry no position, but they arrive in
        # order, which is the same fact by another route.
        item.track_number = item.track_number or index
        item.track_total = len(entries)
        item.collection = f"{artist} — {title}" if artist else title
        item.collection_index = index
        item.extra["collection_size"] = len(entries)
        items.append(item)
    return items


def _collect(block, limit=None):
    """Every page of a paged list, not just the first fifty."""
    entries = list(block.get("data") or [])
    next_url = block.get("next")
    # Deezer caps a page at a few hundred; a long playlist needs following.
    while next_url and (limit is None or len(entries) < limit):
        page = net.get_json(next_url)
        if not isinstance(page, dict) or "error" in page:
            break
        entries.extend(page.get("data") or [])
        next_url = page.get("next")
    if limit:
        entries = entries[:int(limit)]
    return [e for e in entries if e and e.get("readable", True)]


def _track(data, cover=None):
    """One Deezer track into a catalogue Item — deliberately with no url."""
    album = data.get("album") or {}
    artist = data.get("artist") or {}
    contributors = [c.get("name") for c in (data.get("contributors") or []) if c.get("name")]

    return Item(
        origin="deezer",
        url=None,                 # nothing fetchable yet; resolve's problem
        kind="audio",
        title=data.get("title_short") or data.get("title"),
        artist=artist.get("name") or (contributors[0] if contributors else None),
        album=album.get("title"),
        album_artist=(album.get("artist") or {}).get("name") or artist.get("name"),
        track_number=_int(data.get("track_position")),
        disc_number=_int(data.get("disk_number")),
        year=_year(data.get("release_date") or album.get("release_date")),
        isrc=data.get("isrc"),
        duration=_float(data.get("duration")),
        artwork_url=(album.get("cover_xl") or album.get("cover_big")
                     or data.get("cover_xl") or cover),
        webpage_url=data.get("link"),
        extra={
            "deezer_id": data.get("id"),
            "contributors": contributors,
            "explicit": bool(data.get("explicit_lyrics")),
        },
    )


def enrich(item):
    """Fill in what an album listing left out, for one track at a time.

    Called by the resolver, not by expand. An album of fourteen tracks would
    otherwise cost fourteen extra requests at the moment somebody pastes the
    link, for ISRCs that only matter for the tracks they go on to fetch.
    """
    identifier = item.extra.get("deezer_id")
    if not identifier or item.isrc:
        return item
    try:
        payload = net.get_json(f"{API}/track/{identifier}")
    except net.HttpError:
        return item          # metadata worth having, not worth failing over
    if not isinstance(payload, dict) or "error" in payload:
        return item
    item.isrc = item.isrc or payload.get("isrc")
    item.track_number = item.track_number or _int(payload.get("track_position"))
    item.disc_number = item.disc_number or _int(payload.get("disk_number"))
    item.year = item.year or _year(payload.get("release_date"))
    return item


def _int(value):
    try:
        return int(value) or None
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value) or None
    except (TypeError, ValueError):
        return None


def _year(date):
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None

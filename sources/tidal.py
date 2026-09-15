"""Tidal: the one service with no way in without a key.

Deezer answers anyone and Spotify has an embed that works keyless. Tidal has
neither — `openapi.tidal.com/v2` answers `UNAUTHORIZED` to everything, there
is no public embed JSON, and the share pages carry a title and nothing else.
So this module's first job is to fail well: without keys it says exactly what
is missing and where to get it, rather than producing an error about a 401.

**What siphon does not use.** The v2 API also exposes `/trackManifests/{id}`,
which returns a real playback manifest — the actual audio, DRM-protected.
siphon does not call it and will not. This module reads the catalogue: what
the tracks are, not how to decrypt them. `resolve` finds the audio elsewhere,
the same as for every other catalogue source.

**The shape.** v2 is JSON:API, which is a different animal from Deezer's plain
objects: a response has `data` (a resource, or a list of them), each resource
has `attributes` and `relationships`, and related resources arrive alongside
in `included` rather than nested. `_index` and `_related` below do that
joining once so the rest reads normally.

Four things that catch people out, all confirmed against real responses:

  * durations are ISO 8601 strings — `PT5M20S`, not a number of seconds;
  * every request needs a `countryCode`, because the catalogue really does
    differ by territory;
  * **a track's position is not on the track.** `trackNumber` and
    `volumeNumber` live in the `meta` of the album's *reference* to it, which
    is the only place the ordering exists — the track resource itself has no
    idea where it sits;
  * cover art is not an attribute either. It is a `coverArt` relationship to
    an `artworks` resource, whose `files` are the same image at seven sizes.
"""

import locale
import os
import re
import subprocess
import sys
import urllib.parse

import credentials
import net
from model import Item
from sources import SourceError

AUTH = "https://auth.tidal.com/v1/oauth2/token"
API = "https://openapi.tidal.com/v2"
JSONAPI = "application/vnd.api+json"

_PATH = re.compile(r"/(?:browse/)?(playlist|album|track|mix)/([A-Za-z0-9-]+)")
_HOSTS = {"tidal.com", "www.tidal.com", "listen.tidal.com", "embed.tidal.com",
          "link.tidal.com"}

_token = {"value": None, "expires": 0}


def handles(target):
    try:
        host = (urllib.parse.urlparse(target).hostname or "").lower()
    except ValueError:
        return False
    return host in _HOSTS


def expand(url, limit=None, **_options):
    kind, identifier = _identify(url)
    _require_keys()
    country = _country()

    if kind == "track":
        # albums.coverArt, not albums: a nested include has to name the
        # relationship you actually want, or the album arrives without its
        # artwork and the track quietly gets no cover.
        payload = _get(f"tracks/{identifier}", countryCode=country,
                       include="artists,albums.coverArt")
        resource = _resource(payload)
        index = _index(payload)
        item = _track(resource, index)
        albums = _related(resource, "albums", index)
        if albums:
            item.album = _attributes(albums[0]).get("title")
            item.artwork_url = item.artwork_url or artwork_url(albums[0], index)
        return [item]
    if kind == "album":
        return _album(identifier, country, limit=limit)
    if kind == "playlist":
        return _playlist(identifier, country, limit=limit)
    raise SourceError(
        "siphon can read Tidal playlists, albums and tracks. A mix is not one "
        "of those."
    )


def _identify(target):
    parsed = urllib.parse.urlparse(target.strip())
    if (parsed.hostname or "").lower() == "link.tidal.com":
        parsed = urllib.parse.urlparse(net.follow(target))
    match = _PATH.search(parsed.path)
    if not match:
        raise SourceError(f"That does not look like a Tidal link: {target}")
    return match.group(1), match.group(2)


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------

def _require_keys():
    if credentials.get("tidal", "client_id") and credentials.get("tidal", "client_secret"):
        return
    raise SourceError(
        "Tidal needs a key, and it is the only service here that does — there "
        "is no public way to read a Tidal playlist. A free developer app at "
        "developer.tidal.com/dashboard gives you a client id and secret; paste "
        "them into Settings, or run: siphon keys --set tidal.client_id"
    )


def _bearer():
    """A client-credentials token. No user login; this reads public catalogue."""
    import time
    if _token["value"] and _token["expires"] > time.time() + 30:
        return _token["value"]

    import base64
    identifier = credentials.get("tidal", "client_id")
    secret = credentials.get("tidal", "client_secret")
    basic = base64.b64encode(f"{identifier}:{secret}".encode()).decode()
    try:
        payload = net.get_json(
            AUTH,
            headers={"Authorization": f"Basic {basic}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data=b"grant_type=client_credentials",
        )
    except net.HttpError as error:
        raise SourceError(
            f"Tidal would not accept those keys ({error}). Check them in "
            f"Settings."
        ) from error
    _token["value"] = payload.get("access_token")
    _token["expires"] = time.time() + float(payload.get("expires_in") or 3600)
    if not _token["value"]:
        raise SourceError("Tidal returned no token for those keys.")
    return _token["value"]


def _country():
    """Tidal's catalogue differs by territory, so every request needs one."""
    stored = credentials.get("tidal", "country")
    if stored:
        return stored.upper()
    return _region() or "US"


def _region():
    """This machine's country, as well as it can honestly be worked out.

    Worth the trouble: Tidal's catalogue really does differ by territory, so
    guessing wrong means missing tracks rather than a wrong-looking setting.

    `locale.getlocale()` is no help on macOS — it reports ('C', 'UTF-8') in a
    process started from Finder, which carries no region at all. The system
    does know, and keeps it in AppleLocale, where the language and the region
    are allowed to disagree: `en_US@rg=gbzzzz` is English as spoken by someone
    in Britain, and the region that matters is the `rg` override, not the
    `en_US`. Reading only the language tag there would put a London user on
    the American catalogue.
    """
    if sys.platform == "darwin":
        try:
            out = subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"],
                capture_output=True, text=True, timeout=5,
            )
            tag = (out.stdout or "").strip()
        except (OSError, subprocess.SubprocessError):
            tag = ""
        found = region_from_tag(tag)
        if found:
            return found

    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        found = region_from_tag(os.environ.get(name) or "")
        if found:
            return found

    try:
        tag = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        tag = ""
    return region_from_tag(tag)


def region_from_tag(tag):
    """The country out of a locale tag, preferring an explicit `rg` override.

    `en_US@rg=gbzzzz` is the case that matters: the language says US and the
    region says GB, and the region is the one Tidal cares about.
    """
    if not tag:
        return None
    override = re.search(r"@rg=([A-Za-z]{2})", tag)
    if override:
        return override.group(1).upper()
    base = re.match(r"^[a-z]{2,3}[_-]([A-Za-z]{2})\b", tag)
    return base.group(1).upper() if base else None


def _get(path, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    url = f"{API}/{path}" + (f"?{query}" if query else "")
    return net.get_json(url, headers={
        "Authorization": f"Bearer {_bearer()}",
        "Accept": JSONAPI,
    })


# ---------------------------------------------------------------------------
# JSON:API, flattened
# ---------------------------------------------------------------------------

def _resource(payload):
    """The primary resource of a response, whether it came alone or in a list."""
    data = (payload or {}).get("data")
    if isinstance(data, list):
        if not data:
            raise SourceError("Tidal returned nothing for that.")
        return data[0]
    if not isinstance(data, dict):
        raise SourceError("Tidal returned a response siphon could not read.")
    return data


def _index(payload):
    """Everything in `included`, keyed by (type, id), so it can be looked up.

    JSON:API does not nest related resources — it puts them in a flat sidecar
    and leaves you to join. Doing that once here is what keeps the rest of
    this module readable.
    """
    index = {}
    for entry in (payload or {}).get("included") or []:
        if isinstance(entry, dict) and entry.get("id"):
            index[(entry.get("type"), str(entry["id"]))] = entry
    return index


def _linked(resource, name, index):
    """`(resource, meta)` for each entry on the far side of a relationship.

    The meta is the half that is easy to throw away and impossible to do
    without: an album's reference to a track carries `trackNumber` and
    `volumeNumber`, and they exist nowhere else. Drop them and every album
    comes out unnumbered, in whatever order the list happened to arrive.
    """
    relationship = ((resource or {}).get("relationships") or {}).get(name) or {}
    data = relationship.get("data")
    if isinstance(data, dict):
        data = [data]
    found = []
    for reference in data or []:
        entry = index.get((reference.get("type"), str(reference.get("id"))))
        if entry:
            found.append((entry, reference.get("meta") or {}))
    return found


def _related(resource, name, index):
    """Just the resources, for the relationships that carry no useful meta."""
    return [entry for entry, _ in _linked(resource, name, index)]


def _attributes(resource):
    return (resource or {}).get("attributes") or {}


_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>[\d.]+)S)?)?$"
)


def seconds(value):
    """Seconds from an ISO 8601 duration — `PT1H2M11S` is 3731.

    Tidal gives durations this way and nothing else here does. Feeding the
    string straight into a number is the obvious mistake, and it fails
    silently: every track ends up with no duration, and the resolver quietly
    loses a quarter of its evidence without anything looking broken.
    """
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) or None
    text = str(value).strip()
    if text.replace(".", "", 1).isdigit():
        # Not a shape Tidal sends today, but the cost of being wrong here is
        # a track with no duration and no error, so take the plain number too.
        return float(text) or None
    match = _DURATION.match(text)
    if not match:
        return None
    parts = match.groupdict()
    total = (
        int(parts["days"] or 0) * 86400
        + int(parts["hours"] or 0) * 3600
        + int(parts["minutes"] or 0) * 60
        + float(parts["seconds"] or 0)
    )
    return total or None


def artwork_url(resource, index):
    """The largest cover image, followed through the `coverArt` relationship.

    An `artworks` resource carries the same picture at seven sizes, 80px up to
    1280px, each with its dimensions in `meta`. Take the biggest: it is going
    into a file that may outlive several screens.
    """
    for art, _ in _linked(resource, "coverArt", index):
        files = _attributes(art).get("files") or []
        best, width = None, -1
        for entry in files:
            if not isinstance(entry, dict) or not entry.get("href"):
                continue
            size = (entry.get("meta") or {}).get("width") or 0
            if size > width:
                best, width = entry["href"], size
        if best:
            return best
    return None


def external_url(attributes):
    """The tidal.com address for a resource, out of `externalLinks`."""
    for link in attributes.get("externalLinks") or []:
        if isinstance(link, dict) and link.get("href"):
            return link["href"]
    return None


# ---------------------------------------------------------------------------
# Building items
# ---------------------------------------------------------------------------

def _track(resource, index, album=None, cover=None, position=None, meta=None):
    """One track resource into a catalogue Item.

    `meta` is the album or playlist's reference metadata — where the track
    number actually lives. `position` is the fallback for a listing that has
    none, which is what a playlist is.
    """
    attributes = _attributes(resource)
    meta = meta or {}
    artists = [_attributes(a).get("name")
               for a in _related(resource, "artists", index)]
    artists = [name for name in artists if name]

    title = attributes.get("title")
    version = attributes.get("version")
    if version:
        # "Harder, Better, Faster, Stronger" + "Remastered" is two fields on
        # Tidal and one name everywhere else.
        title = f"{title} ({version})"

    return Item(
        origin="tidal",
        url=None,                 # catalogue only; resolve finds the audio
        kind="audio",
        title=title,
        artist=", ".join(artists) if artists else None,
        album=album,
        isrc=attributes.get("isrc"),
        duration=seconds(attributes.get("duration")),
        track_number=meta.get("trackNumber") or position,
        disc_number=meta.get("volumeNumber"),
        artwork_url=artwork_url(resource, index) or cover,
        webpage_url=external_url(attributes),
        extra={"tidal_id": resource.get("id"),
               "explicit": attributes.get("explicit"),
               "cursor": meta.get("itemCursor")},
    )


def _album(identifier, country, limit=None):
    payload = _get(f"albums/{identifier}", countryCode=country,
                   include="items.artists,artists,coverArt")
    album = _resource(payload)
    index = _index(payload)
    attributes = _attributes(album)

    title = attributes.get("title")
    artists = [_attributes(a).get("name") for a in _related(album, "artists", index)]
    artist = ", ".join(n for n in artists if n) or None
    cover = artwork_url(album, index)
    year = _year(attributes.get("releaseDate"))

    tracks = _linked(album, "items", index)
    if len(tracks) < (attributes.get("numberOfItems") or 0):
        tracks += _page(f"albums/{identifier}/relationships/items", country,
                        index, have=len(tracks), limit=limit)
    if not tracks:
        raise SourceError(f"“{title}” came back with no tracks siphon can read.")
    if limit:
        tracks = tracks[: int(limit)]

    items = []
    for position, (resource, meta) in enumerate(tracks, start=1):
        item = _track(resource, index, album=title, cover=cover,
                      position=position, meta=meta)
        item.album_artist = artist
        item.year = item.year or year
        item.track_total = len(tracks)
        item.collection = f"{artist} — {title}" if artist else title
        item.collection_index = item.track_number or position
        item.extra["collection_size"] = len(tracks)
        items.append(item)
    return items


def _playlist(identifier, country, limit=None):
    payload = _get(f"playlists/{identifier}", countryCode=country,
                   include="items,items.artists,items.albums")
    playlist = _resource(payload)
    index = _index(payload)
    title = _attributes(playlist).get("name") or _attributes(playlist).get("title")

    tracks = _linked(playlist, "items", index)
    tracks += _page(f"playlists/{identifier}/relationships/items", country,
                    index, have=len(tracks), limit=limit)
    if not tracks:
        raise SourceError(f"“{title}” came back with no tracks siphon can read.")
    if limit:
        tracks = tracks[: int(limit)]

    items = []
    for position, (resource, meta) in enumerate(tracks, start=1):
        item = _track(resource, index, position=None, meta={})
        item.artwork_url = item.artwork_url or artwork_url(playlist, index)
        item.collection = title
        item.collection_index = position
        item.extra["collection_size"] = len(tracks)
        items.append(item)
    return items


def _page(path, country, index, have=0, limit=None, cap=60):
    """Follow JSON:API cursor pages, folding each page's `included` in as it goes."""
    gathered = []
    cursor = None
    for _ in range(cap):
        if limit and have + len(gathered) >= int(limit):
            break
        params = {"countryCode": country, "include": "items.artists"}
        if cursor:
            params["page[cursor]"] = cursor
        try:
            payload = _get(path, **params)
        except net.HttpError:
            break              # a partial list is better than no list
        index.update(_index(payload))
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            break
        for reference in data:
            entry = index.get((reference.get("type"), str(reference.get("id"))))
            if entry is None and reference.get("attributes"):
                entry = reference
            if entry:
                gathered.append((entry, reference.get("meta") or {}))
        cursor = _next_cursor(payload)
        if not cursor:
            break
    return gathered


def _next_cursor(payload):
    nxt = ((payload or {}).get("links") or {}).get("next")
    if not nxt:
        return None
    query = urllib.parse.parse_qs(urllib.parse.urlparse(nxt).query)
    values = query.get("page[cursor]") or query.get("cursor")
    return values[0] if values else None


def _year(date):
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return None

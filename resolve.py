"""Finding something fetchable for a track that is only a catalogue entry.

Deezer says a track exists, what it is called, who made it and how long it
runs. It does not hand over any audio. This module is what stands between that
description and a file: it searches for candidates, scores them, and either
names a winner or admits it could not find one.

**Why this is not a duration match.** The obvious approach — search the title,
take whatever is closest in length — is wrong, and the failure is not rare.
Searching for Daft Punk's "Harder, Better, Faster, Stronger" (226 seconds on
Deezer) returns the real track at 223 seconds and a completely different
recording, "Daft Hands", at 225. The impostor is closer. Duration is a strong
*confirming* signal and a hopeless *deciding* one, so it is worth a quarter of
the score and never more.

**What the score is for.** Not to pick the best candidate — to say how much
the pick should be trusted. A resolver that always returns its top result has
no way to tell "this is certainly it" from "this is the least bad of five
wrong answers", and the second case is exactly where a music downloader
quietly fills a folder with karaoke versions. Every match carries its
confidence and the reasons behind it, and anything below the floor is a
refusal with the near-miss named, not a shrug.
"""

import difflib
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass, field

from platform_support import require, MissingProgram

# A pick at or above this is acted on without comment.
CONFIDENT = 0.75
# Below this, nothing is downloaded and the closest miss is named instead.
FLOOR = 0.50

CANDIDATES = 6

# Words that mean "this is a different recording from the one asked for" —
# unless the track really is called that, which is why the target title is
# always checked before a penalty is applied.
UNWANTED = {
    "live": 0.30, "cover": 0.35, "karaoke": 0.45, "instrumental": 0.35,
    "remix": 0.30, "sped up": 0.40, "slowed": 0.40, "nightcore": 0.45,
    "8d audio": 0.45, "reaction": 0.50, "tutorial": 0.40, "loop": 0.25,
    "mashup": 0.35, "acapella": 0.35, "a cappella": 0.35, "reverb": 0.25,
    "concert": 0.30, "rehearsal": 0.30, "teaser": 0.35, "trailer": 0.35,
}

# Noise in candidate titles that says nothing about which recording it is.
_DECORATION = re.compile(
    r"\((?:official\s*)?(?:music\s*)?(?:video|audio|visualizer|lyric[s]?|hd|4k|"
    r"mv|clip)\)|\[(?:[^\]]*(?:official|audio|video|lyric|hd|4k)[^\]]*)\]|"
    r"\b(?:official\s+(?:video|audio|music\s+video)|hq|hd|4k|full\s+album)\b",
    re.IGNORECASE,
)
_FEATURING = re.compile(r"\s*[\(\[]?\b(?:feat\.?|ft\.?|featuring|with)\b[^\)\]]*[\)\]]?",
                        re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


@dataclass
class Candidate:
    url: str
    title: str
    uploader: str
    duration: float = None
    verified: bool = False
    score: float = 0.0
    reasons: list = field(default_factory=list)

    def display(self):
        return f"{self.title} — {self.uploader}"


class Unresolved(RuntimeError):
    """Nothing convincing was found. The message names the closest miss."""


def normalise(text):
    """Strip a title down to the part that identifies the recording."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _DECORATION.sub(" ", text)
    text = _FEATURING.sub(" ", text)
    text = _PUNCTUATION.sub(" ", text.casefold())
    return _SPACES.sub(" ", text).strip()


def query_for(item):
    """What to type into the search box."""
    parts = [item.artist, item.title]
    return " ".join(p for p in parts if p).strip() or (item.title or "")


# ---------------------------------------------------------------------------
# Searching
# ---------------------------------------------------------------------------

def search(query, limit=CANDIDATES, timeout=90):
    """Candidates for a query, cheaply — a flat search, nothing downloaded."""
    try:
        binary = require("yt-dlp")
    except MissingProgram as missing:
        raise Unresolved(str(missing)) from missing

    argv = [
        binary, "--dump-single-json", "--flat-playlist", "--no-warnings",
        "--ignore-config", "--no-colors",
        f"ytsearch{int(limit)}:{query}",
    ]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.SubprocessError as error:
        raise Unresolved(f"The search for “{query}” did not come back.") from error
    if out.returncode != 0:
        raise Unresolved(f"Could not search for “{query}”.")
    try:
        payload = json.loads(out.stdout)
    except ValueError:
        raise Unresolved(f"The search for “{query}” returned nothing readable.")

    candidates = []
    for entry in payload.get("entries") or []:
        if not entry or entry.get("live_status") == "is_live":
            continue
        url = entry.get("url") or (
            f"https://www.youtube.com/watch?v={entry['id']}" if entry.get("id") else None
        )
        if not url:
            continue
        candidates.append(Candidate(
            url=url,
            title=entry.get("title") or "",
            uploader=entry.get("channel") or entry.get("uploader") or "",
            duration=_float(entry.get("duration")),
            verified=bool(entry.get("channel_is_verified")),
        ))
    return candidates


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(item, candidate):
    """How much this candidate should be trusted, and why."""
    reasons = []
    wanted_title = normalise(item.title)
    wanted_artist = normalise(item.artist)
    got_title = normalise(candidate.title)
    got_channel = normalise(candidate.uploader)

    # -- the title ------------------------------------------------------
    # The channel name is very often repeated at the front of the title
    # ("Daft Punk - Harder, Better…"), so the artist is taken out of both
    # sides before comparing what is left.
    bare = got_title
    if wanted_artist and bare.startswith(wanted_artist):
        bare = bare[len(wanted_artist):].strip(" -")
    ratio = difflib.SequenceMatcher(None, wanted_title, bare).ratio()
    if wanted_title and wanted_title in got_title:
        ratio = max(ratio, 0.92)
        reasons.append("title matches")
    elif ratio >= 0.8:
        reasons.append(f"title close ({ratio:.0%})")
    else:
        reasons.append(f"title differs ({ratio:.0%})")
    title_signal = ratio

    # -- the artist -----------------------------------------------------
    artist_signal = 0.0
    if not wanted_artist:
        artist_signal = 0.5
    elif got_channel.endswith(" topic") and wanted_artist in got_channel:
        # A "- Topic" channel is YouTube's own auto-generated upload of the
        # distributed release. It is as close to the official audio as this
        # ever gets.
        artist_signal = 1.0
        reasons.append("official auto-generated upload")
    elif wanted_artist and wanted_artist in got_channel:
        artist_signal = 0.95
        reasons.append("channel is the artist")
    elif wanted_artist and wanted_artist in got_title:
        artist_signal = 0.75
        reasons.append("artist named in the title")
    else:
        overlap = _overlap(wanted_artist, f"{got_channel} {got_title}")
        artist_signal = 0.45 * overlap
        if overlap < 0.5:
            reasons.append("artist not named")
    if candidate.verified and artist_signal >= 0.7:
        artist_signal = min(1.0, artist_signal + 0.05)

    # -- the running time -----------------------------------------------
    if item.duration and candidate.duration:
        delta = abs(item.duration - candidate.duration)
        if delta <= 2:
            duration_signal, word = 1.0, "exact"
        elif delta <= 5:
            duration_signal, word = 0.9, f"{delta:.0f}s out"
        elif delta <= 10:
            duration_signal, word = 0.65, f"{delta:.0f}s out"
        elif delta <= 20:
            duration_signal, word = 0.3, f"{delta:.0f}s out"
        else:
            duration_signal, word = 0.0, f"{delta:.0f}s out"
        reasons.append(f"length {word}")
    else:
        duration_signal = 0.5          # unknown is not evidence either way

    total = 0.45 * title_signal + 0.30 * artist_signal + 0.25 * duration_signal

    # -- penalties ------------------------------------------------------
    haystack = f"{got_title} {got_channel}"
    asked_for = f"{wanted_title} {wanted_artist}"
    for word, cost in UNWANTED.items():
        if word in haystack and word not in asked_for:
            total -= cost
            reasons.append(f"looks like a {word} version")

    candidate.score = max(0.0, min(1.0, total))
    candidate.reasons = reasons
    return candidate.score


def rank(item, candidates):
    for candidate in candidates:
        score(item, candidate)
    return sorted(candidates, key=lambda c: c.score, reverse=True)


# ---------------------------------------------------------------------------
# The thing everything else calls
# ---------------------------------------------------------------------------

def resolve(item, limit=CANDIDATES):
    """Give a catalogue item a url, or refuse and say what the closest was.

    Returns the winning Candidate. The item is updated in place with its url
    and the confidence, so whatever happens next can show how sure this was.
    """
    if item.url:
        return None                    # already fetchable; nothing to do

    query = query_for(item)
    if not query:
        raise Unresolved("That track has no title to search for.")

    ranked = rank(item, search(query, limit=limit))
    if not ranked:
        raise Unresolved(f"Nothing at all came back for “{query}”.")

    best = ranked[0]
    if best.score < FLOOR:
        raise Unresolved(
            f"No convincing match for “{query}”. The closest was "
            f"“{best.display()}” at {best.score:.0%} — {', '.join(best.reasons)}."
        )

    item.url = best.url
    item.resolved_from = item.origin
    item.match_confidence = best.score
    item.extra["match"] = {
        "title": best.title,
        "uploader": best.uploader,
        "duration": best.duration,
        "score": best.score,
        "reasons": best.reasons,
        "query": query,
        # The runners-up are kept so an interface can offer them without
        # searching again.
        "alternatives": [
            {"title": c.title, "uploader": c.uploader, "url": c.url,
             "duration": c.duration, "score": c.score}
            for c in ranked[1:4]
        ],
    }
    return best


def _overlap(wanted, haystack):
    if not wanted:
        return 0.0
    words = [w for w in wanted.split() if len(w) > 2]
    if not words:
        return 0.0
    return sum(1 for w in words if w in haystack) / len(words)


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

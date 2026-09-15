"""yt-dlp: the fetch engine, driven as a program rather than imported.

This is the source that claims everything nothing else does, which in practice
is most of the web — YouTube, YouTube Music, SoundCloud, Bandcamp, Vimeo and
roughly eighteen hundred others.

It is a subprocess on purpose. yt-dlp ships a release most weeks because the
sites it reads keep changing underneath it; a copy vendored into siphon would
be broken by the time anybody installed it, and a copy pinned in a lockfile
would be broken on a schedule. Calling the one on the user's PATH means
`brew upgrade yt-dlp` fixes siphon without siphon being touched.

Two things this module will not do. It does not ask yt-dlp to convert
anything — `-x`, `--recode-video` and the rest are absent, because conversion
is the other half of the pipeline and having two things that both re-encode is
how you end up encoding twice. And it does not download while expanding: a
playlist is enumerated flat and cheaply, so that forty tracks can be shown to
somebody before any of them start.
"""

import json
import os
import re
import subprocess
import threading
from pathlib import Path

from model import Item
from platform_support import require, MissingProgram
from sources import SourceError
import formats

PROGRESS_MARK = "@@P|"
FILE_MARK = "@@F|"

# Stdout template. Pipe-separated because every field in it is a number or a
# short status word; anything free-text stays out so nothing needs escaping.
_PROGRESS_TEMPLATE = (
    "download:" + PROGRESS_MARK +
    "%(progress.status)s|%(progress.downloaded_bytes)s|"
    "%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|"
    "%(progress.speed)s|%(progress.eta)s"
)

_BASE_ARGS = (
    "--newline",
    "--no-colors",
    "--ignore-config",        # the user's own yt-dlp.conf must not surprise us
    "--no-playlist-reverse",
    "--retries", "5",
    "--fragment-retries", "10",
    "--concurrent-fragments", "4",
)


def handles(target):
    return target.startswith("http://") or target.startswith("https://")


# ---------------------------------------------------------------------------
# Expanding
# ---------------------------------------------------------------------------

def expand(url, limit=None, playlist=True, **_options):
    """A URL in, one Item or many out. Nothing is downloaded."""
    binary = _binary()
    argv = [binary, "--dump-single-json", "--no-warnings", *_BASE_ARGS]
    if playlist:
        # Flat means "list what is in it without opening each one", which is
        # the difference between two seconds and two minutes for a long list.
        argv += ["--flat-playlist"]
    else:
        argv += ["--no-playlist"]
    if limit:
        argv += ["--playlist-items", f"1:{int(limit)}"]
    argv.append(url)

    result = _run(argv, timeout=180)
    if result.returncode != 0:
        raise SourceError(_explain(result.stderr, url))
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        raise SourceError(
            f"yt-dlp answered for {url} in a way siphon could not read. "
            f"It may be worth running `yt-dlp -J {url}` to see what it says."
        )

    if payload.get("_type") == "playlist":
        entries = [e for e in (payload.get("entries") or []) if e]
        collection = payload.get("title")
        items = []
        for index, entry in enumerate(entries, start=1):
            if entry.get("_type") == "playlist":
                continue            # a playlist of playlists; one level is enough
            item = _item(entry)
            item.collection = collection
            item.collection_index = index
            items.append(item)
        if not items:
            raise SourceError(
                f"{collection or url} looks like a playlist with nothing in it "
                f"siphon can fetch."
            )
        return items

    return [_item(payload)]


def _item(data):
    """One yt-dlp info dict into an Item."""
    url = data.get("webpage_url") or data.get("url") or data.get("original_url")
    if not url and data.get("id") and data.get("ie_key") == "Youtube":
        url = f"https://www.youtube.com/watch?v={data['id']}"

    duration = data.get("duration")
    item = Item(
        origin="ytdlp",
        url=url,
        webpage_url=data.get("webpage_url") or url,
        kind=_kind(data),
        title=data.get("track") or data.get("title"),
        artist=data.get("artist") or data.get("creator"),
        album=data.get("album"),
        album_artist=data.get("album_artist"),
        track_number=_int(data.get("track_number")),
        disc_number=_int(data.get("disc_number")),
        year=_year(data),
        duration=float(duration) if isinstance(duration, (int, float)) else None,
        artwork_url=data.get("thumbnail"),
        uploader=data.get("uploader") or data.get("channel") or data.get("uploader_id"),
        extra={
            "extractor": data.get("extractor_key") or data.get("ie_key"),
            "id": data.get("id"),
            "live": bool(data.get("is_live")),
            "filesize_approx": data.get("filesize_approx"),
        },
    )
    # A music extractor gives an artist; a video one gives a channel. Falling
    # back means the tagger always has something to write.
    if not item.artist and item.kind == "audio":
        item.artist = item.uploader
    return item


def _kind(data):
    if data.get("vcodec") in (None, "none") and data.get("acodec") not in (None, "none"):
        return "audio"
    extractor = (data.get("extractor_key") or data.get("ie_key") or "").lower()
    if extractor in {"soundcloud", "bandcamp", "youtubemusic"}:
        return "audio"
    return "video"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _year(data):
    date = data.get("release_date") or data.get("upload_date")
    if isinstance(date, str) and len(date) >= 4 and date[:4].isdigit():
        return int(date[:4])
    return _int(data.get("release_year"))


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch(item, target, workdir, on_progress=None, should_cancel=None,
          options=None):
    """Download one item into `workdir`. Returns the path written.

    `target` steers which rendition is asked for — there is no sense pulling a
    4K stream to answer a request for 720p — and which container yt-dlp merges
    into, so that the conversion stage afterwards usually finds it has nothing
    to do.
    """
    binary = _binary()
    require("ffmpeg")            # yt-dlp needs it to merge video with audio
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    options = options or {}

    # --print puts yt-dlp into quiet mode, and quiet mode silences progress.
    # --progress asks for it back; without it the bar never moves and the
    # download looks like a hang.
    argv = [binary, *_BASE_ARGS, "--no-playlist", "--no-simulate", "--progress",
            "--progress-template", _PROGRESS_TEMPLATE,
            "--print", "after_move:" + FILE_MARK + "%(filepath)s",
            "-o", str(workdir / "%(id)s.%(ext)s"),
            "--no-warnings"]
    argv += _format_args(target)

    cookies = options.get("cookies_from_browser")
    if cookies:
        argv += ["--cookies-from-browser", cookies]
    cookie_file = options.get("cookie_file")
    if cookie_file:
        argv += ["--cookies", cookie_file]
    if options.get("rate_limit"):
        argv += ["--limit-rate", str(options["rate_limit"])]
    argv += list(options.get("extra_args") or ())

    argv.append(item.url)

    written = []
    errors = []

    def on_line(line):
        if line.startswith(PROGRESS_MARK):
            if on_progress:
                update = _parse_progress(line[len(PROGRESS_MARK):])
                if update:
                    on_progress(update)
        elif line.startswith(FILE_MARK):
            written.append(line[len(FILE_MARK):].strip())
        elif line.startswith("ERROR:"):
            errors.append(line)

    code = _stream(argv, on_line, should_cancel=should_cancel, errors=errors)

    if code != 0:
        if should_cancel and should_cancel():
            raise Cancelled("Stopped before it finished.")
        raise SourceError(_explain("\n".join(errors), item.url))

    path = _settle(written, workdir)
    if path is None:
        raise SourceError(
            f"yt-dlp reported success for {item.display()} but left no file "
            f"behind, which usually means the download was skipped."
        )
    return path


def _settle(written, workdir):
    """The file yt-dlp actually left. Trust its word, then trust the disk."""
    for candidate in reversed(written):
        if candidate and os.path.isfile(candidate):
            return candidate
    leftovers = [
        p for p in workdir.iterdir()
        if p.is_file() and p.suffix not in {".part", ".ytdl", ".temp"}
    ]
    if leftovers:
        return str(max(leftovers, key=lambda p: p.stat().st_size))
    return None


def _format_args(target):
    """Which streams to ask for, and what to merge them into.

    The codec preferences are not cosmetic. Asking for h264 and AAC when the
    target is mp4 means the streams can be dropped into the container
    untouched; taking whatever came first would mean re-encoding a file that
    was already the right shape.
    """
    target = formats.resolve(target)

    if target.audio_only:
        selector = "bestaudio/best"
        sort = []
        if target.acodec == "opus":
            sort = ["acodec:opus"]
        elif target.acodec in {"aac", "alac"}:
            sort = ["acodec:aac"]
        elif target.acodec == "mp3":
            sort = ["acodec:mp3"]
        argv = ["-f", selector]
        if sort:
            argv += ["-S", ",".join(sort)]
        return argv

    height = target.height
    if height:
        selector = (f"bv*[height<=?{height}]+ba/b[height<=?{height}]/"
                    f"bv*+ba/b")
    else:
        selector = "bv*+ba/b"

    sort = []
    table = formats.CONTAINER_CODECS.get(target.container, {})
    # Steering the codec choice is only worth doing for a container that is
    # fussy about what it holds. mkv holds everything, so biasing it towards
    # h264 would do the opposite of what it looks like: on YouTube the h264
    # rendition tops out lower than the VP9 and AV1 ones, so "best quality,
    # nothing re-encoded" has to mean taking yt-dlp's own pick.
    fussy = len(table.get("video", ())) <= 4
    if target.vcodec:
        sort.append(f"vcodec:{target.vcodec}")
    elif fussy and "h264" in table.get("video", set()):
        sort.append("vcodec:h264")
    elif fussy and "vp9" in table.get("video", set()):
        sort.append("vcodec:vp9")
    if target.acodec:
        sort.append(f"acodec:{target.acodec}")
    elif fussy and "aac" in table.get("audio", set()):
        sort.append("acodec:aac")
    elif fussy and "opus" in table.get("audio", set()):
        sort.append("acodec:opus")

    argv = ["-f", selector]
    if sort:
        argv += ["-S", ",".join(sort)]

    # Merge straight into the wanted container when it can hold what we asked
    # for; mkv otherwise, because it holds everything and merging into it is
    # never a re-encode.
    merge = target.container if target.container in {"mp4", "webm", "mkv", "mov"} else "mkv"
    argv += ["--merge-output-format", merge]
    return argv


def _parse_progress(payload):
    status, done, total, estimate, speed, eta = (payload.split("|") + [""] * 6)[:6]
    total_bytes = _number(total) or _number(estimate)
    done_bytes = _number(done)
    update = {
        "status": status.strip() or "downloading",
        "bytes_done": int(done_bytes) if done_bytes else None,
        "bytes_total": int(total_bytes) if total_bytes else None,
        "speed": _number(speed),
        "eta": _number(eta),
    }
    if update["bytes_total"]:
        update["progress"] = min(1.0, (update["bytes_done"] or 0) / update["bytes_total"])
    return update


def _number(text):
    text = (text or "").strip()
    if not text or text in {"NA", "None", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Running yt-dlp
# ---------------------------------------------------------------------------

def _binary():
    try:
        return require("yt-dlp")
    except MissingProgram as missing:
        raise SourceError(str(missing)) from missing


def _run(argv, timeout=120):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def _stream(argv, on_line, should_cancel=None, errors=None):
    """Run yt-dlp, handing every stdout line to `on_line` as it arrives.

    stderr is drained in a thread. This is not tidiness: a pipe nobody is
    reading fills at 64 KB and the child blocks writing to it, which looks
    exactly like a download that has mysteriously stalled.
    """
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )

    def drain():
        for line in process.stderr:
            line = line.rstrip("\n")
            if errors is not None and line.strip():
                errors.append(line)
                del errors[:-40]

    pump = threading.Thread(target=drain, daemon=True)
    pump.start()

    try:
        for line in process.stdout:
            on_line(line.rstrip("\n"))
            if should_cancel and should_cancel():
                process.terminate()
                break
    finally:
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        pump.join(timeout=5)
        # One leaked descriptor per job is a queue that dies after a few
        # hundred of them, on a machine that has been running for a week.
        for pipe in (process.stdout, process.stderr):
            try:
                pipe.close()
            except (OSError, AttributeError):
                pass

    return process.returncode


_SIGNATURES = (
    (r"Sign in to confirm (you|your age)",
     "YouTube is asking this download to prove it is not a robot. This is the "
     "block that took the public cobalt instance down, and on a home "
     "connection it is usually temporary. Waiting is the first remedy; "
     "supplying browser cookies in Settings is the second."),
    (r"Private video|This video is private",
     "That video is private, so there is nothing to fetch."),
    (r"Video unavailable|This video is unavailable",
     "That video is unavailable — removed, or blocked where this machine is."),
    (r"members-only|join this channel",
     "That video is behind a channel membership."),
    (r"is not a valid URL|Unsupported URL",
     "No extractor recognised that address."),
    (r"HTTP Error 429|Too Many Requests",
     "The site is rate-limiting this machine. Leave it a few minutes."),
    (r"DRM|protected",
     "That stream is DRM-protected. siphon does not decrypt protected "
     "streams, and will not."),
)


def _explain(stderr, url):
    """yt-dlp's error, said as a sentence about what to do next."""
    text = stderr or ""
    for pattern, sentence in _SIGNATURES:
        if re.search(pattern, text, re.IGNORECASE):
            return sentence
    for line in reversed(text.strip().splitlines()):
        if line.startswith("ERROR:"):
            return line[len("ERROR:"):].strip() or f"yt-dlp could not fetch {url}."
    return f"yt-dlp could not fetch {url}."


class Cancelled(Exception):
    """The job was stopped on purpose."""

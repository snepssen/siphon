"""What a target format actually means, and what a container will hold.

Two kinds of fact live here and nothing else does:

  * the presets — "mp3" or "1080p" as a thing somebody can ask for, spelled
    out into the container and codecs it implies;
  * the container/codec compatibility table, which is what lets siphon tell
    the difference between a conversion and a rename.

That second one is the whole reason this file is separate from the ffmpeg
engine. "Give me this as an mp4" usually means the video is already h264 in a
webm wrapper and the only work needed is moving the streams into a new
container — seconds, and not a single pixel touched. Deciding that requires
knowing what mp4 is allowed to hold, which is a fact about the format, not
about ffmpeg.
"""

from dataclasses import dataclass, field

AUDIO = "audio"
VIDEO = "video"
IMAGE = "image"
DOCUMENT = "document"


@dataclass(frozen=True)
class Target:
    """A format somebody asked for.

    A codec of None means "keep whatever is already there, if this container
    can hold it" — which is what makes a remux possible. A named codec means
    the stream is re-encoded even when it would technically have fitted.
    """

    name: str
    kind: str
    container: str
    summary: str
    acodec: str = None
    vcodec: str = None
    abitrate: str = None          # e.g. "320k"; None for lossless or source
    height: int = None            # cap the long-ish edge; None leaves it alone
    fps: float = None
    lossless: bool = False
    quality: int = None           # 1-100 for lossy images; None leaves it alone
    max_edge: int = None          # cap the longest side of an image
    extra: tuple = field(default_factory=tuple)   # raw ffmpeg args, escape hatch

    @property
    def extension(self):
        return self.container

    @property
    def audio_only(self):
        return self.kind == AUDIO

    @property
    def visual(self):
        return self.kind == IMAGE

    @property
    def textual(self):
        return self.kind == DOCUMENT


# Codecs each container is allowed to carry. Used only to decide whether the
# existing streams can be copied; it is deliberately conservative, because
# being wrong here means writing a file that some players silently refuse.
CONTAINER_CODECS = {
    "mp4":  {"video": {"h264", "hevc", "av1", "mpeg4"},
             "audio": {"aac", "alac", "mp3", "ac3", "eac3"}},
    "m4a":  {"video": set(),
             "audio": {"aac", "alac"}},
    "mkv":  {"video": {"h264", "hevc", "av1", "vp8", "vp9", "mpeg4", "theora",
                       "prores", "ffv1"},
             "audio": {"aac", "alac", "mp3", "opus", "vorbis", "flac", "ac3",
                       "eac3", "dts", "pcm_s16le", "pcm_s24le", "truehd"}},
    "webm": {"video": {"vp8", "vp9", "av1"},
             "audio": {"opus", "vorbis"}},
    "mov":  {"video": {"h264", "hevc", "prores", "mpeg4"},
             "audio": {"aac", "alac", "pcm_s16le", "pcm_s24le"}},
    "mp3":  {"video": set(), "audio": {"mp3"}},
    "opus": {"video": set(), "audio": {"opus"}},
    "ogg":  {"video": set(), "audio": {"opus", "vorbis", "flac"}},
    "flac": {"video": set(), "audio": {"flac"}},
    "wav":  {"video": set(), "audio": {"pcm_s16le", "pcm_s24le", "pcm_f32le"}},
    "aiff": {"video": set(), "audio": {"pcm_s16be", "pcm_s24be"}},
}


PRESETS = {
    # ---- audio ----------------------------------------------------------
    "mp3": Target(
        name="mp3", kind=AUDIO, container="mp3", acodec="mp3", abitrate="320k",
        summary="MP3 at 320 kbps — plays on anything ever made",
    ),
    "m4a": Target(
        name="m4a", kind=AUDIO, container="m4a", acodec="aac", abitrate="256k",
        summary="AAC in an m4a — the Apple-shaped default, smaller than MP3 "
                "at the same quality",
    ),
    "opus": Target(
        name="opus", kind=AUDIO, container="opus", acodec="opus",
        abitrate="192k",
        summary="Opus at 192 kbps — the best sound per byte, and what YouTube "
                "already serves, so this is usually a copy",
    ),
    "flac": Target(
        name="flac", kind=AUDIO, container="flac", acodec="flac",
        lossless=True,
        summary="FLAC — lossless, and pointless on anything that was streamed",
    ),
    "wav": Target(
        name="wav", kind=AUDIO, container="wav", acodec="pcm_s16le",
        lossless=True,
        summary="16-bit WAV — uncompressed, for editing rather than keeping",
    ),
    "audio": Target(
        name="audio", kind=AUDIO, container="m4a", acodec=None,
        summary="Whatever audio the source already had, untouched, in the "
                "container that fits it",
    ),

    # ---- video ----------------------------------------------------------
    "mp4": Target(
        name="mp4", kind=VIDEO, container="mp4",
        summary="MP4, streams copied where they fit — the safe share format",
    ),
    "mp4-1080": Target(
        name="mp4-1080", kind=VIDEO, container="mp4", vcodec="h264",
        acodec="aac", abitrate="192k", height=1080,
        summary="MP4 capped at 1080p, H.264 — re-encodes, but nothing will "
                "refuse to play it",
    ),
    "mp4-720": Target(
        name="mp4-720", kind=VIDEO, container="mp4", vcodec="h264",
        acodec="aac", abitrate="128k", height=720,
        summary="MP4 capped at 720p — for sending to people",
    ),
    "mkv": Target(
        name="mkv", kind=VIDEO, container="mkv",
        summary="Matroska — holds anything, copies everything, keeps every "
                "subtitle and audio track",
    ),
    "webm": Target(
        name="webm", kind=VIDEO, container="webm",
        summary="WebM — VP9/AV1 and Opus, copied when the source already is",
    ),
    "video": Target(
        name="video", kind=VIDEO, container="mkv",
        summary="Best available video and audio, nothing re-encoded",
    ),
}

# ---- still images -------------------------------------------------------
# ffmpeg handles most of these and ImageMagick handles the rest; the engines
# work out between them which is which, so a preset here says what is wanted
# rather than what will do it.
PRESETS.update({
    "jpg": Target(
        name="jpg", kind=IMAGE, container="jpg", quality=92,
        summary="JPEG — the one every program on earth opens",
    ),
    "png": Target(
        name="png", kind=IMAGE, container="png", lossless=True,
        summary="PNG — lossless, keeps transparency, larger files",
    ),
    "webp": Target(
        name="webp", kind=IMAGE, container="webp", quality=88,
        summary="WebP — about a third smaller than JPEG at the same quality "
                "(needs ImageMagick; ffmpeg here can read it but not write it)",
    ),
    "tiff": Target(
        name="tiff", kind=IMAGE, container="tiff", lossless=True,
        summary="TIFF — lossless, for print and archives",
    ),
    "gif": Target(
        name="gif", kind=IMAGE, container="gif",
        summary="GIF — 256 colours, animates",
    ),
    "web-image": Target(
        name="web-image", kind=IMAGE, container="jpg", quality=82,
        max_edge=2000,
        summary="JPEG, longest side capped at 2000px — for putting on a page",
    ),
})

# ---- documents ----------------------------------------------------------
PRESETS.update({
    "pdf": Target(
        name="pdf", kind=DOCUMENT, container="pdf",
        summary="PDF — from a document, or a smaller PDF from a PDF",
    ),
    "docx": Target(
        name="docx", kind=DOCUMENT, container="docx",
        summary="Word — for people who will send it back with tracked changes",
    ),
    "epub": Target(
        name="epub", kind=DOCUMENT, container="epub",
        summary="EPUB — for e-readers",
    ),
    "html": Target(
        name="html", kind=DOCUMENT, container="html",
        summary="A single self-contained HTML page",
    ),
    "md": Target(
        name="md", kind=DOCUMENT, container="md",
        summary="Markdown — plain text that survives everything",
    ),
    "txt": Target(
        name="txt", kind=DOCUMENT, container="txt",
        summary="Plain text, formatting discarded",
    ),
    "rtf": Target(
        name="rtf", kind=DOCUMENT, container="rtf",
        summary="Rich text — opens in anything, keeps basic formatting",
    ),
})

DEFAULT_PRESET = "video"


def resolve(name):
    """Look up a preset by name, case and punctuation forgiven."""
    if name is None:
        return PRESETS[DEFAULT_PRESET]
    if isinstance(name, Target):
        return name
    key = str(name).strip().lower().replace("_", "-").lstrip(".")
    aliases = {
        "best": "video", "original": "video", "source": "video",
        "jpeg": "jpg", "tif": "tiff", "htm": "html", "markdown": "md",
        "text": "txt", "word": "docx", "ebook": "epub", "image": "jpg",
        "web": "web-image",
        "1080p": "mp4-1080", "720p": "mp4-720", "1080": "mp4-1080",
        "720": "mp4-720", "m4b": "m4a", "aac": "m4a", "mpeg4": "mp4",
        "mka": "mkv", "oga": "opus",
    }
    key = aliases.get(key, key)
    if key not in PRESETS:
        known = ", ".join(sorted(PRESETS))
        raise UnknownFormat(f"No format called {name!r}. Known: {known}")
    return PRESETS[key]


def kinds():
    """Presets grouped by what they are for, so a listing can be read."""
    grouped = {}
    for name, target in PRESETS.items():
        grouped.setdefault(target.kind, []).append(name)
    return grouped


def can_hold(container, codec, stream_kind):
    """Is this codec legal inside this container?"""
    table = CONTAINER_CODECS.get(container)
    if table is None:
        return False
    allowed = table.get(stream_kind, set())
    # pcm comes in a dozen spellings and the container either takes pcm or it
    # does not, so compare on the family rather than the exact variant.
    if codec and codec.startswith("pcm_"):
        return any(a.startswith("pcm_") for a in allowed)
    return codec in allowed


class UnknownFormat(ValueError):
    """Somebody asked for a format that does not exist."""

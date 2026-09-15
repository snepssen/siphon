"""ffmpeg: audio, video, subtitles, and the decision not to re-encode.

The interesting part of this module is not that it runs ffmpeg. It is `plan`,
which works out — before anything is written — whether the file already
contains what was asked for.

"Give this to me as an mp4" is usually not a conversion. The video is already
H.264 and the audio already AAC; both are legal inside an mp4; the work is
moving them into a different wrapper, which takes about a second and changes
not one sample. Re-encoding anyway would cost minutes and quality, in exchange
for nothing. So `plan` compares what is in the file against what the target
asks for, stream by stream, and re-encodes exactly the streams that need it —
and says in a sentence which ones those were, so the answer is visible instead
of being taken on trust.

The three outcomes:

  none    the file already is what was asked for; nothing runs at all
  copy    a different container, same streams — `-c copy`, seconds, lossless
  encode  something genuinely has to be re-made, and the summary says what
"""

import json
import os
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import formats
from platform_support import require, MissingProgram
from engines import ConversionError

NONE = "none"
COPY = "copy"
ENCODE = "encode"

# Containers that can carry a cover image. Opus is absent on purpose: Ogg
# stores a picture as a base64 blob in a comment field, which ffmpeg will not
# write, and pretending otherwise would mean silently dropping the art.
ARTWORK_CONTAINERS = {"mp3", "m4a", "flac"}

# Containers ffmpeg is the right tool for. Still images and documents belong
# to other engines even where ffmpeg would technically produce a file.
HANDLED_KINDS = {"audio", "video", "unknown", None}


@dataclass
class Plan:
    """What is about to happen, in a form that can be shown before it does."""

    action: str
    argv: list = field(default_factory=list)
    summary: str = ""
    detail: list = field(default_factory=list)
    reencodes_video: bool = False
    reencodes_audio: bool = False
    suffix: str = ""
    duration: float = None

    @property
    def lossless(self):
        return not (self.reencodes_video or self.reencodes_audio)


def can(source_path, target, kind=None):
    target = formats.resolve(target)
    if target.kind not in {formats.AUDIO, formats.VIDEO}:
        return False
    if kind not in HANDLED_KINDS and kind not in {"audio", "video"}:
        return False
    return True


# ---------------------------------------------------------------------------
# Reading what is in the file
# ---------------------------------------------------------------------------

def probe(path):
    """Streams and container facts, from one ffprobe call."""
    binary = _need("ffprobe")
    argv = [
        binary, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise ConversionError(f"Could not read {path}: {error}") from error
    if out.returncode != 0:
        raise ConversionError(
            f"ffprobe could not read {os.path.basename(str(path))}. "
            f"{(out.stderr or '').strip().splitlines()[-1] if out.stderr.strip() else ''}"
        )
    try:
        return json.loads(out.stdout)
    except ValueError as error:
        raise ConversionError(f"ffprobe returned unreadable output for {path}") from error


def _streams(info, kind):
    return [s for s in info.get("streams", []) if s.get("codec_type") == kind]


def _duration(info):
    try:
        return float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        return None


def _is_cover(stream):
    """A still image riding along inside an audio file is artwork, not video."""
    if stream.get("disposition", {}).get("attached_pic"):
        return True
    return stream.get("codec_name") in {"mjpeg", "png", "bmp"} and \
        stream.get("avg_frame_rate") in {"0/0", "1/1", None}


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------

def plan(source_path, target, metadata=None, artwork=None):
    """Compare what is in the file with what was asked for.

    `artwork` is a path to a cover image to embed, for the containers that can
    carry one. `metadata` is written in the same pass as the conversion rather
    than in a tagging step afterwards. Tags live in the container, so writing them later
    means opening and rewriting the whole file a second time — for a two-hour
    video that is minutes of disk for a line of text. The one consequence is
    that a file which needed no conversion but does need tags becomes a copy
    rather than a no-op, which is still seconds and still lossless.
    """
    target = formats.resolve(target)
    source_path = str(source_path)
    info = probe(source_path)

    video = [s for s in _streams(info, "video") if not _is_cover(s)]
    covers = [s for s in _streams(info, "video") if _is_cover(s)]
    audio = _streams(info, "audio")
    subtitles = _streams(info, "subtitle")
    duration = _duration(info)

    if not audio and not video:
        raise ConversionError(
            f"{os.path.basename(source_path)} has no audio or video in it."
        )

    source_container = Path(source_path).suffix.lstrip(".").lower()
    detail = []

    # ---- audio decision ------------------------------------------------
    audio_action = None
    if audio:
        current = audio[0].get("codec_name")
        if target.acodec is None:
            fits = formats.can_hold(target.container, current, "audio")
            audio_action = COPY if fits else ENCODE
            if not fits:
                detail.append(
                    f"audio re-encoded: {target.container} cannot hold {current}"
                )
        elif _same_codec(current, target.acodec):
            audio_action = COPY
            detail.append(f"audio copied: already {current}")
        else:
            audio_action = ENCODE
            detail.append(f"audio re-encoded: {current} to {target.acodec}")
        if audio_action == COPY and target.acodec is None:
            detail.append(f"audio copied: {current} fits {target.container}")

    # ---- video decision ------------------------------------------------
    video_action = None
    scaling = False
    if video and not target.audio_only:
        stream = video[0]
        current = stream.get("codec_name")
        height = _int(stream.get("height"))
        scaling = bool(target.height and height and height > target.height)
        if scaling:
            video_action = ENCODE
            detail.append(
                f"video re-encoded: {height}p scaled down to {target.height}p"
            )
        elif target.vcodec is None:
            fits = formats.can_hold(target.container, current, "video")
            video_action = COPY if fits else ENCODE
            detail.append(
                f"video copied: {current} fits {target.container}" if fits else
                f"video re-encoded: {target.container} cannot hold {current}"
            )
        elif _same_codec(current, target.vcodec):
            video_action = COPY
            detail.append(f"video copied: already {current}")
        else:
            video_action = ENCODE
            detail.append(f"video re-encoded: {current} to {target.vcodec}")

    reencodes_audio = audio_action == ENCODE
    reencodes_video = video_action == ENCODE

    # ---- is there anything to do at all? -------------------------------
    same_container = source_container == target.container
    dropping_video = bool(video) and target.audio_only
    tags = _metadata_args(metadata, target)
    embedding = bool(artwork) and target.container in ARTWORK_CONTAINERS
    if (same_container and not reencodes_audio and not reencodes_video
            and not dropping_video and not tags and not embedding):
        return Plan(
            action=NONE, summary="Already in the requested format; left alone.",
            detail=["nothing to do: the file is already what was asked for"],
            suffix=target.container, duration=duration,
        )

    # ---- build the command ---------------------------------------------
    argv = ["-hide_banner", "-nostdin", "-y", "-i", source_path]
    if embedding:
        argv += ["-i", str(artwork)]
    maps, codecs = [], []

    if target.audio_only:
        if not audio:
            raise ConversionError(
                f"{os.path.basename(source_path)} has no audio to extract."
            )
        maps += ["-map", "0:a:0"]
        codecs += _audio_codec_args(target, audio_action)
        if embedding:
            # A cover fetched from the catalogue beats whatever the video had.
            maps += ["-map", "1:v:0"]
            codecs += _artwork_codec_args(artwork)
            detail.append("cover art embedded")
        elif covers and target.container in ARTWORK_CONTAINERS:
            maps += ["-map", f"0:{covers[0]['index']}"]
            codecs += ["-c:v", "copy", "-disposition:v:0", "attached_pic"]
        else:
            codecs += ["-vn"]
    else:
        if video:
            maps += ["-map", "0:v:0"]
            codecs += _video_codec_args(target, video_action, scaling)
        if audio:
            maps += ["-map", "0:a:0"]
            codecs += _audio_codec_args(target, audio_action)
        if subtitles and target.container in {"mkv", "mp4", "mov"}:
            maps += ["-map", "0:s?"]
            codecs += ["-c:s", "mov_text" if target.container in {"mp4", "mov"} else "copy"]

    argv += maps + codecs + tags
    if target.container in {"mp4", "m4a", "mov"}:
        # Metadata at the front, so the file plays before it has finished
        # arriving. Costs nothing on a local write.
        argv += ["-movflags", "+faststart"]
    argv += list(target.extra)

    action = ENCODE if (reencodes_audio or reencodes_video) else COPY
    if action == COPY and not detail:
        detail.append("streams copied into a new container")

    return Plan(
        action=action,
        argv=argv,
        summary=_summary(action, target, reencodes_video, reencodes_audio,
                         source_container),
        detail=detail,
        reencodes_video=reencodes_video,
        reencodes_audio=reencodes_audio,
        suffix=target.container,
        duration=duration,
    )


# Tag names ffmpeg understands across the containers siphon writes. The
# spellings differ per container underneath, but ffmpeg maps these itself.
_TAG_FIELDS = (
    ("title", "title"),
    ("artist", "artist"),
    ("album", "album"),
    ("album_artist", "album_artist"),
    ("year", "date"),
    ("track_number", "track"),
    ("disc_number", "disc"),
    ("isrc", "ISRC"),
)


def _metadata_args(metadata, target):
    """`-metadata` arguments for whatever the item actually knows.

    Empty when there is nothing to say, which is what lets a file with no
    metadata skip conversion entirely.
    """
    if not metadata:
        return []
    argv = []
    for attribute, tag in _TAG_FIELDS:
        value = getattr(metadata, attribute, None) if not isinstance(metadata, dict) \
            else metadata.get(attribute)
        if value in (None, "", 0):
            continue
        if attribute == "track_number":
            total = getattr(metadata, "track_total", None) if not isinstance(metadata, dict) \
                else metadata.get("track_total")
            value = f"{value}/{total}" if total else str(value)
        argv += ["-metadata", f"{tag}={value}"]
    if argv:
        # Without this, ffmpeg copies the source's own tags in underneath and
        # the result carries both sets — usually including a title that is the
        # YouTube video's name rather than the track's.
        argv = ["-map_metadata", "-1"] + argv
    return argv


def _summary(action, target, reencodes_video, reencodes_audio, source_container):
    if action == COPY:
        return (f"Repackaged from {source_container} to {target.container} "
                f"with the streams copied — nothing re-encoded.")
    parts = []
    if reencodes_video:
        parts.append("video")
    if reencodes_audio:
        parts.append("audio")
    what = " and ".join(parts) if parts else "the file"
    return f"Re-encoded the {what} to make a {target.container}."


def _same_codec(current, wanted):
    if not current or not wanted:
        return False
    if current == wanted:
        return True
    families = {
        "aac": {"aac", "aac_latm"},
        "mp3": {"mp3", "mp3float"},
        "h264": {"h264", "libx264"},
        "hevc": {"hevc", "libx265", "h265"},
        "opus": {"opus", "libopus"},
        "vorbis": {"vorbis", "libvorbis"},
        "flac": {"flac"},
        "vp9": {"vp9", "libvpx-vp9"},
        "av1": {"av1", "libaom-av1", "libsvtav1"},
    }
    family = families.get(wanted, {wanted})
    return current in family


def _audio_codec_args(target, action):
    if action == COPY:
        return ["-c:a", "copy"]
    encoders = {
        "mp3": ["-c:a", "libmp3lame"],
        "aac": ["-c:a", "aac"],
        "opus": ["-c:a", "libopus"],
        "flac": ["-c:a", "flac"],
        "alac": ["-c:a", "alac"],
        "pcm_s16le": ["-c:a", "pcm_s16le"],
        "vorbis": ["-c:a", "libvorbis"],
    }
    codec = target.acodec or _default_audio_codec(target.container)
    argv = list(encoders.get(codec, ["-c:a", codec]))
    if target.abitrate and not target.lossless:
        argv += ["-b:a", target.abitrate]
    return argv


def _artwork_codec_args(artwork):
    """Copy a JPEG straight in; re-wrap anything else as one."""
    suffix = str(artwork).lower().rsplit(".", 1)[-1]
    codec = ["-c:v", "copy"] if suffix in {"jpg", "jpeg"} else ["-c:v", "mjpeg"]
    return codec + ["-disposition:v:0", "attached_pic"]


def _default_audio_codec(container):
    table = formats.CONTAINER_CODECS.get(container, {})
    for preferred in ("aac", "opus", "mp3", "flac", "vorbis", "pcm_s16le"):
        if preferred in table.get("audio", set()):
            return preferred
    return "aac"


def _video_codec_args(target, action, scaling):
    if action == COPY:
        return ["-c:v", "copy"]
    encoders = {
        "h264": ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
                 "-pix_fmt", "yuv420p"],
        "hevc": ["-c:v", "libx265", "-preset", "medium", "-crf", "24",
                 "-tag:v", "hvc1", "-pix_fmt", "yuv420p"],
        "vp9": ["-c:v", "libvpx-vp9", "-crf", "31", "-b:v", "0"],
        "av1": ["-c:v", "libsvtav1", "-crf", "32", "-preset", "6"],
    }
    codec = target.vcodec or _default_video_codec(target.container)
    argv = list(encoders.get(codec, ["-c:v", codec]))
    if scaling and target.height:
        # -2 keeps the aspect ratio and lands on an even number, which every
        # 4:2:0 encoder requires and none of them say so politely.
        argv += ["-vf", f"scale=-2:{target.height}"]
    if target.fps:
        argv += ["-r", str(target.fps)]
    return argv


def _default_video_codec(container):
    table = formats.CONTAINER_CODECS.get(container, {})
    for preferred in ("h264", "vp9", "av1", "hevc"):
        if preferred in table.get("video", set()):
            return preferred
    return "h264"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

def run(plan_obj, source_path, output_path, on_progress=None,
        should_cancel=None):
    """Carry out a plan. Returns the path written.

    A plan whose action is NONE writes nothing and returns the source: the
    caller decides whether to copy or move it, because only the caller knows
    whether the source is a downloaded temporary file or somebody's original.
    """
    if plan_obj.action == NONE:
        return str(source_path)

    binary = _need("ffmpeg")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Written under a temporary name and moved into place only on success, so
    # a failure or a cancellation never leaves a half-file that looks finished.
    #
    # The real extension has to stay on the end. ffmpeg picks the output
    # container from the suffix, and a file ending .partial is a container it
    # has never heard of — which it reports as "Invalid argument", one of the
    # least helpful sentences it knows.
    partial = output_path.with_name(
        f".{output_path.stem}.partial{output_path.suffix}"
    )

    argv = [binary, *plan_obj.argv, "-progress", "pipe:1", "-nostats",
            str(partial)]

    duration = plan_obj.duration
    errors = []

    def on_line(line):
        if not on_progress or not duration:
            return
        match = re.match(r"out_time_us=(\d+)", line)
        if match:
            seconds = int(match.group(1)) / 1_000_000
            on_progress({
                "progress": max(0.0, min(1.0, seconds / duration)),
                "status": "converting",
            })

    code = _stream(argv, on_line, should_cancel=should_cancel, errors=errors)

    if code != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        if should_cancel and should_cancel():
            raise Cancelled("Stopped before it finished.")
        raise ConversionError(_explain(errors, source_path))

    partial.replace(output_path)
    return str(output_path)


def _stream(argv, on_line, should_cancel=None, errors=None):
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


def _explain(errors, source_path):
    text = "\n".join(errors)
    name = os.path.basename(str(source_path))
    if "Invalid data found" in text:
        return f"{name} is not a media file ffmpeg can read, or it is truncated."
    if "No space left" in text:
        return "The disk is full."
    if "Permission denied" in text:
        return "ffmpeg was not allowed to write there."
    if "Unknown encoder" in text:
        match = re.search(r"Unknown encoder '([^']+)'", text)
        codec = match.group(1) if match else "that codec"
        return (f"This ffmpeg was built without {codec}. "
                f"`brew install ffmpeg` gets a build with the usual encoders.")
    for line in reversed(errors):
        if line.strip() and not line.startswith(("  ", "frame=", "size=")):
            return f"ffmpeg failed on {name}: {line.strip()}"
    return f"ffmpeg failed on {name}."


def _need(key):
    try:
        return require(key)
    except MissingProgram as missing:
        raise ConversionError(str(missing)) from missing


class Cancelled(Exception):
    """The job was stopped on purpose."""

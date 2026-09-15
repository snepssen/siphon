"""Finding the programs siphon does not ship.

siphon is standard library only. Everything that actually touches media is an
external program: yt-dlp fetches, ffmpeg converts, and a handful of optional
engines cover formats ffmpeg has no business touching. None of them are
bundled.

That is a deliberate trade. yt-dlp is in a permanent arms race with the sites
it reads and ships a new version most weeks; a copy frozen inside siphon would
be broken by the time anyone installed it. Bundling ffmpeg would mean shipping
whichever codecs the build happened to enable, and this machine's ffmpeg
already has better ones than a portable build would.

The cost is that a missing program has to be a sentence rather than a
traceback, which is what this module is for.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Program:
    """An external program, and how to explain its absence."""

    key: str
    binaries: tuple            # tried in order; first hit wins
    purpose: str               # what stops working without it
    required: bool             # False for engines covering optional formats
    install: dict = field(default_factory=dict)   # platform -> install line
    version_args: tuple = ("--version",)

    def install_line(self):
        return self.install.get(_platform_key(), self.install.get("any", ""))


PROGRAMS = {
    "yt-dlp": Program(
        key="yt-dlp",
        binaries=("yt-dlp", "yt_dlp", "youtube-dl"),
        purpose="fetching anything from a URL",
        required=True,
        install={
            "macos": "brew install yt-dlp",
            "linux": "pipx install yt-dlp",
            "windows": "winget install yt-dlp.yt-dlp",
        },
    ),
    "ffmpeg": Program(
        key="ffmpeg",
        binaries=("ffmpeg",),
        purpose="converting audio and video, and muxing what yt-dlp fetches",
        required=True,
        install={
            "macos": "brew install ffmpeg",
            "linux": "sudo apt install ffmpeg",
            "windows": "winget install Gyan.FFmpeg",
        },
    ),
    "ffprobe": Program(
        key="ffprobe",
        binaries=("ffprobe",),
        purpose="reading what is actually inside a file before converting it",
        required=True,
        install={
            "macos": "brew install ffmpeg",
            "linux": "sudo apt install ffmpeg",
            "windows": "winget install Gyan.FFmpeg",
        },
    ),
    "magick": Program(
        key="magick",
        binaries=("magick", "convert"),
        purpose="still image formats ffmpeg handles badly or not at all",
        required=False,
        install={
            "macos": "brew install imagemagick",
            "linux": "sudo apt install imagemagick",
            "windows": "winget install ImageMagick.ImageMagick",
        },
    ),
    "pandoc": Program(
        key="pandoc",
        binaries=("pandoc",),
        purpose="documents, markup and ebooks",
        required=False,
        install={
            "macos": "brew install pandoc",
            "linux": "sudo apt install pandoc",
            "windows": "winget install JohnMacFarlane.Pandoc",
        },
    ),
    "gs": Program(
        key="gs",
        binaries=("gs", "gswin64c"),
        purpose="PDF rewriting and compression",
        required=False,
        install={
            "macos": "brew install ghostscript",
            "linux": "sudo apt install ghostscript",
            "windows": "winget install ArtifexSoftware.GhostScript",
        },
    ),
    "soffice": Program(
        key="soffice",
        binaries=("soffice", "libreoffice"),
        purpose="office documents and spreadsheets",
        required=False,
        install={
            "macos": "brew install --cask libreoffice",
            "linux": "sudo apt install libreoffice",
            "windows": "winget install TheDocumentFoundation.LibreOffice",
        },
    ),
}


def _platform_key():
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


# Homebrew on Apple silicon is not on the PATH of a process launched from
# Finder, which is how most people will start this. Looking in the usual
# places is the difference between working and a false "ffmpeg is missing".
_EXTRA_PATHS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    os.path.expanduser("~/.local/bin"),
    "/usr/bin",
)

_cache = {}


def find(key):
    """Absolute path to a program, or None. Cached; `forget` clears it."""
    if key in _cache:
        return _cache[key]

    program = PROGRAMS[key]
    found = None
    for binary in program.binaries:
        found = shutil.which(binary)
        if found:
            break
        for directory in _EXTRA_PATHS:
            candidate = os.path.join(directory, binary)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                found = candidate
                break
        if found:
            break

    _cache[key] = found
    return found


def forget():
    """Drop the cache. For after somebody installs something mid-session."""
    _cache.clear()


def require(key):
    """Path to a program, or MissingProgram carrying a sentence to show."""
    path = find(key)
    if path is None:
        raise MissingProgram(key)
    return path


def version(key):
    """First line of the program's version output, or None."""
    path = find(key)
    if path is None:
        return None
    program = PROGRAMS[key]
    try:
        out = subprocess.run(
            [path, *program.version_args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or out.stderr or "").strip().splitlines()
    return line[0].strip() if line else None


def survey():
    """Every program, whether it is here, and what to do if it is not."""
    report = {}
    for key, program in PROGRAMS.items():
        path = find(key)
        report[key] = {
            "present": path is not None,
            "path": path,
            "version": version(key) if path else None,
            "required": program.required,
            "purpose": program.purpose,
            "install": program.install_line(),
        }
    return report


def missing_required():
    """The required programs that are not here, as sentences."""
    sentences = []
    for key, program in PROGRAMS.items():
        if program.required and find(key) is None:
            sentences.append(str(MissingProgram(key)))
    return sentences


class MissingProgram(Exception):
    """A program siphon needs is not installed. The message is the remedy."""

    def __init__(self, key):
        self.key = key
        program = PROGRAMS[key]
        line = program.install_line()
        message = (
            f"siphon needs {key} for {program.purpose}, and could not find it."
        )
        if line:
            message += f" Install it with: {line}"
        super().__init__(message)

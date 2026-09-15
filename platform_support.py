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
    """An external program: how to find it, and how to go and get it.

    `packages` holds the name each package manager knows it by, rather than a
    ready-made sentence, so the same fact serves both the line shown to a
    person and the argv siphon runs when they say yes. A sentence cannot be
    executed and a command is unpleasant to read; deriving both from one table
    keeps them from drifting apart.
    """

    key: str
    binaries: tuple            # tried in order; first hit wins
    purpose: str               # what stops working without it
    required: bool             # False for engines covering optional formats
    packages: dict = field(default_factory=dict)   # manager -> package name
    provided_by: str = None    # another program's package installs this one
    version_args: tuple = ("--version",)

    def package_for(self, manager):
        return self.packages.get(manager)

    def install_line(self):
        """The command a person could type, for the manager they have."""
        manager = current_manager()
        if manager is None:
            return ""
        name = self.package_for(manager)
        if not name:
            return ""
        return " ".join(MANAGERS[manager]["command"] + name.split())


PROGRAMS = {
    "yt-dlp": Program(
        key="yt-dlp",
        binaries=("yt-dlp", "yt_dlp", "youtube-dl"),
        purpose="fetching anything from a URL",
        required=True,
        packages={"brew": "yt-dlp", "apt": "yt-dlp", "dnf": "yt-dlp",
                  "pacman": "yt-dlp", "winget": "yt-dlp.yt-dlp"},
    ),
    "ffmpeg": Program(
        key="ffmpeg",
        binaries=("ffmpeg",),
        purpose="converting audio and video, and muxing what yt-dlp fetches",
        required=True,
        packages={"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
                  "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"},
    ),
    "ffprobe": Program(
        key="ffprobe",
        binaries=("ffprobe",),
        purpose="reading what is actually inside a file before converting it",
        required=True,
        packages={"brew": "ffmpeg", "apt": "ffmpeg", "dnf": "ffmpeg",
                  "pacman": "ffmpeg", "winget": "Gyan.FFmpeg"},
        provided_by="ffmpeg",      # one package, two programs
    ),
    "magick": Program(
        key="magick",
        binaries=("magick", "convert"),
        purpose="still image formats ffmpeg handles badly or not at all",
        required=False,
        packages={"brew": "imagemagick", "apt": "imagemagick",
                  "dnf": "ImageMagick", "pacman": "imagemagick",
                  "winget": "ImageMagick.ImageMagick"},
    ),
    "pandoc": Program(
        key="pandoc",
        binaries=("pandoc",),
        purpose="documents, markup and ebooks",
        required=False,
        packages={"brew": "pandoc", "apt": "pandoc", "dnf": "pandoc",
                  "pacman": "pandoc", "winget": "JohnMacFarlane.Pandoc"},
    ),
    "gs": Program(
        key="gs",
        binaries=("gs", "gswin64c"),
        purpose="PDF rewriting and compression",
        required=False,
        packages={"brew": "ghostscript", "apt": "ghostscript",
                  "dnf": "ghostscript", "pacman": "ghostscript",
                  "winget": "ArtifexSoftware.GhostScript"},
    ),
    "soffice": Program(
        key="soffice",
        binaries=("soffice", "libreoffice"),
        purpose="office documents and spreadsheets",
        required=False,
        packages={"brew": "--cask libreoffice", "apt": "libreoffice",
                  "dnf": "libreoffice", "pacman": "libreoffice",
                  "winget": "TheDocumentFoundation.LibreOffice"},
    ),
    "tectonic": Program(
        key="tectonic",
        binaries=("tectonic",),
        purpose="typesetting a PDF from a document — pandoc writes them but "
                "ships no typesetter",
        required=False,
        packages={"brew": "tectonic", "apt": "tectonic", "dnf": "tectonic",
                  "pacman": "tectonic", "winget": "TectonicProject.Tectonic"},
    ),
    "node": Program(
        key="node",
        binaries=("node",),
        purpose="running your own cobalt instance, as a second way to fetch",
        required=False,
        packages={"brew": "node", "apt": "nodejs", "dnf": "nodejs",
                  "pacman": "nodejs", "winget": "OpenJS.NodeJS"},
    ),
    "pnpm": Program(
        key="pnpm",
        binaries=("pnpm",),
        purpose="installing cobalt's dependencies — it is a pnpm workspace, "
                "and npm cannot resolve the `workspace:` protocol it uses",
        required=False,
        packages={"brew": "pnpm", "apt": "pnpm", "dnf": "pnpm",
                  "pacman": "pnpm", "winget": "pnpm.pnpm"},
    ),
    "git": Program(
        key="git",
        binaries=("git",),
        purpose="fetching cobalt's source the first time",
        required=False,
        packages={"brew": "git", "apt": "git", "dnf": "git",
                  "pacman": "git", "winget": "Git.Git"},
    ),
}


# How to install things, per package manager. `needs_root` decides whether
# siphon may run the command itself: it will never invoke sudo on somebody's
# behalf, so those are printed for them to run instead.
MANAGERS = {
    "brew":   {"probe": "brew",   "command": ["brew", "install"],
               "needs_root": False, "label": "Homebrew"},
    "winget": {"probe": "winget", "command": ["winget", "install", "-e", "--id"],
               "needs_root": False, "label": "winget"},
    "apt":    {"probe": "apt-get", "command": ["sudo", "apt-get", "install", "-y"],
               "needs_root": True, "label": "apt"},
    "dnf":    {"probe": "dnf",    "command": ["sudo", "dnf", "install", "-y"],
               "needs_root": True, "label": "dnf"},
    "pacman": {"probe": "pacman", "command": ["sudo", "pacman", "-S", "--noconfirm"],
               "needs_root": True, "label": "pacman"},
}

_manager = None


def current_manager():
    """The package manager on this machine, or None."""
    global _manager
    if _manager is None:
        order = ("brew", "winget", "apt", "dnf", "pacman")
        for name in order:
            if shutil.which(MANAGERS[name]["probe"]):
                _manager = name
                break
        else:
            _manager = False
    return _manager or None


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


def locate(*binaries):
    """Absolute path to the first of these that exists, or None.

    Looks beyond PATH on purpose. A process launched from Finder does not
    inherit a shell's environment, so Homebrew's directory is simply absent
    and a perfectly installed ffmpeg looks missing.
    """
    for binary in binaries:
        found = shutil.which(binary)
        if found:
            return found
        for directory in _EXTRA_PATHS:
            candidate = os.path.join(directory, binary)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def find(key):
    """Absolute path to a known program, or None. Cached; `forget` clears it."""
    if key in _cache:
        return _cache[key]
    _cache[key] = locate(*PROGRAMS[key].binaries)
    return _cache[key]


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


def missing(required_only=False):
    """Programs that are not installed, as Program objects.

    Deduplicated by package: ffprobe and ffmpeg come from one formula, so
    offering to install both would ask somebody to approve the same download
    twice and then do it twice.
    """
    found, seen = [], set()
    for key, program in PROGRAMS.items():
        if find(key) is not None:
            continue
        if required_only and not program.required:
            continue
        if program.provided_by and program.provided_by in seen:
            continue
        seen.add(key)
        found.append(program)
    return found


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

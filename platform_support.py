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
traceback, which is what this module is for. The finding, the package manager
table and the sentences are workshop-core's (`workshop/`, synced in, never
edited here); what is siphon's is the table of programs below.
"""

from workshop import programs as _shared
from workshop.programs import (  # noqa: F401  (siphon's public names)
    MANAGERS, Program, current_manager, locate, Registry,
)


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


_registry = Registry(PROGRAMS, "siphon")
find = _registry.find
forget = _registry.forget
version = _registry.version
survey = _registry.survey
missing = _registry.missing


class MissingProgram(_shared.MissingProgram):
    """A program siphon needs is not installed. The message is the remedy."""

    def __init__(self, key):
        super().__init__(key, "siphon", PROGRAMS[key])


def require(key):
    """Path to a program, or MissingProgram carrying a sentence to show."""
    path = find(key)
    if path is None:
        raise MissingProgram(key)
    return path


def missing_required():
    """The required programs that are not here, as sentences."""
    return [str(MissingProgram(key)) for key, program in PROGRAMS.items()
            if program.required and find(key) is None]

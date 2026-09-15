"""Files already on this machine.

This is what makes siphon a converter rather than only a downloader: a path
enters the same pipeline a URL does, simply skipping the stage that fetches
bytes. A folder expands to the media inside it, one level down, so that
converting a directory of wav files is the same gesture as converting one.
"""

import os
from pathlib import Path

from model import Item

# Extensions worth picking up when somebody points at a folder. A narrow list
# on purpose: expanding a folder should not sweep up a README.
MEDIA_SUFFIXES = {
    # audio
    ".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aiff", ".aif", ".opus",
    ".ogg", ".oga", ".wma", ".alac", ".ape", ".wv",
    # video
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".wmv", ".flv", ".mpg",
    ".mpeg", ".ts", ".m2ts", ".3gp", ".ogv",
    # still image
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic",
    ".heif", ".avif", ".svg",
    # documents
    ".pdf", ".epub", ".mobi", ".docx", ".odt", ".md", ".html", ".rtf", ".txt",
}

AUDIO_SUFFIXES = {
    ".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aiff", ".aif", ".opus",
    ".ogg", ".oga", ".wma", ".alac", ".ape", ".wv",
}
IMAGE_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp", ".heic",
    ".heif", ".avif", ".svg",
}
DOCUMENT_SUFFIXES = {
    ".pdf", ".epub", ".mobi", ".docx", ".odt", ".md", ".html", ".rtf", ".txt",
}


def handles(target):
    if "://" in target:
        return False
    return Path(os.path.expanduser(target)).exists()


def kind_of(path):
    suffix = Path(path).suffix.lower()
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in DOCUMENT_SUFFIXES:
        return "document"
    return "video"


def expand(target, recursive=False, **_options):
    path = Path(os.path.expanduser(target)).resolve()
    if path.is_file():
        return [_item(path)]
    if path.is_dir():
        walker = path.rglob("*") if recursive else path.glob("*")
        found = sorted(
            p for p in walker
            if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
            and not p.name.startswith(".")
        )
        return [_item(p) for p in found]
    return []


def _item(path):
    return Item(
        origin="local",
        path=str(path),
        kind=kind_of(path),
        title=path.stem,
        extra={"size": path.stat().st_size},
    )

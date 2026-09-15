"""Where things live.

Four directories, decided once here so that no other module has to guess:
the config (credentials, preferences), the state (the queue, so it survives a
restart), the cache (artwork and probe results), and the output — which is the
only one the user is expected to ever look in.
"""

import os
import sys
from pathlib import Path

APP = "siphon"


def _home():
    return Path(os.path.expanduser("~"))


def config_dir():
    """Credentials and preferences."""
    if sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", _home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", _home() / ".config"))
    return _ensure(base / APP)


def state_dir():
    """The queue, and anything else that must outlive the process."""
    if sys.platform == "darwin":
        base = _home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", _home() / ".local" / "state"))
    return _ensure(base / APP)


def cache_dir():
    """Artwork, probe results, part files. Safe to delete at any time."""
    if sys.platform == "darwin":
        base = _home() / "Library" / "Caches"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA", _home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", _home() / ".cache"))
    return _ensure(base / APP)


def output_dir():
    """Where finished files land, unless told otherwise.

    A plain folder in the user's Downloads, because the one thing worse than a
    tool that puts files somewhere odd is a tool that puts them somewhere odd
    and remembers where.
    """
    override = os.environ.get("SIPHON_OUTPUT")
    if override:
        return _ensure(Path(override).expanduser())
    downloads = _home() / "Downloads"
    base = downloads if downloads.is_dir() else _home()
    return _ensure(base / "siphon")


def work_dir():
    """Scratch space for a job in flight. Cleaned up when it finishes."""
    return _ensure(cache_dir() / "work")


def _ensure(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


# Characters that make a filename unusable on some platform or other, plus the
# Windows reserved names, because a file called `con.mp3` is a bad afternoon.
_ILLEGAL = '<>:"/\\|?*\0'
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_name(name, fallback="untitled", limit=180):
    """A filename that will survive being copied to another machine."""
    if not name:
        return fallback
    cleaned = "".join("-" if c in _ILLEGAL else c for c in str(name))
    cleaned = "".join(c for c in cleaned if c.isprintable())
    cleaned = " ".join(cleaned.split())          # collapse whitespace runs
    cleaned = cleaned.strip(" .")                 # trailing dots break Windows
    if not cleaned:
        return fallback
    if cleaned.split(".")[0].lower() in _RESERVED:
        cleaned = "_" + cleaned
    if len(cleaned) > limit:
        cleaned = cleaned[:limit].rstrip(" .")
    return cleaned or fallback


def unique(path):
    """`path`, or the first `path (2)`-style variant that does not exist.

    Never overwrites. A tool that silently replaced a file the user had
    already downloaded would be a tool nobody trusted twice.
    """
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for n in range(2, 1000):
        candidate = parent / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Gave up finding a free name near {path}")

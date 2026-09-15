"""Turning something a person pasted into a list of items.

Every source module answers two questions: *is this mine?* and *what is at the
other end of it?* — the second returning a list, because one URL is as likely
to be a forty-track playlist as a single video. Nothing is downloaded at this
stage. Expanding a link is cheap and reversible, and showing somebody the
forty things they are about to fetch before fetching them is the difference
between a tool and an accident.

Sources are tried in order and the first one that claims a URL gets it, so
anything more specific than yt-dlp must be registered ahead of it. yt-dlp goes
last and claims everything left, which is most of the internet.
"""

import importlib

# Specific first, general last. The names are module names in this package.
ORDER = ("local", "cobalt", "deezer", "spotify", "tidal", "ytdlp")

_loaded = {}


def _module(name):
    if name not in _loaded:
        _loaded[name] = importlib.import_module(f"sources.{name}")
    return _loaded[name]


def modules():
    return [_module(name) for name in ORDER]


def module_for(name):
    """The source module a given origin came from, or None."""
    if name in ORDER:
        return _module(name)
    return None


def fetcher_for(item):
    """How to get this item's bytes.

    A source that hands back a plain address may say how to fetch it; anything
    that does not gets yt-dlp, which is the right answer for almost everything.
    """
    module = module_for(item.origin)
    found = getattr(module, "fetch", None) if module else None
    if found is not None:
        return found
    return _module("ytdlp").fetch


def identify(target):
    """The source module that claims this URL or path, or None."""
    text = str(target).strip()
    for module in modules():
        try:
            if module.handles(text):
                return module
        except Exception:
            continue
    return None


def expand(target, **options):
    """A URL or path in, a list of Items out.

    Raises NoSourceFor when nothing claims it, which is a sentence the
    interface can show rather than an empty list that looks like a bug.
    """
    text = str(target).strip()
    module = identify(text)
    if module is None:
        raise NoSourceFor(
            f"Nothing here knows what to do with {text!r}. It should be a URL, "
            f"or the path to a file on this machine."
        )
    return module.expand(text, **options)


class NoSourceFor(ValueError):
    """Nothing claimed this input."""


class SourceError(RuntimeError):
    """A source was reached and could not produce items. Message is shown."""

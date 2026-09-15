"""Programs that turn one format into another, and which one to ask.

An engine answers `can(source_kind, target)` and, if it says yes, `plan()` and
`run()`. Adding a format siphon cannot yet handle is meant to be a new module
here and one line in ORDER — never a change to the pipeline, the queue or the
interface, none of which know what an engine is beyond those three calls.

That is the whole reason this is a registry rather than a function with a long
if-statement in it. The list of things worth converting has no end, and the
part of the program that knows how to keep a queue moving should not have to
be edited every time somebody wants webp.
"""

import importlib

# Specific first, general last — the same rule the source registry uses.
# ImageMagick gets first refusal on images because it reaches formats ffmpeg
# cannot; Ghostscript claims PDFs; LibreOffice claims office files, where the
# question is layout; pandoc claims markup, where the question is meaning;
# ffmpeg takes everything else, and the images ImageMagick is not here for.
ORDER = ("imagemagick", "ghostscript", "libreoffice", "pandoc", "ffmpeg")

_loaded = {}


def _module(name):
    if name not in _loaded:
        _loaded[name] = importlib.import_module(f"engines.{name}")
    return _loaded[name]


def modules():
    return [_module(name) for name in ORDER]


def choose(source_path, target, kind=None):
    """The engine that will convert this file, or None if nothing here can."""
    for module in modules():
        try:
            if module.can(source_path, target, kind=kind):
                return module
        except Exception:
            continue
    return None


def plan(source_path, target, kind=None):
    """What would be done to this file, described before anything is done."""
    engine = choose(source_path, target, kind=kind)
    if engine is None:
        from formats import resolve
        raise NoEngineFor(
            f"Nothing installed here can turn {source_path} into "
            f"{resolve(target).name}."
        )
    return engine.plan(source_path, target)


class NoEngineFor(RuntimeError):
    """No installed program covers this conversion."""


class ConversionError(RuntimeError):
    """An engine ran and failed. The message is shown to the user."""

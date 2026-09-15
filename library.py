"""What a finished file is called, and where it is put.

Naming is not decoration. A folder of files called `dQw4w9WgXcQ.opus` is a
folder nobody can use, and one where every track is called `01.mp3` is worse,
because the names collide the moment two albums meet. So: artist and title
where they are known, the collection as a folder when the item came from one,
a track number in front when the order matters, and never, under any
circumstance, overwriting something already there.
"""

from pathlib import Path

import formats
from paths import output_dir, safe_name, unique


def output_name(item, target, include_index=True):
    """The filename for a finished item, without a directory."""
    target = formats.resolve(target)
    stem = _stem(item)
    if include_index and item.collection_index and item.collection:
        width = 2 if (item.extra.get("collection_size") or 0) < 100 else 3
        stem = f"{item.collection_index:0{width}d} {stem}"
    return f"{safe_name(stem)}.{target.extension}"


def _stem(item):
    if item.artist and item.title:
        # An em dash, because a hyphen is already in half the track titles
        # ever written and the result is unreadable.
        return f"{item.artist} — {item.title}"
    if item.title:
        return item.title
    if item.path:
        return Path(item.path).stem
    return item.id


def destination(item, target, base=None):
    """The full path a finished item should end up at.

    A playlist or album becomes a folder. Everything else lands in the top of
    the output directory, because burying a single video two levels down is
    how people lose files.
    """
    base = Path(base) if base else output_dir()
    if item.collection:
        base = base / safe_name(item.collection)
    return base / output_name(item, target)


def place(produced, destination_path, move=True):
    """Put the finished file where it belongs, without displacing anything.

    `move=False` copies instead, which is how the promise that a source file
    is never touched survives the case where no conversion was needed and the
    "produced" file is somebody's own original.
    """
    import shutil

    produced = Path(produced)
    destination_path = Path(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    final = unique(destination_path)

    if move:
        try:
            produced.replace(final)          # same filesystem: instant
        except OSError:
            shutil.move(str(produced), str(final))   # across devices
    else:
        shutil.copy2(str(produced), str(final))
    return str(final)

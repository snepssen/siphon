"""The two nouns the whole program is built from.

An **Item** is a thing that exists: a video at a URL, a track in a playlist, a
file already on the disk. It knows what it is and where it came from, and
nothing about what anyone intends to do with it.

A **Job** is one trip through the pipeline — fetch, convert, tag, place — for
one item and one target format. Everything the interface shows is a view of
these, and both survive a restart because both are plain data.

The separation matters because of playlists: one URL produces forty items and
forty jobs, and the item list is worth showing to somebody *before* the jobs
start running.
"""

import time
import uuid
from dataclasses import dataclass, field, asdict, fields

UNKNOWN = "unknown"


@dataclass
class Item:
    """One piece of media, wherever it came from.

    `url` is set when bytes can be fetched from somewhere. `path` is set when
    they are already on this disk. A catalogue item — a Spotify track, say —
    has neither at first: it carries metadata and an `isrc`, and the resolver's
    job is to find it a `url`. `fetchable` is the question everything else
    asks.
    """

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    origin: str = UNKNOWN          # which source module produced this
    url: str = None                # fetchable address, if there is one
    path: str = None               # a file already on this machine
    kind: str = UNKNOWN            # audio / video / image / document

    title: str = None
    artist: str = None
    album: str = None
    album_artist: str = None
    track_number: int = None
    track_total: int = None
    disc_number: int = None
    year: int = None
    isrc: str = None               # the one identifier that matches exactly
    duration: float = None         # seconds
    artwork_url: str = None

    uploader: str = None           # channel/user, for things that have no artist
    webpage_url: str = None        # where a human would go to see this
    collection: str = None         # playlist or album this arrived as part of
    collection_index: int = None

    resolved_from: str = None      # set when a catalogue item was matched
    match_confidence: float = None # 0..1; None means it was never a guess
    extra: dict = field(default_factory=dict)

    @property
    def fetchable(self):
        return bool(self.url) or bool(self.path)

    @property
    def local(self):
        return bool(self.path) and not self.url

    def display(self):
        """The one line a human should see for this item."""
        if self.artist and self.title:
            return f"{self.artist} — {self.title}"
        return self.title or self.url or self.path or self.id

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return _build(cls, data)


# Job lifecycle. A job is in exactly one state; `stage` says where inside
# RUNNING it has got to, which is what the progress bar is labelled with.
QUEUED = "queued"
RUNNING = "running"
WAITING = "waiting"          # stopped, on purpose, until a person answers
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

TERMINAL = {DONE, FAILED, CANCELLED}

# WAITING is deliberately not terminal and deliberately not queued. The job
# has not failed and is not going to make any more progress on its own: it is
# a question waiting for an answer, and it survives a restart because the
# question is still worth asking tomorrow.

# Stages, in order. Not every job has every one: a local file that needs no
# conversion goes straight from RESOLVE to PLACE.
RESOLVE = "resolve"
FETCH = "fetch"
CONVERT = "convert"
TAG = "tag"
PLACE = "place"
STAGES = (RESOLVE, FETCH, CONVERT, TAG, PLACE)


@dataclass
class Job:
    """One item, one target format, one trip through the pipeline."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    item: Item = field(default_factory=Item)
    target: str = "video"          # a preset name from formats.PRESETS
    state: str = QUEUED
    stage: str = None
    progress: float = 0.0          # 0..1 within the current stage
    message: str = ""              # what to show under the bar

    output_path: str = None
    output_dir: str = None         # None means the configured default
    error: str = None
    log: list = field(default_factory=list)   # last few lines, for diagnosis

    choice: dict = None            # the options, when state is WAITING
    bytes_done: int = None
    bytes_total: int = None
    speed: float = None            # bytes/sec
    eta: float = None              # seconds

    created_at: float = field(default_factory=time.time)
    started_at: float = None
    finished_at: float = None
    batch: str = None              # groups the jobs one playlist produced

    @property
    def finished(self):
        return self.state in TERMINAL

    @property
    def elapsed(self):
        if self.started_at is None:
            return None
        return (self.finished_at or time.time()) - self.started_at

    def note(self, line, limit=40):
        """Record a line of working detail, keeping only the recent tail."""
        self.log.append(line)
        if len(self.log) > limit:
            del self.log[: len(self.log) - limit]

    def to_dict(self):
        data = asdict(self)
        data["item"] = self.item.to_dict()
        return data

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["item"] = Item.from_dict(data.get("item") or {})
        return _build(cls, data)


def _build(cls, data):
    """Construct from a dict, ignoring keys this version does not know.

    State written by an older siphon should open in a newer one rather than
    crashing it, and a field removed later should not strand somebody's queue.
    """
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})

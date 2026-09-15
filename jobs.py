"""The queue, and the four stages every job goes through.

One pipeline serves both halves of the program:

    fetch → convert → place

A URL starts at the first stage. A file already on this machine starts at the
second, and that is the only difference between siphon downloading something
and siphon converting something. There is no separate code path for offline
work, which is why offline work does not rot.

The queue survives being closed. Jobs are written to disk as they change
state, and anything found still marked *running* at startup is put back to
*queued* — because a job that was running when the process died is a job that
did not finish, and pretending otherwise would lose it silently.
"""

import json
import shutil
import threading
import time
from collections import deque
from pathlib import Path

import engines
import formats
import library
import model
import net
import paths
import resolve
import sources
from model import Job, Item

PROGRESS_INTERVAL = 0.25          # seconds between progress notifications


class Queue:
    """Jobs, workers, and the state on disk that outlives both."""

    def __init__(self, workers=2, on_change=None, state_path=None,
                 confirm_uncertain=True):
        self.workers = max(1, int(workers))
        # When a track match is merely plausible, stop and ask rather than
        # download and apologise. Turn it off for unattended runs.
        self.confirm_uncertain = confirm_uncertain
        self.on_change = on_change
        self.state_path = Path(state_path) if state_path else \
            paths.state_dir() / "queue.json"

        self._jobs = {}                  # id -> Job, insertion ordered
        self._pending = deque()
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._threads = []
        self._running = False
        self._cancelled = set()
        self._last_notify = 0.0

    # -- adding ----------------------------------------------------------

    def add_url(self, target_url, format_name, output=None, **options):
        """Expand a URL or path and queue everything it turned out to be."""
        items = sources.expand(target_url, **options)
        batch = None
        if len(items) > 1:
            batch = items[0].collection or target_url
            for item in items:
                item.extra["collection_size"] = len(items)
        return self.add(items, format_name, output=output, batch=batch)

    def add(self, items, format_name, output=None, batch=None):
        """Queue items against a target format. Returns the jobs created."""
        formats.resolve(format_name)          # fail now, not in a worker
        created = []
        with self._lock:
            for item in items:
                job = Job(
                    item=item,
                    target=str(format_name),
                    output_dir=str(output) if output else None,
                    batch=batch,
                )
                self._jobs[job.id] = job
                self._pending.append(job.id)
                created.append(job)
            self._wake.notify_all()
        self._save()
        self._notify(force=True)
        return created

    # -- running ---------------------------------------------------------

    def start(self):
        with self._lock:
            if self._running:
                return
            self._running = True
            self._threads = [
                threading.Thread(target=self._work, name=f"siphon-{n}",
                                 daemon=True)
                for n in range(self.workers)
            ]
            for thread in self._threads:
                thread.start()

    def stop(self, wait=True, timeout=30):
        with self._lock:
            self._running = False
            self._wake.notify_all()
        if wait:
            for thread in self._threads:
                thread.join(timeout=timeout)
        self._threads = []

    def drain(self, timeout=None):
        """Block until nothing is queued or running. Returns True if it did."""
        deadline = time.time() + timeout if timeout else None
        while True:
            with self._lock:
                busy = bool(self._pending) or any(
                    j.state == model.RUNNING for j in self._jobs.values()
                )
            if not busy:
                return True
            if deadline and time.time() > deadline:
                return False
            time.sleep(0.1)

    def _work(self):
        while True:
            with self._lock:
                while self._running and not self._pending:
                    self._wake.wait(timeout=0.5)
                if not self._running:
                    return
                job_id = self._pending.popleft()
                job = self._jobs.get(job_id)
                if job is None or job.state != model.QUEUED:
                    continue
                job.state = model.RUNNING
                job.started_at = time.time()
            self._notify(force=True)
            self._process(job)

    # -- the pipeline ----------------------------------------------------

    def _process(self, job):
        workdir = paths.work_dir() / job.id
        produced_is_original = False
        # A `return` from inside the try still runs the finally below, so the
        # waiting path needs to say out loud that it has not finished —
        # otherwise a job stopped on a question gets stamped with a finish
        # time and starts looking like one that completed.
        paused = False
        try:
            target = formats.resolve(job.target)
            item = job.item

            # ---- resolve -----------------------------------------------
            # A catalogue item knows what it is and not where to get it.
            if not item.url and not item.path:
                job.stage = model.RESOLVE
                job.message = "Looking for a source"
                self._notify(force=True)
                _enrich(item)
                proposal = resolve.propose(item)
                if proposal.hopeless:
                    raise resolve.Unresolved(resolve.refusal(proposal))
                if proposal.confident or not self.confirm_uncertain:
                    best = resolve.accept(item, proposal.best, proposal)
                    job.note(f"matched to {best.display()} ({best.score:.0%})")
                else:
                    # Plausible, not convincing. The promise is that this is
                    # shown *before* it downloads, so the job stops here and
                    # the worker goes to do something else.
                    raise _NeedsChoice(proposal)
                self._check_cancelled(job)

            # ---- fetch -------------------------------------------------
            if item.path and not item.url:
                source_path = item.path
                job.note(f"using the file already at {item.path}")
            elif item.url:
                job.stage = model.FETCH
                job.message = "Fetching"
                self._notify(force=True)
                fetch = sources.fetcher_for(item)
                source_path = fetch(
                    item, target, workdir,
                    on_progress=lambda u: self._progress(job, u),
                    should_cancel=lambda: job.id in self._cancelled,
                )
                job.note(f"fetched {Path(source_path).name}")
            else:
                raise RuntimeError(
                    f"{item.display()} has neither an address to fetch from "
                    f"nor a file on this machine."
                )
            self._check_cancelled(job)

            # ---- convert -----------------------------------------------
            job.stage = model.CONVERT
            job.progress = 0.0
            job.message = "Working out what needs doing"
            self._notify(force=True)

            artwork = _artwork(item, workdir, target)
            engine = engines.choose(source_path, target, kind=item.kind)
            if engine is None:
                raise engines.NoEngineFor(
                    f"Nothing installed here can turn "
                    f"{Path(source_path).suffix or 'that file'} into "
                    f"{target.name}."
                )
            # Metadata is written only for things siphon fetched. A file
            # already on this disk arrived with whatever tags its owner gave
            # it, and `local` fills in a title from the filename purely so the
            # queue has something to display — writing that back would
            # overwrite a real title with a filename. ffmpeg's default is to
            # carry the existing tags across, which is exactly right here.
            fetched_metadata = None if (item.path and not item.url) else item
            plan = engine.plan(source_path, target, metadata=fetched_metadata,
                               artwork=artwork)
            job.message = plan.summary
            job.note(plan.summary)
            for line in plan.detail:
                job.note(line)
            self._notify(force=True)

            if plan.action == "none":
                produced = source_path
                # The one case where the produced file is the user's own: it
                # must be copied out, never moved, and never written over.
                produced_is_original = bool(item.path) and not item.url
            else:
                produced = engine.run(
                    plan, source_path, workdir / f"out.{plan.suffix}",
                    on_progress=lambda u: self._progress(job, u),
                    should_cancel=lambda: job.id in self._cancelled,
                )
            # An engine may legitimately produce more than one file — a PDF
            # rendered to images is one per page. Taking only the first would
            # lose the rest to the working directory's cleanup, silently.
            extras = []
            if isinstance(produced, (list, tuple)):
                produced, extras = produced[0], list(produced[1:])
            self._check_cancelled(job)

            # ---- place -------------------------------------------------
            job.stage = model.PLACE
            job.progress = 1.0
            job.message = "Filing it"
            self._notify(force=True)

            destination = library.destination(item, target, job.output_dir)
            if extras:
                # Numbered from one, so page ten sorts after page nine.
                width = max(2, len(str(len(extras) + 1)))
                destination = destination.with_name(
                    f"{destination.stem} {1:0{width}d}{destination.suffix}")
            final = library.place(produced, destination,
                                  move=not produced_is_original)

            placed = [final]
            for number, spare in enumerate(extras, start=2):
                width = max(2, len(str(len(extras) + 1)))
                beside = Path(final).with_name(
                    f"{Path(final).stem[:-len(str(1).zfill(width))].rstrip()} "
                    f"{number:0{width}d}{Path(final).suffix}")
                placed.append(library.place(spare, beside, move=True))

            job.output_path = final
            job.extra_outputs = placed[1:] if len(placed) > 1 else []
            job.state = model.DONE
            job.message = (f"Saved {len(placed)} files to "
                           f"{Path(final).parent}" if len(placed) > 1
                           else f"Saved to {final}")
            job.note(job.message)

        except _NeedsChoice as question:
            paused = True
            with self._lock:
                job.state = model.WAITING
                job.stage = model.RESOLVE
                job.progress = 0.0
                job.choice = question.proposal.to_dict()
                best = question.proposal.best
                job.message = (
                    f"Not sure this is right: “{best.display()}” "
                    f"({best.score:.0%}) — {', '.join(best.reasons)}"
                )
                job.note(job.message)
            shutil.rmtree(workdir, ignore_errors=True)
            self._save()
            self._notify(force=True)
            return                       # not finished; finished_at stays None

        except Exception as error:               # noqa: BLE001 — reported, not swallowed
            if job.id in self._cancelled or _is_cancellation(error):
                job.state = model.CANCELLED
                job.message = "Stopped."
            else:
                job.state = model.FAILED
                job.error = str(error) or error.__class__.__name__
                job.message = job.error
                job.note(f"failed: {job.error}")
        finally:
            if not paused:
                job.finished_at = time.time()
                self._cancelled.discard(job.id)
                shutil.rmtree(workdir, ignore_errors=True)
                self._save()
                self._notify(force=True)

    def _check_cancelled(self, job):
        if job.id in self._cancelled:
            raise _Cancelled()

    def _progress(self, job, update):
        job.progress = update.get("progress", job.progress) or job.progress
        for key in ("bytes_done", "bytes_total", "speed", "eta"):
            if update.get(key) is not None:
                setattr(job, key, update[key])
        status = update.get("status")
        if status == "finished" and job.stage == model.FETCH:
            job.message = "Fetched"
        self._notify()

    # -- control ---------------------------------------------------------

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.finished:
                return False
            self._cancelled.add(job_id)
            if job.state == model.QUEUED:
                job.state = model.CANCELLED
                job.message = "Stopped before it started."
                job.finished_at = time.time()
        self._save()
        self._notify(force=True)
        return True

    def waiting(self):
        """Jobs stopped on a question nobody has answered yet."""
        with self._lock:
            return [j for j in self._jobs.values() if j.state == model.WAITING]

    def choose(self, job_id, url):
        """Answer a waiting job: this is the one. Puts it back in the queue."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != model.WAITING:
                return False
            options = (job.choice or {}).get("options") or []
            picked = next((o for o in options if o.get("url") == url), None)
            if picked is None:
                return False
            job.item.url = picked["url"]
            job.item.resolved_from = job.item.origin
            job.item.match_confidence = picked.get("score")
            job.item.extra["match"] = {
                "title": picked.get("title"),
                "uploader": picked.get("uploader"),
                "duration": picked.get("duration"),
                "score": picked.get("score"),
                "reasons": picked.get("reasons") or [],
                "query": (job.choice or {}).get("query"),
                "chosen_by": "you",
            }
            job.choice = None
            job.state = model.QUEUED
            job.stage = None
            job.message = f"Using “{picked.get('title')}”."
            job.note(job.message)
            self._pending.append(job.id)
            self._wake.notify_all()
        self._save()
        self._notify(force=True)
        return True

    def skip(self, job_id):
        """Answer a waiting job: none of these. Nothing is downloaded."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != model.WAITING:
                return False
            job.state = model.CANCELLED
            job.choice = None
            job.message = "Skipped — none of the matches looked right."
            job.finished_at = time.time()
        self._save()
        self._notify(force=True)
        return True

    def retry(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or not job.finished:
                return False
            job.state = model.QUEUED
            job.stage = None
            job.progress = 0.0
            job.error = None
            job.message = "Queued again."
            job.started_at = job.finished_at = None
            self._pending.append(job.id)
            self._wake.notify_all()
        self._save()
        self._notify(force=True)
        return True

    def forget_finished(self):
        with self._lock:
            for job_id in [j.id for j in self._jobs.values() if j.finished]:
                del self._jobs[job_id]
        self._save()
        self._notify(force=True)

    # -- reading ---------------------------------------------------------

    def all(self):
        with self._lock:
            return list(self._jobs.values())

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def snapshot(self):
        with self._lock:
            return [job.to_dict() for job in self._jobs.values()]

    def summary(self):
        counts = {}
        for job in self.all():
            counts[job.state] = counts.get(job.state, 0) + 1
        return counts

    # -- persistence -----------------------------------------------------

    def _save(self):
        try:
            payload = {"version": 1, "jobs": self.snapshot()}
            tmp = self.state_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=1)
            tmp.replace(self.state_path)
        except OSError:
            pass          # a queue that cannot be written is not a queue that stops

    def load(self):
        """Restore a saved queue. Anything mid-flight goes back to queued."""
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return 0
        restored = 0
        with self._lock:
            for data in payload.get("jobs", []):
                try:
                    job = Job.from_dict(data)
                except Exception:
                    continue
                if job.state == model.RUNNING:
                    # It was not finished when the process ended, whatever the
                    # file says. Half a download is not a download.
                    job.state = model.QUEUED
                    job.stage = None
                    job.progress = 0.0
                    job.message = "Queued again after a restart."
                self._jobs[job.id] = job
                if job.state == model.QUEUED:
                    self._pending.append(job.id)
                restored += 1
        return restored

    # -- notification ----------------------------------------------------

    def _notify(self, force=False):
        if not self.on_change:
            return
        now = time.time()
        if not force and now - self._last_notify < PROGRESS_INTERVAL:
            return
        self._last_notify = now
        try:
            self.on_change(self)
        except Exception:
            pass          # an interface that throws must not stop the queue


def _enrich(item):
    """Let a catalogue source fill in what its listing left out."""
    if item.origin == "deezer":
        from sources import deezer
        deezer.enrich(item)
    return item


def _artwork(item, workdir, target):
    """Cover art on disk, or None. Never a reason for a job to fail.

    A missing cover is a worse file, not a broken one, so every failure here
    returns None and the conversion carries on without it.
    """
    if not item.artwork_url or target.kind != formats.AUDIO:
        return None
    from engines.ffmpeg import ARTWORK_CONTAINERS
    if target.container not in ARTWORK_CONTAINERS:
        return None
    try:
        raw = net.get_bytes(item.artwork_url, timeout=20, retries=2)
    except Exception:
        return None
    if not raw or len(raw) < 512:
        return None
    suffix = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    path = Path(workdir) / f"cover{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_bytes(raw)
    except OSError:
        return None
    return str(path)


class _NeedsChoice(Exception):
    """A match good enough to offer and not good enough to assume."""

    def __init__(self, proposal):
        self.proposal = proposal
        super().__init__("waiting for somebody to pick")


class _Cancelled(Exception):
    pass


def _is_cancellation(error):
    return type(error).__name__ == "Cancelled" or isinstance(error, _Cancelled)

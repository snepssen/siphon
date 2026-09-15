#!/usr/bin/env python3
"""siphon — fetch media from a URL, or convert what you already have.

The command line and the window are two views of the same queue, and neither
knows anything the other does not. This file is the first of those views.

    siphon                               open the window
    siphon get https://…                 fetch it, best quality, nothing re-encoded
    siphon get https://… --as mp3        fetch it and make an mp3
    siphon convert album/ --as flac      convert a folder already on this disk
    siphon list https://…/playlist       show what is in it, download nothing
    siphon formats                       what --as will accept
    siphon check                         what is installed and what is missing
    siphon keys                          API keys, and how to find them
"""

import argparse
import os
import shutil
import sys
import time

import credentials
import engines
import formats
import jobs as jobs_module
import model
import paths
import platform_support
import sources

VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def _tty():
    return sys.stdout.isatty()


def _width():
    return shutil.get_terminal_size((80, 24)).columns


def _size(count):
    if not count:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024


def _clock(seconds):
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


STAGE_WORDS = {
    model.FETCH: "fetching",
    model.CONVERT: "converting",
    model.PLACE: "filing",
    model.RESOLVE: "looking up",
}


def _bar(fraction, width=24):
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled + "░" * (width - filled)


class Progress:
    """One line per job, rewritten in place while it runs."""

    def __init__(self, quiet=False):
        self.quiet = quiet or not _tty()
        self._printed = 0

    def update(self, queue):
        if self.quiet:
            return
        running = [j for j in queue.all() if j.state == model.RUNNING]
        lines = []
        for job in running[:4]:
            stage = STAGE_WORDS.get(job.stage, job.stage or "starting")
            bits = [f"{_bar(job.progress)} {job.progress * 100:3.0f}%",
                    f"{stage:11}", job.item.display()[:40]]
            if job.stage == model.FETCH and job.speed:
                bits.append(f"{job.speed / 1e6:.1f} MB/s")
            if job.eta:
                bits.append(f"eta {_clock(job.eta)}")
            lines.append("  " + "  ".join(bits))
        self._render(lines)

    def _render(self, lines):
        if self._printed:
            sys.stdout.write(f"\033[{self._printed}A\033[J")
        width = _width() - 1
        for line in lines:
            sys.stdout.write(line[:width] + "\n")
        sys.stdout.flush()
        self._printed = len(lines)

    def clear(self):
        if not self.quiet and self._printed:
            sys.stdout.write(f"\033[{self._printed}A\033[J")
            sys.stdout.flush()
            self._printed = 0


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def command_get(args):
    """Fetch a URL — or a file path, which is the same pipeline minus a stage."""
    missing = platform_support.missing_required()
    if missing:
        for sentence in missing:
            print(sentence, file=sys.stderr)
        return 1

    try:
        target = formats.resolve(args.format)
    except formats.UnknownFormat as error:
        print(error, file=sys.stderr)
        return 2

    # "ask" only means anything if there is somebody there to ask. Piped into
    # a script, the choice has to be made in advance, and taking the best
    # match silently is the behaviour that surprises nobody.
    picking = getattr(args, "pick", "ask")
    if picking == "ask" and not sys.stdin.isatty():
        picking = "best"

    progress = Progress(quiet=args.quiet)
    queue = jobs_module.Queue(workers=args.workers, on_change=progress.update,
                              confirm_uncertain=picking != "best")

    try:
        items = sources.expand(args.url, recursive=args.recursive)
    except (sources.NoSourceFor, sources.SourceError) as error:
        print(error, file=sys.stderr)
        return 1

    if not items:
        print(f"Nothing to fetch at {args.url}", file=sys.stderr)
        return 1

    notice = items[0].extra.get("truncated")
    if notice:
        print(notice)

    if len(items) > 1:
        collection = items[0].collection
        for item in items:
            item.extra["collection_size"] = len(items)
        print(f"{len(items)} items"
              + (f" in “{collection}”" if collection else "")
              + f" → {target.name}")
    else:
        print(f"{items[0].display()} → {target.name}")

    queue.add(items, target.name, output=args.output,
              batch=items[0].collection if len(items) > 1 else None)
    queue.start()
    try:
        queue.drain()
    except KeyboardInterrupt:
        progress.clear()
        print("Stopping…")
        for job in queue.all():
            queue.cancel(job.id)
        queue.stop(wait=True, timeout=10)
        return 130
    progress.clear()

    # Anything that stopped to ask is asked about now, and whatever is chosen
    # goes back through the queue.
    while queue.waiting():
        for job in queue.waiting():
            _settle(queue, job, picking)
        progress = Progress(quiet=args.quiet)
        queue.on_change = progress.update
        try:
            queue.drain()
        except KeyboardInterrupt:
            break
        progress.clear()

    queue.stop()
    return _report(queue, verbose=args.verbose)


def _settle(queue, job, picking):
    """Put one uncertain match to the user, or apply the policy they chose."""
    options = (job.choice or {}).get("options") or []
    if not options:
        queue.skip(job.id)
        return
    if picking == "skip":
        queue.skip(job.id)
        return
    if picking == "best":
        queue.choose(job.id, options[0]["url"])
        return

    print(f"\nNot sure about: {job.item.display()}", end="")
    if job.item.duration:
        print(f"  ({_clock(job.item.duration)})")
    else:
        print()
    for number, option in enumerate(options[:5], start=1):
        length = _clock(option.get("duration")) if option.get("duration") else "  —  "
        print(f"  {number}. {option['score']:>4.0%}  {length:>7}  "
              f"{(option.get('uploader') or '?')[:22]:22} {option['title'][:44]}")
        if option.get("reasons"):
            print(f"          {', '.join(option['reasons'])}")
    print("  s. skip this one")

    while True:
        try:
            answer = input("Which one? [1] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            queue.skip(job.id)
            return
        if answer in {"s", "skip", "n", "no"}:
            queue.skip(job.id)
            return
        if not answer:
            answer = "1"
        if answer.isdigit() and 1 <= int(answer) <= len(options[:5]):
            queue.choose(job.id, options[int(answer) - 1]["url"])
            return
        print("  A number from the list, or s to skip.")


def _report(queue, verbose=False):
    done = [j for j in queue.all() if j.state == model.DONE]
    failed = [j for j in queue.all() if j.state == model.FAILED]
    cancelled = [j for j in queue.all() if j.state == model.CANCELLED]

    for job in done:
        where = job.output_path or ""
        size = ""
        try:
            size = f"  ({_size(os.path.getsize(where))})"
        except OSError:
            pass
        if job.extra_outputs:
            size = f"  (+{len(job.extra_outputs)} more pages)"
        uncertain = ""
        confidence = job.item.match_confidence
        if confidence is not None and confidence < 0.75:
            uncertain = f"  ← matched at {confidence:.0%}, worth a listen"
        print(f"✓ {os.path.basename(where)}{size}{uncertain}")
        if verbose:
            for line in job.log:
                print(f"    {line}")
    for job in failed:
        print(f"✗ {job.item.display()}\n    {job.error}", file=sys.stderr)
    for job in cancelled:
        print(f"– {job.item.display()} ({job.message or 'stopped'})")
    for job in queue.waiting():
        print(f"? {job.item.display()}\n    {job.message}")

    if done:
        folder = os.path.dirname(done[0].output_path or "")
        # A job can put down more than one file — a PDF rendered to images is
        # one per page — so count what was written, not how many jobs ran.
        written = sum(1 + len(job.extra_outputs or []) for job in done)
        print(f"\n{written} file{'s' if written != 1 else ''} in {folder}")
    return 1 if failed else 0


def command_list(args):
    """Show what is behind a link without fetching any of it."""
    try:
        items = sources.expand(args.url, limit=args.limit)
    except (sources.NoSourceFor, sources.SourceError) as error:
        print(error, file=sys.stderr)
        return 1

    collection = items[0].collection if items else None
    if items and items[0].extra.get("truncated"):
        print(items[0].extra["truncated"] + "\n")
    if collection:
        print(f"{collection} — {len(items)} items\n")
    for index, item in enumerate(items, start=1):
        duration = _clock(item.duration) if item.duration else ""
        print(f"{index:3d}. {item.display()[:60]:60} {duration:>8}")
    if not collection:
        item = items[0]
        print(f"\n     kind: {item.kind}")
        if item.uploader:
            print(f" uploader: {item.uploader}")
        if item.webpage_url:
            print(f"      url: {item.webpage_url}")
    return 0


def command_formats(_args):
    """What --as will accept, grouped, with what is missing marked.

    A preset nothing installed can produce is still listed — with the reason —
    because "webp is not a format siphon knows" and "webp needs one more brew
    command" are very different things to be told.
    """
    width = max(len(name) for name in formats.PRESETS)
    headings = {formats.AUDIO: "Audio", formats.VIDEO: "Video",
                formats.IMAGE: "Images", formats.DOCUMENT: "Documents"}

    for kind, names in formats.kinds().items():
        print(f"\n{headings.get(kind, kind)}")
        for name in names:
            target = formats.PRESETS[name]
            print(f"  {name:<{width}}  {target.summary}")

    missing = platform_support.survey()
    absent = [f"{key} ({entry['install']})"
              for key, entry in missing.items()
              if not entry["present"] and not entry["required"]]
    if absent:
        print("\nSome of these need a program that is not installed:")
        for line in absent:
            print(f"  {line}")

    print(f"\nThe default is “{formats.DEFAULT_PRESET}”.")
    return 0


def command_check(_args):
    """What is installed, what is missing, and what to type about it."""
    report = platform_support.survey()
    print("siphon", VERSION, "\n")

    for key, entry in report.items():
        mark = "✓" if entry["present"] else ("✗" if entry["required"] else "–")
        label = f"{mark} {key}"
        if entry["present"]:
            print(f"{label:<12} {entry['version'] or entry['path']}")
        else:
            need = "needed" if entry["required"] else "optional"
            print(f"{label:<12} not installed ({need}) — {entry['purpose']}")
            if entry["install"]:
                print(f"{'':<12} {entry['install']}")

    print(f"\noutput   {paths.output_dir()}")
    print(f"config   {paths.config_dir()}")
    print(f"state    {paths.state_dir()}")

    missing = platform_support.missing_required()
    return 1 if missing else 0


def command_keys(args):
    """API keys: what is set, how to set it, and where to go and get it."""
    if args.set:
        try:
            service, field = args.set.split(".", 1)
        except ValueError:
            print("Use service.field, for example spotify.client_id",
                  file=sys.stderr)
            return 2
        value = args.value
        if value is None:
            import getpass
            value = getpass.getpass(f"{service}.{field}: ")
        try:
            credentials.set(service, field, value)
        except KeyError as error:
            print(error, file=sys.stderr)
            return 2
        print(f"Saved {service}.{field}.")
        return 0

    if args.clear:
        try:
            service, field = args.clear.split(".", 1)
            credentials.delete(service, field)
        except (ValueError, KeyError) as error:
            print(error, file=sys.stderr)
            return 2
        print(f"Cleared {args.clear}.")
        return 0

    status = credentials.status()
    print(f"Kept in {status['storage']}.\n")
    for service in status["services"]:
        mark = "✓" if service["complete"] else "·"
        print(f"{mark} {service['label']}")
        print(f"    {service['why']}")
        for field in service["fields"]:
            state = field["hint"] if field["set"] else "not set"
            print(f"    {field['key']:<16} {state}")
        if not service["complete"] and service["steps"]:
            print(f"    Where to find it — {service['url']}")
            for step in service["steps"]:
                print(f"      · {step}")
        print()
    for name, sentence in status["no_setup_needed"].items():
        print(f"  {name:<12} {sentence}")
    print(f"\nSet one with:  siphon keys --set spotify.client_id")
    return 0


def command_window(args):
    """Open the window. The queue is the same one the command line uses."""
    import app
    return app.serve(port=args.port, open_browser=not args.no_open,
                     workers=args.workers)


def command_setup(args):
    """Offer to install everything siphon needs and does not ship."""
    import bootstrap
    return 0 if bootstrap.offer(assume_yes=args.yes) else 1


def command_cobalt(args):
    """Install, start, stop or check a local cobalt — no Docker involved."""
    import cobalt_service as service

    if args.action == "status":
        state = service.status()
        print(f"installed : {'yes' if state['installed'] else 'no'}  "
              f"({state['path']})")
        print(f"running   : {'yes' if state['running'] else 'no'}  "
              f"{state['url']}")
        if state["version"]:
            print(f"version   : cobalt {state['version']}")
        if state["node"]:
            print(f"node      : {state['node']}")
        if state["missing"]:
            print(f"missing   : {', '.join(state['missing'])} — "
                  f"run `siphon setup`")
        return 0

    if args.action == "install":
        ok, detail = service.install(on_line=print, update=args.update)
        print(detail)
        return 0 if ok else 1

    if args.action == "start":
        ok, detail = service.ensure(on_line=print)
        print(detail)
        if ok:
            credentials.set("cobalt", "instance_url", detail)
            print("Saved as siphon's cobalt instance. Prefix a link with "
                  "`cobalt:` to fetch it that way.")
        return 0 if ok else 1

    if args.action == "stop":
        print("Stopped." if service.stop() else "It was not running.")
        return 0

    if args.action == "remove":
        service.remove()
        print("Removed cobalt and everything it installed.")
        return 0

    return 2


def command_plan(args):
    """Say what would be done to a file, and do none of it."""
    try:
        target = formats.resolve(args.format)
        plan = engines.plan(args.path, target)
    except (formats.UnknownFormat, engines.NoEngineFor,
            engines.ConversionError) as error:
        print(error, file=sys.stderr)
        return 1
    print(plan.summary)
    for line in plan.detail:
        print(f"  · {line}")
    if plan.action != "none":
        print(f"\n  ffmpeg {' '.join(plan.argv)}")
    return 0


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="siphon",
        description="Fetch media from a URL, or convert what you already have.",
    )
    parser.add_argument("--version", action="version", version=f"siphon {VERSION}")
    sub = parser.add_subparsers(dest="command")

    def add_common(p):
        p.add_argument("--as", dest="format", default=formats.DEFAULT_PRESET,
                       metavar="FORMAT",
                       help="target format; see `siphon formats`")
        p.add_argument("--to", dest="output", default=None, metavar="DIR",
                       help="where to put the result")
        p.add_argument("-j", "--workers", type=int, default=2,
                       help="how many at once (default 2)")
        p.add_argument("-q", "--quiet", action="store_true")
        p.add_argument("-v", "--verbose", action="store_true",
                       help="show what was decided for each file")
        p.add_argument("-r", "--recursive", action="store_true",
                       help="for a folder, include what is inside its folders")
        p.add_argument("--pick", choices=("ask", "best", "skip"), default="ask",
                       help="what to do when a track match is uncertain: ask "
                            "(default), take the best, or skip it")

    get = sub.add_parser("get", help="fetch a URL")
    get.add_argument("url", help="a link, or a path to a file or folder")
    add_common(get)
    get.set_defaults(handler=command_get)

    convert = sub.add_parser("convert", help="convert a file or folder")
    convert.add_argument("url", metavar="path", help="a file or folder")
    add_common(convert)
    convert.set_defaults(handler=command_get)

    listing = sub.add_parser("list", help="show what is behind a link")
    listing.add_argument("url")
    listing.add_argument("--limit", type=int, default=None)
    listing.set_defaults(handler=command_list)

    plan = sub.add_parser("plan", help="say what converting a file would do")
    plan.add_argument("path")
    plan.add_argument("--as", dest="format", default="mp3", metavar="FORMAT")
    plan.set_defaults(handler=command_plan)

    window = sub.add_parser("window", help="open the window")
    window.add_argument("--port", type=int, default=7788)
    window.add_argument("--no-open", action="store_true",
                        help="start the server without opening a browser")
    window.add_argument("-j", "--workers", type=int, default=2)
    window.set_defaults(handler=command_window)

    setup = sub.add_parser("setup", help="install what siphon needs")
    setup.add_argument("-y", "--yes", action="store_true",
                       help="install everything without asking")
    setup.set_defaults(handler=command_setup)

    cob = sub.add_parser("cobalt", help="run your own cobalt, without Docker")
    cob.add_argument("action", nargs="?", default="status",
                     choices=("status", "install", "start", "stop", "remove"))
    cob.add_argument("--update", action="store_true",
                     help="for install: pull cobalt's latest source first")
    cob.set_defaults(handler=command_cobalt)

    fmt = sub.add_parser("formats", help="list the target formats")
    fmt.set_defaults(handler=command_formats)

    check = sub.add_parser("check", help="what is installed, what is missing")
    check.set_defaults(handler=command_check)

    keys = sub.add_parser("keys", help="API keys and where to find them")
    keys.add_argument("--set", metavar="SERVICE.FIELD")
    keys.add_argument("--value", metavar="VALUE",
                      help="omit to be prompted without it being echoed")
    keys.add_argument("--clear", metavar="SERVICE.FIELD")
    keys.set_defaults(handler=command_keys)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        # No command means the window. Somebody who types `siphon` and gets a
        # wall of usage has been told to go away by the thing they just opened.
        import app
        return app.serve()
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())

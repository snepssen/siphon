#!/usr/bin/env python3
"""siphon — fetch media from a URL, or convert what you already have.

The command line and the window are two views of the same queue, and neither
knows anything the other does not. This file is the first of those views.

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

    progress = Progress(quiet=args.quiet)
    queue = jobs_module.Queue(workers=args.workers, on_change=progress.update)

    try:
        items = sources.expand(args.url, recursive=args.recursive)
    except (sources.NoSourceFor, sources.SourceError) as error:
        print(error, file=sys.stderr)
        return 1

    if not items:
        print(f"Nothing to fetch at {args.url}", file=sys.stderr)
        return 1

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
    queue.stop()
    progress.clear()

    return _report(queue, verbose=args.verbose)


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
        print(f"✓ {os.path.basename(where)}{size}")
        if verbose:
            for line in job.log:
                print(f"    {line}")
    for job in failed:
        print(f"✗ {job.item.display()}\n    {job.error}", file=sys.stderr)
    for job in cancelled:
        print(f"– {job.item.display()} (stopped)")

    if done:
        folder = os.path.dirname(done[0].output_path or "")
        print(f"\n{len(done)} file{'s' if len(done) != 1 else ''} in {folder}")
    return 1 if failed else 0


def command_list(args):
    """Show what is behind a link without fetching any of it."""
    try:
        items = sources.expand(args.url, limit=args.limit)
    except (sources.NoSourceFor, sources.SourceError) as error:
        print(error, file=sys.stderr)
        return 1

    collection = items[0].collection if items else None
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
    print("Formats --as will accept:\n")
    width = max(len(name) for name in formats.PRESETS)
    for name, target in formats.PRESETS.items():
        print(f"  {name:<{width}}  {target.summary}")
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
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
